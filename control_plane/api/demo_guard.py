"""데모 모드 가드 (P9.3, D-52): 공개 데모에서 익명 방문자의 쓰기를 제한한다.

- `require_admin`: `demo_mode`면 `X-Admin-Token`이 설정값과 같아야 한다(401).
  토큰이 비어 있으면 항상 401(fail-closed).
- `goal_quota`: 프로젝트에 진행 중(draft|planning|active) Goal이 `demo_max_running_goals`
  이상이거나 1시간 내 생성이 `demo_goals_per_hour` 이상이면 429.
  awaiting_plan_approval은 사람 대기라 실행 중으로 세지 않는다.
- `RateLimitMiddleware`: IP별 POST/PATCH/PUT/DELETE 분당 한도(429). IP는 scope["client"] —
  nginx 뒤에서는 uvicorn `--proxy-headers --forwarded-allow-ips 127.0.0.1`이 채운다.
`demo_mode=False`면 세 가지 모두 no-op. 토큰 값은 로그에 남기지 않는다(§12).
"""

from __future__ import annotations

import hmac
import time
from collections import deque
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Header, HTTPException
from sqlalchemy import func, select
from starlette.types import ASGIApp, Receive, Scope, Send

from control_plane.api.deps import StateDep
from control_plane.api.idempotency import WRITE_METHODS, _send_json
from control_plane.store import models as m
from control_plane.store.enums import GoalStatus

RUNNING = (GoalStatus.DRAFT, GoalStatus.PLANNING, GoalStatus.ACTIVE)


async def require_admin(
    state: StateDep, x_admin_token: Annotated[str | None, Header()] = None
) -> None:
    settings = state.settings
    if not settings.demo_mode:
        return
    expected = settings.admin_token.get_secret_value()
    if not expected or x_admin_token is None or not hmac.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "demo: admin token required")


AdminDep = Depends(require_admin)


async def goal_quota(state: StateDep, project_id: str) -> None:
    settings = state.settings
    if not settings.demo_mode:
        return
    since = datetime.now(UTC) - timedelta(hours=1)
    async with state.factory() as s:
        running = await s.scalar(
            select(func.count())
            .select_from(m.Goal)
            .where(m.Goal.project_id == project_id, m.Goal.status.in_(RUNNING))
        )
        recent = await s.scalar(
            select(func.count())
            .select_from(m.Goal)
            .where(m.Goal.project_id == project_id, m.Goal.created_at >= since)
        )
    if int(running or 0) >= settings.demo_max_running_goals:
        raise HTTPException(
            429, "demo: a goal is already running in this project — wait for it to finish"
        )
    if int(recent or 0) >= settings.demo_goals_per_hour:
        raise HTTPException(
            429, f"demo: hourly goal limit ({settings.demo_goals_per_hour}/hour) reached"
        )


class RateLimitMiddleware:
    """IP별 쓰기 요청 분당 한도. 순수 ASGI(IdempotencyMiddleware 골격), 메모리 슬라이딩 윈도."""

    def __init__(self, app: ASGIApp, *, per_minute: int) -> None:
        self._app = app
        self._limit = per_minute
        self._hits: dict[str, deque[float]] = {}

    def _allow(self, ip: str, now: float) -> bool:
        window = self._hits.setdefault(ip, deque())
        while window and window[0] <= now - 60.0:
            window.popleft()
        if len(window) >= self._limit:
            return False
        window.append(now)
        if len(self._hits) > 10_000:  # 오래된 IP 정리
            for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - 60.0]:
                del self._hits[k]
        return True

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") not in WRITE_METHODS:
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        ip = str(client[0]) if client else "unknown"
        if not self._allow(ip, time.monotonic()):
            await _send_json(
                send, 429, {"detail": "demo: too many requests — try again in a minute"}
            )
            return
        await self._app(scope, receive, send)
