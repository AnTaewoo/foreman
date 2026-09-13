"""PR 생성은 control plane (D-37, 리뷰 A4): task.completed{branch, summary} → open_pr → pr.opened.

- 워커는 push까지만 하고 GitHub 토큰을 받지 않는다(§12). Dry/실 client 선택은 여기서만.
- 멱등: 프로세스 내 ``(project_id, task_id)`` memo + ``tasks.pr_number`` + client의 head/base 멱등.
  같은 ``task.completed``가 미서명·서명본으로 두 번 와도 PR 1건, ``pr.opened`` 1건.
- Issue 요약 코멘트(key ``summary:<run>``)도 여기서.
"""

from __future__ import annotations

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import Delivery, EventBus
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.session import get_session
from github_adapter.protocol import GitHubClient, PrMeta

log = structlog.get_logger(__name__)

PR_OPENER_ACTOR = Actor(type="system", id="pr-opener")


class PrOpener:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        github: GitHubClient,
        *,
        agent_id: str = "coding-1",
    ) -> None:
        self._factory = factory
        self._bus = bus
        self._github = github
        self._agent_id = agent_id
        self.opened: list[tuple[str, str]] = []
        self._seen: set[tuple[str, str]] = set()

    async def handle(self, delivery: Delivery) -> None:
        event = delivery.event
        if event.type is not EventType.TASK_COMPLETED:
            return
        branch = event.payload.get("branch")
        if not branch:
            return  # D-37 이전 형식 또는 브랜치 없는 완료 — 할 일 없음
        key = (event.project_id, event.subject.id)
        if key in self._seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, event.subject.id)
            project = await s.get(m.Project, event.project_id)
        if task is None or project is None:
            log.warning(
                "pr_opener.missing_rows", task_id=event.subject.id, project_id=event.project_id
            )
            return
        self._seen.add(key)
        run_id = str(event.payload.get("run_id") or "")
        summary = str(event.payload.get("summary") or "")
        title = f"[T-{task.issue_number or '?'}] {task.title}"
        meta = PrMeta(
            task_id=task.id, run_id=run_id, agent_id=self._agent_id, tier=task.risk_tier.value
        )
        pr = await self._github.open_pr(
            project.repo_full_name, str(branch), project.default_branch, title, summary, True, meta
        )
        if not pr.created and task.pr_number == pr.number:
            log.info("pr_opener.already_open", task_id=task.id, pr_number=pr.number)
            return
        if task.issue_number is not None:
            await self._github.comment(
                project.repo_full_name, task.issue_number, summary, key=f"summary:{run_id}"
            )
        opened = Event(
            project_id=event.project_id,
            actor=PR_OPENER_ACTOR,
            type=EventType.PR_OPENED,
            subject=Subject(entity="pr", id=str(pr.number)),
            payload={
                "task_id": task.id,
                "run_id": run_id,
                "pr_number": pr.number,
                "head": str(branch),
                "base": project.default_branch,
                "draft": True,
                "url": pr.url,
            },
            correlation_id=event.correlation_id,
            causation_id=event.id,
        )
        async with get_session(self._factory) as s:
            await self._bus.publish(s, opened)
        self.opened.append(key)
        log.info("pr_opener.opened", task_id=task.id, pr_number=pr.number, head=branch)
