"""Coding Agent (설계 §5.2, §15.1 루프). LangGraph:

    load_context → plan_changes → edit → check_scope → commit → run_tests
        ├─ pass                      → push → open_pr → summarize → END (outcome=done)
        ├─ fail ∧ attempt < max      → edit (테스트 출력을 다음 프롬프트에 포함)
        └─ fail ∧ attempt ≥ max      → fail (WIP push, task.failed attempt=max)
    check_scope: 의존성 파일 변경 → needs_decision (Issue 코멘트 "승인 필요" + task.blocked, D-16)
    edit: owned_paths 밖 경로는 **쓰기 전에** 거부 → task.failed(scope_violation), 커밋 0

브랜치 ``ai/<epic_slug>/<issue>-<slug(title)>``는 ``run()`` 진입 시 plain git으로 만든다.
이벤트 순서: task.started → run.started → run.tool_called(CONTEXT.md가 첫 툴) … → run.finished.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field, ValidationError

from agents.base import (
    AgentInput,
    AgentOutput,
    Artifact,
    BaseAgent,
    DecisionRequest,
    Publish,
)
from agents.context import assemble_context
from agents.llm.base import Message, ModelProvider, extract_json
from agents.llm.pricing import Prices
from agents.prompts import load_prompt
from agents.tools.base import ToolContext, ToolDenied
from agents.tools.fs import FsTool
from agents.tools.git import GitTool, _git
from agents.tools.github import GitHubTool
from agents.tools.shell import ShellResult, ShellTool
from control_plane.events.schema import EntityType, EventType
from github_adapter.protocol import GitHubClient

log = structlog.get_logger(__name__)

DEPENDENCY_FILES = re.compile(
    r"(^|/)(pyproject\.toml|requirements[^/]*\.txt|uv\.lock|poetry\.lock|Pipfile(\.lock)?|"
    r"package\.json|[^/]*-lock\.(json|yaml|yml)|yarn\.lock|pnpm-lock\.yaml|go\.(mod|sum)|Cargo\.(toml|lock))$"
)


def annotate_test_output(exit_code: int, output: str) -> str:
    """pytest exit 5(수집된 테스트 0)는 실패지만 모델이 원인을 알아야 한다 (2차 라이브 (f))."""
    if exit_code == 5:
        return (
            f"{output}\n\n[no tests ran — pytest exit code 5: this task must add tests in its "
            "owned tests/ path (see owned_paths); write them, then implement]"
        )
    return output


_FRAME_RE = re.compile(r"^([\w./-]+\.py):\d+: in ")
_MISSING_RE = re.compile(r"^E\s+ModuleNotFoundError: No module named '([\w.]+)'")


def _repo_has_module(worktree: Path, top: str) -> bool:
    for _dir, dirs, files in os.walk(worktree):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        if top in dirs or f"{top}.py" in files:
            return True
    return False


def missing_environment_modules(
    output: str, worktree: Path, touched: list[str]
) -> dict[str, list[str]]:
    """P9.24: 편집으로 못 고치는 실패 — 이번 run이 안 건드린 파일이 repo에 없는 모듈을 import.
    {모듈: [그 모듈을 import한 파일]}. repo 안 모듈이나 에이전트가 쓴 파일의 누락은 제외."""
    found: dict[str, list[str]] = {}
    frame: str | None = None
    for line in output.splitlines():
        if m := _FRAME_RE.match(line):
            frame = m.group(1)
        elif (m := _MISSING_RE.match(line)) and frame is not None:
            module, file = m.group(1).split(".")[0], frame
            frame = None
            if file in touched or _repo_has_module(worktree, module):
                continue
            files = found.setdefault(module, [])
            if file not in files:
                files.append(file)
    return found


SYSTEM_PROMPT = load_prompt(
    "system", test_command="{test_command}"
)  # X.2: agents/prompts/system.md


EDIT_FORMAT = load_prompt("edit")  # X.2: agents/prompts/edit.md (형식 + 전체 파일 규칙)
# 작은 모델은 "=== FILE:"을 "### FILE:"로 쓰거나 뒤 마커·END FILE을 빼먹는다 → 경로는 한 줄,
# 뒤 마커는 선택, 블록은 다음 FILE/END FILE 마커 또는 끝까지
_MESSAGE_RE = re.compile(r"^[=#* ]*MESSAGE: ([^\n]+?)[ =#*]*$", re.MULTILINE)
_FILE_RE = re.compile(
    r"^[=#* ]*FILE: ([^\n]+?)[ =#*]*\n(.*?)(?=^[=#* ]*(?:END FILE|FILE:)|\Z)",
    re.MULTILINE | re.DOTALL,
)
_CODE_FENCE_RE = re.compile(r"\A```[\w-]*\n(.*?)```\s*\Z", re.DOTALL)


def parse_edit_plan(text: str) -> EditPlan | None:
    """파일 블록 텍스트 → EditPlan. 블록이 없으면 JSON(EditPlan) 폴백, 아니면 None (PC-4 기록)."""
    files = []
    for path, body in _FILE_RE.findall(text):
        m = (
            _CODE_FENCE_RE.match(body.strip("\n") + "\n")
            if body.lstrip().startswith("```")
            else None
        )
        content = m.group(1) if m else body
        files.append(FileEdit(path=path.strip().strip("`"), content=content))
    if files:
        msg = _MESSAGE_RE.search(text)
        return EditPlan(files=files, message=msg.group(1).strip() if msg else "wip")
    try:
        return EditPlan.model_validate_json(extract_json(text))
    except (ValidationError, ValueError):
        return None


def is_dependency_file(path: str) -> bool:
    return DEPENDENCY_FILES.search(path) is not None


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40].strip("-") or "task"


class FileEdit(BaseModel):
    path: str
    content: str


class EditPlan(BaseModel):
    """편집 결과: 전체 파일 내용으로 덮어쓴다. 응답 형식은 파일 블록 텍스트(``parse_edit_plan``)."""

    files: list[FileEdit] = Field(min_length=1)
    message: str = "wip"


class CodingState(TypedDict, total=False):
    context_md: str
    plan: str
    edit: dict[str, Any]
    changed_files: list[str]
    test_output: str
    tests_passed: bool
    attempt: int
    branch: str
    pr_number: int | None
    pr_url: str | None
    comment_id: int | None
    outcome: str
    error: str | None
    denied_files: list[str]
    dependency_files: list[str]
    touched: list[str]  # P9.24: 이번 run에서 쓴 파일 (편집 누적)
    environment: dict[str, list[str]]  # P9.24: 누락 외부 모듈 → import한 파일
    summary: str
    tokens_in: int
    tokens_out: int


class CodingAgent(BaseAgent):
    def __init__(
        self,
        *,
        publish: Publish,
        provider: ModelProvider,
        github: GitHubClient,
        repo: str,
        worktree: Path,
        model: str | None = None,
        test_command: str = "pytest -q",
        shell_timeout: float = 600.0,
        token_budget: int = 60_000,
        prices: Prices | None = None,
    ) -> None:
        super().__init__(publish=publish, provider=provider, model=model, prices=prices)
        self._github_client = github
        self._repo = repo
        self._worktree = Path(worktree)
        self._test_command = test_command
        self._shell_timeout = shell_timeout
        self._token_budget = token_budget
        self.last_state: CodingState | None = None

    # ------------------------------------------------------------------ 진입
    @staticmethod
    def branch_name(input: AgentInput) -> str:
        issue = input.task.issue_number if input.task.issue_number is not None else 0
        return f"ai/{slugify(input.task.epic_slug)}/{issue}-{slugify(input.task.title)}"

    async def run(self, input: AgentInput) -> AgentOutput:
        # 브랜치는 이벤트 전에 plain git으로 (worker의 worktree 준비와 같은 단계)
        branch = self.branch_name(input)
        await _git(self._worktree, "checkout", "-q", "-B", branch)
        return await super().run(input)

    # ------------------------------------------------------------------ 실행 (그래프)
    async def execute(self, input: AgentInput) -> AgentOutput:
        ctx = ToolContext(
            worktree=self._worktree,
            owned_paths=list(input.task.owned_paths),
            run_id=input.run_id,
            task_id=input.task.id,
            project_id=input.project_context.project_id,
            goal_id=input.project_context.goal_id,
            publish=self._publish,
            default_branch=input.project_context.default_branch,
            agent_id=input.agent_id,
            last_event_id=self.last_event_id,
        )
        fs, shell, git, gh = (
            FsTool(ctx),
            ShellTool(ctx),
            GitTool(ctx),
            GitHubTool(ctx, client=self._github_client, repo=self._repo, tier=input.task.risk_tier),
        )
        branch = self.branch_name(input)
        tokens = {"in": 0, "out": 0}
        system = SYSTEM_PROMPT.format(test_command=self._test_command)

        async def llm(messages: list[Message], schema: type[BaseModel] | None = None) -> Any:
            c = await self.provider.complete(
                messages, system=system, schema=schema, model=self.model
            )
            tokens["in"] += c.tokens_in
            tokens["out"] += c.tokens_out
            return c

        async def emit(
            type_: EventType, entity: EntityType, id_: str, payload: dict[str, Any]
        ) -> None:
            # 툴 이벤트(ctx)와 Task/Run 이벤트(BaseAgent)가 한 체인을 공유
            self.last_event_id = ctx.last_event_id
            ev = await self.publish(input, type_, entity, id_, payload)
            ctx.last_event_id = ev.id

        # -- nodes
        async def load_context(state: CodingState) -> dict[str, Any]:
            try:
                context_md = await fs.read(".ai-platform/CONTEXT.md")  # §5.1: 첫 툴 호출
            except FileNotFoundError:
                context_md = ""
            return {"context_md": context_md, "attempt": 0, "branch": branch, "tokens_in": 0}

        async def plan_changes(state: CodingState) -> dict[str, Any]:
            pc = input.project_context.model_copy(
                update={"context_md": state.get("context_md") or None}
            )
            assembled = assemble_context(
                input.model_copy(update={"project_context": pc}),
                token_budget=self._token_budget,
                system=system,
                related_files=await related_files(fs, ctx, spec=input.task.spec),
            )
            c = await llm(
                [
                    Message(
                        role="user",
                        content=assembled.user_message()
                        + "\n\nWrite a short implementation plan (files, functions, tests).",
                    )
                ]
            )
            return {"plan": c.text}

        async def edit(state: CodingState) -> dict[str, Any]:
            attempt = int(state.get("attempt", 0)) + 1
            pc = input.project_context.model_copy(
                update={"context_md": state.get("context_md") or None}
            )
            parts = [
                assemble_context(
                    input.model_copy(update={"project_context": pc}),
                    token_budget=self._token_budget,
                    system=system,
                    related_files=await related_files(fs, ctx, spec=input.task.spec),
                ).user_message(),
                f"## Plan\n{state.get('plan', '')}",
            ]
            if state.get("test_output"):
                parts.append(
                    load_prompt(
                        "retry",
                        attempt=str(attempt - 1),
                        test_output=str(state["test_output"])[-4000:],
                    )
                )
            parts.append(EDIT_FORMAT)
            c = await llm([Message(role="user", content="\n\n".join(parts))])
            plan = parse_edit_plan(c.text)
            if plan is None:
                return {
                    "attempt": attempt,
                    "error": "edit plan invalid",
                    "tests_passed": False,
                    "test_output": f"model returned invalid EditPlan: {c.text[:500]}",
                }
            # owned 사전 검사: 하나라도 밖이면 아무것도 쓰지 않는다 (거부 이벤트는 fs.write가)
            unowned = [f.path for f in plan.files if not ctx.is_owned(ctx.relative(f.path))]
            if unowned:
                try:
                    await fs.write(unowned[0], "")  # ToolDenied → run.tool_denied
                except ToolDenied:
                    pass
                return {"attempt": attempt, "denied_files": unowned, "outcome": "failed"}
            for f in plan.files:
                await fs.write(f.path, f.content)
            touched = list(state.get("touched") or [])
            touched += [
                ctx.relative(f.path) for f in plan.files if ctx.relative(f.path) not in touched
            ]
            return {
                "attempt": attempt,
                "edit": plan.model_dump(),
                "denied_files": [],
                "touched": touched,
            }

        def route_after_edit(state: CodingState) -> str:
            if state.get("denied_files"):
                return "scope_violation"
            if state.get("error") == "edit plan invalid":
                return "run_tests"  # 실패로 흘려서 재시도/포기 규칙을 탄다
            return "check_scope"

        async def scope_violation(state: CodingState) -> dict[str, Any]:
            await emit(
                EventType.TASK_FAILED,
                "task",
                input.task.id,
                {
                    "run_id": input.run_id,
                    "reason": "scope_violation",
                    "attempt": input.task.attempt,
                    "files": state.get("denied_files", []),
                },
            )
            return {"outcome": "failed", "error": f"scope violation: {state.get('denied_files')}"}

        async def check_scope(state: CodingState) -> dict[str, Any]:
            changed = await git.changed_files()
            deps = [p for p in changed if is_dependency_file(p)]
            return {"changed_files": changed, "dependency_files": deps}

        def route_after_scope(state: CodingState) -> str:
            return "needs_decision" if state.get("dependency_files") else "commit"

        async def needs_decision(state: CodingState) -> dict[str, Any]:
            files = state.get("dependency_files", [])
            body = (
                "**승인 필요** — 이 Task는 의존성 파일을 바꾸려 합니다: "
                + ", ".join(f"`{f}`" for f in files)
                + "\n\n설계 §8.2에 따라 T2 결정입니다. `/approve` 또는 `/reject <이유>`."
            )
            if input.task.issue_number is not None:
                await gh.comment(
                    input.task.issue_number, body, key=f"needs-decision:{input.run_id}"
                )
            await emit(
                EventType.TASK_BLOCKED,
                "task",
                input.task.id,
                {"reason": "needs_decision", "files": files},
            )
            return {"outcome": "needs_decision"}

        async def commit(state: CodingState) -> dict[str, Any]:
            message = str((state.get("edit") or {}).get("message") or "wip")
            await git.commit(message, issue_number=input.task.issue_number)
            return {}

        async def run_tests(state: CodingState) -> dict[str, Any]:
            if state.get("error") == "edit plan invalid":
                return {"tests_passed": False, "error": None}
            result: ShellResult = await shell.run(self._test_command, timeout=self._shell_timeout)
            output = annotate_test_output(
                result.exit_code, (result.stdout + "\n" + result.stderr).strip()
            )
            env: dict[str, list[str]] = {}
            if result.exit_code != 0:
                env = missing_environment_modules(
                    output, ctx.worktree, list(state.get("touched") or [])
                )
            return {
                "tests_passed": result.exit_code == 0,
                "test_output": output,
                "environment": env,
            }

        def route_after_tests(state: CodingState) -> str:
            if state.get("tests_passed"):
                return "push"
            if state.get("environment"):
                return "environment"  # P9.24: 편집으로 못 고친다 — 재시도 없이 멈춘다
            return "edit" if int(state.get("attempt", 0)) < input.task.max_attempts else "fail"

        async def fail(state: CodingState) -> dict[str, Any]:
            await git.commit(
                "wip: failing attempt (kept for inspection)", issue_number=input.task.issue_number
            )
            try:
                await git.push(branch)
            except Exception as exc:  # WIP 보존은 최선 노력
                log.warning("coding.wip_push_failed", error=str(exc))
            # P9 버그 #4: attempt는 이 run의 번호(Scheduler 기준), 편집 반복 횟수는 edit_rounds
            # P9 버그 #5: 마지막 테스트 출력 꼬리와 WIP 브랜치를 남긴다 (콘솔·Issue 코멘트·push)
            await emit(
                EventType.TASK_FAILED,
                "task",
                input.task.id,
                {
                    "run_id": input.run_id,
                    "reason": "tests_failed",
                    "attempt": input.task.attempt,
                    "edit_rounds": int(state.get("attempt", 0)),
                    "branch": branch,
                    "test_output": str(state.get("test_output") or "")[-2000:],
                },
            )
            return {
                "outcome": "failed",
                "error": f"tests failed after {state.get('attempt')} edit rounds",
            }

        async def environment(state: CodingState) -> dict[str, Any]:
            modules = dict(state.get("environment") or {})
            await emit(
                EventType.TASK_BLOCKED,
                "task",
                input.task.id,
                {
                    "run_id": input.run_id,
                    "reason": "environment",
                    "attempt": input.task.attempt,
                    "edit_rounds": int(state.get("attempt", 0)),
                    "modules": modules,
                    "test_output": str(state.get("test_output") or "")[-2000:],
                },
            )
            return {
                "outcome": "blocked",
                "error": f"environment: missing modules {sorted(modules)}",
            }

        async def push(state: CodingState) -> dict[str, Any]:
            await git.push(branch)
            await emit(  # D-37: 산출물은 브랜치. PR은 control plane(PrOpener)이 연다
                EventType.RUN_ARTIFACT_PRODUCED,
                "run",
                input.run_id,
                {"kind": "branch", "ref": branch},
            )
            return {}

        async def summarize(state: CodingState) -> dict[str, Any]:
            c = await llm(
                [
                    Message(
                        role="user",
                        content=(
                            f"Task: {input.task.title}\n"
                            f"Changed files: {state.get('changed_files')}\n"
                            f"Branch: {branch}\n"
                            "Write a 2-4 sentence summary of what was done and what remains, "
                            "for the pull request description and the issue comment."
                        ),
                    )
                ]
            )
            # D-37: Issue 코멘트·PR은 control plane(PrOpener)이 task.completed를 받아 처리한다
            await emit(
                EventType.TASK_COMPLETED,
                "task",
                input.task.id,
                {"run_id": input.run_id, "branch": branch, "summary": c.text},
            )
            return {"summary": c.text, "outcome": "done"}

        g: StateGraph[CodingState, None, CodingState, CodingState] = StateGraph(CodingState)
        for name, fn in (
            ("load_context", load_context),
            ("plan_changes", plan_changes),
            ("edit", edit),
            ("scope_violation", scope_violation),
            ("check_scope", check_scope),
            ("needs_decision", needs_decision),
            ("commit", commit),
            ("run_tests", run_tests),
            ("fail", fail),
            ("environment", environment),
            ("push", push),
            ("summarize", summarize),
        ):
            g.add_node(name, fn)
        g.add_edge(START, "load_context")
        g.add_edge("load_context", "plan_changes")
        g.add_edge("plan_changes", "edit")
        g.add_conditional_edges(
            "edit",
            route_after_edit,
            {
                "scope_violation": "scope_violation",
                "check_scope": "check_scope",
                "run_tests": "run_tests",
            },
        )
        g.add_edge("scope_violation", END)
        g.add_conditional_edges(
            "check_scope",
            route_after_scope,
            {"needs_decision": "needs_decision", "commit": "commit"},
        )
        g.add_edge("needs_decision", END)
        g.add_edge("commit", "run_tests")
        g.add_conditional_edges(
            "run_tests",
            route_after_tests,
            {"push": "push", "edit": "edit", "fail": "fail", "environment": "environment"},
        )
        g.add_edge("fail", END)
        g.add_edge("environment", END)
        g.add_edge("push", "summarize")
        g.add_edge("summarize", END)
        graph = g.compile()

        raw = await graph.ainvoke(CodingState(attempt=0), {"recursion_limit": 60})
        state = cast(CodingState, raw)
        self.last_state = state
        self.last_event_id = ctx.last_event_id
        outcome = str(state.get("outcome") or "failed")
        artifacts: list[Artifact] = [
            Artifact(kind="branch", ref=branch)
        ]  # D-37: PR은 control plane
        decision = None
        if outcome == "needs_decision":
            decision = DecisionRequest(
                type="dependency",
                reason="dependency manifest changed",
                files=state.get("dependency_files", []),
                options=["approve the dependency", "reject and re-plan"],
                recommendation="review the diff",
            )
        return AgentOutput(
            outcome=cast(Any, outcome),  # Any: Outcome Literal로 좁히기
            artifacts=artifacts,
            decision_request=decision,
            summary=str(state.get("summary") or state.get("error") or ""),
            tokens_in=tokens["in"],
            tokens_out=tokens["out"],
            error=state.get("error"),
        )


SKIP_DIRS = (".venv", "node_modules", ".git", ".ai-platform", "__pycache__", ".pytest_cache")
_SPEC_PATH = re.compile(r"([\w./-]+\.[A-Za-z0-9]+)")  # 확장자 있는 경로 토큰(백틱 유무 무관)


async def related_files(
    fs: FsTool, ctx: ToolContext, *, spec: str = "", limit: int = 12
) -> dict[str, str]:
    """편집 프롬프트의 관련 파일 (PC-4 기록): owned_paths에 걸리는 기존 파일 → spec에 백틱으로
    언급된 경로 → owned 파일과 같은 디렉토리의 기존 파일(import 관례·기존 API). 최대 limit개."""
    listed = [
        rel
        for rel in await fs.list(".")
        if not rel.startswith(SKIP_DIRS) and "/__pycache__/" not in rel
    ]
    owned = [rel for rel in listed if ctx.is_owned(rel)]
    mentioned = [
        p.strip("/") for p in _SPEC_PATH.findall(spec) if (ctx.worktree / p.strip("/")).is_file()
    ]
    owned_dirs = {Path(p).parent.as_posix() for p in ctx.owned_paths} | {
        Path(p).parent.as_posix() for p in owned
    }
    siblings = [rel for rel in listed if Path(rel).parent.as_posix() in owned_dirs]
    out: dict[str, str] = {}
    for rel in [*owned, *mentioned, *siblings]:
        if rel in out or len(out) >= limit:
            continue
        try:
            out[rel] = await fs.read(rel)
        except (ToolDenied, FileNotFoundError, UnicodeDecodeError):
            continue
    return out
