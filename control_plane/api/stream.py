"""``WS /projects/{id}/stream`` (설계 §13): 이벤트 실시간 전달.

- 연결마다 consumer group ``ws:<ulid>``를 ``$``(지금부터)로 만들고 ``EventBus``로 읽어 큐에
  넣는다. 연결이 끊기면 group을 지운다.
- ``?since=<seq>``(D-29)면 group을 먼저 만든 뒤 DB에서 ``seq > since``를 replay 하고 live로 간다.
  replay와 live가 겹치는 이벤트는 id로 한 번만 보낸다.
- 메시지 형식은 ``GET /events``의 항목과 같다(워커가 직접 XADD한 미서명 이벤트는 ``seq: null``).
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any

import structlog
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from ulid import ULID

from control_plane.api.deps import AppState
from control_plane.events.bus import Delivery, EventBus, stream_key
from control_plane.events.chain import row_to_event
from control_plane.events.schema import Event
from control_plane.store import models as m

log = structlog.get_logger(__name__)
router = APIRouter(tags=["stream"])


def serialize(event: Event, seq: int | None) -> dict[str, Any]:
    return {
        "seq": seq,
        "id": event.id,
        "ts": event.ts.isoformat(),
        "type": event.type.value,
        "actor": {"type": event.actor.type, "id": event.actor.id},
        "subject": {"entity": event.subject.entity, "id": event.subject.id},
        "payload": dict(event.payload),
        "correlation_id": event.correlation_id,
        "causation_id": event.causation_id,
    }


@router.websocket("/projects/{project_id}/stream")
async def stream(
    websocket: WebSocket,
    project_id: str,
    since: int | None = Query(default=None, ge=0),
) -> None:
    state: AppState = websocket.app.state.ctx
    group = f"ws:{ULID()}"
    stream_name = stream_key(project_id)
    # group을 accept 전에 만든다: accept 뒤에 만들면 그 사이 발행된 이벤트를 놓친다
    # (부하 때 tests/api/test_stream.py가 receive에서 영원히 멈추던 경쟁, 2026-09-18)
    await _create_group(state, stream_name, group)
    await websocket.accept()
    bus = EventBus(state.redis)  # 연결 전용 (stop이 연결 단위)
    queue: asyncio.Queue[Delivery] = asyncio.Queue()
    sent: set[str] = set()

    async def enqueue(delivery: Delivery) -> None:
        await queue.put(delivery)

    pump = asyncio.create_task(
        bus.subscribe(group, enqueue, consumer="ws", project_id=project_id, block_ms=500)
    )
    try:
        if since is not None:
            async with state.factory() as s:
                rows = (
                    await s.execute(
                        select(m.Event)
                        .where(m.Event.project_id == project_id, m.Event.seq > since)
                        .order_by(m.Event.seq)
                    )
                ).scalars()
                for row in rows.all():
                    event = row_to_event(row)
                    sent.add(event.id)
                    await websocket.send_json(serialize(event, int(row.seq)))
        while True:
            delivery = await queue.get()
            if delivery.event.id in sent:
                continue
            sent.add(delivery.event.id)
            await websocket.send_json(serialize(delivery.event, delivery.seq))
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("stream.closed", project_id=project_id, error=repr(exc))
    finally:
        bus.stop()
        pump.cancel()
        with contextlib.suppress(BaseException):
            await pump
        with contextlib.suppress(Exception):
            await state.redis.xgroup_destroy(stream_name, group)


async def _create_group(state: AppState, stream_name: str, group: str) -> None:
    """``$``부터 읽는 group. (``EventBus._ensure_group``은 ``0``부터라 여기서 먼저 만든다.)"""
    try:
        await state.redis.xgroup_create(stream_name, group, id="$", mkstream=True)
    except Exception as exc:  # BUSYGROUP는 ULID 충돌뿐 — 무시
        if "BUSYGROUP" not in str(exc):
            raise
