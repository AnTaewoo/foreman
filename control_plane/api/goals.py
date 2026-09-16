"""Goal API (설계 §13): 생성(202, goal.created 루트) · 진행률 · 취소(goal/task.cancelled cascade).

P5.2가 `AppState.on_goal_created` 훅으로 Orchestrator 실행을 붙인다.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from ulid import ULID

from control_plane.api.demo_guard import AdminDep, goal_quota
from control_plane.api.deps import StateDep, UserDep, find_project, gh_url, human, publish
from control_plane.events.schema import Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus

router = APIRouter(prefix="/projects/{project_id}/goals", tags=["goals"])

FINISHED = (TaskStatus.DONE, TaskStatus.CANCELLED)


class GoalIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""


class GoalAccepted(BaseModel):
    id: str
    project_id: str
    status: str = "draft"


class EpicOut(BaseModel):
    id: str
    title: str
    order: int
    status: str
    milestone_number: int | None


class GoalProgress(BaseModel):
    id: str
    project_id: str
    title: str
    status: str
    plan_revision: int
    plan_discussion_number: int | None
    plan_discussion_url: str | None = None  # P9.1 (owner/name repo일 때)
    plan_markdown: str | None = None  # D-53
    tasks: dict[str, int]  # status → count
    total: int
    done: int
    epics: list[EpicOut]


class GoalSummary(BaseModel):
    """`GET /projects/{id}/goals` 항목 (P9.1 데모 콘솔용) — created_at desc."""

    id: str
    title: str
    status: str
    plan_revision: int
    plan_discussion_number: int | None
    created_at: datetime
    total: int
    done: int


class GoalList(BaseModel):
    items: list[GoalSummary]


class CancelIn(BaseModel):
    reason: str = ""


class CancelOut(BaseModel):
    id: str
    cancelled_tasks: list[str]


async def _goal(state: StateDep, project_id: str, goal_id: str) -> m.Goal:
    async with state.factory() as s:
        row = await s.get(m.Goal, goal_id)
    if row is None or row.project_id != project_id:
        raise HTTPException(404, "goal not found")
    return row


@router.post("", status_code=202)
async def create_goal(
    project_id: str, body: GoalIn, state: StateDep, user: UserDep
) -> GoalAccepted:
    project = await find_project(state, project_id)  # D-46: projection 전이면 events
    if project is None:
        raise HTTPException(404, "project not found")
    if project.archived_at is not None:  # D-54
        raise HTTPException(409, "project is archived")
    await goal_quota(state, project_id)  # 데모 모드 한도 (P9.3)
    gid = str(ULID())
    await publish(
        state,
        Event(
            project_id=project_id,
            actor=human(user),
            type=EventType.GOAL_CREATED,
            subject=Subject(entity="goal", id=gid),
            payload={"title": body.title, "description": body.description},
            correlation_id=gid,
            causation_id=None,  # 루트 (D-25)
        ),
    )
    if state.on_goal_created is not None:
        await state.on_goal_created(project_id, gid)
    return GoalAccepted(id=gid, project_id=project_id)


@router.get("")
async def list_goals(project_id: str, state: StateDep) -> GoalList:
    async with state.factory() as s:
        goals = (
            (
                await s.execute(
                    select(m.Goal)
                    .where(m.Goal.project_id == project_id)
                    .order_by(m.Goal.created_at.desc(), m.Goal.id.desc())
                )
            )
            .scalars()
            .all()
        )
        counts = (
            await s.execute(
                select(m.Task.goal_id, m.Task.status, func.count())
                .where(m.Task.project_id == project_id)
                .group_by(m.Task.goal_id, m.Task.status)
            )
        ).all()
    total: dict[str, int] = {}
    done: dict[str, int] = {}
    for gid, status, n in counts:
        total[gid] = total.get(gid, 0) + int(n)
        if status is TaskStatus.DONE:
            done[gid] = done.get(gid, 0) + int(n)
    return GoalList(
        items=[
            GoalSummary(
                id=g.id,
                title=g.title,
                status=g.status.value,
                plan_revision=g.plan_revision,
                plan_discussion_number=g.plan_discussion_id,
                created_at=g.created_at,
                total=total.get(g.id, 0),
                done=done.get(g.id, 0),
            )
            for g in goals
        ]
    )


@router.get("/{goal_id}")
async def get_goal(project_id: str, goal_id: str, state: StateDep) -> GoalProgress:
    goal = await _goal(state, project_id, goal_id)
    project = await find_project(state, project_id)
    repo = project.repo if project is not None else ""
    async with state.factory() as s:
        counts = (
            await s.execute(
                select(m.Task.status, func.count())
                .where(m.Task.goal_id == goal_id)
                .group_by(m.Task.status)
            )
        ).all()
        epics = (
            (
                await s.execute(
                    select(m.Epic).where(m.Epic.goal_id == goal_id).order_by(m.Epic.order)
                )
            )
            .scalars()
            .all()
        )
    tasks = {str(status.value): int(n) for status, n in counts}
    return GoalProgress(
        id=goal.id,
        project_id=goal.project_id,
        title=goal.title,
        status=goal.status.value,
        plan_revision=goal.plan_revision,
        plan_discussion_number=goal.plan_discussion_id,
        plan_discussion_url=gh_url(repo, "discussions", goal.plan_discussion_id) if repo else None,
        plan_markdown=goal.plan_markdown,
        tasks=tasks,
        total=sum(tasks.values()),
        done=tasks.get(TaskStatus.DONE.value, 0),
        epics=[
            EpicOut(
                id=e.id,
                title=e.title,
                order=e.order,
                status=e.status.value,
                milestone_number=e.milestone_number,
            )
            for e in epics
        ],
    )


class DecisionIn(BaseModel):
    reason: str = ""


class DecisionOut(BaseModel):
    id: str
    decision: str
    by: str


async def _decide(
    project_id: str, goal_id: str, state: StateDep, user: str, *, approved: bool, reason: str
) -> DecisionOut:
    """D-51: Plan 승인/거절을 API로 (웹훅·터널 없이). 권한은 project.members의 owner|approver."""
    project = await find_project(state, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    role = next(
        (str(mem.get("role")) for mem in project.members if mem.get("user_id") == user), None
    )
    if role not in ("owner", "approver"):
        raise HTTPException(403, f"{user} is not an owner/approver of this project")
    runner = state.runner
    if runner is None or not runner.is_waiting(goal_id):
        raise HTTPException(409, "goal is not awaiting plan approval")
    await runner.resume(goal_id, approved=approved, by=user, reason=reason)
    return DecisionOut(id=goal_id, decision="approve" if approved else "reject", by=user)


@router.post("/{goal_id}/approve", status_code=202)
async def approve_goal(
    project_id: str, goal_id: str, state: StateDep, user: UserDep
) -> DecisionOut:
    return await _decide(project_id, goal_id, state, user, approved=True, reason="")


@router.post("/{goal_id}/reject", status_code=202)
async def reject_goal(
    project_id: str, goal_id: str, body: DecisionIn, state: StateDep, user: UserDep
) -> DecisionOut:
    return await _decide(project_id, goal_id, state, user, approved=False, reason=body.reason)


async def cancel_goal_cascade(
    state: StateDep, project_id: str, goal_id: str, user: str, reason: str
) -> list[str]:
    """goal.cancelled + 미완 Task마다 task.cancelled (B9). 취소된 Task id. DELETE /projects도 씀."""
    async with state.factory() as s:
        pending = (
            (
                await s.execute(
                    select(m.Task.id)
                    .where(m.Task.goal_id == goal_id, m.Task.status.not_in(FINISHED))
                    .order_by(m.Task.created_at, m.Task.id)
                )
            )
            .scalars()
            .all()
        )
    actor = human(user)
    (cancelled,) = await publish(
        state,
        Event(
            project_id=project_id,
            actor=actor,
            type=EventType.GOAL_CANCELLED,
            subject=Subject(entity="goal", id=goal_id),
            payload={"reason": reason, "by": user},
            correlation_id=goal_id,
            causation_id=None,
        ),
    )
    cascade = [
        Event(
            project_id=project_id,
            actor=actor,
            type=EventType.TASK_CANCELLED,
            subject=Subject(entity="task", id=tid),
            payload={"reason": reason, "by": user, "cascade_from": goal_id},
            correlation_id=goal_id,
            causation_id=cancelled.id,
        )
        for tid in pending
    ]
    if cascade:
        await publish(state, *cascade)
    return list(pending)


@router.post("/{goal_id}/cancel", status_code=202, dependencies=[AdminDep])
async def cancel_goal(
    project_id: str, goal_id: str, body: CancelIn, state: StateDep, user: UserDep
) -> CancelOut:
    await _goal(state, project_id, goal_id)
    pending = await cancel_goal_cascade(state, project_id, goal_id, user, body.reason)
    return CancelOut(id=goal_id, cancelled_tasks=pending)
