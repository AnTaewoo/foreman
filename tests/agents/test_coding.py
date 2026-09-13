"""P4.3 — Coding Agent (red a~f): FakeProvider 스크립트 + worktree + bare remote."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from agents.base import AgentInput, AgentMemory, ProjectContext, RunBudget, TaskRef
from agents.coding import CodingAgent, is_dependency_file
from agents.llm.fake import FakeProvider
from github_adapter.dry_run import DryRunGitHubClient
from tests.agents.conftest import Spy, git

SCRIPTS = Path(__file__).resolve().parent.parent / "fixtures" / "coding_scripts"
REPO = "org/demo"


def script(name: str) -> list[Any]:
    return json.loads((SCRIPTS / f"{name}.json").read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def make_input(worktree: Path, owned: list[str] | None = None) -> AgentInput:
    return AgentInput(
        task=TaskRef(
            id="01TASK",
            title="Add users module",
            spec="Add src/app/users.py list_users() + tests",
            kind="feature",
            role_required="coding",
            owned_paths=owned or ["src/app/**", "tests/**"],
            issue_number=12,
            epic_slug="users-api",
            risk_tier="T1",
            attempt=1,
            max_attempts=3,
        ),
        project_context=ProjectContext(
            project_id="P1",
            goal_id="G1",
            repo=REPO,
            default_branch="main",
            context_md=(worktree / ".ai-platform" / "CONTEXT.md").read_text(),
        ),
        memory=AgentMemory(),
        budget=RunBudget(),
        run_id="01RUN",
        agent_id="coding-1",
    )


def make_agent(
    name: str, spy: Spy, worktree: Path, github: DryRunGitHubClient | None = None
) -> CodingAgent:
    return CodingAgent(
        publish=spy.publish,
        provider=FakeProvider(script=script(name)),
        github=github or DryRunGitHubClient(),
        repo=REPO,
        worktree=worktree,
        model="fake",
        test_command="pytest -q",
        shell_timeout=120,
    )


def test_is_dependency_file() -> None:
    for p in (
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "uv.lock",
        "poetry.lock",
        "Pipfile",
        "Pipfile.lock",
        "package.json",
        "package-lock.json",
        "yarn.lock",
        "go.mod",
        "go.sum",
        "Cargo.toml",
        "Cargo.lock",
        "sub/package.json",
    ):
        assert is_dependency_file(p), p
    for p in ("src/app/main.py", "README.md", "tests/test_x.py", "docs/lock.md"):
        assert not is_dependency_file(p), p


# (a) pass 경로 (D-37: 워커는 push + task.completed{branch, summary}까지, PR은 control plane PrOpener)
async def test_pass_path(worktree: Path, remote: Path, spy: Spy) -> None:
    github = DryRunGitHubClient()
    agent = make_agent("pass", spy, worktree, github)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "done", out
    kinds = {a.kind: a for a in out.artifacts}
    assert kinds["branch"].ref == "ai/users-api/12-add-users-module"
    assert "pr" not in kinds and "comment" not in kinds
    types = spy.types()
    assert types[:2] == ["task.started", "run.started"]
    assert (
        types[2] == "run.tool_called" and spy.events[2].payload["tool"] == "fs.read"
    )  # CONTEXT.md 첫 툴
    assert "pr.opened" not in types and "task.completed" in types and types[-1] == "run.finished"
    assert types.index("run.artifact_produced") < types.index("task.completed")
    assert "run.tool_denied" not in types and "task.failed" not in types
    produced = next(e for e in spy.events if e.type.value == "run.artifact_produced")
    assert produced.subject.entity == "run" and produced.payload == {
        "kind": "branch",
        "ref": "ai/users-api/12-add-users-module",
    }
    completed = next(e for e in spy.events if e.type.value == "task.completed")
    assert completed.payload["run_id"] == "01RUN"
    assert completed.payload["branch"] == "ai/users-api/12-add-users-module"
    assert completed.payload["summary"] == out.summary and out.summary
    # 원격에 브랜치·커밋(트레일러). GitHub 쓰기는 0 (PR·코멘트는 control plane)
    assert "ai/users-api/12-add-users-module" in git(remote, "branch", "--list")
    body = git(remote, "log", "-1", "--format=%B", "ai/users-api/12-add-users-module")
    assert "Task #12 / Run 01RUN" in body
    assert github.snapshot()["repos"] == {}
    assert not any(e.payload.get("tool") == "github.open_pr" for e in spy.events)
    assert agent.last_state is not None and agent.last_state["attempt"] == 1
    assert out.tokens_in > 0 or out.tokens_out >= 0


# (b) fail → pass, 2회차 프롬프트에 테스트 출력, WIP 커밋 유지
async def test_fail_then_pass(worktree: Path, remote: Path, spy: Spy) -> None:
    agent = make_agent("fail_then_pass", spy, worktree)
    provider = agent.provider
    assert isinstance(provider, FakeProvider)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "done"
    edit_calls = [c for c in provider.calls if "=== FILE:" in c.messages[-1].content]  # 편집 호출
    assert len(edit_calls) == 2
    second_prompt = "\n".join(m.content for m in edit_calls[1].messages)
    assert "test_list_users_empty" in second_prompt and (
        "FAILED" in second_prompt or "assert" in second_prompt
    )
    log = git(remote, "log", "--format=%s", "ai/users-api/12-add-users-module")
    assert "wip: users module" in log and "fix: correct users test" in log
    assert agent.last_state is not None and agent.last_state["attempt"] == 2


# (c) 3회 실패 → failed, task.failed(attempt=3), WIP push, PR 없음
async def test_three_failures(worktree: Path, remote: Path, spy: Spy) -> None:
    github = DryRunGitHubClient()
    agent = make_agent("fail3", spy, worktree, github)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "failed"
    failed = next(e for e in spy.events if e.type.value == "task.failed")
    assert failed.payload["attempt"] == 3 and failed.payload["reason"] == "tests_failed"
    assert (
        "pr.opened" not in spy.types()
        and github.snapshot()["repos"].get(REPO, {}).get("pulls", {}) == {}
    )
    assert "ai/users-api/12-add-users-module" in git(remote, "branch", "--list")  # WIP push
    assert spy.types()[-1] == "run.finished"
    assert (
        spy.events[-1].payload["outcome"] == "failed"
        and spy.events[-1].payload["agent_outcome"] == "failed"
    )


# (d) owned_paths 밖 → 쓰기 전에 거부, task.failed(scope_violation), 커밋 0
async def test_scope_violation(worktree: Path, remote: Path, spy: Spy) -> None:
    agent = make_agent("scope_violation", spy, worktree)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "failed"
    assert "run.tool_denied" in spy.types()
    failed = next(e for e in spy.events if e.type.value == "task.failed")
    assert failed.payload["reason"] == "scope_violation" and failed.payload["files"] == [
        "README.md"
    ]
    assert (worktree / "README.md").read_text().startswith("# sample-app")  # 안 바뀜
    assert not (worktree / "src/app/users.py").exists()  # 부분 쓰기도 없음
    assert git(worktree, "log", "--format=%s", "-n", "5").count("\n") == 0  # fixture 커밋 하나뿐
    assert "ai/users-api/12-add-users-module" not in git(remote, "branch", "--list")


# (e) 의존성 파일 diff → needs_decision, 코멘트 "승인 필요", task.blocked
async def test_dependency_change_needs_decision(worktree: Path, spy: Spy) -> None:
    github = DryRunGitHubClient()
    agent = make_agent("dependency", spy, worktree, github)
    out = await agent.run(make_input(worktree, owned=["src/app/**", "tests/**", "pyproject.toml"]))
    assert out.outcome == "needs_decision"
    assert out.decision_request is not None and out.decision_request.type == "dependency"
    assert out.decision_request.files == ["pyproject.toml"]
    blocked = next(e for e in spy.events if e.type.value == "task.blocked")
    assert blocked.payload["reason"] == "needs_decision"
    comments = github.snapshot()["repos"][REPO]["issues"][12]["comments"]
    assert comments[0]["key"] == "needs-decision:01RUN" and "승인 필요" in comments[0]["body"]
    assert spy.events[-1].payload["outcome"] == "escalated"
    assert "pr.opened" not in spy.types()


# (f) 이벤트 순서
async def test_event_order(worktree: Path, spy: Spy) -> None:
    await make_agent("pass", spy, worktree).run(make_input(worktree))
    types = spy.types()
    assert types[0] == "task.started" and types[1] == "run.started" and types[-1] == "run.finished"
    prev = None
    for e in spy.events:
        if e.type.value == "run.tool_called":
            continue  # 체인 밖
        if prev is not None:
            assert e.causation_id == prev
        prev = e.id


# PC-4 (기록): 새 파일만 owned면 기존 API를 못 본다 → spec 언급 경로 + 같은 디렉토리 파일
async def test_related_files_include_spec_and_siblings(worktree: Path, spy: Spy) -> None:
    from agents.coding import related_files
    from agents.tools.base import ToolContext
    from agents.tools.fs import FsTool

    ctx = ToolContext(
        worktree=worktree,
        owned_paths=["src/app/users.py", "tests/test_users.py"],
        run_id="01RUN",
        task_id="01TASK",
        project_id="P1",
        goal_id="G1",
        publish=spy.publish,
        default_branch="main",
        agent_id="coding-1",
        last_event_id="EV0",
    )
    files = await related_files(
        FsTool(ctx), ctx, spec="Import UserStore from `src/app/models.py`; see README.md."
    )
    assert "src/app/models.py" in files  # spec 언급
    assert "src/app/main.py" in files and "tests/test_main.py" in files  # owned 디렉토리 형제
    assert "README.md" in files
    assert not any(p.startswith((".venv", "node_modules", ".ai-platform")) for p in files)
    assert not any(k.endswith("users.py") for k in files)  # 아직 없는 파일은 제외


# PC-4 (기록): edit 노드가 원본 input으로 컨텍스트를 조립해 CONTEXT.md가 빠졌다(plan 노드만 포함)
async def test_edit_prompt_includes_context_md(worktree: Path, remote: Path, spy: Spy) -> None:
    from agents.llm.base import Completion, Message

    calls: list[list[Message]] = []
    inner = FakeProvider(script=script("pass"))

    class Capture:
        async def complete(self, messages: list[Message], **kw: Any) -> Completion:
            calls.append(messages)
            return await inner.complete(messages, **kw)

    agent = CodingAgent(
        publish=spy.publish,
        provider=Capture(),  # type: ignore[arg-type]
        github=DryRunGitHubClient(),
        repo=REPO,
        worktree=worktree,
        test_command="pytest -q",
    )
    inp = make_input(worktree)
    inp = inp.model_copy(
        update={"project_context": inp.project_context.model_copy(update={"context_md": None})}
    )
    out = await agent.run(inp)  # Scheduler spec처럼 context_md 없음 → 워커가 CONTEXT.md를 읽는다
    assert out.outcome == "done" and len(calls) >= 2
    assert all("Flask app factory" in c[-1].content for c in calls[:2])  # plan + edit 둘 다


# PC-4 (기록): 7B 모델은 JSON 문자열 안의 코드(docstring """, @decorator, 빈 줄)를 망가뜨린다 →
# 편집 응답은 파일 블록 텍스트, JSON EditPlan은 폴백(Fake 스크립트·구조화 출력 provider)
def test_parse_edit_plan_blocks_and_json_fallback() -> None:
    from agents.coding import parse_edit_plan

    text = (
        "Here are the files.\n\n=== MESSAGE: feat: add update ===\n"
        '=== FILE: src/app/models.py ===\n"""Doc."""\n\n@dataclass\nclass A:\n    x: int\n'
        "=== END FILE ===\n\n=== FILE: tests/test_a.py ===\ndef test_a() -> None:\n    assert 1\n"
        "=== END FILE ===\n"
    )
    plan = parse_edit_plan(text)
    assert plan is not None and plan.message == "feat: add update"
    assert [f.path for f in plan.files] == ["src/app/models.py", "tests/test_a.py"]
    assert plan.files[0].content == '"""Doc."""\n\n@dataclass\nclass A:\n    x: int\n'
    # 코드펜스로 감싸도 벗겨낸다
    fenced = "=== FILE: a.py ===\n```python\nx = 1\n```\n=== END FILE ===\n"
    assert parse_edit_plan(fenced).files[0].content == "x = 1\n"  # type: ignore[union-attr]
    # 마커 접두가 달라도(### FILE: … ===) 인식
    md = "### MESSAGE: m ===\n### FILE: a.py ===\nx = 1\n### END FILE ===\n"
    assert parse_edit_plan(md).files[0].path == "a.py"  # type: ignore[union-attr]
    # 뒤 마커 없음 + END FILE 누락 (Ollama 4차에서 관측): 경로에 코드가 섞이면 안 된다
    loose = "### FILE: src/app/u.py\n```python\nx = 1\n```\n\n### FILE: tests/t.py\ny = 2\n"
    plan = parse_edit_plan(loose)
    assert plan is not None and [f.path for f in plan.files] == ["src/app/u.py", "tests/t.py"]
    assert plan.files[0].content == "x = 1\n" and plan.files[1].content.strip() == "y = 2"
    # JSON 폴백 (FakeProvider 스크립트가 dict를 json.dumps 한 텍스트)
    js = '{"files": [{"path": "a.py", "content": "x = 1\\n"}], "message": "m"}'
    assert parse_edit_plan(js).message == "m"  # type: ignore[union-attr]
    assert parse_edit_plan("no files here") is None


