"""Scheduler 루프 (설계 §3.2): 이벤트 구독 → ready 판정 → task.assigned(+epic.activated) → 워커 기동.

- 배정 순서: ``task.assigned`` 발행 → (Epic 첫 배정이면) ``epic.activated`` → ``launcher.launch``.
  기동 실패 → ``task.failed {reason: launch_failed, attempt}`` (D-28: assigned→ready/blocked).
- read-your-writes (B11): projection이 아직 반영 전이어도 ``in_flight``/``activated_epics`` memo로 중복 배정을 막는다.
- ``run.finished``(또는 ``task.failed``/``task.blocked``)로 슬롯을 돌려받는다.
- ingest (D-26): 워커가 XADD한 **미서명** 이벤트는 ``chain.append_signed``로 DB에 넣고(event.id 멱등) projection을
  적용한다. relay가 보낸 서명된 이벤트는 projection consumer가 처리하므로 여기서는 배정 판단에만 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from ulid import ULID

from control_plane.events.bus import Delivery, EventBus
from control_plane.events.chain import append_signed
from control_plane.events.projection import Projection
from control_plane.events.schema import UNCHAINED, Actor, Event, EventType, Subject
from control_plane.scheduler.launcher import LaunchError, LaunchSpec, WorkerLauncher
from control_plane.scheduler.queue import Candidate, load_project_tasks, pick_ready
from control_plane.store import models as m
from control_plane.store.enums import EpicStatus

log = structlog.get_logger(__name__)

SCHEDULER_ACTOR = Actor(type="system", id="scheduler")
TRIGGERS = frozenset(
    {
        EventType.TASK_CREATED, EventType.TASK_RETRIED, EventType.TASK_FAILED, EventType.TASK_BLOCKED,
        EventType.TASK_CANCELLED, EventType.RUN_FINISHED, EventType.PR_MERGED, EventType.GOAL_ACTIVATED,
    }
)  # fmt: skip
RELEASERS = frozenset(
    {
        EventType.RUN_FINISHED,
        EventType.TASK_FAILED,
        EventType.TASK_BLOCKED,
        EventType.TASK_CANCELLED,
    }
)


def _slugify(text: str) -> str:
    import re

    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug[:40].strip("-") or "task"


class Scheduler:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        launcher: WorkerLauncher,
        *,
        projection: Projection | None = None,
        max_workers: int = 4,
        repo_url: str = "",
        default_branch: str = "main",
        timeout_min: int = 45,
        agent_id: str = "coding-1",
        run_id_factory: Callable[[], str] = lambda: str(ULID()),
    ) -> None:
        self._factory = session_factory
        self._bus = bus
        self._launcher = launcher
        self._projection = projection
        self.max_workers = max_workers
        self._repo_url = repo_url
        self._default_branch = default_branch
        self._timeout_min = timeout_min
        self._agent_id = agent_id
        self._new_run_id = run_id_factory
        self.in_flight: set[str] = set()  # task_id
        self.activated_epics: set[str] = set()
        self._run_to_task: dict[str, str] = {}

    # ------------------------------------------------------------------ 이벤트 입력
    async def handle(self, delivery: Delivery) -> None:
        event = delivery.event
        if event.signature is None:
            await self.ingest(event)
        if event.type in RELEASERS:
            self._release(event)
        if event.type in TRIGGERS:
            await self.tick(event.project_id)

    async def ingest(self, event: Event) -> None:
        """워커 발 미서명 이벤트 → append_signed(멱등) + projection 적용."""
        async with self._factory() as session:
            if event.type in UNCHAINED:
                exists = await session.scalar(
                    select(m.ToolCall.id).where(m.ToolCall.id == event.id)
                )
            else:
                exists = await session.scalar(select(m.Event.id).where(m.Event.id == event.id))
            if exists is None:
                await append_signed(session, event)
                await session.commit()
        if self._projection is not None:
            await self._projection.apply(event)

    def _release(self, event: Event) -> None:
        if event.type is EventType.RUN_FINISHED:
            task_id = self._run_to_task.pop(event.subject.id, None) or str(
                event.payload.get("task_id", "")
            )
        else:
            task_id = event.subject.id
        if task_id:
            self.in_flight.discard(task_id)

    # ------------------------------------------------------------------ 배정
    async def tick(self, project_id: str) -> list[str]:
        """배정 한 바퀴. 배정한 task id 목록."""
        free = self.max_workers - len(self.in_flight)
        async with self._factory() as session:
            tasks = await load_project_tasks(session, project_id)
            candidates = pick_ready(tasks, in_flight=self.in_flight, free_slots=free)
            epics = (
                {
                    e.id: e
                    for e in (
                        await session.execute(
                            select(m.Epic).where(m.Epic.id.in_({c.epic_id for c in candidates}))
                        )
                    )
                    .scalars()
                    .all()
                }
                if candidates
                else {}
            )
        assigned: list[str] = []
        for cand in candidates:
            run_id = self._new_run_id()
            self.in_flight.add(cand.id)
            self._run_to_task[run_id] = cand.id
            prev = await self._publish(project_id, cand, EventType.TASK_ASSIGNED, "task", cand.id,
                                       {"agent_id": self._agent_id, "run_id": run_id}, None)  # fmt: skip
            epic = epics.get(cand.epic_id)
            if (
                epic is not None
                and epic.status is EpicStatus.PENDING
                and cand.epic_id not in self.activated_epics
            ):
                self.activated_epics.add(cand.epic_id)
                prev = await self._publish(
                    project_id, cand, EventType.EPIC_ACTIVATED, "epic", cand.epic_id, {}, prev
                )
            spec = self._spec(
                project_id, cand, run_id, epic.title if epic is not None else cand.epic_id
            )
            try:
                await self._launcher.launch(spec)
            except LaunchError as exc:
                log.error("scheduler.launch_failed", task_id=cand.id, error=str(exc))
                self.in_flight.discard(cand.id)
                self._run_to_task.pop(run_id, None)
                await self._publish(project_id, cand, EventType.TASK_FAILED, "task", cand.id,
                                    {"run_id": run_id, "reason": "launch_failed", "attempt": cand.attempt_count + 1}, prev)  # fmt: skip
                continue
            assigned.append(cand.id)
        if assigned:
            log.info(
                "scheduler.assigned",
                project_id=project_id,
                tasks=assigned,
                in_flight=len(self.in_flight),
            )
        return assigned

    def _spec(self, project_id: str, cand: Candidate, run_id: str, epic_title: str) -> LaunchSpec:
        branch = f"ai/{_slugify(epic_title)}/{cand.issue_number or 0}-{_slugify(cand.title)}"
        task_json = {
            "task": {
                "id": cand.id, "title": cand.title, "spec": cand.spec, "kind": cand.kind,
                "role_required": cand.role_required, "owned_paths": list(cand.owned_paths),
                "issue_number": cand.issue_number, "epic_slug": _slugify(epic_title),
                "risk_tier": cand.risk_tier, "depends_on": list(cand.depends_on),
                "attempt": cand.attempt_count + 1, "max_attempts": cand.max_attempts,
            },
            "project_context": {
                "project_id": project_id, "goal_id": cand.goal_id, "repo": self._repo_url,
                "default_branch": self._default_branch,
            },
            "run_id": run_id,
            "agent_id": self._agent_id,
        }  # fmt: skip
        return LaunchSpec(
            task_id=cand.id, run_id=run_id, project_id=project_id, goal_id=cand.goal_id, branch=branch,
            repo_url=self._repo_url, task_json=task_json, timeout_min=self._timeout_min,
        )  # fmt: skip

    async def _publish(
        self, project_id: str, cand: Candidate, type_: EventType, entity: str, id_: str,
        payload: dict[str, object], causation: str | None,
    ) -> str:  # fmt: skip
        event = Event(
            project_id=project_id, actor=SCHEDULER_ACTOR, type=type_,
            subject=Subject(entity=entity, id=id_),  # type: ignore[arg-type]
            payload=payload, correlation_id=cand.goal_id, causation_id=causation,
        )  # fmt: skip
        async with self._factory() as session:
            out = await self._bus.publish(session, event)
            await session.commit()
        return out.id

    # ------------------------------------------------------------------ 루프
    async def run(
        self, group: str = "scheduler", consumer: str = "s1", project_id: str | None = None
    ) -> None:
        await self._bus.subscribe(group, self.handle, consumer=consumer, project_id=project_id)

    def stop(self) -> None:
        self._bus.stop()
