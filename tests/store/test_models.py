"""P1.2 — SQLAlchemy 모델 (설계 §4.1 + D-29/D-30/D-31). aiosqlite로 검증."""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import StatementError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from control_plane.store import models as m
from control_plane.store.enums import (
    EpicStatus,
    GoalStatus,
    RiskTier,
    Role,
    RunOutcome,
    TaskKind,
    TaskStatus,
)

EXPECTED_TABLES = {
    "projects", "goals", "epics", "tasks", "runs", "decisions", "agents", "events", "tool_calls"
}  # fmt: skip


@pytest.fixture
async def session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(m.Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        yield s
    await engine.dispose()


def _project() -> m.Project:
    return m.Project(
        id="P1", name="demo", repo_full_name="org/demo", installation_id=None,
        policy={"version": 1}, budget={"daily_usd": 30}, default_branch="main",
        protected_paths=["infra/**"], members=[{"user_id": "u1", "role": "owner"}],
    )  # fmt: skip


# (f) 9개 테이블
def test_nine_tables() -> None:
    assert set(m.Base.metadata.tables) == EXPECTED_TABLES


# (g) Project → Goal → Epic → Task → Run 왕복
async def test_entity_roundtrip(session: AsyncSession) -> None:
    session.add(_project())
    session.add(m.Goal(id="G1", project_id="P1", title="goal", description="desc"))
    session.add(m.Epic(id="E1", goal_id="G1", title="epic", order=1, milestone_number=7))
    session.add(
        m.Task(
            id="T1", epic_id="E1", project_id="P1", goal_id="G1", title="task", spec="spec",
            kind=TaskKind.FEATURE, role_required=Role.CODING, depends_on=[], owned_paths=["src/**"],
            risk_tier=RiskTier.T1, status=TaskStatus.READY, issue_number=12,
        )  # fmt: skip
    )
    session.add(
        m.Run(
            id="R1", task_id="T1", project_id="P1", agent_id="A1", outcome=RunOutcome.SUCCESS,
            agent_outcome="done", tokens_in=10, tokens_out=20, cost_usd=0.01, model="claude-opus-5",
        )  # fmt: skip
    )
    await session.commit()

    task = (await session.execute(select(m.Task).where(m.Task.id == "T1"))).scalar_one()
    assert task.status is TaskStatus.READY
    assert task.kind is TaskKind.FEATURE
    assert task.attempt_count == 0 and task.max_attempts == 3
    assert task.pr_merged_at is None  # (j)
    assert task.owned_paths == ["src/**"]
    epic = await session.get(m.Epic, "E1")
    assert epic is not None and epic.status is EpicStatus.PENDING
    goal = await session.get(m.Goal, "G1")
    assert goal is not None and goal.status is GoalStatus.DRAFT
    run = await session.get(m.Run, "R1")
    assert run is not None and run.outcome is RunOutcome.SUCCESS and run.task_id == "T1"
    project = await session.get(m.Project, "P1")
    assert project is not None and project.members[0]["role"] == "owner"


# (h) Enum 밖 문자열 → 예외
async def test_task_status_rejects_unknown_string(session: AsyncSession) -> None:
    session.add(_project())
    session.add(m.Goal(id="G1", project_id="P1", title="g", description="d"))
    session.add(m.Epic(id="E1", goal_id="G1", title="e", order=1))
    session.add(
        m.Task(
            id="T1", epic_id="E1", project_id="P1", goal_id="G1", title="t", spec="s",
            kind=TaskKind.FEATURE, role_required=Role.CODING, risk_tier=RiskTier.T0,
            status="flying",  # type: ignore[arg-type]
        )  # fmt: skip
    )
    with pytest.raises((StatementError, LookupError, ValueError)):
        await session.commit()


# (i) events: ts tz-aware UTC, seq insert 순
async def test_event_rows_ts_utc_and_seq_increase(session: AsyncSession) -> None:
    session.add(_project())
    naive = datetime(2026, 9, 13, 3, 4, 5, 678)
    for i in range(3):
        session.add(
            m.Event(
                id=f"01EV{i}", project_id="P1", ts=naive + timedelta(seconds=i),
                actor_type="system", actor_id="api", type="goal.created",
                subject_entity="goal", subject_id="G1", payload={"i": i},
                canonical_json="{}", correlation_id="G1", causation_id=None, signature=None,
            )  # fmt: skip
        )
        await session.flush()
    await session.commit()
    rows = (await session.execute(select(m.Event).order_by(m.Event.seq))).scalars().all()
    assert [r.id for r in rows] == ["01EV0", "01EV1", "01EV2"]
    assert rows[0].seq < rows[1].seq < rows[2].seq
    assert rows[0].ts.tzinfo is not None and rows[0].ts.utcoffset() == timedelta(0)
    assert rows[0].ts == naive.replace(tzinfo=UTC)
    assert rows[0].published_at is None and rows[0].projected_at is None
    assert rows[0].projection_error is None and rows[0].stream_id is None


async def test_event_id_is_unique(session: AsyncSession) -> None:
    session.add(_project())
    for _ in range(2):
        session.add(
            m.Event(
                id="01DUP", project_id="P1", ts=datetime.now(UTC), actor_type="system",
                actor_id="api", type="goal.created", subject_entity="goal", subject_id="G1",
                payload={}, canonical_json="{}", correlation_id="G1", causation_id=None,
            )  # fmt: skip
        )
    with pytest.raises(Exception, match="(?i)unique|integrity"):
        await session.commit()


async def test_tool_call_row(session: AsyncSession) -> None:
    session.add(_project())
    session.add(
        m.ToolCall(
            id="01TC", run_id="R1", project_id="P1", tool="fs.read", args_digest="ab" * 32,
            duration_ms=3, ts=datetime.now(UTC),
        )  # fmt: skip
    )
    await session.commit()
    row = (await session.execute(select(m.ToolCall))).scalar_one()
    assert row.seq >= 1 and row.tool == "fs.read"


async def test_utc_datetime_rejects_nothing_but_normalizes(session: AsyncSession) -> None:
    session.add(_project())
    kst = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC).astimezone(
        datetime.now().astimezone().tzinfo
    )
    session.add(m.Goal(id="G1", project_id="P1", title="g", description="d", created_at=kst))
    await session.commit()
    goal = await session.get(m.Goal, "G1")
    assert goal is not None
    assert goal.created_at == kst and goal.created_at.utcoffset() == timedelta(0)
