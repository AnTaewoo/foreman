"""Projection — 이벤트로 Project/Goal/Epic/Task/Run 상태를 갱신하는 **유일한** 곳 (§3.2, D-08).

- 모든 상태 전이는 ``store.transitions.assert_transition``을 거친다.
- 44개 EventType 전부 ``HANDLERS``에 있다. MVP 1 미사용 타입은 ``noop``.
- 멱등: ``events.projected_at``이 있으면 다시 적용하지 않는다(``force=True``는 replay 재구축용).
- D-30: ``InvalidTransition``/``OrderingError``(선행 이벤트 미도착)는 retry 스트림으로,
  DB/네트워크 예외는 ``ProjectionTransient``(ack 안 함, attempt 미소모),
  ``UnhandledEvent``는 즉시 ``projection_error``.
- D-30(b): ``pr.merged``가 먼저 오면 ``pr_merged_at``만 기록하고, 완료는 ``task.completed``가.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

import structlog
from redis.exceptions import RedisError
from sqlalchemy import func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError, OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import Delivery, EventBus, RetryExhausted
from control_plane.events.chain import row_to_event
from control_plane.events.schema import UNCHAINED, Event, EventType
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
from control_plane.store.transitions import InvalidTransition, assert_transition

log = structlog.get_logger(__name__)

E = EventType


class ProjectionError(Exception):
    """복구 불가 — projection_error에 기록하고 ack."""


class UnhandledEvent(ProjectionError):
    """HANDLERS에 없는 타입."""


class OrderingError(Exception):
    """선행 이벤트가 아직 안 왔다(엔티티 없음). InvalidTransition과 같은 retry 경로."""


class ProjectionTransient(Exception):
    """DB/네트워크 일시 오류 — ack 하지 않고 재전달, attempt 미소모."""


Handler = Callable[[AsyncSession, Event], Awaitable[None]]
HANDLERS: dict[EventType, Handler] = {}
_TRANSIENT = (OperationalError, DBAPIError, RedisError, OSError)
# D-49 (F-3): 제약 위반(FK 등)은 재전달해도 못 고친다 → D-30 재시도 큐(5회 후 포기)
_PERMANENT = (IntegrityError,)


def on(*types: EventType) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        for t in types:
            HANDLERS[t] = fn
        return fn

    return register


async def noop(session: AsyncSession, event: Event) -> None:
    """MVP 1에서 상태를 바꾸지 않는 타입."""
    return None


# --------------------------------------------------------------------------- 조회 헬퍼


async def _require[T](session: AsyncSession, model: type[T], id_: str, event: Event) -> T:
    row = await session.get(model, id_)
    if row is None:
        raise OrderingError(f"{event.type.value}: {model.__name__} {id_} not found yet")
    return row


def _payload_str(event: Event, key: str) -> str | None:
    v = event.payload.get(key)
    return None if v is None else str(v)


# --------------------------------------------------------------------------- project / goal


@on(E.PROJECT_CREATED)
async def _project_created(session: AsyncSession, event: Event) -> None:
    p = event.payload
    row = await session.get(m.Project, event.subject.id)
    if row is None:
        row = m.Project(id=event.subject.id)
        session.add(row)
    row.name = str(p.get("name", ""))
    row.repo_full_name = str(p.get("repo", ""))
    row.default_branch = str(p.get("default_branch", "main"))
    row.members = list(p.get("members", []))  # P5.2 (additive key)
    row.created_at = event.ts


@on(E.GOAL_CREATED)
async def _goal_created(session: AsyncSession, event: Event) -> None:
    p = event.payload
    row = await session.get(m.Goal, event.subject.id)
    if row is None:
        row = m.Goal(id=event.subject.id, project_id=event.project_id)
        session.add(row)
    row.title = str(p.get("title", ""))
    row.description = str(p.get("description", ""))
    row.status = GoalStatus.DRAFT
    row.created_at = event.ts


@on(E.GOAL_PLAN_PROPOSED)
async def _goal_plan_proposed(session: AsyncSession, event: Event) -> None:
    goal = await _require(session, m.Goal, event.subject.id, event)
    # draft(초안) 또는 awaiting(재제출) → planning → awaiting 을 한 번에 (B6)
    assert_transition(goal.status, GoalStatus.PLANNING)
    assert_transition(GoalStatus.PLANNING, GoalStatus.AWAITING_PLAN_APPROVAL)
    goal.status = GoalStatus.AWAITING_PLAN_APPROVAL
    if (n := event.payload.get("plan_discussion_number")) is not None:
        goal.plan_discussion_id = int(n)
    goal.plan_revision = int(event.payload.get("revision", goal.plan_revision + 1))
    if (md := event.payload.get("plan_markdown")) is not None:  # D-53
        goal.plan_markdown = str(md)


def _goal_transition(dst: GoalStatus) -> Handler:
    async def handler(session: AsyncSession, event: Event) -> None:
        goal = await _require(session, m.Goal, event.subject.id, event)
        assert_transition(goal.status, dst)
        goal.status = dst

    return handler


on(E.GOAL_ACTIVATED)(_goal_transition(GoalStatus.ACTIVE))
on(E.GOAL_BLOCKED)(_goal_transition(GoalStatus.BLOCKED))
on(E.GOAL_COMPLETED)(_goal_transition(GoalStatus.DONE))
on(E.GOAL_CANCELLED)(_goal_transition(GoalStatus.CANCELLED))

# --------------------------------------------------------------------------- epic


@on(E.EPIC_CREATED)
async def _epic_created(session: AsyncSession, event: Event) -> None:
    p = event.payload
    row = await session.get(m.Epic, event.subject.id)
    if row is None:
        row = m.Epic(id=event.subject.id, goal_id=str(p.get("goal_id") or event.correlation_id))
        session.add(row)
    row.title = str(p.get("title", ""))
    row.order = int(p.get("order", 0))
    row.milestone_number = p.get("milestone_number")
    row.status = EpicStatus.PENDING


@on(E.EPIC_ACTIVATED)
async def _epic_activated(session: AsyncSession, event: Event) -> None:
    epic = await _require(session, m.Epic, event.subject.id, event)
    assert_transition(epic.status, EpicStatus.ACTIVE)  # active→active 멱등 (B11)
    epic.status = EpicStatus.ACTIVE


@on(E.EPIC_COMPLETED)
async def _epic_completed(session: AsyncSession, event: Event) -> None:
    epic = await _require(session, m.Epic, event.subject.id, event)
    assert_transition(epic.status, EpicStatus.DONE)
    epic.status = EpicStatus.DONE


# --------------------------------------------------------------------------- task


@on(E.TASK_CREATED)
async def _task_created(session: AsyncSession, event: Event) -> None:
    p = event.payload
    epic_id = str(p["epic_id"])
    epic = await session.get(m.Epic, epic_id)
    goal_id = epic.goal_id if epic is not None else event.correlation_id
    row = await session.get(m.Task, event.subject.id)
    if row is None:
        row = m.Task(id=event.subject.id, epic_id=epic_id, project_id=event.project_id)
        session.add(row)
    row.goal_id = goal_id
    row.title = str(p.get("title", ""))
    row.spec = str(p.get("spec", ""))
    row.kind = TaskKind(str(p.get("kind", "feature")))
    row.role_required = Role(str(p.get("role_required", "coding")))
    row.depends_on = list(p.get("depends_on") or [])
    row.owned_paths = list(p.get("owned_paths") or [])
    row.risk_tier = RiskTier(str(p.get("risk_tier", "T1")))
    row.issue_number = p.get("issue_number")
    row.max_attempts = int(p.get("max_attempts", 3))
    row.attempt_count = 0
    row.created_at = event.ts
    # issue_number가 있으면 draft→ready (D-20)
    status = TaskStatus.DRAFT
    if row.issue_number is not None:
        assert_transition(TaskStatus.DRAFT, TaskStatus.READY)
        status = TaskStatus.READY
    row.status = status
    # 역방향 blocks 유지
    for dep_id in row.depends_on:
        dep = await session.get(m.Task, str(dep_id))
        if dep is not None and event.subject.id not in dep.blocks:
            dep.blocks = [*dep.blocks, event.subject.id]


def _task_transition(dst: TaskStatus) -> Handler:
    async def handler(session: AsyncSession, event: Event) -> None:
        task = await _require(session, m.Task, event.subject.id, event)
        assert_transition(task.status, dst)
        task.status = dst

    return handler


@on(E.TASK_ASSIGNED)
async def _task_assigned(session: AsyncSession, event: Event) -> None:
    task = await _require(session, m.Task, event.subject.id, event)
    assert_transition(task.status, TaskStatus.ASSIGNED)
    task.status = TaskStatus.ASSIGNED
    task.assignee_agent_id = _payload_str(event, "agent_id")


on(E.TASK_STARTED)(_task_transition(TaskStatus.RUNNING))
on(E.TASK_BLOCKED)(_task_transition(TaskStatus.BLOCKED))
on(E.TASK_RETRIED)(_task_transition(TaskStatus.READY))
on(E.TASK_CANCELLED)(_task_transition(TaskStatus.CANCELLED))
on(E.TASK_ESCALATED)(noop)


@on(E.TASK_COMPLETED)
async def _task_completed(session: AsyncSession, event: Event) -> None:
    task = await _require(session, m.Task, event.subject.id, event)
    assert_transition(task.status, TaskStatus.IN_REVIEW)
    task.status = TaskStatus.IN_REVIEW
    if (pr := event.payload.get("pr_number")) is not None:
        task.pr_number = int(pr)
    if (branch := event.payload.get("branch")) is not None:  # D-37
        task.branch_name = str(branch)
    if task.pr_merged_at is not None:  # D-30(b): pr.merged가 먼저 왔다
        assert_transition(TaskStatus.IN_REVIEW, TaskStatus.DONE)
        task.status = TaskStatus.DONE


@on(E.TASK_FAILED)
async def _task_failed(session: AsyncSession, event: Event) -> None:
    task = await _require(session, m.Task, event.subject.id, event)
    task.attempt_count = int(event.payload.get("attempt", task.attempt_count + 1))
    dst = TaskStatus.READY if task.attempt_count < task.max_attempts else TaskStatus.BLOCKED
    assert_transition(task.status, dst)  # running 또는 assigned(D-28)에서
    task.status = dst


# --------------------------------------------------------------------------- run


async def _recount_tool_calls(session: AsyncSession, run: m.Run) -> None:
    count = await session.scalar(
        select(func.count()).select_from(m.ToolCall).where(m.ToolCall.run_id == run.id)
    )
    run.tool_call_count = int(count or 0)


@on(E.RUN_STARTED)
async def _run_started(session: AsyncSession, event: Event) -> None:
    p = event.payload
    row = await session.get(m.Run, event.subject.id)
    if row is None:
        row = m.Run(id=event.subject.id, project_id=event.project_id)
        session.add(row)
    row.task_id = str(p.get("task_id", ""))
    row.agent_id = str(p.get("agent_id", ""))
    row.model = _payload_str(event, "model")
    row.started_at = event.ts
    await session.flush()
    await _recount_tool_calls(session, row)  # 먼저 도착한 tool_called 반영


@on(E.RUN_TOOL_CALLED)
async def _run_tool_called(session: AsyncSession, event: Event) -> None:
    """tool_calls 행 수를 다시 센다(멱등). 체인 밖 이벤트라 relay보다 먼저 올 수 있다 —
    Run이 아직 없으면 건너뛴다. run.started / run.finished가 다시 센다 (PC-1에서 발견)."""
    run = await session.get(m.Run, event.subject.id)
    if run is None:
        return
    await _recount_tool_calls(session, run)


@on(E.RUN_TOOL_DENIED)
async def _run_tool_denied(session: AsyncSession, event: Event) -> None:
    run = await _require(session, m.Run, event.subject.id, event)
    run.denied_count += 1


@on(E.RUN_FINISHED)
async def _run_finished(session: AsyncSession, event: Event) -> None:
    p = event.payload
    run = await _require(session, m.Run, event.subject.id, event)
    run.outcome = RunOutcome(str(p["outcome"]))
    run.agent_outcome = _payload_str(event, "agent_outcome")
    run.tokens_in = int(p.get("tokens_in", 0))
    run.tokens_out = int(p.get("tokens_out", 0))
    run.cost_usd = float(p.get("cost_usd", 0.0))
    run.error = _payload_str(event, "error")
    run.ended_at = event.ts
    await _recount_tool_calls(session, run)


on(E.RUN_ARTIFACT_PRODUCED)(noop)

# --------------------------------------------------------------------------- pr


async def _task_for_pr(session: AsyncSession, event: Event) -> m.Task:
    if (task_id := event.payload.get("task_id")) is not None:
        return await _require(session, m.Task, str(task_id), event)
    pr_number = int(event.payload.get("pr_number") or event.subject.id)
    task = await session.scalar(select(m.Task).where(m.Task.pr_number == pr_number))
    if task is None:
        raise OrderingError(f"{event.type.value}: no task with pr_number {pr_number}")
    return task


@on(E.PR_OPENED)
async def _pr_opened(session: AsyncSession, event: Event) -> None:
    task = await _task_for_pr(session, event)
    task.pr_number = int(event.payload.get("pr_number") or event.subject.id)
    if (head := event.payload.get("head")) is not None:
        task.branch_name = str(head)


@on(E.PR_MERGED)
async def _pr_merged(session: AsyncSession, event: Event) -> None:
    task = await _task_for_pr(session, event)
    task.pr_merged_at = event.ts
    if task.status is TaskStatus.IN_REVIEW:
        assert_transition(task.status, TaskStatus.DONE)
        task.status = TaskStatus.DONE
    # 아니면 플래그만 — task.completed가 done까지 옮긴다 (D-30 b)


on(E.PR_CLOSED, E.PR_CHECKS_PASSED, E.PR_CHECKS_FAILED, E.PR_REVIEW_SUBMITTED)(noop)

# --------------------------------------------------------------------------- MVP 1 미사용

on(
    E.DECISION_OPENED, E.DECISION_AGENT_VOTED, E.DECISION_HUMAN_RESPONDED, E.DECISION_RESOLVED,
    E.DECISION_EXPIRED, E.POLICY_UPDATED, E.POLICY_TIER_OVERRIDDEN, E.BUDGET_WARNING,
    E.BUDGET_EXCEEDED, E.PROJECT_UPDATED, E.PROJECT_PAUSED, E.PROJECT_RESUMED, E.AGENT_KILLED,
    E.CONTROL_EMERGENCY_STOP,
)(noop)  # fmt: skip


# --------------------------------------------------------------------------- Projection


class Projection:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus | None = None,
        *,
        max_attempts: int = 5,
    ) -> None:
        self._factory = session_factory
        self._bus = bus
        self._max_attempts = max_attempts

    # -- 부기 (D-24: projection.py는 자유롭게 갱신)
    async def _already_projected(self, session: AsyncSession, event: Event) -> bool:
        if event.type in UNCHAINED:
            return False  # tool_calls는 재계산이라 멱등
        projected = await session.scalar(select(m.Event.projected_at).where(m.Event.id == event.id))
        return projected is not None

    async def _mark_projected(self, session: AsyncSession, event: Event) -> None:
        if event.type in UNCHAINED:
            return
        await session.execute(
            update(m.Event)
            .where(m.Event.id == event.id)
            .values(projected_at=datetime.now(UTC), projection_error=None)
        )

    async def _record_error(self, event: Event, message: str) -> None:
        async with self._factory() as session:
            await session.execute(
                update(m.Event).where(m.Event.id == event.id).values(projection_error=message)
            )
            await session.commit()
        log.error("projection.error", event_id=event.id, type=event.type.value, error=message)

    # -- 적용
    async def apply(self, event: Event, *, force: bool = False) -> bool:
        """이벤트 하나를 자기 트랜잭션에서 적용. 이미 적용됐으면 False. 예외는 그대로 전파."""
        handler = HANDLERS.get(event.type)
        if handler is None:
            raise UnhandledEvent(f"unhandled event type {event.type.value}")
        async with self._factory() as session:
            if not force and await self._already_projected(session, event):
                return False
            await handler(session, event)
            await self._mark_projected(session, event)
            await session.commit()
            return True

    async def apply_or_retry(self, event: Event, attempt: int = 1) -> bool:
        """``apply`` + D-30 분기 — 순서 역전은 예외가 아니라 retry 스트림으로 (ingest용, PC-6).

        반환: 적용 True / 재시도 큐·이미 적용 False. DB·네트워크 예외는 그대로 전파.
        """
        try:
            return await self.apply(event)
        except (InvalidTransition, OrderingError) as exc:
            await self._schedule_or_give_up(event, attempt, str(exc))
            return False
        except _PERMANENT as exc:
            await self._schedule_or_give_up(event, attempt, f"integrity: {exc.orig}")
            return False
        except UnhandledEvent as exc:
            await self._record_error(event, str(exc))
            return False

    async def handle(self, delivery: Delivery) -> None:
        """consumer group 핸들러 (D-30 분기). 정상 반환 = ack.

        워커가 직접 XADD한 이벤트(서명 없음, seq 없음)는 여기서 건너뛴다 — Scheduler.ingest가
        append_signed로 DB에 넣은 뒤 apply 한다 (D-26). 두 번 적용되면 불허 전이가 난다.
        """
        event = delivery.event
        if event.signature is None and delivery.seq is None:
            log.debug("projection.skip_unsigned", event_id=event.id, type=event.type.value)
            return
        try:
            await self.apply(event)
        except (InvalidTransition, OrderingError) as exc:
            await self._schedule_or_give_up(event, delivery.attempt, str(exc))
        except UnhandledEvent as exc:
            await self._record_error(event, str(exc))
        except _PERMANENT as exc:  # D-49: 순서 역전으로 보고 유한 재시도
            await self._schedule_or_give_up(event, delivery.attempt, f"integrity: {exc.orig}")
        except _TRANSIENT as exc:
            raise ProjectionTransient(f"{event.id}: {exc}") from exc

    async def _schedule_or_give_up(self, event: Event, attempt: int, reason: str) -> None:
        if self._bus is None or attempt > self._max_attempts:
            await self._record_error(event, f"gave up after {attempt - 1} retries: {reason}")
            return
        try:
            await self._bus.schedule_retry(event.project_id, event.id, attempt)
        except RetryExhausted:
            await self._record_error(event, f"gave up after {attempt - 1} retries: {reason}")
            return
        log.info("projection.retry_scheduled", event_id=event.id, attempt=attempt, reason=reason)

    async def apply_retries(self, project_id: str, *, now: datetime | None = None) -> int:
        """retry 스트림에서 기한이 지난 이벤트를 다시 적용. 적용 성공 건수 반환."""
        if self._bus is None:
            return 0
        applied = 0
        for item in await self._bus.due_retries(project_id, now=now):
            async with self._factory() as session:
                row = await session.scalar(select(m.Event).where(m.Event.id == item.event_id))
            if row is None:
                log.warning("projection.retry_event_missing", event_id=item.event_id)
                continue
            event = row_to_event(row)
            try:
                if await self.apply(event):
                    applied += 1
            except (InvalidTransition, OrderingError) as exc:
                await self._schedule_or_give_up(event, item.attempt + 1, str(exc))
            except UnhandledEvent as exc:
                await self._record_error(event, str(exc))
        return applied


__all__: list[str] = [
    "HANDLERS", "Handler", "OrderingError", "Projection", "ProjectionError",
    "ProjectionTransient", "UnhandledEvent", "noop", "on",
]  # fmt: skip
