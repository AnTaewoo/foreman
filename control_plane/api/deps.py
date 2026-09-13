"""API 의존성: 앱 상태(설정·세션 팩토리·Redis·EventBus), 사용자, 이벤트 발행 도우미.

API는 이벤트를 **발행만** 한다 — DB 갱신은 projection(§0 코딩 규칙). 읽기는 projection 테이블.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.events.bus import EventBus
from control_plane.events.schema import Actor, Event
from control_plane.store import session as sess

if TYPE_CHECKING:
    from control_plane.orchestrator.runner import GoalRunner

GoalHook = Callable[[str, str], Awaitable[None]]  # (project_id, goal_id) — P5.2 runner가 건다


@dataclass
class AppState:
    settings: Settings
    factory: async_sessionmaker[AsyncSession]
    redis: Redis
    bus: EventBus
    engine: AsyncEngine | None = None  # 앱이 직접 만든 경우만 (shutdown에서 dispose)
    owns_redis: bool = False
    on_goal_created: GoalHook | None = None
    runner: GoalRunner | None = None  # P5.2: Goal 실행기(웹훅 승인 재개)


def build_state(
    settings: Settings,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
) -> AppState:
    engine: AsyncEngine | None = None
    if factory is None:
        engine = sess.create_engine(settings)
        factory = sess.create_session_factory(engine)
    owns_redis = redis is None
    if redis is None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return AppState(
        settings=settings,
        factory=factory,
        redis=redis,
        bus=EventBus(redis),
        engine=engine,
        owns_redis=owns_redis,
    )


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.ctx
    return state


def current_user(x_user_id: Annotated[str | None, Header()] = None) -> str:
    """MVP 1: 인증 없음. `X-User-Id` 헤더가 actor.id, 없으면 anonymous."""
    return x_user_id or "anonymous"


StateDep = Annotated[AppState, Depends(get_state)]
UserDep = Annotated[str, Depends(current_user)]


def human(user_id: str) -> Actor:
    return Actor(type="human", id=user_id)


async def publish(state: AppState, *events: Event) -> list[Event]:
    """outbox에 넣고 commit (D-06). 반환은 서명·id가 채워진 사본."""
    out: list[Event] = []
    async with sess.get_session(state.factory) as s:
        for e in events:
            out.append(await state.bus.publish(s, e))
    return out
