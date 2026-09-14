"""GitHub App 인증 (설계 §7.1, §12): App JWT(RS256) → installation access token 발급·캐시·갱신.

- App JWT: ``iss``=app id, ``iat``=now, ``exp``=now+600s (GitHub 최대 10분).
- installation token은 만료 5분 전에 갱신한다. 그 전에는 네트워크 호출이 없다.
- ``InstallationAuth``는 401을 받으면 토큰을 강제 재발급해 한 번 더 시도한다. 두 번째 401은 그대로.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Generator
from datetime import UTC, datetime, timedelta

import httpx
import jwt

API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
JWT_TTL = timedelta(seconds=600)
REFRESH_MARGIN = timedelta(minutes=5)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def app_jwt(app_id: str, private_key_pem: str, *, now: datetime | None = None) -> str:
    """App 수준 JWT. ``exp - iat == 600``."""
    issued = now or _utc_now()
    iat = int(issued.timestamp())
    payload = {"iss": app_id, "iat": iat, "exp": iat + int(JWT_TTL.total_seconds())}
    return jwt.encode(payload, private_key_pem, algorithm="RS256")


def _parse_expires(value: str) -> datetime:
    # GitHub: "2026-09-13T13:00:00Z"
    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)


class TokenUnavailable(Exception):
    """캐시된 installation 토큰이 없거나 만료 임박 — 먼저 ``await provider.token()``."""


class InstallationTokenProvider:
    """installation access token 캐시. ``client``는 base_url이 GitHub API인 AsyncClient."""

    def __init__(
        self,
        app_id: str,
        private_key_pem: str,
        installation_id: int,
        client: httpx.AsyncClient,
        *,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._app_id = app_id
        self._pem = private_key_pem
        self._installation_id = installation_id
        self._client = client
        self._now = now
        self._token: str | None = None
        self._expires_at: datetime | None = None
        self._lock = asyncio.Lock()

    def _fresh(self) -> bool:
        if self._token is None or self._expires_at is None:
            return False
        return self._now() < self._expires_at - REFRESH_MARGIN

    async def token(self, *, force: bool = False) -> str:
        async with self._lock:
            if not force and self._fresh():
                assert self._token is not None
                return self._token
            res = await self._client.post(
                f"/app/installations/{self._installation_id}/access_tokens",
                headers={
                    **API_HEADERS,
                    "Authorization": f"Bearer {app_jwt(self._app_id, self._pem, now=self._now())}",
                },
            )
            res.raise_for_status()
            data = res.json()
            self._token = str(data["token"])
            self._expires_at = _parse_expires(str(data["expires_at"]))
            return self._token

    def token_nowait(self) -> str:
        """동기 경로(git clone/push, P7.2)용: 신선한 캐시 토큰만. 없으면 TokenUnavailable."""
        if not self._fresh() or self._token is None:
            raise TokenUnavailable("no fresh installation token cached")
        return self._token

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = None


class InstallationAuth(httpx.Auth):
    """``Authorization: token <installation token>``. 401이면 재발급 후 1회 재시도."""

    requires_response_body = False

    def __init__(self, provider: InstallationTokenProvider) -> None:
        self._provider = provider

    def _apply(self, request: httpx.Request, token: str) -> httpx.Request:
        request.headers["Authorization"] = f"token {token}"
        for k, v in API_HEADERS.items():
            request.headers.setdefault(k, v)
        return request

    async def async_auth_flow(
        self, request: httpx.Request
    ) -> AsyncGenerator[httpx.Request, httpx.Response]:
        response = yield self._apply(request, await self._provider.token())
        if response.status_code == 401:
            self._provider.invalidate()
            yield self._apply(request, await self._provider.token(force=True))

    def sync_auth_flow(
        self, request: httpx.Request
    ) -> Generator[httpx.Request, httpx.Response, None]:
        raise RuntimeError("InstallationAuth is async-only; use httpx.AsyncClient")
