"""P1 이벤트 테스트 공용 픽스처: aiosqlite 파일 DB + fakeredis."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from fakeredis import aioredis
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


@pytest.fixture
async def redis() -> AsyncIterator[aioredis.FakeRedis]:
    r = aioredis.FakeRedis(decode_responses=True)
    yield r
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
