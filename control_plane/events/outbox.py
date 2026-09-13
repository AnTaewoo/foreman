"""Outbox relay (D-06): ``published_at IS NULL``인 행을 ``seq`` 순으로 XADD 하고 부기 컬럼을 채운다.

XADD 뒤 ``_mark_published``가 실패하면 다음 바퀴에 같은 행을 한 번 더 XADD 한다 (at-least-once).
projection은 event.id로 중복을 흡수한다 (P1.5). 이 파일의 UPDATE는 D-24가 허용한 부기 컬럼 예외다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import cast

import structlog
from redis.asyncio import Redis
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import stream_fields, stream_key
from control_plane.events.chain import row_to_event
from control_plane.store import models as m

log = structlog.get_logger(__name__)


class OutboxRelay:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        *,
        poll_interval: float = 0.5,
        batch: int = 100,
    ) -> None:
        self._factory = session_factory
        self._redis = redis
        self._poll_interval = poll_interval
        self._batch = batch
        self._task: asyncio.Task[None] | None = None
        self._stop = asyncio.Event()

    async def _mark_published(self, session: AsyncSession, seq: int, stream_id: str) -> None:
        await session.execute(
            update(m.Event)
            .where(m.Event.seq == seq)
            .values(stream_id=stream_id, published_at=datetime.now(UTC))
        )

    async def relay_once(self) -> int:
        """한 배치 전달. 전달한 행 수 반환."""
        async with self._factory() as session:
            stmt = (
                select(m.Event)
                .where(m.Event.published_at.is_(None))
                .order_by(m.Event.seq)
                .limit(self._batch)
            )
            rows = (await session.execute(stmt)).scalars().all()
            for row in rows:
                event = row_to_event(row)
                stream_id = cast(
                    str,
                    await self._redis.xadd(
                        stream_key(row.project_id), stream_fields(event, seq=row.seq)
                    ),
                )
                await self._mark_published(session, row.seq, stream_id)
            await session.commit()
            if rows:
                log.debug("outbox.relayed", count=len(rows))
            return len(rows)

    async def run(self) -> None:
        self._stop.clear()
        while not self._stop.is_set():
            try:
                n = await self.relay_once()
            except Exception:
                log.exception("outbox.relay_failed")
                n = 0
            if n == 0:
                await asyncio.sleep(self._poll_interval)

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="outbox-relay")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None
