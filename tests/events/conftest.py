"""P1 이벤트 테스트 공용 픽스처: aiosqlite 파일 DB + **진짜 Redis** (D-32).

Redis는 ``FOREMAN_TEST_REDIS_URL``(기본 ``redis://localhost:6379/15``, 테스트 전용 DB 번호).
연결이 안 되면 skip이 아니라 **fail** — ``make test`` 전에 ``make docker-up``(또는 redis만).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import session as sess
from control_plane.store.models import Base


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    settings = Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{tmp_path / 'ev.db'}")
    eng = sess.create_engine(settings)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
def factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return sess.create_session_factory(engine)


@pytest.fixture
async def session(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with factory() as s:
        yield s


TEST_REDIS_URL = os.environ.get("FOREMAN_TEST_REDIS_URL", "redis://localhost:6379/15")


@pytest.fixture
async def redis() -> AsyncIterator[Redis]:
    r: Redis = Redis.from_url(TEST_REDIS_URL, decode_responses=True)
    try:
        await r.ping()
    except (RedisError, OSError) as exc:
        pytest.fail(f"Redis not reachable at {TEST_REDIS_URL}: {exc} — run `make docker-up`")
    await r.flushdb()
    yield r
    await r.flushdb()
    await r.aclose()


def make_event(
    type_: EventType = EventType.GOAL_CREATED,
    *,
    project_id: str = "P1",
    payload: dict[str, Any] | None = None,
    causation_id: str | None = None,
    subject: tuple[str, str] = ("goal", "G1"),
    ts: datetime | None = None,
) -> Event:
    default_payloads: dict[EventType, dict[str, Any]] = {
        EventType.GOAL_CREATED: {"title": "t", "description": "d"},
        EventType.RUN_TOOL_CALLED: {"tool": "fs.read", "args_digest": "ab" * 32, "duration_ms": 3},
        EventType.RUN_TOOL_DENIED: {"tool": "fs.read", "reason": "secret", "args_digest": "cd" * 32},
    }
    return Event(
        project_id=project_id,
        ts=ts or datetime.now(UTC),
        actor=Actor(type="system", id="test"),
        type=type_,
        subject=Subject(entity=subject[0], id=subject[1]),  # type: ignore[arg-type]  # 테스트 편의
        payload=default_payloads.get(type_, {}) if payload is None else payload,
        correlation_id="G1",
        causation_id=causation_id,
    )