# X.2: Coding Agent 프롬프트는 파일(agents/prompts/*.md, 근거 주석 포함)에서 읽고, 재시도 프롬프트는
# "테스트가 틀렸는지 코드가 틀렸는지" 진단을 요구한다 (PC-4/PC-5: 모델이 쓴 테스트의 잘못된 기대값)
def test_prompt_files_have_rationale() -> None:
    from agents.prompts import PROMPTS_DIR, load_prompt

    for name in ("system", "edit", "retry"):
        raw = (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")
        assert raw.lstrip().startswith("<!--") and "근거" in raw
        assert "<!--" not in load_prompt(name)
    system = load_prompt("system", test_command="pytest -q")
    assert "pytest -q" in system and "fresh" in system.lower()
    assert "{test_command}" not in system


async def test_retry_prompt_asks_for_diagnosis(worktree: Path, remote: Path, spy: Spy) -> None:
    from agents.llm.base import Completion, Message

    calls: list[list[Message]] = []
    inner = FakeProvider(script=script("fail_then_pass"))

    class Capture:
        async def complete(self, messages: list[Message], **kw: Any) -> Completion:
            calls.append(messages)
            return await inner.complete(messages, **kw)

    agent = CodingAgent(
        publish=spy.publish,
        provider=Capture(),  # type: ignore[arg-type]
        github=DryRunGitHubClient(),
        repo=REPO,
        worktree=worktree,
        test_command="pytest -q",
    )
    out = await agent.run(make_input(worktree))
    assert out.outcome == "done"
    retry = calls[2][-1].content  # plan, edit 1, edit 2(재시도)
    assert "Previous attempt 1 failed" in retry
    flat = " ".join(retry.split())  # 프롬프트 파일의 줄바꿈 무시
    assert "test you wrote" in flat and "same object the code uses" in flat  # 진단 1(a)
