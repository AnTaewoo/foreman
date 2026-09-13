"""P4.1 git 툴 (red h~k). 브랜치 규약, 기본 브랜치 push 거부, 트레일러, bare remote."""

from __future__ import annotations

from pathlib import Path

import pytest

from agents.tools.base import ToolContext, ToolDenied
from agents.tools.git import GitTool
from tests.agents.conftest import Spy, git


@pytest.fixture
def gt(ctx: ToolContext) -> GitTool:
    return GitTool(ctx)


# (h) 브랜치 이름 규약 ai/<epic>/<n>-<slug>
@pytest.mark.parametrize(
    "name", ["feature/x", "ai/users", "ai/users/x-slug", "main", "ai/users/12", "ai/Users/12-x"]
)
async def test_branch_name_rejected(gt: GitTool, name: str) -> None:
    with pytest.raises(ToolDenied, match="branch"):
        await gt.branch(name)


async def test_branch_create_and_checkout_existing(gt: GitTool, worktree: Path) -> None:
    assert await gt.branch("ai/users-api/12-add-users") == "ai/users-api/12-add-users"
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "ai/users-api/12-add-users"
    git(worktree, "checkout", "-q", "main")
    assert (
        await gt.branch("ai/users-api/12-add-users") == "ai/users-api/12-add-users"
    )  # 있으면 checkout
    assert git(worktree, "rev-parse", "--abbrev-ref", "HEAD") == "ai/users-api/12-add-users"


# (i) 기본 브랜치 push 거부
@pytest.mark.parametrize("target", ["main", "master"])
async def test_push_default_branch_denied(gt: GitTool, target: str, spy: Spy) -> None:
    with pytest.raises(ToolDenied, match="default"):
        await gt.push(target)
    assert spy.types()[-1] == "run.tool_denied"


async def test_push_current_branch_main_denied(gt: GitTool) -> None:
    with pytest.raises(ToolDenied, match="default"):
        await gt.push()  # 현재 브랜치가 main


# (j) commit 트레일러, 변경 없으면 None
async def test_commit_trailer_and_noop(gt: GitTool, worktree: Path) -> None:
    await gt.branch("ai/users-api/12-add-users")
    assert await gt.commit("nothing to do", issue_number=12) is None
    (worktree / "src/app/users.py").write_text("X = 1\n")
    sha = await gt.commit("feat: add users module", issue_number=12)
    assert sha and len(sha) >= 7
    body = git(worktree, "log", "-1", "--format=%B")
    assert body.startswith("feat: add users module")
    assert "Task #12 / Run 01RUN" in body
    assert git(worktree, "status", "--porcelain") == ""


# (k) bare remote push, changed_files, diff
async def test_push_changed_files_diff(gt: GitTool, worktree: Path, remote: Path, spy: Spy) -> None:
    await gt.branch("ai/users-api/12-add-users")
    (worktree / "src/app/users.py").write_text("X = 1\n")
    (worktree / "src/app/main.py").write_text(
        (worktree / "src/app/main.py").read_text() + "# touched\n"
    )
    assert sorted(await gt.changed_files()) == ["src/app/main.py", "src/app/users.py"]
    d = await gt.diff()
    assert "+# touched" in d and "users.py" in d
    await gt.commit("feat: users", issue_number=12)
    assert await gt.changed_files() == []
    await gt.push()
    heads = git(remote, "branch", "--list")
    assert "ai/users-api/12-add-users" in heads
    assert git(remote, "log", "-1", "--format=%s", "ai/users-api/12-add-users") == "feat: users"
    assert spy.types()[-1] == "run.tool_called" and spy.events[-1].payload["tool"] == "git.push"
