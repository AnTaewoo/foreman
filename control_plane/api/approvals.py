"""웹훅 슬래시 명령 → 권한 검사 → Plan 승인 재개 (설계 §7.4, §13; D-13).

- ``/approve``·``/reject <reason>``: 작성자가 ``project.members``의 owner|approver(아니면 403).
- 대상 Goal: 그 project에서 interrupt 대기 중인 Goal 중 Plan Discussion 번호가 코멘트 대상인 것.
  issue_comment(개발용 우회)만 대기 Goal이 하나뿐이면 그것으로 폴백. 없으면 no-op(202).
- ``/changes``는 MVP 1에서 미지원(B6 재계획은 후속) — 기록만.
- 같은 delivery 두 번은 ``WebhookHandler``의 DeliveryCache가 204로 막는다.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.api.deps import AppState, publish
from control_plane.events.schema import Event
from control_plane.orchestrator.runner import GoalRunner
from control_plane.store import models as m
from github_adapter.webhooks import (
    ProjectRef,
    SlashCommand,
    WebhookHandler,
    build_webhook_router,
)

log = structlog.get_logger(__name__)

APPROVER_ROLES = frozenset({"owner", "approver"})


class ApprovalService:
    def __init__(self, *, factory: async_sessionmaker[AsyncSession], runner: GoalRunner) -> None:
        self._factory = factory
        self._runner = runner

    async def resolve_project(self, repo: str) -> ProjectRef | None:
        async with self._factory() as s:
            row = await s.scalar(select(m.Project).where(m.Project.repo_full_name == repo))
        if row is None:
            return None
        return ProjectRef(project_id=row.id, default_branch=row.default_branch)

    async def resolve_goal(self, project_id: str, task_id: str) -> str | None:
        async with self._factory() as s:
            task = await s.get(m.Task, task_id)
        return task.goal_id if task is not None and task.project_id == project_id else None

    async def on_slash(self, cmd: SlashCommand) -> None:
        async with self._factory() as s:
            project = await s.get(m.Project, cmd.project_id)
        members = list(project.members) if project is not None else []
        role = next(
            (str(mem.get("role")) for mem in members if mem.get("user_id") == cmd.author), None
        )
        if role not in APPROVER_ROLES:
            log.warning("approval.forbidden", author=cmd.author, role=role, project=cmd.project_id)
            raise HTTPException(403, f"{cmd.author} is not an owner/approver of this project")
        if cmd.command == "changes":
            log.info("approval.changes_unsupported", project=cmd.project_id, by=cmd.author)
            return
        waiting = self._runner.waiting(cmd.project_id)
        goal_id = next(
            (g for g, w in waiting.items() if w.plan_discussion_number == cmd.number), None
        )
        # issue_comment(dev 우회)만 "유일한 대기 Goal" 폴백. discussion은 번호가 맞아야 한다 (P6.4)
        if goal_id is None and cmd.source == "issue" and len(waiting) == 1:
            goal_id = next(iter(waiting))
        if goal_id is None:
            log.info("approval.no_waiting_goal", project=cmd.project_id, issue=cmd.issue_number)
            return
        await self._runner.resume(
            goal_id, approved=cmd.command == "approve", by=cmd.author, reason=cmd.argument
        )


def build_webhook(state: AppState, runner: GoalRunner) -> APIRouter:
    """``POST /webhooks/github`` 라우터 — 서명 검증·이벤트 변환은 P2.4 ``WebhookHandler``."""
    service = ApprovalService(factory=state.factory, runner=runner)

    async def publish_one(event: Event) -> None:
        await publish(state, event)

    handler = WebhookHandler(
        secret=state.settings.github_webhook_secret.get_secret_value(),
        publish=publish_one,
        resolve_project=service.resolve_project,
        on_slash_command=service.on_slash,
        resolve_goal=service.resolve_goal,
    )
    return build_webhook_router(handler)
