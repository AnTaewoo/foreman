"""P1.4 — events/bus.py + outbox.py: publish/relay/subscribe/replay/retry (red g~l)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events import outbox as outbox_mod
from control_plane.events.bus import (
    RETRY_DELAYS,
    Delivery,
    EventBus,
    RetryExhausted,
    retry_stream_key,
    stream_key,
)
from control_plane.events.outbox import OutboxRelay
from control_plane.events.schema import Event, EventType, verify_chain
from control_plane.store import models as m
from tests.events.conftest import make_event


# (k) 키
def test_stream_keys() -> None:
    assert stream_key("P1") == "events:P1"
    assert retry_stream_key("P1") == "events:P1:retry"


# (g) publish → outbox → relay → XADD + published_at/stream_id
async def test_publish_outbox_relay(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        ev = await bus.publish(s, make_event())
        row = (await s.execute(select(m.Event))).scalar_one()
        assert row.published_at is None and row.stream_id is None
        assert ev.signature is not None
        await s.commit()
    assert await redis.xlen(stream_key("P1")) == 0

    relay = OutboxRelay(factory, redis, poll_interval=0.01, batch=10)
    assert await relay.relay_once() == 1
    assert await redis.xlen(stream_key("P1")) == 1
    async with factory() as s:
        row = (await s.execute(select(m.Event))).scalar_one()
        assert row.published_at is not None and row.published_at.tzinfo is not None
        assert row.stream_id is not None and "-" in row.stream_id
    assert await relay.relay_once() == 0  # 두 번째는 할 일 없음

    msgs = await redis.xrange(stream_key("P1"))
    fields = msgs[0][1]
    assert fields["id"] == ev.id and fields["type"] == "goal.created"
    assert fields["signature"] == ev.signature
    restored = Event.model_validate_json(fields["canonical"]).model_copy(
        update={"signature": fields["signature"]}
    )
    assert restored == ev


async def test_publish_tool_called_streams_directly(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        await bus.publish(s, make_event(EventType.RUN_TOOL_CALLED, subject=("run", "R1")))
        await s.commit()
    assert await redis.xlen(stream_key("P1")) == 1  # outbox 안 거치고 바로 스트림 (D-31)
    async with factory() as s:
        assert (await s.execute(select(m.Event))).scalars().all() == []
        assert len((await s.execute(select(m.ToolCall))).scalars().all()) == 1


# (h) at-least-once: mark 실패 → 다시 XADD
async def test_relay_is_at_least_once(
    factory: async_sessionmaker[AsyncSession],
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        await bus.publish(s, make_event())
        await s.commit()
    relay = OutboxRelay(factory, redis)

    async def broken(*args: object, **kwargs: object) -> None:
        raise RuntimeError("mark failed")

    monkeypatch.setattr(outbox_mod.OutboxRelay, "_mark_published", broken)
    with pytest.raises(RuntimeError):
        await relay.relay_once()
    assert await redis.xlen(stream_key("P1")) == 1
    monkeypatch.undo()
    assert await relay.relay_once() == 1
    assert await redis.xlen(stream_key("P1")) == 2  # 중복 전달 — projection이 id로 흡수


# (i) subscribe: group, ack, 재전달, attempt 증가
async def test_subscribe_ack_and_reclaim(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        for i in range(2):
            await bus.publish(s, make_event(payload={"title": str(i), "description": "d"}))
        await s.commit()
    await OutboxRelay(factory, redis).relay_once()

    seen: list[Delivery] = []
    fail_ids: set[str] = set()

    async def handler(d: Delivery) -> None:
        seen.append(d)
        if d.event.id in fail_ids:
            raise RuntimeError("boom")

    # 첫 이벤트는 실패시킨다
    msgs = await redis.xrange(stream_key("P1"))
    first_id = msgs[0][1]["id"]
    fail_ids.add(first_id)

    n = await bus.poll_once("proj", handler, consumer="c1", project_id="P1", reclaim_idle_ms=0)
    assert n == 2
    assert [d.attempt for d in seen] == [1, 1]
    pending = await redis.xpending(stream_key("P1"), "proj")
    assert pending["pending"] == 1  # 실패한 것만 ack 안 됨

    # 재전달: idle 0ms 기준으로 XAUTOCLAIM → attempt 2
    seen.clear()
    n = await bus.poll_once("proj", handler, consumer="c2", project_id="P1", reclaim_idle_ms=0)
    assert n == 1
    assert seen[0].event.id == first_id and seen[0].attempt == 2
    seen.clear()
    fail_ids.clear()
    n = await bus.poll_once("proj", handler, consumer="c1", project_id="P1", reclaim_idle_ms=0)
    assert n == 1 and seen[0].attempt == 3
    pending = await redis.xpending(stream_key("P1"), "proj")
    assert pending["pending"] == 0


async def test_subscribe_all_projects_via_scan(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        await bus.publish(s, make_event(project_id="A"))
        await bus.publish(s, make_event(project_id="B"))
        await s.commit()
    await OutboxRelay(factory, redis).relay_once()
    await bus.schedule_retry("A", "x", 1)  # retry 스트림은 구독 대상이 아님
    got: list[str] = []

    async def handler(d: Delivery) -> None:
        got.append(d.event.project_id)

    n = await bus.poll_once("g", handler, consumer="c", project_id=None)
    assert n == 2 and sorted(got) == ["A", "B"]


async def test_subscribe_loop_stops(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    bus = EventBus(redis)

    async def handler(d: Delivery) -> None:
        bus.stop()

    async with factory() as s:
        await bus.publish(s, make_event())
        await s.commit()
    await OutboxRelay(factory, redis).relay_once()
    import asyncio

    await asyncio.wait_for(
        bus.subscribe("g", handler, consumer="c", project_id="P1", block_ms=10), timeout=5
    )


# (j) replay: DB seq 순, tool_called 제외, since_seq
async def test_replay_from_db(factory: async_sessionmaker[AsyncSession], redis: Redis) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        a = await bus.publish(s, make_event())
        await bus.publish(s, make_event(EventType.RUN_TOOL_CALLED, subject=("run", "R1")))
        b = await bus.publish(s, make_event(causation_id=a.id))
        c = await bus.publish(s, make_event(causation_id=b.id))
        await s.commit()
    async with factory() as s:
        events = await bus.replay(s, "P1")
        assert [e.id for e in events] == [a.id, b.id, c.id]
        assert verify_chain(events) is True
        seqs = (await s.execute(select(m.Event.seq).order_by(m.Event.seq))).scalars().all()
        later = await bus.replay(s, "P1", since_seq=seqs[0])
        assert [e.id for e in later] == [b.id, c.id]
        assert await bus.replay(s, "OTHER") == []


# (l) retry 스트림
async def test_schedule_and_due_retries(redis: Redis) -> None:
    bus = EventBus(redis)
    assert RETRY_DELAYS == {1: 5, 2: 30, 3: 300, 4: 1800, 5: 7200}
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
    await bus.schedule_retry("P1", "E1", 1, now=now)
    await bus.schedule_retry("P1", "E2", 3, now=now)
    with pytest.raises(RetryExhausted):
        await bus.schedule_retry("P1", "E3", 6, now=now)
    assert await redis.xlen(retry_stream_key("P1")) == 2

    assert await bus.due_retries("P1", now=now + timedelta(seconds=4)) == []
    due = await bus.due_retries("P1", now=now + timedelta(seconds=5))
    assert [(r.event_id, r.attempt) for r in due] == [("E1", 1)]
    assert await redis.xlen(retry_stream_key("P1")) == 1  # 반환된 건 제거
    due = await bus.due_retries("P1", now=now + timedelta(seconds=301))
    assert [(r.event_id, r.attempt) for r in due] == [("E2", 3)]
    assert await bus.due_retries("P1", now=now + timedelta(days=1)) == []
