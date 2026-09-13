"""`POST /projects`, `GET /projects/{id}` (설계 §13). project.created는 루트 이벤트 (D-19, D-25)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from ulid import ULID

from control_plane.api.deps import StateDep, UserDep, human, publish
from control_plane.events.schema import Event, EventType, Subject
from control_plane.store import models as m

router = APIRouter(prefix="/projects", tags=["projects"])


class Member(BaseModel):
    user_id: str  # MVP 1: GitHub login과 같다 (설계 §4.1 members, §7.4 승인 권한)
    role: Literal["owner", "approver", "viewer"]


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    repo: str = Field(min_length=1, description="repo_full_name(owner/name) 또는 로컬 경로")
    default_branch: str = "main"
    members: list[Member] | None = None  # 없으면 요청자가 owner


class ProjectOut(BaseModel):
    id: str
    name: str
    repo: str
    default_branch: str
    created_at: datetime


@router.post("", status_code=201)
async def create_project(body: ProjectIn, state: StateDep, user: UserDep) -> ProjectOut:
    pid = str(ULID())
    members = body.members if body.members is not None else [Member(user_id=user, role="owner")]
    (event,) = await publish(
        state,
        Event(
            project_id=pid,
            actor=human(user),
            type=EventType.PROJECT_CREATED,
            subject=Subject(entity="project", id=pid),
            payload={
                "name": body.name,
                "repo": body.repo,
                "default_branch": body.default_branch,
                "members": [mem.model_dump() for mem in members],
            },
            correlation_id=pid,  # Goal 밖 → project_id (D-25)
            causation_id=None,
        ),
    )
    return ProjectOut(
        id=pid,
        name=body.name,
        repo=body.repo,
        default_branch=body.default_branch,
        created_at=event.ts,
    )


@router.get("/{project_id}")
async def get_project(project_id: str, state: StateDep) -> ProjectOut:
    async with state.factory() as s:
        row = await s.get(m.Project, project_id)
    if row is None:
        raise HTTPException(404, "project not found")
    return ProjectOut(
        id=row.id,
        name=row.name,
        repo=row.repo_full_name,
        default_branch=row.default_branch,
        created_at=row.created_at,
    )
