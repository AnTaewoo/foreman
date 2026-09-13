"""GitHub Adapter: App 인증, 멱등 쓰기, 웹훅 → 이벤트. 팩토리는 DRY_RUN이면 Dry client (D-10)."""

from __future__ import annotations

import httpx

from github_adapter.auth import InstallationAuth, InstallationTokenProvider
from github_adapter.client import GITHUB_API_BASE_URL, GitHubRestClient
from github_adapter.discussions import DiscussionsClient
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from github_adapter.protocol import GitHubClient

__all__ = ["get_discussions_client", "get_github_client", "make_installation_http"]


def _require_real(settings: object, installation_id: int | None) -> tuple[str, str, int]:
    app_id = str(getattr(settings, "github_app_id", "") or "")
    secret = getattr(settings, "github_app_private_key", None)
    pem = (
        secret.get_secret_value()
        if secret is not None and hasattr(secret, "get_secret_value")
        else ""
    )
    if not app_id or not pem:
        raise ValueError("github_app_id and github_app_private_key are required when dry_run=False")
    inst = (
        installation_id
        if installation_id is not None
        else getattr(settings, "github_installation_id", None)
    )
    if inst is None:
        raise ValueError("installation_id is required when dry_run=False")
    return app_id, str(pem), int(inst)


def make_installation_http(
    settings: object, installation_id: int | None = None
) -> httpx.AsyncClient:
    """InstallationAuth가 붙은 api.github.com AsyncClient (실 모드 전용)."""
    app_id, pem, inst = _require_real(settings, installation_id)
    token_http = httpx.AsyncClient(base_url=GITHUB_API_BASE_URL)
    provider = InstallationTokenProvider(app_id, pem, inst, token_http)
    return httpx.AsyncClient(base_url=GITHUB_API_BASE_URL, auth=InstallationAuth(provider))


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
