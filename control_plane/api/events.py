"""`GET /projects/{id}/events?since=<seq>&type=&limit=` — 커서는 DB append 순번 `seq` (D-29)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select

from control_plane.api.deps import StateDep
from control_plane.events.chain import row_to_event
from control_plane.store import models as m

router = APIRouter(prefix="/projects/{project_id}/events", tags=["events"])


class EventOut(BaseModel):
    seq: int
    id: str
    ts: datetime
    type: str
    actor: dict[str, str]
    subject: dict[str, str]
    payload: dict[str, Any]
    correlation_id: str
    causation_id: str | None


class EventPage(BaseModel):
    items: list[EventOut]
    next_since: int


@router.get("")
async def list_events(
    project_id: str,
    state: StateDep,
    since: Annotated[int, Query(ge=0)] = 0,
    type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> EventPage:
    stmt = select(m.Event).where(m.Event.project_id == project_id, m.Event.seq > since)
    if type is not None:
        stmt = stmt.where(m.Event.type == type)
    async with state.factory() as s:
        rows = (await s.execute(stmt.order_by(m.Event.seq).limit(limit))).scalars().all()
    items = []
    for row in rows:
        e = row_to_event(row)
        items.append(
            EventOut(
                seq=int(row.seq),
                id=e.id,
                ts=e.ts,
                type=e.type.value,
                actor={"type": e.actor.type, "id": e.actor.id},
                subject={"entity": e.subject.entity, "id": e.subject.id},
                payload=dict(e.payload),
                correlation_id=e.correlation_id,
                causation_id=e.causation_id,
            )
        )
    return EventPage(items=items, next_since=int(rows[-1].seq) if rows else since)
