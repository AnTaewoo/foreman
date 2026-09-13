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

import re
from pathlib import Path
from typing import Any, TypedDict, cast

import structlog
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from agents.base import (
    AgentInput,
    AgentOutput,
    Artifact,
    BaseAgent,
    DecisionRequest,
    Publish,
)
from agents.context import assemble_context
from agents.llm.base import Message, ModelProvider
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
SYSTEM_PROMPT = """You are a Coding Agent working inside a git worktree of a real repository.
Rules: modify only files under owned_paths; never add a dependency or change pyproject/package
manifests; keep the existing conventions; make the smallest change that satisfies the task and
its tests. Verification command: {test_command}.
File paths are relative to the repository root. Copy the import style of the existing files shown
(e.g. if existing tests import `app.x`, do the same — never invent a package prefix). Only call
functions and attributes that exist in the files shown. When you change an existing file, return
its complete content with every existing line preserved unless the task says to change it."""


def is_dependency_file(path: str) -> bool:
    return DEPENDENCY_FILES.search(path) is not None


def slugify(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40].strip("-") or "task"


class FileEdit(BaseModel):
    path: str
    content: str


class EditPlan(BaseModel):
    """LLM 구조화 출력: 전체 파일 내용으로 덮어쓴다 (작은 모델도 안정적)."""

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
    ) -> None:
        super().__init__(publish=publish, provider=provider, model=model)
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
            parts = [
                assemble_context(
                    input,
                    token_budget=self._token_budget,
                    system=system,
                    related_files=await related_files(fs, ctx, spec=input.task.spec),
                ).user_message(),
                f"## Plan\n{state.get('plan', '')}",
            ]
            if state.get("test_output"):
                parts.append(
                    f"## Previous attempt {attempt - 1} failed. Test output:\n"
                    f"```\n{state['test_output'][-4000:]}\n```\n"
                    "Fix the code and/or tests so the command passes."
                )
            parts.append("Return the complete content of every file you create or change.")
            c = await llm([Message(role="user", content="\n\n".join(parts))], schema=EditPlan)
            plan = c.parsed if isinstance(c.parsed, EditPlan) else None
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
            return {"attempt": attempt, "edit": plan.model_dump(), "denied_files": []}

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
            output = (result.stdout + "\n" + result.stderr).strip()
            return {"tests_passed": result.exit_code == 0, "test_output": output}

        def route_after_tests(state: CodingState) -> str:
            if state.get("tests_passed"):
                return "push"
            return "edit" if int(state.get("attempt", 0)) < input.task.max_attempts else "fail"

        async def fail(state: CodingState) -> dict[str, Any]:
            await git.commit(
                "wip: failing attempt (kept for inspection)", issue_number=input.task.issue_number
            )
            try:
                await git.push(branch)
            except Exception as exc:  # WIP 보존은 최선 노력
                log.warning("coding.wip_push_failed", error=str(exc))
            await emit(
                EventType.TASK_FAILED,
                "task",
                input.task.id,
                {
                    "run_id": input.run_id,
                    "reason": "tests_failed",
                    "attempt": int(state.get("attempt", 0)),
                },
            )
            return {
                "outcome": "failed",
                "error": f"tests failed after {state.get('attempt')} attempts",
            }

        async def push(state: CodingState) -> dict[str, Any]:
            await git.push(branch)
            return {}

        async def open_pr(state: CodingState) -> dict[str, Any]:
            title = f"[T-{input.task.issue_number or '?'}] {input.task.title}"
            pr = await gh.open_pr(
                head=branch, title=title, body=str(state.get("plan", ""))[:2000], draft=True
            )
            await emit(
                EventType.PR_OPENED,
                "pr",
                str(pr.number),
                {
                    "task_id": input.task.id,
                    "run_id": input.run_id,
                    "pr_number": pr.number,
                    "head": branch,
                    "base": input.project_context.default_branch,
                    "draft": True,
                    "url": pr.url,
                },
            )
            return {"pr_number": pr.number, "pr_url": pr.url}

        async def summarize(state: CodingState) -> dict[str, Any]:
            c = await llm(
                [
                    Message(
                        role="user",
                        content=(
                            f"Task: {input.task.title}\n"
                            f"Changed files: {state.get('changed_files')}\n"
                            f"PR: #{state.get('pr_number')}\n"
                            "Write a 2-4 sentence summary of what was done and what remains, "
                            "for the issue comment."
                        ),
                    )
                ]
            )
            comment_id: int | None = None
            if input.task.issue_number is not None:
                ref = await gh.comment(
                    input.task.issue_number, c.text, key=f"summary:{input.run_id}"
                )
                comment_id = ref.id
            await emit(
                EventType.TASK_COMPLETED,
                "task",
                input.task.id,
                {"run_id": input.run_id, "pr_number": state.get("pr_number")},
            )
            return {"summary": c.text, "comment_id": comment_id, "outcome": "done"}

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
            ("push", push),
            ("open_pr", open_pr),
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
            "run_tests", route_after_tests, {"push": "push", "edit": "edit", "fail": "fail"}
        )
        g.add_edge("fail", END)
        g.add_edge("push", "open_pr")
        g.add_edge("open_pr", "summarize")
        g.add_edge("summarize", END)
        graph = g.compile()

        raw = await graph.ainvoke(CodingState(attempt=0), {"recursion_limit": 60})
        state = cast(CodingState, raw)
        self.last_state = state
        self.last_event_id = ctx.last_event_id
        outcome = str(state.get("outcome") or "failed")
        artifacts: list[Artifact] = [Artifact(kind="branch", ref=branch)]
        if state.get("pr_number") is not None:
            artifacts.append(
                Artifact(kind="pr", ref=str(state["pr_number"]), url=state.get("pr_url"))
            )
        if state.get("comment_id") is not None:
            artifacts.append(Artifact(kind="comment", ref=str(state["comment_id"])))
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
