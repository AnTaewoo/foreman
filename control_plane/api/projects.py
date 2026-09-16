"""`POST /projects`, `GET /projects/{id}` (설계 §13). project.created는 루트 이벤트 (D-19, D-25)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from ulid import ULID

from control_plane.api.demo_guard import AdminDep
from control_plane.api.deps import (
    StateDep,
    UserDep,
    find_project,
    gh_url,
    human,
    publish,
    repo_taken,
    validate_repo,
)
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
    repo_url: str | None = None  # owner/name이면 GitHub 링크 (P9.1)
    default_branch: str
    created_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]


@router.get("")
async def list_projects(state: StateDep) -> ProjectList:
    async with state.factory() as s:
        rows = (await s.execute(select(m.Project).order_by(m.Project.created_at))).scalars().all()
    return ProjectList(
        items=[
            ProjectOut(
                id=r.id,
                name=r.name,
                repo=r.repo_full_name,
                repo_url=gh_url(r.repo_full_name),
                default_branch=r.default_branch,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


@router.post("", status_code=201, dependencies=[AdminDep])  # 데모 모드면 X-Admin-Token (P9.3)
async def create_project(body: ProjectIn, state: StateDep, user: UserDep) -> ProjectOut:
    if (problem := validate_repo(body.repo)) is not None:
        raise HTTPException(400, problem)
    if await repo_taken(state, body.repo):  # D-45
        raise HTTPException(409, f"a project for {body.repo!r} already exists")
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
        repo_url=gh_url(body.repo),
        default_branch=body.default_branch,
        created_at=event.ts,
    )


@router.get("/{project_id}")
async def get_project(project_id: str, state: StateDep) -> ProjectOut:
    view = await find_project(state, project_id)  # D-46: projection 전이면 events
    if view is None:
        raise HTTPException(404, "project not found")
    return ProjectOut(
        id=view.id,
        name=view.name,
        repo=view.repo,
        repo_url=gh_url(view.repo),
        default_branch=view.default_branch,
        created_at=view.created_at,
    )
