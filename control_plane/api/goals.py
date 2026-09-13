"""Goal API (설계 §13): 생성(202, goal.created 루트) · 진행률 · 취소(goal/task.cancelled cascade).

P5.2가 `AppState.on_goal_created` 훅으로 Orchestrator 실행을 붙인다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from ulid import ULID

from control_plane.api.deps import StateDep, UserDep, human, publish
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
    tasks: dict[str, int]  # status → count
    total: int
    done: int
    epics: list[EpicOut]


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
    async with state.factory() as s:
        if await s.get(m.Project, project_id) is None:
            raise HTTPException(404, "project not found")
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


@router.get("/{goal_id}")
async def get_goal(project_id: str, goal_id: str, state: StateDep) -> GoalProgress:
    goal = await _goal(state, project_id, goal_id)
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


@router.post("/{goal_id}/cancel", status_code=202)
async def cancel_goal(
    project_id: str, goal_id: str, body: CancelIn, state: StateDep, user: UserDep
) -> CancelOut:
    await _goal(state, project_id, goal_id)
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
            payload={"reason": body.reason, "by": user},
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
            payload={"reason": body.reason, "by": user, "cascade_from": goal_id},
            correlation_id=goal_id,
            causation_id=cancelled.id,
        )
        for tid in pending
    ]
    if cascade:
        await publish(state, *cascade)
    return CancelOut(id=goal_id, cancelled_tasks=list(pending))
