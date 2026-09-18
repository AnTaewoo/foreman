"""API 의존성: 앱 상태(설정·세션 팩토리·Redis·EventBus), 사용자, 이벤트 발행 도우미.

API는 이벤트를 **발행만** 한다 — DB 갱신은 projection(§0 코딩 규칙). 읽기는 projection 테이블.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

from fastapi import Depends, Header, Request
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.events.bus import EventBus
from control_plane.events.schema import Actor, Event
from control_plane.store import models as m
from control_plane.store import session as sess

if TYPE_CHECKING:
    from control_plane.orchestrator.runner import GoalRunner

GoalHook = Callable[[str, str], Awaitable[None]]  # (project_id, goal_id) — P5.2 runner가 건다
LlmProbe = Callable[[str], Awaitable[None]]  # profile → ProviderError를 올린다


@dataclass
class AppState:
    settings: Settings
    factory: async_sessionmaker[AsyncSession]
    redis: Redis
    bus: EventBus
    engine: AsyncEngine | None = None  # 앱이 직접 만든 경우만 (shutdown에서 dispose)
    owns_redis: bool = False
    on_goal_created: GoalHook | None = None
    runner: GoalRunner | None = None  # P5.2: Goal 실행기(웹훅 승인 재개)
    llm_probe: LlmProbe | None = None  # Goal 생성 전 원격 프로파일 1콜 검증 (None = 검증 없음)


def build_state(
    settings: Settings,
    *,
    factory: async_sessionmaker[AsyncSession] | None = None,
    redis: Redis | None = None,
) -> AppState:
    engine: AsyncEngine | None = None
    if factory is None:
        engine = sess.create_engine(settings)
        factory = sess.create_session_factory(engine)
    owns_redis = redis is None
    if redis is None:
        redis = Redis.from_url(settings.redis_url, decode_responses=True)
    return AppState(
        settings=settings,
        factory=factory,
        redis=redis,
        bus=EventBus(redis),
        engine=engine,
        owns_redis=owns_redis,
    )


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.ctx
    return state


def current_user(x_user_id: Annotated[str | None, Header()] = None) -> str:
    """MVP 1: 인증 없음. `X-User-Id` 헤더가 actor.id, 없으면 anonymous."""
    return x_user_id or "anonymous"


StateDep = Annotated[AppState, Depends(get_state)]
UserDep = Annotated[str, Depends(current_user)]


def human(user_id: str) -> Actor:
    return Actor(type="human", id=user_id)


@dataclass(frozen=True)
class ProjectView:
    """projection 행 또는(반영 전이면) ``project.created`` 이벤트로 만든 읽기 뷰 (D-46, F-7)."""

    id: str
    name: str
    repo: str
    default_branch: str
    members: list[dict[str, str]]
    created_at: datetime
    projected: bool
    archived_at: datetime | None = None  # D-54


async def find_project(state: AppState, project_id: str) -> ProjectView | None:
    async with state.factory() as s:
        row = await s.get(m.Project, project_id)
        if row is not None:
            return ProjectView(
                row.id,
                row.name,
                row.repo_full_name,
                row.default_branch,
                [dict(x) for x in row.members],
                row.created_at,
                True,
                row.archived_at,
            )
        ev = await s.scalar(
            select(m.Event).where(
                m.Event.type == "project.created", m.Event.subject_id == project_id
            )
        )
    if ev is None:
        return None
    p = ev.payload
    return ProjectView(
        project_id,
        str(p.get("name", "")),
        str(p.get("repo", "")),
        str(p.get("default_branch", "main")),
        [dict(x) for x in p.get("members", [])],
        ev.ts,
        False,
    )


def repo_key(repo: str) -> str:
    """비교용 키: GitHub `owner/name`은 대소문자 무시(인계서 #4), 로컬 경로는 그대로."""
    return repo.lower() if _OWNER_NAME.match(repo) else repo


async def repo_taken(state: AppState, repo: str) -> bool:
    """같은 repo의 (보관되지 않은) 프로젝트가 이미 있나 (D-45, D-54) — projection·events 둘 다."""
    key = repo_key(repo)
    async with state.factory() as s:
        rows = [
            (pid, archived_at)
            for pid, name, archived_at in (
                await s.execute(
                    select(m.Project.id, m.Project.repo_full_name, m.Project.archived_at)
                )
            ).all()
            if repo_key(str(name)) == key
        ]
        if any(archived_at is None for _, archived_at in rows):
            return True
        projected = {pid for pid, _ in rows}
        created = await s.execute(
            select(m.Event.subject_id, m.Event.payload).where(m.Event.type == "project.created")
        )
        candidates = [pid for pid, p in created.all() if repo_key(str(p.get("repo"))) == key]
        updated = await s.execute(
            select(m.Event.subject_id, m.Event.payload).where(m.Event.type == "project.updated")
        )
        archived = {pid for pid, p in updated.all() if p.get("archived") is True}
    return any(pid not in projected and pid not in archived for pid in candidates)


_OWNER_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def gh_url(repo: str, kind: str = "", number: int | None = None) -> str | None:
    """`owner/name`이면 GitHub 링크, 로컬 경로·URL이면 None (P9.1 데모 콘솔용).

    kind: "" | "issues" | "pull" | "discussions"
    """
    if not _OWNER_NAME.match(repo):
        return None
    base = f"https://github.com/{repo}"
    if not kind:
        return base
    if number is None:
        return None
    return f"{base}/{kind}/{number}"


def validate_repo(repo: str) -> str | None:
    """오류 메시지 또는 None (리포트 #8). 존재하는 로컬 경로(절대·상대) / owner/name / git URL만."""
    if repo.startswith(("http://", "https://", "git@", "ssh://")):
        return None
    if Path(repo).is_dir():
        return None
    if _OWNER_NAME.match(repo):
        return None
    if "/" in repo or repo.startswith("."):
        return f"local path {repo!r} does not exist"
    return f"repo {repo!r} must be an existing local path, owner/name, or a git URL"


async def publish(state: AppState, *events: Event) -> list[Event]:
    """outbox에 넣고 commit (D-06). 반환은 서명·id가 채워진 사본."""
    out: list[Event] = []
    async with sess.get_session(state.factory) as s:
        for e in events:
            out.append(await state.bus.publish(s, e))
    return out
