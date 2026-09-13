"""P1.3 (d)(e) — Postgres: events append-only 트리거, JSONB 왕복 후 체인 검증 (D-29)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from control_plane.events.schema import Actor, Event, EventType, Subject, canonical_json, sign
from control_plane.store import models as m

pytestmark = pytest.mark.integration


def _row(i: int, payload: dict[str, Any], prev: str | None) -> tuple[m.Event, str]:
    ev = Event(
        project_id="P1", ts=datetime.now(UTC), actor=Actor(type="system", id="test"),
        type=EventType.GOAL_CREATED, subject=Subject(entity="goal", id="G1"), payload=payload,
        correlation_id="G1", causation_id=None,
    )  # fmt: skip
    canonical = canonical_json(ev)
    sig = sign(prev, canonical)
    row = m.Event(
        id=ev.id, project_id=ev.project_id, ts=ev.ts, actor_type=ev.actor.type,
        actor_id=ev.actor.id, type=ev.type.value, subject_entity=ev.subject.entity,
        subject_id=ev.subject.id, payload=ev.payload, canonical_json=canonical,
        correlation_id=ev.correlation_id, causation_id=ev.causation_id, signature=sig,
    )  # fmt: skip
    return row, sig


# (d) 트리거: 부기 컬럼 외 UPDATE / DELETE 거부
async def test_events_append_only_trigger(pg_session: AsyncSession) -> None:
    row, _ = _row(0, {"title": "t", "description": "d"}, None)
    row_id = row.id  # rollback 뒤 만료된 속성을 읽으면 동기 IO(MissingGreenlet)
    pg_session.add(row)
    await pg_session.commit()

    # 부기 컬럼은 허용
    for col, val in (
        ("published_at", datetime.now(UTC)),
        ("stream_id", "1-0"),
        ("projected_at", datetime.now(UTC)),
        ("projection_error", "x"),
    ):
        await pg_session.execute(
            text(f"UPDATE events SET {col} = :v WHERE id = :id"), {"v": val, "id": row_id}
        )
        await pg_session.commit()

    # 본문 컬럼 UPDATE 거부
    for stmt in (
        "UPDATE events SET payload = '{}'::jsonb WHERE id = :id",
        "UPDATE events SET canonical_json = 'x' WHERE id = :id",
        "UPDATE events SET signature = 'x' WHERE id = :id",
        "UPDATE events SET type = 'goal.cancelled' WHERE id = :id",
        "DELETE FROM events WHERE id = :id",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            await pg_session.execute(text(stmt), {"id": row_id})
        await pg_session.rollback()

    assert (await pg_session.execute(select(m.Event))).scalar_one().signature is not None


# (e) D-29: JSONB 왕복 후에도 체인이 검증되고 payload 사본은 의미 동치
PAYLOADS: list[dict[str, Any]] = [
    {"f": 1e-5},
    {"g": 0.1 + 0.2},
    {"big": 2**53 + 1},
    {"neg0": -0.0},
    {"s": "한글 🚀   \t tab"},
    {"empty": {}},
    {"list": []},
    {"nested": [[1, [2, [3]]]]},
    {"z": 1, "a": {"y": 2, "x": [{"k": "v", "b": None}]}},
    {"long": "x" * 10_000, "bools": [True, False]},
]


async def test_chain_survives_jsonb_roundtrip(pg_session: AsyncSession) -> None:
    prev: str | None = None
    expected: list[tuple[str, dict[str, Any], str]] = []
    for i, payload in enumerate(PAYLOADS):
        row, sig = _row(i, payload, prev)
        pg_session.add(row)
        await pg_session.flush()
        expected.append((row.id, payload, sig))
        prev = sig
    await pg_session.commit()
    pg_session.expunge_all()

    rows = (await pg_session.execute(select(m.Event).order_by(m.Event.seq))).scalars().all()
    assert [r.id for r in rows] == [e[0] for e in expected]

    # 체인 검증은 canonical_json 텍스트만 쓴다
    prev = None
    for r in rows:
        assert sign(prev, r.canonical_json) == r.signature
        prev = r.signature

    # payload JSONB 사본과 canonical 텍스트는 의미 동치 (표기는 달라도 됨)
    for r, (_, payload, _) in zip(rows, expected, strict=True):
        assert json.loads(r.canonical_json)["payload"] == payload
        assert r.payload == payload

    # JSONB가 실제로 표기를 바꾸는지 확인 (D-29의 근거): 1e-5는 텍스트로 0.00001이 된다
    raw = (
        await pg_session.execute(text("SELECT payload::text FROM events ORDER BY seq LIMIT 1"))
    ).scalar_one()
    assert raw == '{"f": 0.00001}'
    assert '"f":1e-05' in rows[0].canonical_json


async def test_seq_is_bigserial_and_id_unique(pg_session: AsyncSession) -> None:
    row, _ = _row(0, {"a": 1}, None)
    pg_session.add(row)
    await pg_session.commit()
    dup, _ = _row(0, {"a": 1}, None)
    dup.id = row.id
    pg_session.add(dup)
    with pytest.raises(DBAPIError, match="(?i)unique|duplicate"):
        await pg_session.commit()
