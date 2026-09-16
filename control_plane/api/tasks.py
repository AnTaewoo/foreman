"""Task API (설계 §13): 목록(status/epic 필터), `PATCH {action: cancel}` → task.cancelled (D-27)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select

from control_plane.api.demo_guard import AdminDep
from control_plane.api.deps import StateDep, UserDep, find_project, gh_url, human, publish
from control_plane.events.schema import Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus

router = APIRouter(prefix="/projects/{project_id}/tasks", tags=["tasks"])


class TaskOut(BaseModel):
    id: str
    epic_id: str
    goal_id: str
    title: str
    status: str
    kind: str
    role_required: str
    risk_tier: str
    issue_number: int | None
    pr_number: int | None
    branch_name: str | None
    attempt_count: int
    owned_paths: list[str]
    depends_on: list[str]
    spec: str = ""  # P9.1 데모 콘솔
    issue_url: str | None = None
    pr_url: str | None = None


class TaskList(BaseModel):
    items: list[TaskOut]


class TaskPatch(BaseModel):
    action: Literal["cancel"]
    reason: str = ""


class TaskPatched(BaseModel):
    id: str
    action: str


def _out(t: m.Task, repo: str = "") -> TaskOut:
    return TaskOut(
        id=t.id,
        epic_id=t.epic_id,
        goal_id=t.goal_id,
        title=t.title,
        status=t.status.value,
        kind=t.kind.value,
        role_required=t.role_required.value,
        risk_tier=t.risk_tier.value,
        issue_number=t.issue_number,
        pr_number=t.pr_number,
        branch_name=t.branch_name,
        attempt_count=t.attempt_count,
        owned_paths=[str(p) for p in t.owned_paths],
        depends_on=[str(d) for d in t.depends_on],
        spec=t.spec or "",
        issue_url=gh_url(repo, "issues", t.issue_number) if repo else None,
        pr_url=gh_url(repo, "pull", t.pr_number) if repo else None,
    )


@router.get("")
async def list_tasks(
    project_id: str,
    state: StateDep,
    status: TaskStatus | None = None,
    epic: str | None = None,
) -> TaskList:
    stmt = select(m.Task).where(m.Task.project_id == project_id)
    if status is not None:
        stmt = stmt.where(m.Task.status == status)
    if epic is not None:
        stmt = stmt.where(m.Task.epic_id == epic)
    async with state.factory() as s:
        rows = (await s.execute(stmt.order_by(m.Task.created_at, m.Task.id))).scalars().all()
    project = await find_project(state, project_id)
    repo = project.repo if project is not None else ""
    return TaskList(items=[_out(t, repo) for t in rows])


@router.patch("/{task_id}", status_code=202, dependencies=[AdminDep])
async def patch_task(
    project_id: str, task_id: str, body: TaskPatch, state: StateDep, user: UserDep
) -> TaskPatched:
    async with state.factory() as s:
        task = await s.get(m.Task, task_id)
    if task is None or task.project_id != project_id:
        raise HTTPException(404, "task not found")
    await publish(
        state,
        Event(
            project_id=project_id,
            actor=human(user),
            type=EventType.TASK_CANCELLED,
            subject=Subject(entity="task", id=task_id),
            payload={"reason": body.reason, "by": user, "cascade_from": None},
            correlation_id=task.goal_id,
            causation_id=None,
        ),
    )
    return TaskPatched(id=task_id, action=body.action)
