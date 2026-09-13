"""해시 체인 append — ``events``/``tool_calls`` 행을 만드는 **유일한** 곳 (D-26, D-29, D-31).

- ``append_signed(session, event)``: 같은 project의 직전 행 signature로 서명해 insert. 발행자는
  ``signature=None``으로 넘긴다. ``UNCHAINED``(run.tool_called)는 ``tool_calls``에만, 서명 없음.
- 체인 직렬화: Postgres는 ``pg_advisory_xact_lock(hashtext(project_id))``(트랜잭션 끝까지 유지),
  그 외(sqlite)는 프로세스 내 ``asyncio.Lock``을 **세션 commit/rollback까지** 잡는다 — 그래야
  다른 세션이 아직 안 보이는 행을 prev로 놓치지 않는다. 같은 세션의 재진입은 통과.
- ``verify_chain_db(session, project_id)``: ``seq`` 순으로 ``canonical_json`` 텍스트만 재계산.
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Iterable

from sqlalchemy import event as sa_event
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Session

from control_plane.events.schema import (
    UNCHAINED,
    Event,
    canonical_json,
    required_payload_keys,
    sign,
    signed,
)
from control_plane.store import models as m


class PayloadError(ValueError):
    """``PAYLOAD_TYPES``의 필수 키가 빠졌다."""


# 루프별 락 (테스트는 루프를 매번 새로 만든다). 프로세스 내 직렬화용 — Postgres는 advisory lock.
_locks_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, dict[str, asyncio.Lock]] = (
    weakref.WeakKeyDictionary()
)
_INFO_LOCKS = "chain_locks"  # session.info: 이 세션이 잡고 있는 project_id 집합
_INFO_HOOK = "chain_hook"  # session.info: 해제 리스너 등록 여부


def _local_lock(project_id: str) -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    locks = _locks_by_loop.setdefault(loop, {})
    lock = locks.get(project_id)
    if lock is None:
        lock = locks[project_id] = asyncio.Lock()
    return lock


def _install_release_hook(session: AsyncSession) -> None:
    """세션당 한 번: commit/rollback 시 이 세션이 잡은 락을 전부 푼다."""
    if session.info.get(_INFO_HOOK):
        return
    session.info[_INFO_HOOK] = True
    sync_session: Session = session.sync_session

    def release(*_: object) -> None:
        held: set[str] = session.info.get(_INFO_LOCKS, set())
        for project_id in list(held):
            lock = _local_lock(project_id)
            if lock.locked():
                lock.release()
        held.clear()

    sa_event.listen(sync_session, "after_commit", release)
    sa_event.listen(sync_session, "after_rollback", release)


async def _acquire_chain_lock(session: AsyncSession, project_id: str) -> None:
    bind = session.get_bind()
    if bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:pid))"), {"pid": project_id}
        )
        return
    held: set[str] = session.info.setdefault(_INFO_LOCKS, set())
    if project_id in held:
        return  # 같은 세션 재진입 — commit까지 이미 잡고 있다
    await _local_lock(project_id).acquire()
    held.add(project_id)
    _install_release_hook(session)


def _check_required_keys(event: Event) -> None:
    missing = required_payload_keys(event.type) - event.payload.keys()
    if missing:
        raise PayloadError(f"{event.type.value}: missing payload keys {sorted(missing)}")


async def _last_signature(session: AsyncSession, project_id: str) -> str | None:
    stmt = (
        select(m.Event.signature)
        .where(m.Event.project_id == project_id)
        .order_by(m.Event.seq.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


def _event_row(event: Event, canonical: str, signature: str) -> m.Event:
    return m.Event(
        id=event.id,
        project_id=event.project_id,
        ts=event.ts,
        actor_type=event.actor.type,
        actor_id=event.actor.id,
        type=event.type.value,
        subject_entity=event.subject.entity,
        subject_id=event.subject.id,
        payload=event.payload,
        canonical_json=canonical,
        correlation_id=event.correlation_id,
        causation_id=event.causation_id,
        signature=signature,
    )


def _tool_call_row(event: Event) -> m.ToolCall:
    p = event.payload
    return m.ToolCall(
        id=event.id,
        run_id=event.subject.id,
        project_id=event.project_id,
        tool=str(p["tool"]),
        args_digest=str(p["args_digest"]),
        duration_ms=p.get("duration_ms"),
        denied=False,
        ts=event.ts,
    )


async def append_signed(session: AsyncSession, event: Event) -> Event:
    """서명해 append. 반환값은 서명이 채워진 사본 (UNCHAINED는 그대로, signature None)."""
    if event.signature is not None:
        raise ValueError("producer must not set signature; it is assigned at append time")
    _check_required_keys(event)

    if event.type in UNCHAINED:
        session.add(_tool_call_row(event))
        await session.flush()
        return event

    await _acquire_chain_lock(session, event.project_id)
    prev = await _last_signature(session, event.project_id)
    canonical = canonical_json(event)
    signature = sign(prev, canonical)
    session.add(_event_row(event, canonical, signature))
    await session.flush()
    return signed(event, signature)


def row_to_event(row: m.Event) -> Event:
    """저장된 canonical 텍스트로 Event 복원 (payload JSONB 사본은 쓰지 않는다)."""
    return Event.model_validate_json(row.canonical_json).model_copy(
        update={"signature": row.signature}
    )


def verify_rows(rows: Iterable[tuple[str, str | None]]) -> bool:
    """(canonical_json, signature) 열을 순서대로 검증."""
    prev: str | None = None
    for canonical, signature in rows:
        if signature is None or sign(prev, canonical) != signature:
            return False
        prev = signature
    return True


async def verify_chain_db(session: AsyncSession, project_id: str) -> bool:
    stmt = (
        select(m.Event.canonical_json, m.Event.signature)
        .where(m.Event.project_id == project_id)
        .order_by(m.Event.seq)
    )
    rows = (await session.execute(stmt)).all()
    return verify_rows((c, s) for c, s in rows)
