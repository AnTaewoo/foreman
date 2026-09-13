"""P1.4 — events/chain.py: append_signed 유일 경로, 서명, tool_calls 분기, 락, AST 가드 (a~f)."""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.chain import PayloadError, append_signed, verify_chain_db
from control_plane.events.schema import EventType, canonical_json, sign, verify_chain
from control_plane.store import models as m
from tests.events.conftest import make_event

ROOT = Path(__file__).resolve().parent.parent.parent


# (a) 서명·체인·seq, 세션을 넘어서도 이어진다
async def test_append_signed_chains_across_sessions(
    factory: async_sessionmaker[AsyncSession],
) -> None:
    async with factory() as s1:
        e1 = await append_signed(s1, make_event())
        await s1.commit()
    async with factory() as s2:
        e2 = await append_signed(s2, make_event(causation_id=e1.id))
        await s2.commit()

    assert e1.signature == sign(None, canonical_json(e1))
    assert e2.signature == sign(e1.signature, canonical_json(e2))
    assert verify_chain([e1, e2]) is True

    async with factory() as s3:
        rows = (await s3.execute(select(m.Event).order_by(m.Event.seq))).scalars().all()
        assert [r.id for r in rows] == [e1.id, e2.id]
        assert rows[0].seq < rows[1].seq
        assert rows[0].canonical_json == canonical_json(e1)
        assert rows[1].signature == e2.signature
        assert await verify_chain_db(s3, "P1") is True


async def test_chain_is_per_project(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        a = await append_signed(s, make_event(project_id="A"))
        b = await append_signed(s, make_event(project_id="B"))
        a2 = await append_signed(s, make_event(project_id="A"))
        await s.commit()
    assert b.signature == sign(None, canonical_json(b))  # B의 루트
    assert a2.signature == sign(a.signature, canonical_json(a2))


# (b) 발행자 서명 금지
async def test_append_signed_rejects_presigned(session: AsyncSession) -> None:
    from control_plane.events.schema import signed

    with pytest.raises(ValueError, match="signature"):
        await append_signed(session, signed(make_event(), "0" * 64))


# (c) run.tool_called → tool_calls 테이블만
async def test_tool_called_goes_to_tool_calls_only(session: AsyncSession) -> None:
    ev = make_event(EventType.RUN_TOOL_CALLED, subject=("run", "R1"))
    out = await append_signed(session, ev)
    await session.commit()
    assert out.signature is None  # 체인 밖
    assert (await session.execute(select(func.count()).select_from(m.Event))).scalar_one() == 0
    row = (await session.execute(select(m.ToolCall))).scalar_one()
    assert row.id == ev.id and row.run_id == "R1" and row.tool == "fs.read"
    assert row.duration_ms == 3 and row.denied is False


async def test_tool_denied_is_chained(session: AsyncSession) -> None:
    ev = make_event(EventType.RUN_TOOL_DENIED, subject=("run", "R1"))
    out = await append_signed(session, ev)
    await session.commit()
    assert out.signature is not None
    assert (await session.execute(select(func.count()).select_from(m.Event))).scalar_one() == 1


# (d) 필수 키 검사
async def test_missing_required_payload_key(session: AsyncSession) -> None:
    with pytest.raises(PayloadError, match="description"):
        await append_signed(session, make_event(payload={"title": "only"}))
    # 미등록 타입은 검사 안 함
    await append_signed(session, make_event(EventType.DECISION_OPENED, payload={}))


# (e) 동시 append 20개 → 체인 유지 (락이 commit까지 유지)
async def test_concurrent_appends_keep_chain(factory: async_sessionmaker[AsyncSession]) -> None:
    async def one(i: int) -> None:
        async with factory() as s:
            await append_signed(s, make_event(payload={"title": str(i), "description": "d"}))
            await asyncio.sleep(0)  # 다른 태스크에 기회
            await s.commit()

    await asyncio.gather(*(one(i) for i in range(20)))
    async with factory() as s:
        assert (await s.execute(select(func.count()).select_from(m.Event))).scalar_one() == 20
        assert await verify_chain_db(s, "P1") is True


async def test_rollback_releases_lock(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        await append_signed(s, make_event())
        await s.rollback()
    async with factory() as s:  # 락이 풀려 있어야 여기서 멈추지 않는다
        await asyncio.wait_for(append_signed(s, make_event()), timeout=2)
        await s.commit()
        assert await verify_chain_db(s, "P1") is True


async def test_verify_chain_db_detects_tamper(factory: async_sessionmaker[AsyncSession]) -> None:
    async with factory() as s:
        for _ in range(3):
            await append_signed(s, make_event())
        await s.commit()
    async with factory() as s:
        row = (await s.execute(select(m.Event).order_by(m.Event.seq))).scalars().first()
        assert row is not None
        row.canonical_json = row.canonical_json.replace('"t"', '"X"')  # sqlite: 트리거 없음
        await s.commit()
    async with factory() as s:
        assert await verify_chain_db(s, "P1") is False


# (f) AST: Event/ToolCall 모델 row를 만드는 곳은 chain.py뿐
def _model_ctor_calls(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases: set[str] = set()  # models 모듈 별칭
    names: set[str] = set()  # 직접 import한 Event/ToolCall 이름
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "control_plane.store.models":
                    aliases.add(a.asname or "control_plane.store.models")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "control_plane.store.models":
                for a in node.names:
                    if a.name in {"Event", "ToolCall"}:
                        names.add(a.asname or a.name)
            elif node.module == "control_plane.store":
                for a in node.names:
                    if a.name == "models":
                        aliases.add(a.asname or "models")
    hits: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if isinstance(f, ast.Name) and f.id in names:
            hits.append(f"{path.name}:{node.lineno} {f.id}(")
        elif (
            isinstance(f, ast.Attribute)
            and f.attr in {"Event", "ToolCall"}
            and isinstance(f.value, ast.Name)
            and f.value.id in aliases
        ):
            hits.append(f"{path.name}:{node.lineno} {f.value.id}.{f.attr}(")
    return hits


def test_only_chain_py_constructs_event_rows() -> None:
    offenders: list[str] = []
    for p in (ROOT / "control_plane").rglob("*.py"):
        if p.name == "chain.py" and p.parent.name == "events":
            continue
        offenders.extend(_model_ctor_calls(p))
    assert offenders == [], offenders
