"""P1.5 — events/projection.py (red a~i). 진짜 Redis + aiosqlite."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events import projection as proj_mod
from control_plane.events.bus import Delivery, EventBus
from control_plane.events.projection import (
    HANDLERS,
    OrderingError,
    Projection,
    ProjectionTransient,
    UnhandledEvent,
    noop,
)
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.enums import EpicStatus, GoalStatus, RunOutcome, TaskStatus
from control_plane.store.transitions import InvalidTransition

E = EventType


def ev(
    type_: EventType,
    subject: tuple[str, str],
    payload: dict[str, Any],
    *,
    project_id: str = "P1",
    correlation_id: str = "G1",
    causation_id: str | None = None,
    ts: datetime | None = None,
) -> Event:
    return Event(
        project_id=project_id,
        ts=ts or datetime.now(UTC),
        actor=Actor(type="system", id="test"),
        type=type_,
        subject=Subject(entity=subject[0], id=subject[1]),  # type: ignore[arg-type]
        payload=payload,
        correlation_id=correlation_id,
        causation_id=causation_id,
    )


def task_created(task_id: str, issue: int | None = 10, deps: list[str] | None = None) -> Event:
    return ev(
        E.TASK_CREATED,
        ("task", task_id),
        {
            "epic_id": "E1",
            "epic_title": "epic",
            "title": f"task {task_id}",
            "spec": "s",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": deps or [],
            "owned_paths": ["src/**"],
            "risk_tier": "T1",
            "issue_number": issue,
            "issue_url": None if issue is None else f"https://x/issues/{issue}",
        },
    )


def run_finished(outcome: str = "success", agent_outcome: str = "done") -> Event:
    return ev(
        E.RUN_FINISHED,
        ("run", "R1"),
        {
            "outcome": outcome,
            "agent_outcome": agent_outcome,
            "tokens_in": 100,
            "tokens_out": 50,
            "cost_usd": 0.02,
            "duration_s": 12.5,
            "error": None,
        },
    )


# (a) 전체 시퀀스
SEQUENCE: list[Event] = [
    ev(
        E.PROJECT_CREATED,
        ("project", "P1"),
        {"name": "demo", "repo": "org/demo", "default_branch": "main"},
        correlation_id="P1",
    ),
    ev(E.GOAL_CREATED, ("goal", "G1"), {"title": "goal", "description": "d"}),
    ev(E.GOAL_PLAN_PROPOSED, ("goal", "G1"), {"plan_discussion_number": 5, "revision": 1}),
    ev(E.GOAL_ACTIVATED, ("goal", "G1"), {}),
    ev(
        E.EPIC_CREATED,
        ("epic", "E1"),
        {"goal_id": "G1", "title": "epic", "order": 1, "milestone_number": 3},
    ),
    task_created("T1"),
    ev(E.TASK_ASSIGNED, ("task", "T1"), {"agent_id": "A1", "run_id": "R1"}),
    ev(E.EPIC_ACTIVATED, ("epic", "E1"), {}),
    ev(E.TASK_STARTED, ("task", "T1"), {"run_id": "R1"}),
    ev(E.RUN_STARTED, ("run", "R1"), {"task_id": "T1", "agent_id": "A1", "model": "claude-opus-5"}),
    ev(E.RUN_TOOL_CALLED, ("run", "R1"), {"tool": "fs.read", "args_digest": "ab" * 32}),
    ev(
        E.PR_OPENED,
        ("pr", "42"),
        {
            "task_id": "T1",
            "run_id": "R1",
            "pr_number": 42,
            "head": "ai/epic/10-task",
            "base": "main",
        },
    ),
    ev(E.TASK_COMPLETED, ("task", "T1"), {"run_id": "R1", "pr_number": 42}),
    run_finished(),
    ev(E.PR_MERGED, ("pr", "42"), {"task_id": "T1", "pr_number": 42}),
]


@pytest.fixture
def bus(redis: Redis) -> EventBus:
    return EventBus(redis)


@pytest.fixture
def projection(factory: async_sessionmaker[AsyncSession], bus: EventBus) -> Projection:
    return Projection(factory, bus, max_attempts=5)


async def publish_all(
    bus: EventBus, factory: async_sessionmaker[AsyncSession], events: list[Event]
) -> list[Event]:
    out: list[Event] = []
    async with factory() as s:
        for e in events:
            out.append(await bus.publish(s, e))
        await s.commit()
    return out


async def _task(factory: async_sessionmaker[AsyncSession], task_id: str = "T1") -> m.Task:
    async with factory() as s:
        t = await s.get(m.Task, task_id)
        assert t is not None
        return t


async def test_full_sequence(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE)
    expected_task = [
        None,
        None,
        None,
        None,
        None,
        TaskStatus.READY,
        TaskStatus.ASSIGNED,
        TaskStatus.ASSIGNED,
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
        TaskStatus.RUNNING,
        TaskStatus.IN_REVIEW,
        TaskStatus.IN_REVIEW,
        TaskStatus.DONE,
    ]
    for e, exp in zip(events, expected_task, strict=True):
        assert await projection.apply(e) is True
        if exp is not None:
            assert (await _task(factory)).status is exp, e.type

    async with factory() as s:
        goal = await s.get(m.Goal, "G1")
        assert goal is not None and goal.status is GoalStatus.ACTIVE
        assert goal.plan_discussion_id == 5 and goal.plan_revision == 1
        epic = await s.get(m.Epic, "E1")
        assert epic is not None and epic.status is EpicStatus.ACTIVE and epic.milestone_number == 3
        task = await s.get(m.Task, "T1")
        assert task is not None
        assert task.issue_number == 10 and task.goal_id == "G1" and task.project_id == "P1"
        assert task.pr_number == 42 and task.branch_name == "ai/epic/10-task"
        assert task.assignee_agent_id == "A1" and task.pr_merged_at is not None
        run = await s.get(m.Run, "R1")
        assert run is not None and run.outcome is RunOutcome.SUCCESS
        assert run.agent_outcome == "done" and run.tokens_in == 100 and run.tokens_out == 50
        assert run.cost_usd == 0.02 and run.ended_at is not None and run.tool_call_count == 1
        project = await s.get(m.Project, "P1")
        assert project is not None and project.repo_full_name == "org/demo"
        rows = (await s.execute(select(m.Event))).scalars().all()
        assert len(rows) == 14 and all(r.projected_at is not None for r in rows)


# (b) 같은 id 두 번 → 동일, 두 번째는 False
async def test_apply_is_idempotent(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:7])
    for e in events:
        await projection.apply(e)
    assert await projection.apply(events[-1]) is False  # 이미 projected
    assert (await _task(factory)).status is TaskStatus.ASSIGNED


# (c) 순서 역전 → InvalidTransition; handle → retry; 소진 → projection_error; DB 예외 → Transient
async def test_out_of_order_and_retry(
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    projection: Projection,
    redis: Redis,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:7])  # ... task.assigned
    for e in events:
        await projection.apply(e)
    completed = (await publish_all(bus, factory, [SEQUENCE[12]]))[0]  # task.completed
    with pytest.raises(InvalidTransition):
        await projection.apply(completed)

    scheduled: list[tuple[str, int]] = []
    orig = bus.schedule_retry

    async def spy(project_id: str, event_id: str, attempt: int, **kw: Any) -> str:
        scheduled.append((event_id, attempt))
        return await orig(project_id, event_id, attempt, **kw)

    monkeypatch.setattr(bus, "schedule_retry", spy)
    for attempt in range(1, 6):
        await projection.handle(
            Delivery(
                event=completed,
                message_id="1-0",
                attempt=attempt,
                group="g",
                consumer="c",
                seq=None,
            )
        )
    assert scheduled == [(completed.id, a) for a in range(1, 6)]
    assert await redis.xlen("events:P1:retry") == 5
    # 6회째: 소진 → 정상 반환 + projection_error
    await projection.handle(
        Delivery(event=completed, message_id="1-0", attempt=6, group="g", consumer="c", seq=None)
    )
    async with factory() as s:
        row = (await s.execute(select(m.Event).where(m.Event.id == completed.id))).scalar_one()
        assert row.projection_error and "in_review" in row.projection_error
        assert row.projected_at is None

    # DB 예외 → ProjectionTransient, schedule_retry 안 부름
    scheduled.clear()

    async def boom(self: Projection, event: Event, *, force: bool = False) -> bool:
        raise OperationalError("select 1", {}, Exception("db down"))

    monkeypatch.setattr(proj_mod.Projection, "apply", boom)
    with pytest.raises(ProjectionTransient):
        await projection.handle(
            Delivery(
                event=completed, message_id="1-0", attempt=1, group="g", consumer="c", seq=None
            )
        )
    assert scheduled == []


async def test_apply_retries_reapplies_when_predecessor_arrived(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:7])
    for e in events:
        await projection.apply(e)
    started, completed = await publish_all(bus, factory, [SEQUENCE[8], SEQUENCE[12]])
    await projection.handle(
        Delivery(event=completed, message_id="1-0", attempt=1, group="g", consumer="c", seq=None)
    )
    assert (await _task(factory)).status is TaskStatus.ASSIGNED
    await projection.apply(started)
    now = datetime.now(UTC).timestamp() + 10
    n = await projection.apply_retries("P1", now=datetime.fromtimestamp(now, tz=UTC))
    assert n == 1
    assert (await _task(factory)).status is TaskStatus.IN_REVIEW


# (d) pr.merged 선착 → pr_merged_at만, 이후 task.completed → done
async def test_pr_merged_before_completed(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:9])  # ... task.started (running)
    for e in events:
        await projection.apply(e)
    merged, completed = await publish_all(bus, factory, [SEQUENCE[14], SEQUENCE[12]])
    assert await projection.apply(merged) is True
    t = await _task(factory)
    assert t.status is TaskStatus.RUNNING and t.pr_merged_at is not None
    await projection.apply(completed)
    assert (await _task(factory)).status is TaskStatus.DONE


# (e) task.failed from assigned (launch_failed) → ready / attempt≥max → blocked
async def test_task_failed_from_assigned(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:7])
    for e in events:
        await projection.apply(e)
    (f1,) = await publish_all(
        bus,
        factory,
        [
            ev(
                E.TASK_FAILED,
                ("task", "T1"),
                {"run_id": "R1", "reason": "launch_failed", "attempt": 1},
            )
        ],
    )
    await projection.apply(f1)
    t = await _task(factory)
    assert t.status is TaskStatus.READY and t.attempt_count == 1
    a2, f3 = await publish_all(
        bus,
        factory,
        [
            ev(E.TASK_ASSIGNED, ("task", "T1"), {"agent_id": "A1", "run_id": "R2"}),
            ev(
                E.TASK_FAILED,
                ("task", "T1"),
                {"run_id": "R2", "reason": "launch_failed", "attempt": 3},
            ),
        ],
    )
    await projection.apply(a2)
    await projection.apply(f3)
    t = await _task(factory)
    assert t.status is TaskStatus.BLOCKED and t.attempt_count == 3


# (f) retried / cancelled / epic 멱등·완료 / tool_denied
async def test_retried_cancelled_epic_denied(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:10])  # ... run.started
    for e in events:
        await projection.apply(e)
    more = await publish_all(
        bus,
        factory,
        [
            ev(E.TASK_BLOCKED, ("task", "T1"), {"reason": "needs_decision"}),
            ev(E.TASK_RETRIED, ("task", "T1"), {"reason": "approved", "by": "u1"}),
            ev(E.EPIC_ACTIVATED, ("epic", "E1"), {}),  # 두 번째 → active 유지
            ev(
                E.RUN_TOOL_DENIED,
                ("run", "R1"),
                {"tool": "fs.read", "reason": "secret", "args_digest": "cd" * 32},
            ),
            ev(
                E.RUN_TOOL_DENIED,
                ("run", "R1"),
                {"tool": "shell", "reason": "not_allowed", "args_digest": "ef" * 32},
            ),
            ev(
                E.TASK_CANCELLED,
                ("task", "T1"),
                {"reason": "goal cancelled", "by": "u1", "cascade_from": "G1"},
            ),
            ev(E.EPIC_COMPLETED, ("epic", "E1"), {}),
        ],
    )
    await projection.apply(more[0])
    assert (await _task(factory)).status is TaskStatus.BLOCKED
    await projection.apply(more[1])
    assert (await _task(factory)).status is TaskStatus.READY
    await projection.apply(more[2])
    async with factory() as s:
        epic = await s.get(m.Epic, "E1")
        assert epic is not None and epic.status is EpicStatus.ACTIVE
    await projection.apply(more[3])
    await projection.apply(more[4])
    async with factory() as s:
        run = await s.get(m.Run, "R1")
        assert run is not None and run.denied_count == 2
    await projection.apply(more[5])
    assert (await _task(factory)).status is TaskStatus.CANCELLED
    await projection.apply(more[6])
    async with factory() as s:
        epic = await s.get(m.Epic, "E1")
        assert epic is not None and epic.status is EpicStatus.DONE


# (g) plan_proposed 재제출
async def test_plan_proposed_revision(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:3])
    for e in events:
        await projection.apply(e)
    (rev2,) = await publish_all(
        bus,
        factory,
        [ev(E.GOAL_PLAN_PROPOSED, ("goal", "G1"), {"plan_discussion_number": 5, "revision": 2})],
    )
    await projection.apply(rev2)
    async with factory() as s:
        goal = await s.get(m.Goal, "G1")
        assert goal is not None
        assert goal.status is GoalStatus.AWAITING_PLAN_APPROVAL and goal.plan_revision == 2


# P9.1 (D-53): plan_markdown 키가 있으면 goals.plan_markdown에 저장, 없으면 None(구 이벤트 호환)
async def test_plan_proposed_stores_markdown(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:3])
    for e in events:
        await projection.apply(e)
    async with factory() as s:
        goal = await s.get(m.Goal, "G1")
        assert goal is not None and goal.plan_markdown is None
    (rev2,) = await publish_all(
        bus,
        factory,
        [
            ev(
                E.GOAL_PLAN_PROPOSED,
                ("goal", "G1"),
                {"plan_discussion_number": 5, "revision": 2, "plan_markdown": "# Plan\n- a"},
            )
        ],
    )
    await projection.apply(rev2)
    async with factory() as s:
        goal = await s.get(m.Goal, "G1")
        assert goal is not None and goal.plan_markdown == "# Plan\n- a"


# P9 (D-54): 프로젝트 "삭제" = project.updated{archived: true} → projects.archived_at (행·이벤트는 남는다)
async def test_project_archived_via_project_updated(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:1])
    await projection.apply(events[0])
    async with factory() as s:
        p = await s.get(m.Project, "P1")
        assert p is not None and p.archived_at is None
    (upd,) = await publish_all(
        bus, factory, [ev(E.PROJECT_UPDATED, ("project", "P1"), {"archived": True, "by": "alice"})]
    )
    await projection.apply(upd)
    async with factory() as s:
        p = await s.get(m.Project, "P1")
        assert p is not None and p.archived_at is not None
    (undo,) = await publish_all(
        bus, factory, [ev(E.PROJECT_UPDATED, ("project", "P1"), {"archived": False, "by": "alice"})]
    )
    await projection.apply(undo)
    async with factory() as s:
        p = await s.get(m.Project, "P1")
        assert p is not None and p.archived_at is None


# (h)(i) 핸들러 등록
def test_all_event_types_have_handlers_and_unused_are_noop() -> None:
    assert set(HANDLERS) == set(EventType)
    for t in (
        E.PR_CLOSED,
        E.PR_CHECKS_PASSED,
        E.PR_CHECKS_FAILED,
        E.PR_REVIEW_SUBMITTED,
        E.DECISION_OPENED,
        E.DECISION_AGENT_VOTED,
        E.DECISION_HUMAN_RESPONDED,
        E.DECISION_RESOLVED,
        E.DECISION_EXPIRED,
        E.POLICY_UPDATED,
        E.POLICY_TIER_OVERRIDDEN,
        E.BUDGET_WARNING,
        E.BUDGET_EXCEEDED,
        E.PROJECT_UPDATED,
        E.PROJECT_PAUSED,
        E.PROJECT_RESUMED,
        E.AGENT_KILLED,
        E.CONTROL_EMERGENCY_STOP,
        E.TASK_ESCALATED,
        E.RUN_ARTIFACT_PRODUCED,
    ):
        assert HANDLERS[t] is noop, t
        assert HANDLERS[t].__name__ == "noop"


async def test_unhandled_event_records_error(
    factory: async_sessionmaker[AsyncSession],
    bus: EventBus,
    projection: Projection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (e,) = await publish_all(bus, factory, [SEQUENCE[0]])
    monkeypatch.delitem(HANDLERS, E.PROJECT_CREATED)
    with pytest.raises(UnhandledEvent):
        await projection.apply(e)
    await projection.handle(
        Delivery(event=e, message_id="1-0", attempt=1, group="g", consumer="c", seq=None)
    )
    async with factory() as s:
        row = (await s.execute(select(m.Event).where(m.Event.id == e.id))).scalar_one()
        assert row.projection_error and "unhandled" in row.projection_error.lower()


async def test_missing_entity_is_ordering_error(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    (e,) = await publish_all(bus, factory, [SEQUENCE[6]])  # task.assigned, Task 없음
    with pytest.raises(OrderingError):
        await projection.apply(e)


# replay 재구축: TRUNCATE 후 apply(force=True)
async def test_force_replay_rebuilds(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE)
    for e in events:
        await projection.apply(e)
    async with factory() as s:
        from sqlalchemy import delete

        for model in (m.Run, m.Task, m.Epic, m.Goal, m.Project):
            await s.execute(delete(model))
        await s.commit()
    async with factory() as s:
        replayed = await bus.replay(s, "P1")
    assert [e.id for e in replayed] == [e.id for e in events if e.type is not E.RUN_TOOL_CALLED]
    for e in replayed:
        assert await projection.apply(e, force=True) is True
    assert (await _task(factory)).status is TaskStatus.DONE


# PC-1에서 발견: tool_called는 relay보다 먼저 스트림에 도착한다(D-31 직접 XADD).
# Run이 아직 없어도 오류·retry 없이 통과하고, run.started/run.finished가 tool_calls를 재계산한다.
async def test_tool_called_before_run_started_is_tolerated(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection, redis: Redis
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:9])  # ... task.started
    for e in events:
        await projection.apply(e)
    tool1, tool2, started = await publish_all(
        bus,
        factory,
        [
            ev(E.RUN_TOOL_CALLED, ("run", "R1"), {"tool": "fs.read", "args_digest": "ab" * 32}),
            ev(E.RUN_TOOL_CALLED, ("run", "R1"), {"tool": "shell", "args_digest": "cd" * 32}),
            SEQUENCE[9],  # run.started
        ],
    )
    for tool in (tool1, tool2):
        await projection.handle(
            Delivery(event=tool, message_id="1-0", attempt=1, group="g", consumer="c", seq=None)
        )
    assert await redis.xlen("events:P1:retry") == 0  # 체인 밖 이벤트는 retry 대상이 아니다
    assert await projection.apply(started) is True
    async with factory() as s:
        run = await s.get(m.Run, "R1")
        assert run is not None and run.tool_call_count == 2
    (fin,) = await publish_all(bus, factory, [run_finished()])
    await projection.apply(fin)
    async with factory() as s:
        run = await s.get(m.Run, "R1")
        assert run is not None and run.tool_call_count == 2


# D-26: relay를 거치지 않은 미서명 이벤트(워커 XADD)는 projection consumer가 건너뛴다
async def test_handle_skips_unsigned_events_without_seq(
    factory: async_sessionmaker[AsyncSession], bus: EventBus, projection: Projection
) -> None:
    events = await publish_all(bus, factory, SEQUENCE[:7])
    for e in events:
        await projection.apply(e)
    unsigned = SEQUENCE[8]  # task.started, signature None, DB에 없음
    await projection.handle(
        Delivery(event=unsigned, message_id="9-0", attempt=1, group="g", consumer="c", seq=None)
    )
    assert (await _task(factory)).status is TaskStatus.ASSIGNED  # 적용 안 됨


# P6.7 (D-37): task.completed.branch → tasks.branch_name (pr.opened 전에 브랜치를 안다)
async def test_task_completed_sets_branch_name(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from tests.runtime.conftest import bootstrap, ev, task_created

    bus = EventBus(redis)
    projection = Projection(factory, bus)
    events = [*bootstrap("P1", "G1", "/r"), task_created("P1", "G1", "T1", ["a/**"], 1)]
    events += [
        ev(
            "P1",
            EventType.TASK_ASSIGNED,
            "task",
            "T1",
            {"agent_id": "a", "run_id": "R1"},
            correlation_id="G1",
        ),
        ev("P1", EventType.TASK_STARTED, "task", "T1", {"run_id": "R1"}, correlation_id="G1"),
        ev(
            "P1",
            EventType.TASK_COMPLETED,
            "task",
            "T1",
            {"run_id": "R1", "branch": "ai/e/1-t", "summary": "s"},
            correlation_id="G1",
        ),
    ]
    async with factory() as s:
        for e in events:
            await bus.publish(s, e)
        await s.commit()
    from control_plane.events.outbox import OutboxRelay

    while await OutboxRelay(factory, redis).relay_once() > 0:
        pass
    await bus.poll_once("t", projection.handle, consumer="t", project_id="P1")
    async with factory() as s:
        task = await s.get(m.Task, "T1")
    assert task is not None and task.status is TaskStatus.IN_REVIEW
    assert task.branch_name == "ai/e/1-t"


# P8.1 (D-49, F-3): IntegrityError(FK 등)는 transient가 아니다 → D-30 재시도 큐(ack), 5회 뒤 포기
async def test_integrity_error_goes_to_retry_not_transient(
    factory: async_sessionmaker[AsyncSession], redis: Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sqlalchemy.exc import IntegrityError

    from control_plane.events.bus import retry_stream_key

    bus = EventBus(redis)
    projection = Projection(factory, bus)
    ev_ = Event(
        project_id="PX", actor=Actor(type="system", id="t"), type=EventType.PROJECT_CREATED,
        subject=Subject(entity="project", id="PX"),
        payload={"name": "x", "repo": "r", "default_branch": "main"},
        correlation_id="PX", causation_id=None,
    )  # fmt: skip
    async with factory() as s:
        signed = await bus.publish(s, ev_)
        await s.commit()

    async def boom(session: AsyncSession, event: Event) -> None:
        raise IntegrityError("insert", {}, Exception("FOREIGN KEY constraint failed"))

    monkeypatch.setitem(proj_mod.HANDLERS, EventType.PROJECT_CREATED, boom)
    delivery = Delivery(event=signed, message_id="1-0", attempt=1, group="g", consumer="c", seq=1)
    await projection.handle(delivery)  # ProjectionTransient가 아니라 정상 반환(ack)
    assert await redis.xlen(retry_stream_key("PX")) == 1
    # 6번째 시도(attempt > max)면 포기 → projection_error 기록
    await projection.handle(
        Delivery(event=signed, message_id="1-1", attempt=6, group="g", consumer="c", seq=1)
    )
    async with factory() as s:  # events PK는 seq — id로 조회
        row = await s.scalar(select(m.Event).where(m.Event.id == signed.id))
    assert row is not None and row.projection_error and "gave up" in row.projection_error
