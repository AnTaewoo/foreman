"""P5.1 API 픽스처: sqlite + 진짜 Redis 위의 앱, ASGI 클라이언트, relay+projection pump."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api.app import create_app
from control_plane.config import Settings
from control_plane.events.bus import EventBus
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Event
from tests.events import conftest as _events

Pump = Callable[[], Awaitable[list[str]]]

# P1 픽스처 재노출 (aiosqlite + 진짜 Redis)
engine = _events.engine
factory = _events.factory
redis = _events.redis
session = _events.session


@pytest.fixture
def app(factory: async_sessionmaker[AsyncSession], redis: Redis) -> Any:
    return create_app(Settings(_env_file=None), factory=factory, redis=redis)


@pytest.fixture
async def client(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        yield c


@pytest.fixture
def pump(factory: async_sessionmaker[AsyncSession], redis: Redis) -> Pump:
    """outbox → stream → projection. 조용해질 때까지. 소비한 이벤트 타입 목록 반환."""
    bus = EventBus(redis)
    relay = OutboxRelay(factory, redis, batch=100)
    projection = Projection(factory, bus)

    async def run() -> list[str]:
        seen: list[str] = []

        async def handler(d: Any) -> None:
            await projection.handle(d)
            seen.append(d.event.type.value)

        for _ in range(20):
            relayed = 0
            while (n := await relay.relay_once()) > 0:
                relayed += n
            consumed = await bus.poll_once("api-test", handler, consumer="t")
            if relayed == 0 and consumed == 0:
                break
        # 순서 역전 재시도(D-30)도 바로 흘려보낸다
        async with factory() as s:
            from sqlalchemy import select

            from control_plane.store import models as m

            pids = set((await s.execute(select(m.Event.project_id).distinct())).scalars().all())
        for pid in pids:
            await projection.apply_retries(pid, now=datetime.now(UTC) + timedelta(hours=3))
        return seen

    return run


@pytest.fixture
def publish(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> Callable[..., Awaitable[list[Event]]]:
    """테스트가 projection 상태를 만들 때 쓰는 직접 발행 (emit/Scheduler 대역)."""
    bus = EventBus(redis)

    async def run(*events: Event) -> list[Event]:
        out: list[Event] = []
        async with factory() as s:
            for e in events:
                out.append(await bus.publish(s, e))
            await s.commit()
        return out

    return run
