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


# (a) pass 경로
async def test_pass_path(worktree: Path, remote: Path, spy: Spy) -> None:
    github = DryRunGitHubClient()
    agent = make_agent("pass", spy, worktree, github)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "done", out
    kinds = {a.kind: a for a in out.artifacts}
    assert kinds["branch"].ref == "ai/users-api/12-add-users-module"
    assert kinds["pr"].ref == "1" and kinds["comment"].ref
    types = spy.types()
    assert types[:2] == ["task.started", "run.started"]
    assert (
        types[2] == "run.tool_called" and spy.events[2].payload["tool"] == "fs.read"
    )  # CONTEXT.md 첫 툴
    assert "pr.opened" in types and "task.completed" in types and types[-1] == "run.finished"
    assert types.index("pr.opened") < types.index("task.completed") < types.index("run.finished")
    assert "run.tool_denied" not in types and "task.failed" not in types
    pr_opened = next(e for e in spy.events if e.type.value == "pr.opened")
    assert pr_opened.subject.entity == "pr" and pr_opened.payload["task_id"] == "01TASK"
    assert (
        pr_opened.payload["head"] == "ai/users-api/12-add-users-module"
        and pr_opened.payload["base"] == "main"
    )
    completed = next(e for e in spy.events if e.type.value == "task.completed")
    assert completed.payload == {"run_id": "01RUN", "pr_number": 1}
    # 원격에 브랜치·커밋(트레일러), Dry PR(draft, 메타 블록), 요약 코멘트(key)
    assert "ai/users-api/12-add-users-module" in git(remote, "branch", "--list")
    body = git(remote, "log", "-1", "--format=%B", "ai/users-api/12-add-users-module")
    assert "Task #12 / Run 01RUN" in body
    snap = github.snapshot()["repos"][REPO]
    pr = snap["pulls"][1]
    assert pr["draft"] is True and pr["body"].startswith(
        "<!-- ai-platform:meta task=01TASK run=01RUN"
    )
    assert pr["head"] == "ai/users-api/12-add-users-module" and pr["base"] == "main"
    assert snap["issues"][12]["comments"][0]["key"] == "summary:01RUN"
    assert agent.last_state is not None and agent.last_state["attempt"] == 1
    assert out.tokens_in > 0 or out.tokens_out >= 0


# (b) fail → pass, 2회차 프롬프트에 테스트 출력, WIP 커밋 유지
async def test_fail_then_pass(worktree: Path, remote: Path, spy: Spy) -> None:
    agent = make_agent("fail_then_pass", spy, worktree)
    provider = agent.provider
    assert isinstance(provider, FakeProvider)
    out = await agent.run(make_input(worktree))
    assert out.outcome == "done"
    edit_calls = [c for c in provider.calls if c.schema is not None]
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
