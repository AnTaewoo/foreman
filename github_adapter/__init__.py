"""GitHub Adapter: App 인증, 멱등 쓰기, 웹훅 → 이벤트. 팩토리는 DRY_RUN이면 Dry client (D-10)."""

from __future__ import annotations

import httpx

from github_adapter.auth import (
    InstallationAuth,
    InstallationTokenPool,
    InstallationTokenProvider,
)
from github_adapter.client import GITHUB_API_BASE_URL, GitHubRestClient
from github_adapter.discussions import DiscussionsClient
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from github_adapter.protocol import GitHubClient

__all__ = [
    "ClientRegistry",
    "get_discussions_client",
    "get_github_client",
    "make_installation_http",
    "make_token_pool",
    "make_token_provider",
]


def _app_creds(settings: object) -> tuple[str, str]:
    app_id = str(getattr(settings, "github_app_id", "") or "")
    secret = getattr(settings, "github_app_private_key", None)
    pem = (
        secret.get_secret_value()
        if secret is not None and hasattr(secret, "get_secret_value")
        else ""
    )
    if not app_id or not pem:
        raise ValueError("github_app_id and github_app_private_key are required when dry_run=False")
    return app_id, str(pem)


def _require_real(settings: object, installation_id: int | None) -> tuple[str, str, int]:
    app_id, pem = _app_creds(settings)
    inst = (
        installation_id
        if installation_id is not None
        else getattr(settings, "github_installation_id", None)
    )
    if inst is None:
        raise ValueError("installation_id is required when dry_run=False")
    return app_id, str(pem), int(inst)


def make_token_provider(
    settings: object, installation_id: int | None = None
) -> InstallationTokenProvider:
    """RepoCache clone·PrOpener push(D-41)용 토큰 제공자 (실 모드 전용)."""
    app_id, pem, inst = _require_real(settings, installation_id)
    return InstallationTokenProvider(
        app_id, pem, inst, httpx.AsyncClient(base_url=GITHUB_API_BASE_URL)
    )


def make_installation_http(
    settings: object, installation_id: int | None = None
) -> httpx.AsyncClient:
    """InstallationAuth가 붙은 GitHub API AsyncClient (실 모드 전용)."""
    app_id, pem, inst = _require_real(settings, installation_id)
    token_http = httpx.AsyncClient(base_url=GITHUB_API_BASE_URL)
    provider = InstallationTokenProvider(app_id, pem, inst, token_http)
    return httpx.AsyncClient(base_url=GITHUB_API_BASE_URL, auth=InstallationAuth(provider))


class ClientRegistry:
    """공개 App (P9.8): installation별 GitHub client 캐시. 프로젝트의 ``installation_id``로 고르고,
    ``None``이면 env ``github_installation_id``. DRY_RUN이면 Dry client 하나를 공유한다 (D-10)."""

    def __init__(self, settings: object) -> None:
        self.dry_run = bool(getattr(settings, "dry_run", True))
        self.pool: InstallationTokenPool | None = None
        self._dry_github = DryRunGitHubClient()
        self._dry_discussions = DryRunDiscussionsClient()
        self._http: dict[int, httpx.AsyncClient] = {}
        self._github: dict[int, GitHubClient] = {}
        self._discussions: dict[int, DiscussionsClient] = {}
        if not self.dry_run:
            self.pool = make_token_pool(settings)

    def _iid(self, installation_id: int | None) -> int:
        assert self.pool is not None
        return self.pool.provider(installation_id).installation_id

    def http(self, installation_id: int | None) -> httpx.AsyncClient:
        if self.pool is None:
            raise RuntimeError("dry_run: no GitHub HTTP client")
        iid = self._iid(installation_id)
        if iid not in self._http:
            self._http[iid] = httpx.AsyncClient(
                base_url=GITHUB_API_BASE_URL, auth=InstallationAuth(self.pool.provider(iid))
            )
        return self._http[iid]

    def github(self, installation_id: int | None) -> GitHubClient:
        if self.pool is None:
            return self._dry_github
        iid = self._iid(installation_id)
        if iid not in self._github:
            self._github[iid] = GitHubRestClient(self.http(iid))
        return self._github[iid]

    def discussions(
        self, installation_id: int | None
    ) -> DiscussionsClient | DryRunDiscussionsClient:
        if self.pool is None:
            return self._dry_discussions
        iid = self._iid(installation_id)
        if iid not in self._discussions:
            self._discussions[iid] = DiscussionsClient(self.http(iid))
        return self._discussions[iid]

    async def aclose(self) -> None:
        for client in self._http.values():
            await client.aclose()


def make_token_pool(settings: object) -> InstallationTokenPool:
    """installation별 토큰 풀 (실 모드 전용). env installation은 기본값일 뿐 필수가 아니다."""
    app_id, pem = _app_creds(settings)
    return InstallationTokenPool(
        app_id,
        pem,
        httpx.AsyncClient(base_url=GITHUB_API_BASE_URL),
        default_installation_id=getattr(settings, "github_installation_id", None),
    )


def get_github_client(settings: object, installation_id: int | None = None) -> GitHubClient:
    """DRY_RUN(기본 true)이면 DryRunGitHubClient, 아니면 실 client (installation_id 필수)."""
    if getattr(settings, "dry_run", True):
        return DryRunGitHubClient()
    return GitHubRestClient(make_installation_http(settings, installation_id))


def get_discussions_client(
    settings: object, installation_id: int | None = None
) -> DiscussionsClient | DryRunDiscussionsClient:
    if getattr(settings, "dry_run", True):
        return DryRunDiscussionsClient()
    return DiscussionsClient(make_installation_http(settings, installation_id))
