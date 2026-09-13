"""Event Bus — Redis Streams (설계 §3.2). publish는 outbox(D-06), 전달은 consumer group, 재시도는 D-30.

스트림 메시지 필드: ``id``, ``type``, ``seq``, ``canonical``(서명 대상 텍스트), ``signature``.
수신 측은 canonical 텍스트로 Event를 복원하므로 JSON 표기가 변하지 않는다 (D-29).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, cast

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from control_plane.events.chain import append_signed, row_to_event
from control_plane.events.schema import UNCHAINED, Event, canonical_json
from control_plane.store import models as m

log = structlog.get_logger(__name__)

STREAM_PREFIX = "events:"
RETRY_SUFFIX = ":retry"
# D-30: InvalidTransition 지연 재처리 (attempt → 초)
RETRY_DELAYS: dict[int, int] = {1: 5, 2: 30, 3: 300, 4: 1800, 5: 7200}


def stream_key(project_id: str) -> str:
    return f"{STREAM_PREFIX}{project_id}"


def retry_stream_key(project_id: str) -> str:
    return f"{STREAM_PREFIX}{project_id}{RETRY_SUFFIX}"


class RetryExhausted(Exception):
    """D-30 재시도 5회 소진."""


@dataclass(frozen=True)
class Delivery:
    """consumer group이 전달한 메시지 하나."""

    event: Event
    message_id: str
    attempt: int
    group: str
    consumer: str
    seq: int | None


@dataclass(frozen=True)
class RetryItem:
    event_id: str
    attempt: int
    not_before: datetime
    message_id: str


Handler = Callable[[Delivery], Awaitable[None]]


def stream_fields(event: Event, seq: int | None = None) -> dict[str, str]:
    """XADD 필드. 워커(P4.4)도 이 형식으로 직접 XADD 한다."""
    return {
        "id": event.id,
        "type": event.type.value,
        "seq": "" if seq is None else str(seq),
        "canonical": canonical_json(event),
        "signature": event.signature or "",
    }


def parse_fields(fields: dict[str, str]) -> tuple[Event, int | None]:
    event = Event.model_validate_json(fields["canonical"])
    if fields.get("signature"):
        event = event.model_copy(update={"signature": fields["signature"]})
    seq = int(fields["seq"]) if fields.get("seq") else None
    return event, seq


class EventBus:
    def __init__(self, redis: Redis) -> None:
        self._redis = redis
        self._stop = asyncio.Event()

    # ------------------------------------------------------------------ publish
    async def publish(self, session: AsyncSession, event: Event) -> Event:
        """outbox: 서명·insert만 한다. 스트림 전달은 ``OutboxRelay``. UNCHAINED는 바로 XADD (D-31)."""
        out = await append_signed(session, event)
        if event.type in UNCHAINED:
            await self._redis.xadd(stream_key(event.project_id), stream_fields(out))
        return out

    # ------------------------------------------------------------------ subscribe
    def stop(self) -> None:
        self._stop.set()

    async def _streams(self, project_id: str | None) -> list[str]:
        if project_id is not None:
            return [stream_key(project_id)]
        keys: list[str] = []
        async for key in self._redis.scan_iter(match=f"{STREAM_PREFIX}*"):
            k = key if isinstance(key, str) else key.decode()
            if not k.endswith(RETRY_SUFFIX):
                keys.append(k)
        return sorted(keys)

    async def _ensure_group(self, stream: str, group: str) -> None:
        try:
            await self._redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as exc:  # BUSYGROUP = 이미 있음
            if "BUSYGROUP" not in str(exc):
                raise

    async def _deliver(
        self,
        stream: str,
        group: str,
        consumer: str,
        handler: Handler,
        message_id: str,
        fields: dict[str, str],
        attempt: int,
    ) -> None:
        event, seq = parse_fields(fields)
        delivery = Delivery(
            event=event, message_id=message_id, attempt=attempt, group=group,
            consumer=consumer, seq=seq,
        )  # fmt: skip
        try:
            await handler(delivery)
        except Exception:
            log.exception(
                "bus.handler_failed", stream=stream, message_id=message_id, attempt=attempt
            )
            return  # ack 안 함 → pending → XAUTOCLAIM 재전달
        await self._redis.xack(stream, group, message_id)

    async def _delivery_counts(self, stream: str, group: str, ids: list[str]) -> dict[str, int]:
        if not ids:
            return {}
        rows = cast(
            list[dict[str, Any]],
            await self._redis.xpending_range(stream, group, min="-", max="+", count=1000),
        )
        wanted = set(ids)
        return {
            r["message_id"]: int(r["times_delivered"]) for r in rows if r["message_id"] in wanted
        }

    async def poll_once(
        self,
        group: str,
        handler: Handler,
        *,
        consumer: str = "c1",
        project_id: str | None = None,
        block_ms: int = 0,
        reclaim_idle_ms: int = 30_000,
        count: int = 100,
    ) -> int:
        """한 바퀴: (1) idle pending 재전달(XAUTOCLAIM) (2) 새 메시지(XREADGROUP '>'). 처리 건수 반환."""
        handled = 0
        streams = await self._streams(project_id)
        for stream in streams:
            await self._ensure_group(stream, group)
            # (1) 재전달
            _, claimed, *_rest = cast(
                tuple[Any, ...],
                await self._redis.xautoclaim(
                    stream, group, consumer, min_idle_time=reclaim_idle_ms, start_id="0-0",
                    count=count,
                ),  # fmt: skip
            )
            claimed = cast(list[tuple[str, dict[str, str]]], claimed)
            counts = await self._delivery_counts(stream, group, [mid for mid, _ in claimed])
            for message_id, fields in claimed:
                attempt = counts.get(message_id, 2)
                await self._deliver(stream, group, consumer, handler, message_id, fields, attempt)
                handled += 1
        if not streams:
            return handled
        # (2) 새 메시지
        results = cast(
            list[tuple[str, list[tuple[str, dict[str, str]]]]],
            await self._redis.xreadgroup(
                group, consumer, dict.fromkeys(streams, ">"), count=count, block=block_ms or None
            ),
        )
        for stream, messages in results or []:
            for message_id, fields in messages:
                await self._deliver(stream, group, consumer, handler, message_id, fields, 1)
                handled += 1
        return handled

    async def subscribe(
        self,
        group: str,
        handler: Handler,
        *,
        consumer: str = "c1",
        project_id: str | None = None,
        block_ms: int = 1000,
        reclaim_idle_ms: int = 30_000,
    ) -> None:
        """``stop()``까지 ``poll_once`` 반복."""
        self._stop.clear()
        while not self._stop.is_set():
            n = await self.poll_once(
                group, handler, consumer=consumer, project_id=project_id, block_ms=block_ms,
                reclaim_idle_ms=reclaim_idle_ms,
            )  # fmt: skip
            if n == 0 and project_id is None:
                await asyncio.sleep(block_ms / 1000)

    # ------------------------------------------------------------------ replay
    async def replay(
        self, session: AsyncSession, project_id: str, since_seq: int | None = None
    ) -> list[Event]:
        """DB ``seq`` 순. UNCHAINED는 events에 없으므로 자연히 제외."""
        stmt = select(m.Event).where(m.Event.project_id == project_id)
        if since_seq is not None:
            stmt = stmt.where(m.Event.seq > since_seq)
        rows = (await session.execute(stmt.order_by(m.Event.seq))).scalars().all()
        return [row_to_event(r) for r in rows]

    # ------------------------------------------------------------------ retry (D-30)
    async def schedule_retry(
        self, project_id: str, event_id: str, attempt: int, *, now: datetime | None = None
    ) -> str:
        delay = RETRY_DELAYS.get(attempt)
        if delay is None:
            raise RetryExhausted(f"{event_id}: attempt {attempt} > {max(RETRY_DELAYS)}")
        base = now or datetime.now(UTC)
        not_before = base.timestamp() + delay
        message_id = await self._redis.xadd(
            retry_stream_key(project_id),
            {"event_id": event_id, "attempt": str(attempt), "not_before": repr(not_before)},
        )
        return cast(str, message_id)

    async def due_retries(
        self, project_id: str, *, now: datetime | None = None
    ) -> list[RetryItem]:
        """기한이 지난 항목만 돌려주고 스트림에서 제거."""
        ts_now = (now or datetime.now(UTC)).timestamp()
        key = retry_stream_key(project_id)
        rows = cast(list[tuple[str, dict[str, str]]], await self._redis.xrange(key))
        due: list[RetryItem] = []
        for message_id, f in rows:
            not_before = float(f["not_before"])
            if not_before <= ts_now:
                due.append(
                    RetryItem(
                        event_id=f["event_id"],
                        attempt=int(f["attempt"]),
                        not_before=datetime.fromtimestamp(not_before, tz=UTC),
                        message_id=message_id,
                    )
                )
        if due:
            await self._redis.xdel(key, *[d.message_id for d in due])
        return due
