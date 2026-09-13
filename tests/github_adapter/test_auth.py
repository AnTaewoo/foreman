"""P2.1 — GitHub App JWT / installation token / InstallationAuth (red a~e)."""

from __future__ import annotations

from datetime import timedelta

import httpx
import jwt
import pytest
import respx

from github_adapter.auth import InstallationAuth, InstallationTokenProvider, app_jwt
from tests.github_adapter.conftest import Clock

APP_ID = "12345"
INSTALLATION = 777


# (a) App JWT
def test_app_jwt_claims(private_key_pem: str, public_key_pem: str, clock: Clock) -> None:
    token = app_jwt(APP_ID, private_key_pem, now=clock())
    claims = jwt.decode(
        token,
        public_key_pem,
        algorithms=["RS256"],
        options={"verify_exp": False, "verify_iat": False},  # 고정 시계라 시간 검증은 제외
    )
    assert claims["iss"] == APP_ID
    assert claims["exp"] - claims["iat"] == 600
    assert claims["iat"] == int(clock().timestamp())
    assert jwt.get_unverified_header(token)["alg"] == "RS256"


def _token_route(github_mock: respx.MockRouter, token: str, expires_at: str) -> respx.Route:
    return github_mock.post(f"/app/installations/{INSTALLATION}/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": token, "expires_at": expires_at})
    )


def _iso(clock: Clock, seconds: int) -> str:
    return (clock() + timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


# (b) 토큰 캐시 + 헤더
async def test_installation_token_cached(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    route = _token_route(github_mock, "ghs_first", _iso(clock, 3600))
    provider = InstallationTokenProvider(APP_ID, private_key_pem, INSTALLATION, http, now=clock)
    assert await provider.token() == "ghs_first"
    assert await provider.token() == "ghs_first"
    assert route.call_count == 1
    req = route.calls[0].request
    assert req.headers["Accept"] == "application/vnd.github+json"
    assert req.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert req.headers["Authorization"].startswith("Bearer ")
    jwt.get_unverified_header(req.headers["Authorization"].removeprefix("Bearer "))


# (c) 만료 5분 전 갱신, 그 전엔 네트워크 0
async def test_token_refreshes_five_minutes_before_expiry(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    route = _token_route(github_mock, "ghs_first", _iso(clock, 3600))
    provider = InstallationTokenProvider(APP_ID, private_key_pem, INSTALLATION, http, now=clock)
    await provider.token()
    clock.advance(3600 - 301)  # 만료 5분 1초 전
    assert await provider.token() == "ghs_first"
    assert route.call_count == 1
    route.mock(
        return_value=httpx.Response(
            201, json={"token": "ghs_second", "expires_at": _iso(clock, 3600)}
        )
    )
    clock.advance(2)  # 만료 4분 59초 전 → 갱신
    assert await provider.token() == "ghs_second"
    assert route.call_count == 2


async def test_token_endpoint_error_raises(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    github_mock.post(f"/app/installations/{INSTALLATION}/access_tokens").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )
    provider = InstallationTokenProvider(APP_ID, private_key_pem, INSTALLATION, http, now=clock)
    with pytest.raises(httpx.HTTPStatusError):
        await provider.token()


# (d) InstallationAuth: 401 → 재발급 1회 후 재시도, 두 번째 401은 그대로
async def test_installation_auth_retries_once_on_401(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    token_route = github_mock.post(f"/app/installations/{INSTALLATION}/access_tokens").mock(
        side_effect=[
            httpx.Response(201, json={"token": "ghs_old", "expires_at": _iso(clock, 3600)}),
            httpx.Response(201, json={"token": "ghs_new", "expires_at": _iso(clock, 3600)}),
        ]
    )
    api_route = github_mock.get("/repos/o/r").mock(
        side_effect=[
            httpx.Response(401, json={"message": "Bad credentials"}),
            httpx.Response(200, json={"full_name": "o/r"}),
        ]
    )
    provider = InstallationTokenProvider(APP_ID, private_key_pem, INSTALLATION, http, now=clock)
    async with httpx.AsyncClient(
        base_url="https://api.github.com", auth=InstallationAuth(provider)
    ) as client:
        res = await client.get("/repos/o/r")
    assert res.status_code == 200
    assert token_route.call_count == 2
    assert [c.request.headers["Authorization"] for c in api_route.calls] == [
        "token ghs_old",
        "token ghs_new",
    ]


async def test_installation_auth_second_401_is_returned(
    github_mock: respx.MockRouter, http: httpx.AsyncClient, private_key_pem: str, clock: Clock
) -> None:
    github_mock.post(f"/app/installations/{INSTALLATION}/access_tokens").mock(
        return_value=httpx.Response(201, json={"token": "ghs_x", "expires_at": _iso(clock, 3600)})
    )
    api_route = github_mock.get("/repos/o/r").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )
    provider = InstallationTokenProvider(APP_ID, private_key_pem, INSTALLATION, http, now=clock)
    async with httpx.AsyncClient(
        base_url="https://api.github.com", auth=InstallationAuth(provider)
    ) as client:
        res = await client.get("/repos/o/r")
    assert res.status_code == 401
    assert api_route.call_count == 2


# (e) 미매칭 요청 = 실패 (autouse respx)
async def test_unmatched_request_fails(http: httpx.AsyncClient) -> None:
    with pytest.raises(respx.models.AllMockedAssertionError):
        await http.get("/rate_limit")
