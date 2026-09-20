"""`POST /projects`, `GET /projects/{id}` (설계 §13). project.created는 루트 이벤트 (D-19, D-25)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from ulid import ULID

from control_plane.api.demo_guard import AdminDep, require_admin
from control_plane.api.deps import (
    _OWNER_NAME,
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
from github_adapter.app_check import run_check
from github_adapter.client import GITHUB_API_BASE_URL

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
    archived_at: datetime | None = None  # D-54: 보관됨(목록에서 제외, 새 Goal 409)
    # P9.12: 승인할 수 있는 GitHub 로그인(owner/approver) — 콘솔은 자기 id가 없으면 GitHub 안내
    approvers: list[str] = []


def approvers_of(members: list[dict[str, str]] | list[Member]) -> list[str]:
    out = []
    for mem in members:
        d = mem.model_dump() if isinstance(mem, Member) else mem
        if d.get("role") in ("owner", "approver"):
            out.append(str(d.get("user_id")))
    return out


class ProjectList(BaseModel):
    items: list[ProjectOut]


def github_http() -> httpx.AsyncClient:
    """`run_check`용 GitHub API client. auth 없음 — 테스트가 monkeypatch 한다."""
    return httpx.AsyncClient(base_url=GITHUB_API_BASE_URL, timeout=30)


class CheckItemOut(BaseModel):
    name: str
    ok: bool
    detail: str
    required: bool = True  # False = 경고(연결을 막지 않는다, P9.10)


class RepoCheckOut(BaseModel):
    """`GET /projects/check?repo=owner/name` — 연결 전 읽기 전용 App 점검 (P9 콘솔)."""

    repo: str
    dry_run: bool
    ok: bool
    items: list[CheckItemOut]
    canonical: str | None = None  # GitHub의 정식 owner/name — 콘솔은 이 이름으로 연결한다
    install_url: str | None = None  # P9.10: App 설치 링크 (공개 App)
    installation_id: int | None = None


@router.get("")
async def list_projects(state: StateDep, include_archived: bool = False) -> ProjectList:
    stmt = select(m.Project).order_by(m.Project.created_at)
    if not include_archived:  # D-54
        stmt = stmt.where(m.Project.archived_at.is_(None))
    async with state.factory() as s:
        rows = (await s.execute(stmt)).scalars().all()
    return ProjectList(
        items=[
            ProjectOut(
                id=r.id,
                name=r.name,
                repo=r.repo_full_name,
                repo_url=gh_url(r.repo_full_name),
                default_branch=r.default_branch,
                created_at=r.created_at,
                archived_at=r.archived_at,
                approvers=approvers_of(list(r.members or [])),
            )
            for r in rows
        ]
    )


class ProjectDeleted(BaseModel):
    id: str
    cancelled_goals: list[str]


@router.delete("/{project_id}", status_code=202, dependencies=[AdminDep])
async def delete_project(project_id: str, state: StateDep, user: UserDep) -> ProjectDeleted:
    """D-54: 삭제는 archived_at을 찍는 soft delete. 미완 Goal·Task를 취소하고
    project.updated{archived: true}를 낸다(이벤트 필드는 동결).
    이벤트·GitHub Issue/PR/Discussion은 그대로 남는다. 같은 repo는 다시 연결할 수 있다."""
    from control_plane.api.goals import cancel_goal_cascade
    from control_plane.store.enums import GoalStatus

    view = await find_project(state, project_id)
    if view is None:
        raise HTTPException(404, "project not found")
    if view.archived_at is not None:
        raise HTTPException(409, "project is already deleted")  # P9.19
    async with state.factory() as s:
        open_goals = (
            (
                await s.execute(
                    select(m.Goal.id)
                    .where(
                        m.Goal.project_id == project_id,
                        m.Goal.status.not_in((GoalStatus.DONE, GoalStatus.CANCELLED)),
                    )
                    .order_by(m.Goal.created_at, m.Goal.id)
                )
            )
            .scalars()
            .all()
        )
    cancelled: list[str] = []
    for gid in open_goals:
        await cancel_goal_cascade(state, project_id, gid, user, "project deleted")
        cancelled.append(gid)
    await publish(
        state,
        Event(
            project_id=project_id,
            actor=human(user),
            type=EventType.PROJECT_UPDATED,
            subject=Subject(entity="project", id=project_id),
            payload={"archived": True, "by": user},
            correlation_id=project_id,
            causation_id=None,
        ),
    )
    return ProjectDeleted(id=project_id, cancelled_goals=cancelled)


@router.post("", status_code=201)
async def create_project(
    body: ProjectIn,
    state: StateDep,
    user: UserDep,
    x_admin_token: Annotated[str | None, Header()] = None,
) -> ProjectOut:
    """P9.10 공개 App: 실 모드의 owner/name은 App 점검(installation 탐지)을 먼저 통과해야 한다.
    외부 installation이면 **설치가 곧 권한 증명** — 관리 토큰 없이 연결, owner는 그 계정.
    서버 자체 installation·로컬 경로·Dry는 종전대로 데모 모드면 관리 토큰 (P9.3)."""
    public_app = not state.settings.dry_run and bool(_OWNER_NAME.match(body.repo))
    if not public_app:  # 종전 순서: 관리 토큰 먼저 (P9.3)
        await require_admin(state, x_admin_token)
    if (problem := validate_repo(body.repo)) is not None:
        raise HTTPException(400, problem)
    repo, default_branch = body.repo, body.default_branch
    members = body.members if body.members is not None else [Member(user_id=user, role="owner")]
    installation_id: int | None = None
    if public_app:
        async with github_http() as http:  # run_check가 JWT·installation 토큰을 직접 만든다
            report = await run_check(state.settings, http, repo=body.repo)
        if report.installation_id is None:
            raise HTTPException(
                400,
                f"app_not_installed: install the GitHub App on {body.repo} first — "
                f"{report.install_url or 'GitHub App settings → Install App'}",
            )
        if not report.ok:
            failed = [f"{i.name}: {i.detail}" for i in report.items if i.required and not i.ok]
            raise HTTPException(400, "repo check failed — " + "; ".join(failed))
        installation_id = report.installation_id
        repo = report.canonical or body.repo
        default_branch = report.default_branch or body.default_branch
        if installation_id == state.settings.github_installation_id:  # 서버 자체 → 종전대로
            await require_admin(state, x_admin_token)
        else:  # 결정 (3): 외부 설치 — 승인·머지는 GitHub에서 그 계정으로
            members = [Member(user_id=str(report.account_login), role="owner")]
    if await repo_taken(state, repo):  # D-45
        raise HTTPException(409, f"a project for {repo!r} already exists")
    pid = str(ULID())
    payload: dict[str, object] = {
        "name": body.name,
        "repo": repo,
        "default_branch": default_branch,
        "members": [mem.model_dump() for mem in members],
    }
    if installation_id is not None:
        payload["installation_id"] = installation_id  # P9.10 (additive)
    (event,) = await publish(
        state,
        Event(
            project_id=pid,
            actor=human(user),
            type=EventType.PROJECT_CREATED,
            subject=Subject(entity="project", id=pid),
            payload=payload,
            correlation_id=pid,  # Goal 밖 → project_id (D-25)
            causation_id=None,
        ),
    )
    return ProjectOut(
        id=pid,
        name=body.name,
        repo=repo,
        repo_url=gh_url(repo),
        default_branch=default_branch,
        created_at=event.ts,
        approvers=approvers_of(members),
    )


@router.get("/check")
async def check_repo(repo: str, state: StateDep) -> RepoCheckOut:
    """App 인증·설치·권한·웹훅·Discussions(Plans) 점검. 쓰기 없음. `/{project_id}`보다 먼저 선언."""
    if not _OWNER_NAME.match(repo):
        raise HTTPException(400, "repo must be owner/name")
    settings = state.settings
    if settings.dry_run:
        return RepoCheckOut(
            repo=repo,
            dry_run=True,
            ok=False,
            items=[
                CheckItemOut(
                    name="dry_run",
                    ok=False,
                    detail="HITL_DRY_RUN=true: remote repos are not cloned (D-48) — "
                    "run with HITL_DRY_RUN=false or use a local path",
                )
            ],
        )
    async with (
        github_http() as http
    ):  # run_check가 JWT·installation 토큰을 직접 만든다 (인증 없는 client)
        report = await run_check(settings, http, repo=repo)
    return RepoCheckOut(
        repo=repo,
        dry_run=False,
        ok=report.ok,
        items=[
            CheckItemOut(name=i.name, ok=i.ok, detail=i.detail, required=i.required)
            for i in report.items
        ],
        canonical=report.canonical,
        install_url=report.install_url,
        installation_id=report.installation_id,
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
        archived_at=view.archived_at,
        approvers=approvers_of(view.members),
    )
