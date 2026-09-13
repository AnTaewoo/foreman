"""Scheduler 루프 (설계 §3.2): 이벤트 구독 → ready 판정 → task.assigned(+epic.activated) → 기동.

- 배정 순서: ``task.assigned`` 발행 → (Epic 첫 배정이면) ``epic.activated`` → ``launcher.launch``.
  기동 실패 → ``task.failed {reason: launch_failed, attempt}`` (D-28: assigned→ready/blocked).
- read-your-writes (B11): projection 반영 전이어도 in_flight/activated_epics memo로 중복 방지.
- ``run.finished``(또는 ``task.failed``/``task.blocked``)로 슬롯을 돌려받는다.
- ingest (D-26): 워커가 XADD한 **미서명** 이벤트는 ``chain.append_signed``로 DB에 넣고(event.id
  멱등) projection을 적용한다. relay가 보낸 서명된 이벤트는 projection consumer가 처리한다.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from ulid import ULID

from control_plane.events.bus import Delivery, EventBus
from control_plane.events.chain import append_signed
from control_plane.events.projection import Projection
from control_plane.events.schema import UNCHAINED, Actor, EntityType, Event, EventType, Subject
from control_plane.scheduler.launcher import LaunchError, LaunchSpec, WorkerLauncher
from control_plane.scheduler.queue import Candidate, load_project_tasks, pick_ready
from control_plane.store import models as m
from control_plane.store.enums import EpicStatus

log = structlog.get_logger(__name__)

SCHEDULER_ACTOR = Actor(type="system", id="scheduler")
TRIGGERS = frozenset(
    {
        EventType.TASK_CREATED,
        EventType.TASK_RETRIED,
        EventType.TASK_FAILED,
        EventType.TASK_BLOCKED,
        EventType.TASK_CANCELLED,
        EventType.RUN_FINISHED,
        EventType.PR_MERGED,
        EventType.GOAL_ACTIVATED,
    }
)
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
        repo_resolver: Callable[[str], str] | None = None,
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
        self._repo_resolver = (
            repo_resolver  # D-38: project.repo → 로컬 경로(RepoCache). None이면 그대로
        )
        self.in_flight: set[str] = set()  # task_id
        self.activated_epics: set[str] = set()
        self._run_to_task: dict[str, str] = {}
        self._task_run: dict[str, str] = {}  # task_id → 현재 run_id (오래된 run의 종료를 무시)

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
            signed = event
            if exists is None:
                signed = await append_signed(session, event)
                await session.commit()
        if self._projection is not None:
            # PC-6: task.assigned가 아직 projection 전이면 순서 역전 → 예외 대신 D-30 재시도 큐
            await self._projection.apply_or_retry(signed)

    def _release(self, event: Event) -> None:
        """슬롯 반환. 재배정 뒤에 도착한 **이전 run**의 종료 이벤트는 무시한다 (PC-4 기록)."""
        if event.type is EventType.RUN_FINISHED:
            run_id = event.subject.id
            task_id = self._run_to_task.pop(run_id, None) or str(event.payload.get("task_id", ""))
        else:
            task_id = event.subject.id
            run_id = str(event.payload.get("run_id") or "")
            if run_id:
                self._run_to_task.pop(run_id, None)
        if not task_id:
            return
        current = self._task_run.get(task_id)
        if run_id and current is not None and current != run_id:
            log.debug("scheduler.release_stale", task_id=task_id, run_id=run_id, current=current)
            return
        self._task_run.pop(task_id, None)
        self.in_flight.discard(task_id)

    # ------------------------------------------------------------------ 배정
    async def tick(self, project_id: str) -> list[str]:
        """배정 한 바퀴. 배정한 task id 목록."""
        free = self.max_workers - len(self.in_flight)
        async with self._factory() as session:
            tasks = await load_project_tasks(session, project_id)
            candidates = pick_ready(tasks, in_flight=self.in_flight, free_slots=free)
            project = await session.get(m.Project, project_id) if candidates else None
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
            self._task_run[cand.id] = run_id
            prev = await self._publish(
                project_id,
                cand,
                EventType.TASK_ASSIGNED,
                "task",
                cand.id,
                {"agent_id": self._agent_id, "run_id": run_id},
                None,
            )
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
            # P6.1 (리뷰 A3): repo/브랜치는 프로젝트 행에서, 생성자 값은 행이 없을 때의 폴백
            repo_name = project.repo_full_name if project is not None else self._repo_url
            spec = self._spec(
                project_id,
                cand,
                run_id,
                epic.title if epic is not None else cand.epic_id,
                repo_url=repo_name,
                default_branch=project.default_branch
                if project is not None
                else self._default_branch,
            )
            try:
                if self._repo_resolver is not None:  # D-38: clone 대상은 로컬 경로
                    try:
                        spec = replace(spec, repo_url=self._repo_resolver(repo_name))
                    except Exception as exc:
                        raise LaunchError(f"repo unavailable: {exc}") from exc
                await self._launcher.launch(spec)
            except LaunchError as exc:
                log.error("scheduler.launch_failed", task_id=cand.id, error=str(exc))
                self.in_flight.discard(cand.id)
                self._run_to_task.pop(run_id, None)
                self._task_run.pop(cand.id, None)
                await self._publish(
                    project_id,
                    cand,
                    EventType.TASK_FAILED,
                    "task",
                    cand.id,
                    {
                        "run_id": run_id,
                        "reason": "launch_failed",
                        "attempt": cand.attempt_count + 1,
                    },
                    prev,
                )
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

    def _spec(
        self,
        project_id: str,
        cand: Candidate,
        run_id: str,
        epic_title: str,
        *,
        repo_url: str | None = None,
        default_branch: str | None = None,
    ) -> LaunchSpec:
        repo_url = repo_url or self._repo_url
        default_branch = default_branch or self._default_branch
        branch = f"ai/{_slugify(epic_title)}/{cand.issue_number or 0}-{_slugify(cand.title)}"
        task_json = {
            "task": {
                "id": cand.id,
                "title": cand.title,
                "spec": cand.spec,
                "kind": cand.kind,
                "role_required": cand.role_required,
                "owned_paths": list(cand.owned_paths),
                "issue_number": cand.issue_number,
                "epic_slug": _slugify(epic_title),
                "risk_tier": cand.risk_tier,
                "depends_on": list(cand.depends_on),
                "attempt": cand.attempt_count + 1,
                "max_attempts": cand.max_attempts,
            },
            "project_context": {
                "project_id": project_id,
                "goal_id": cand.goal_id,
                "repo": repo_url,
                "default_branch": default_branch,
            },
            "run_id": run_id,
            "agent_id": self._agent_id,
        }
        return LaunchSpec(
            task_id=cand.id,
            run_id=run_id,
            project_id=project_id,
            goal_id=cand.goal_id,
            branch=branch,
            repo_url=repo_url,
            task_json=task_json,
            timeout_min=self._timeout_min,
        )

    async def _publish(
        self,
        project_id: str,
        cand: Candidate,
        type_: EventType,
        entity: EntityType,
        id_: str,
        payload: dict[str, object],
        causation: str | None,
    ) -> str:
        event = Event(
            project_id=project_id,
            actor=SCHEDULER_ACTOR,
            type=type_,
            subject=Subject(entity=entity, id=id_),
            payload=payload,
            correlation_id=cand.goal_id,
            causation_id=causation,
        )
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
