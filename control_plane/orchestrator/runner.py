"""Goal 실행기 (설계 §3.3, D-13): ``goal.created`` → 백그라운드로 Orchestrator 그래프
(thread_id = goal_id) → Plan 승인 interrupt에서 대기 → 웹훅 ``/approve``·``/reject`` →
``Command(resume)`` → decompose → emit(Issue) 까지.

- 이벤트는 ``EventBus.publish``(outbox)로만 나간다. Goal 제목/설명은 projection 지연과 무관하게
  ``events`` 테이블의 ``goal.created`` 행에서 읽는다.
- repo 입력은 로컬 경로만 (D-11): ``repo_path_for(repo_full_name)``가 경로를 정한다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agents.llm.base import ModelProvider
from agents.llm.router import CURRENT_PROFILE
from control_plane.events.bus import EventBus
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.orchestrator import emit as emit_mod
from control_plane.orchestrator.graph import (
    DiscussionsLike,
    Emit,
    OrchestratorDeps,
    build_graph,
    open_postgres_checkpointer,
)
from control_plane.orchestrator.repo_layout import nested_project_root
from control_plane.orchestrator.state import OrchestratorState, initial_state
from control_plane.repo_cache import RepoUnavailable
from control_plane.store import models as m
from control_plane.store.session import get_session
from github_adapter.protocol import GitHubClient

log = structlog.get_logger(__name__)

RepoPathFor = Callable[[str], Path]


def default_repo_path(repo: str) -> Path:
    """``repo``가 존재하는 로컬 경로면 그대로, 아니면 ``repos/<name>`` (로컬 클론 디렉토리)."""
    p = Path(repo)
    return p if p.exists() else Path("repos") / repo.rsplit("/", 1)[-1]


@dataclass
class Waiting:
    project_id: str
    plan_discussion_number: int | None
    plan_revision: int


class GoalRunner:
    def __init__(
        self,
        *,
        factory: async_sessionmaker[AsyncSession],
        bus: EventBus,
        provider: ModelProvider,
        github: GitHubClient,
        discussions: DiscussionsLike,
        checkpointer: BaseCheckpointSaver[Any] | None = None,
        model: str | None = None,
        repo_path_for: RepoPathFor | None = None,
        emit: Emit | None = None,
        min_tasks: int = 3,  # X.2: 운영 기본 3, Fake 스크립트 테스트는 1
        token_provider: Any = None,  # Any: RepoRouter(prepare(repo)) — D-41, 실 모드 clone용
        repo_cache: Any = None,  # Any: RepoCache — 관측·테스트용 참조
    ) -> None:
        self._factory = factory
        self._bus = bus
        self._model = model
        self._repo_path_for = repo_path_for or default_repo_path
        self.token_provider = token_provider
        self.repo_cache = repo_cache
        self._checkpointer: BaseCheckpointSaver[Any] = checkpointer or MemorySaver()
        self._pg_cm: Any = None  # Any: AsyncPostgresSaver 컨텍스트 매니저

        async def publish(event: Event) -> Event:
            async with get_session(factory) as s:
                return await bus.publish(s, event)

        async def do_emit(state: OrchestratorState) -> dict[str, Any]:
            return await emit_mod.emit(state, github=github, publish=publish)

        self.publish = publish
        self._deps = OrchestratorDeps(
            provider=provider,
            github=github,
            discussions=discussions,
            publish=publish,
            emit=emit or do_emit,
            model=model,
            min_tasks=min_tasks,
        )
        self._graph: Any = None  # Any: CompiledStateGraph, 체크포인터 확정 후 lazy
        self._running: dict[str, asyncio.Task[None]] = {}
        self._waiting: dict[str, Waiting] = {}
        self.errors: dict[str, str] = {}
        self.restore_skipped: list[str] = []  # P6.2: 체크포인트가 없어 복원 못 한 Goal

    # ------------------------------------------------------------------ 수명
    async def startup(self, database_url: str) -> None:
        """Postgres면 AsyncPostgresSaver를 연다(sqlite면 no-op). 뒤이어 대기 목록 복원(P6.2)."""
        if self._pg_cm is None and not database_url.startswith("sqlite"):
            if isinstance(self._checkpointer, MemorySaver):
                self._pg_cm = open_postgres_checkpointer(_Db(database_url))
                saver = await self._pg_cm.__aenter__()
                await saver.setup()
                self._checkpointer = saver
                self._graph = None
        await self.restore_waiting()

    async def restore_waiting(self) -> list[str]:
        """projection의 ``awaiting_plan_approval`` Goal 중 체크포인트가 있는 것을 ``_waiting``에.

        API 재시작 뒤에도 ``/approve``가 동작하게 한다(리뷰 A6). 스레드가 없는 Goal(다른 saver)은
        ``restore_skipped``에 남기고 경고만 — 그 Goal은 다시 ``POST /goals``로 만들어야 한다.
        """
        from control_plane.store.enums import GoalStatus

        async with self._factory() as s:
            rows = (
                (
                    await s.execute(
                        select(m.Goal).where(m.Goal.status == GoalStatus.AWAITING_PLAN_APPROVAL)
                    )
                )
                .scalars()
                .all()
            )
        restored: list[str] = []
        for goal in rows:
            if goal.id in self._waiting:
                continue
            tup = await self._checkpointer.aget_tuple(self._cfg(goal.id))
            if tup is None:
                self.restore_skipped.append(goal.id)
                log.warning(
                    "runner.restore_skipped", goal_id=goal.id, reason="no checkpoint thread"
                )
                continue
            self._waiting[goal.id] = Waiting(
                project_id=goal.project_id,
                plan_discussion_number=goal.plan_discussion_id,
                plan_revision=goal.plan_revision,
            )
            restored.append(goal.id)
        if restored:
            log.info("runner.restored_waiting", goals=restored)
        return restored

    async def shutdown(self) -> None:
        for t in list(self._running.values()):
            t.cancel()
        if self._pg_cm is not None:
            await self._pg_cm.__aexit__(None, None, None)
            self._pg_cm = None

    def graph(self) -> Any:
        if self._graph is None:
            self._graph = build_graph(self._deps, checkpointer=self._checkpointer)
        return self._graph

    # ------------------------------------------------------------------ 실행
    async def start(self, project_id: str, goal_id: str) -> None:
        """``AppState.on_goal_created`` 훅: 백그라운드 Task로 실행하고 바로 돌아온다."""
        self._spawn(goal_id, self._run(project_id, goal_id))

    def _spawn(self, goal_id: str, coro: Any) -> None:
        task = asyncio.create_task(coro)
        self._running[goal_id] = task
        task.add_done_callback(lambda t: self._running.pop(goal_id, None))

    async def _run(self, project_id: str, goal_id: str) -> None:
        try:
            async with self._factory() as s:
                project = await s.get(m.Project, project_id)
                created = await s.scalar(
                    select(m.Event).where(
                        m.Event.type == "goal.created", m.Event.subject_id == goal_id
                    )
                )
                repo_full_name = project.repo_full_name if project is not None else None
                if repo_full_name is None:  # D-46: projection 전이면 project.created (P9.6)
                    pc = await s.scalar(
                        select(m.Event).where(
                            m.Event.type == "project.created", m.Event.subject_id == project_id
                        )
                    )
                    if pc is not None:
                        repo_full_name = str(pc.payload.get("repo", "")) or None
            if repo_full_name is None or created is None:
                raise RuntimeError(f"project {project_id} or goal.created {goal_id} not found")
            payload = created.payload
            CURRENT_PROFILE.set(str(payload.get("llm")) if payload.get("llm") else None)  # D-57
            if self.token_provider is not None:  # D-41: 동기 clone 전에 토큰을 신선하게 (PC-7 발견)
                await self.token_provider.prepare(repo_full_name)  # P9.9: 그 repo의 installation
            repo_path = await asyncio.to_thread(self._repo_path_for, repo_full_name)
            nested = await asyncio.to_thread(nested_project_root, Path(repo_path))
            if nested is not None:  # P9.25: Plan(LLM) 전에 멈춘다 — 경로·테스트가 전부 어긋난다
                await self._cancel_nested(project_id, goal_id, nested)
                return
            state = initial_state(
                project_id=project_id,
                goal_id=goal_id,
                goal_title=str(payload.get("title", "")),
                goal_description=str(payload.get("description", "")),
                repo_path=str(repo_path),
                repo_full_name=repo_full_name,
                model=self._model,
                last_event_id=created.id,
            )
            out = await self.graph().ainvoke(state, self._cfg(goal_id))
            self._after_invoke(project_id, goal_id, out)
        except RepoUnavailable as exc:
            # §6.1에 draft→blocked가 없으므로 Goal을 종료(cancelled)하고 사유를 남긴다 (P6.6 기록)
            log.error("runner.repo_unavailable", goal_id=goal_id, error=str(exc))
            self.errors[goal_id] = f"repo_unavailable: {exc.detail}"
            await self.publish(
                Event(
                    project_id=project_id,
                    actor=Actor(type="system", id="orchestrator"),
                    type=EventType.GOAL_CANCELLED,
                    subject=Subject(entity="goal", id=goal_id),
                    payload={"reason": f"repo_unavailable: {exc.detail}", "by": "system"},
                    correlation_id=goal_id,
                    causation_id=None,
                )
            )
        except Exception as exc:
            # 예상 못한 오류도 Goal을 draft에 남기지 않는다 — 데모 한도가 영원히 막힌다 (P9.6 발견)
            log.error("runner.failed", goal_id=goal_id, error=repr(exc))
            self.errors[goal_id] = repr(exc)
            reason = f"runner_error: {type(exc).__name__}: {exc}"[:500]
            try:
                await self.publish(
                    Event(
                        project_id=project_id,
                        actor=Actor(type="system", id="orchestrator"),
                        type=EventType.GOAL_CANCELLED,
                        subject=Subject(entity="goal", id=goal_id),
                        payload={"reason": reason, "by": "system"},
                        correlation_id=goal_id,
                        causation_id=None,
                    )
                )
            except Exception as pub_exc:  # 취소 발행마저 실패하면 로그만 (DB 다운 등)
                log.error("runner.cancel_failed", goal_id=goal_id, error=repr(pub_exc))

    async def _cancel_nested(self, project_id: str, goal_id: str, folder: str) -> None:
        reason = (
            f"repo_subfolder: 프로젝트 파일이 '{folder}/' 폴더 안에 있습니다. Foreman은 repo "
            "루트를 프로젝트 루트로 씁니다 — 파일을 루트로 옮긴 뒤 새 Goal을 만들어 주세요."
        )
        log.warning("runner.repo_subfolder", goal_id=goal_id, folder=folder)
        self.errors[goal_id] = reason
        await self.publish(
            Event(
                project_id=project_id,
                actor=Actor(type="system", id="orchestrator"),
                type=EventType.GOAL_CANCELLED,
                subject=Subject(entity="goal", id=goal_id),
                payload={"reason": reason, "by": "system"},
                correlation_id=goal_id,
                causation_id=None,
            )
        )

    def _after_invoke(self, project_id: str, goal_id: str, out: dict[str, Any]) -> None:
        if "__interrupt__" in out:
            self._waiting[goal_id] = Waiting(
                project_id=project_id,
                plan_discussion_number=out.get("plan_discussion_number"),
                plan_revision=int(out.get("plan_revision") or 1),
            )
            log.info("runner.waiting_approval", goal_id=goal_id)
            return
        log.info("runner.finished", goal_id=goal_id, tasks=len(out.get("tasks") or []))

    # ------------------------------------------------------------------ 재개
    def is_waiting(self, goal_id: str) -> bool:
        return goal_id in self._waiting

    def waiting(self, project_id: str) -> dict[str, Waiting]:
        return {g: w for g, w in self._waiting.items() if w.project_id == project_id}

    async def resume(self, goal_id: str, *, approved: bool, by: str, reason: str = "") -> None:
        """interrupt 재개를 백그라운드로. 대기 중이 아니면 KeyError."""
        waiting = self._waiting.pop(goal_id)
        self._spawn(goal_id, self._resume(waiting.project_id, goal_id, approved, by, reason))

    async def _resume(
        self, project_id: str, goal_id: str, approved: bool, by: str, reason: str
    ) -> None:
        try:
            async with self._factory() as s:  # D-57: 재개 시에도 Goal의 프로파일로
                goal = await s.get(m.Goal, goal_id)
            CURRENT_PROFILE.set(goal.llm_profile if goal is not None else None)
            out = await self.graph().ainvoke(
                Command(resume={"approved": approved, "by": by, "reason": reason}),
                self._cfg(goal_id),
            )
            self._after_invoke(project_id, goal_id, out)
        except Exception as exc:
            log.error("runner.resume_failed", goal_id=goal_id, error=repr(exc))
            self.errors[goal_id] = repr(exc)

    async def wait_idle(self) -> None:
        """테스트/스크립트용: 진행 중인 백그라운드 실행이 끝날 때까지."""
        while self._running:
            await asyncio.gather(*list(self._running.values()), return_exceptions=True)

    @staticmethod
    def _cfg(goal_id: str) -> RunnableConfig:
        return RunnableConfig(configurable={"thread_id": goal_id})


@dataclass
class _Db:  # graph._DbSettings 프로토콜(쓰기 가능한 속성) 충족
    database_url: str
