"""공개 App의 프로젝트별 installation (P9.8–P9.10, design §12).

repo → 보관되지 않은 최신 프로젝트의 ``installation_id``(없으면 env 기본) → 그 installation의
client·토큰. GoalRunner·PrOpener·RepoCache는 repo만 알면 되고 installation을 몰라도 된다.

- 비동기 경로(REST·GraphQL): ``RoutingGitHubClient``/``RoutingDiscussionsClient``가 첫 인자 repo로
  installation client를 골라 위임한다.
- 동기 경로(git clone/push, D-41): ``token_nowait(repo)``는 캐시만 본다. API 프로세스는 clone 전에
  ``prepare(repo)``, control plane은 retry 루프에서 ``refresh_all()``로 미리 발급한다.
- DRY_RUN이면 라우팅 없이 Dry client 하나(D-10), 토큰 경로 없음.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, cast

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.store import models as m
from github_adapter import ClientRegistry
from github_adapter.auth import TokenUnavailable
from github_adapter.discussions import DiscussionsClient
from github_adapter.dry_run import DryRunDiscussionsClient
from github_adapter.protocol import GitHubClient

log = structlog.get_logger(__name__)

# Any: 위임 대상 client의 메서드는 각자 시그니처가 다르다 (Protocol이 검증)
Resolve = Callable[[str], Awaitable[Any]]


class RoutingGitHubClient:
    """``GitHubClient`` 메서드는 전부 첫 인자가 repo — 그 repo의 installation client로 위임."""

    def __init__(self, resolve: Resolve) -> None:
        self._resolve = resolve

    def __getattr__(self, name: str) -> Callable[..., Awaitable[Any]]:
        if name.startswith("_"):
            raise AttributeError(name)

        async def call(repo: str, *args: Any, **kwargs: Any) -> Any:
            client = await self._resolve(repo)
            return await getattr(client, name)(repo, *args, **kwargs)

        return call


class RoutingDiscussionsClient(RoutingGitHubClient):
    """Discussions도 repo로 위임. ``add_discussion_comment``는 만든 client를 기억한다."""

    def __init__(self, resolve: Resolve) -> None:
        super().__init__(resolve)
        self._by_node: dict[str, Any] = {}  # Any: DiscussionsClient

    async def create_discussion(self, repo: str, *args: Any, **kwargs: Any) -> Any:
        client = await self._resolve(repo)
        ref = await client.create_discussion(repo, *args, **kwargs)
        self._by_node[ref.id] = client
        return ref

    async def add_discussion_comment(self, discussion_id: str, body: str) -> Any:
        client = self._by_node.get(discussion_id)
        if client is None:
            raise LookupError(f"unknown discussion {discussion_id} (create it through this client)")
        return await client.add_discussion_comment(discussion_id, body)


class RepoRouter:
    def __init__(self, factory: async_sessionmaker[AsyncSession], registry: ClientRegistry) -> None:
        self._factory = factory
        self.registry = registry
        self._iids: dict[str, int | None] = {}  # repo(lower) → installation (None = env 기본)
        self.github: GitHubClient
        self.discussions: DiscussionsClient | DryRunDiscussionsClient
        if registry.pool is None:
            self.github = registry.github(None)
            self.discussions = registry.discussions(None)
        else:  # cast: __getattr__ 위임이라 정적으로는 Protocol을 만족하지 않는다
            self.github = cast(GitHubClient, RoutingGitHubClient(self._github_for))
            self.discussions = cast(
                DiscussionsClient, RoutingDiscussionsClient(self._discussions_for)
            )

    @property
    def token_getter(self) -> Callable[[str], str] | None:
        return None if self.registry.pool is None else self.token_nowait

    async def installation_for(self, repo: str) -> int | None:
        async with self._factory() as s:
            row = await s.scalar(
                select(m.Project)
                .where(func.lower(m.Project.repo_full_name) == repo.lower())
                .where(m.Project.archived_at.is_(None))
                .order_by(m.Project.created_at.desc())
                .limit(1)
            )
        iid = row.installation_id if row is not None else None
        self._iids[repo.lower()] = iid
        return iid

    def client_for_installation(self, installation_id: int | None) -> GitHubClient:
        return self.registry.github(installation_id)

    async def _github_for(self, repo: str) -> GitHubClient:
        return self.registry.github(await self.installation_for(repo))

    async def _discussions_for(self, repo: str) -> DiscussionsClient | DryRunDiscussionsClient:
        return self.registry.discussions(await self.installation_for(repo))

    def token_nowait(self, repo: str) -> str:
        """동기 clone/push용. ``prepare``/``refresh_all`` 전이면 TokenUnavailable."""
        pool = self.registry.pool
        key = repo.lower()
        if pool is None or key not in self._iids:
            raise TokenUnavailable(f"no installation resolved for {repo}")
        return pool.provider(self._iids[key]).token_nowait()

    async def prepare(self, repo: str) -> None:
        """동기 clone 전에 그 repo의 installation 토큰을 신선하게 (D-41, PC-7)."""
        pool = self.registry.pool
        if pool is None:
            return
        await pool.provider(await self.installation_for(repo)).token()

    async def refresh_all(self) -> None:
        """보관되지 않은 모든 프로젝트의 토큰을 미리 발급 — control plane retry 루프."""
        pool = self.registry.pool
        if pool is None:
            return
        async with self._factory() as s:
            rows = (
                await s.execute(
                    select(m.Project.repo_full_name, m.Project.installation_id)
                    .where(m.Project.archived_at.is_(None))
                    .order_by(m.Project.created_at)
                )
            ).all()
        for repo, iid in rows:  # 같은 repo면 최신이 이긴다 (created_at 오름차순)
            self._iids[str(repo).lower()] = iid
        for key, iid in list(self._iids.items()):
            if iid is None and pool.default_installation_id is None:
                continue  # 발급할 installation이 없다 — 매초 로그를 남기지 않는다
            try:
                await pool.provider(iid).token()
            except Exception:
                log.exception("router.token_refresh_failed", repo=key, installation_id=iid)
