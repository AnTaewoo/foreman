"""P9.8 — 공개 App: repo별 installation 탐지 + installation별 토큰 풀 + client 레지스트리."""

from __future__ import annotations

import httpx
import jwt
import pytest
import respx

from control_plane.config import Settings
from github_adapter import ClientRegistry
from github_adapter.auth import Installation, InstallationTokenPool, find_installation
from github_adapter.client import GitHubRestClient
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from tests.github_adapter.conftest import Clock

APP_ID = "12345"
REPO = "judge-org/demo"


def _tokens(github_mock: respx.MockRouter, *iids: int) -> dict[int, respx.Route]:
    return {
        iid: github_mock.post(f"/app/installations/{iid}/access_tokens").mock(
            return_value=httpx.Response(
                201, json={"token": f"ghs_{iid}", "expires_at": "2026-09-01T13:00:00Z"}
            )
        )
        for iid in iids
    }


# repo → installation (App JWT로 호출)
async def test_find_installation(
    github_mock: respx.MockRouter,
    http: httpx.AsyncClient,
    private_key_pem: str,
    public_key_pem: str,
    clock: Clock,
) -> None:
    route = github_mock.get(f"/repos/{REPO}/installation").mock(
        return_value=httpx.Response(200, json={"id": 42, "account": {"login": "judge-org"}})
    )
    found = await find_installation(APP_ID, private_key_pem, REPO, http, now=clock)
    assert found == Installation(id=42, account_login="judge-org")
    auth = route.calls[0].request.headers["Authorization"]
    assert auth.startswith("Bearer ")
    claims = jwt.decode(
        auth.removeprefix("Bearer "),
        public_key_pem,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},
    )
    assert claims["iss"] == APP_ID


async def test_find_installation_missing(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    github_mock.get(f"/repos/{REPO}/installation").mock(return_value=httpx.Response(404))
    assert await find_installation(APP_ID, private_key_pem, REPO, http, now=clock) is None


# installation별 토큰 분리 + provider 재사용, None이면 env 기본
async def test_pool_separates_installations(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    routes = _tokens(github_mock, 42, 43, 7)
    pool = InstallationTokenPool(APP_ID, private_key_pem, http, default_installation_id=7, now=clock)
    assert await pool.provider(42).token() == "ghs_42"
    assert await pool.provider(43).token() == "ghs_43"
    assert await pool.provider(None).token() == "ghs_7"
    assert pool.provider(42) is pool.provider(42)
    await pool.provider(42).token()
    assert routes[42].call_count == 1  # 캐시
    assert sorted(pool.installation_ids()) == [7, 42, 43]


def test_pool_without_default(private_key_pem: str) -> None:
    pool = InstallationTokenPool(APP_ID, private_key_pem, httpx.AsyncClient())
    with pytest.raises(ValueError, match="installation"):
        pool.provider(None)


# 레지스트리: installation별 client 캐시, 요청은 그 installation 토큰
async def test_registry_real_uses_installation_token(
    github_mock: respx.MockRouter, private_key_pem: str
) -> None:
    _tokens(github_mock, 42, 43)
    issue = github_mock.get(f"/repos/{REPO}/issues/1").mock(
        return_value=httpx.Response(200, json={"number": 1})
    )
    settings = Settings(
        _env_file=None, dry_run=False, github_app_id=APP_ID, github_app_private_key=private_key_pem
    )
    reg = ClientRegistry(settings)
    gh = reg.github(42)
    assert isinstance(gh, GitHubRestClient) and reg.github(42) is gh
    await reg.http(42).get(f"/repos/{REPO}/issues/1")
    await reg.http(43).get(f"/repos/{REPO}/issues/1")
    assert [c.request.headers["Authorization"] for c in issue.calls] == [
        "token ghs_42",
        "token ghs_43",
    ]
    with pytest.raises(ValueError, match="installation"):
        reg.github(None)  # env installation 없음
    await reg.aclose()


def test_registry_dry() -> None:
    reg = ClientRegistry(Settings(_env_file=None))
    assert isinstance(reg.github(42), DryRunGitHubClient)
    assert reg.github(42) is reg.github(None)  # dry는 하나 (상태 공유)
    assert isinstance(reg.discussions(42), DryRunDiscussionsClient)
    assert reg.pool is None
