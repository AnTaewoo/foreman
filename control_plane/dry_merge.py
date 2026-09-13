"""Dry 자동 머지 (D-36, 리뷰 A7): ``dry_run=true``면 ``pr.opened`` → ``pr.merged`` (사람 머지 흉내).

- 상태 머신·§6.1·``pick_ready``는 그대로: projection의 ``pr.merged`` 핸들러가 in_review → done (또는
  ``pr_merged_at`` 플래그, D-30 b)을 옮긴다. 실 모드에서는 ``enabled=False``로 아무것도 하지 않는다.
- 멱등: 프로세스 내 ``(project_id, pr_number)`` memo와 ``tasks.pr_merged_at`` 둘 다 확인. 같은
  ``pr.opened``가 미서명(워커 XADD)·서명본(relay) 두 번 와도 1건. 새어 나가도 projection이 전이 없이
  흡수한다.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import Delivery, EventBus
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store.session import get_session

log = structlog.get_logger(__name__)

DRY_MERGE_ACTOR = Actor(type="system", id="dry-merge")
OnMerge = Callable[[str, int, str], None]  # (project_id, pr_number, task_id)


class DryMerger:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        *,
        enabled: bool,
        on_merge: OnMerge | None = None,
    ) -> None:
        self._factory = factory
        self._bus = bus
        self._enabled = enabled
        self._on_merge = on_merge
        self.merged: list[tuple[str, int]] = []
        self._seen: set[tuple[str, int]] = set()

    async def handle(self, delivery: Delivery) -> None:
        event = delivery.event
        if not self._enabled or event.type is not EventType.PR_OPENED:
            return
        pr_number = int(event.payload.get("pr_number") or event.subject.id)
        task_id = str(event.payload.get("task_id") or "")
        key = (event.project_id, pr_number)
        if key in self._seen:
            return
        async with self._factory() as s:
            task = await s.get(m.Task, task_id) if task_id else None
        if task is not None and task.pr_merged_at is not None:
            self._seen.add(key)
            return
        self._seen.add(key)
        merged = Event(
            project_id=event.project_id,
            actor=DRY_MERGE_ACTOR,
            type=EventType.PR_MERGED,
            subject=Subject(entity="pr", id=str(pr_number)),
            payload={"task_id": task_id, "pr_number": pr_number, "merged_by": "dry-run"},
            correlation_id=event.correlation_id,
            causation_id=event.id,
        )
        async with get_session(self._factory) as s:
            await self._bus.publish(s, merged)
        self.merged.append(key)
        log.info(
            "dry_merge.merged", project_id=event.project_id, pr_number=pr_number, task_id=task_id
        )
        if self._on_merge is not None:
            self._on_merge(event.project_id, pr_number, task_id)
