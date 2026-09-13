"""github 툴: Coding Agent의 GitHub 동작은 ``open_pr``·``comment``뿐 (§5.2). base는 default만."""

from __future__ import annotations

from agents.tools.base import ToolContext, ToolDenied, guarded
from github_adapter.protocol import CommentRef, GitHubClient, PrMeta, PullRef


class GitHubTool:
    name = "github"

    def __init__(
        self, ctx: ToolContext, *, client: GitHubClient, repo: str, tier: str = "T1"
    ) -> None:
        self._ctx = ctx
        self._client = client
        self._repo = repo
        self._tier = tier

    async def open_pr(
        self, *, head: str, title: str, body: str, draft: bool = True, base: str | None = None
    ) -> PullRef:
        target_base = base or self._ctx.default_branch
        args = {
            "head": head,
            "base": target_base,
            "title": title,
            "draft": draft,
            "body_len": len(body),
        }
        async with guarded(self._ctx, "github.open_pr", args):
            if target_base != self._ctx.default_branch:
                raise ToolDenied("base", f"{target_base!r} is not the default branch")
            meta = PrMeta(
                task_id=self._ctx.task_id,
                run_id=self._ctx.run_id,
                agent_id=self._ctx.agent_id,
                tier=self._tier,
            )
            return await self._client.open_pr(
                self._repo, head, target_base, title, body, draft, meta
            )

    async def comment(self, number: int, body: str, key: str | None = None) -> CommentRef:
        async with guarded(
            self._ctx, "github.comment", {"number": number, "key": key, "body_len": len(body)}
        ):
            return await self._client.comment(self._repo, number, body, key=key)
