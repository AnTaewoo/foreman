"""P4.1 github 툴 (red l). 공개 메서드는 open_pr, comment뿐. base가 default 브랜치가 아니면 거부."""

from __future__ import annotations

import pytest

from agents.tools.base import ToolContext, ToolDenied
from agents.tools.github import GitHubTool
from github_adapter.dry_run import DryRunGitHubClient
from tests.agents.conftest import Spy


@pytest.fixture
def ght(ctx: ToolContext) -> GitHubTool:
    return GitHubTool(ctx, client=DryRunGitHubClient(), repo="org/demo")


def test_public_surface() -> None:
    public = {
        n for n in dir(GitHubTool) if not n.startswith("_") and callable(getattr(GitHubTool, n))
    }
    assert public == {"open_pr", "comment"}


async def test_open_pr_and_comment(ght: GitHubTool, spy: Spy) -> None:
    pr = await ght.open_pr(
        head="ai/users-api/12-add-users", title="[T-12] users", body="summary", draft=True
    )
    assert pr.number == 1 and pr.created is True
    again = await ght.open_pr(
        head="ai/users-api/12-add-users", title="[T-12] users", body="summary", draft=True
    )
    assert again.number == 1 and again.created is False
    c = await ght.comment(12, "done", key="summary:01RUN")
    assert c.created is True
    assert spy.types() == ["run.tool_called"] * 3
    assert spy.events[0].payload["tool"] == "github.open_pr"
    assert "summary" not in spy.events[0].model_dump_json().replace("summary:01RUN", "")


async def test_open_pr_wrong_base_denied(ght: GitHubTool, spy: Spy) -> None:
    with pytest.raises(ToolDenied, match="base"):
        await ght.open_pr(head="ai/x/1-y", title="t", body="b", draft=True, base="develop")
    assert spy.types() == ["run.tool_denied"]
