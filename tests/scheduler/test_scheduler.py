"""P4.5 — Scheduler (red a~i): ready 판정, 위상/사이클, 겹침, 슬롯, launcher, ingest, epic memo."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import EventBus
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.scheduler.graph import SchedulerError, topo_order
from control_plane.scheduler.launcher import FakeLauncher, LaunchError, LaunchSpec
from control_plane.scheduler.scheduler import Scheduler
from control_plane.store import models as m
from control_plane.store.enums import EpicStatus, TaskStatus

E = EventType
PID, GID = "P1", "G1"


def ev(
    type_: EventType, entity: str, id_: str, payload: dict[str, Any], *, actor: Actor | None = None
) -> Event:
    return Event(
        project_id=PID,
        actor=actor or Actor(type="system", id="test"),
        type=type_,
        subject=Subject(entity=entity, id=id_),  # type: ignore[arg-type]
        payload=payload,
        correlation_id=GID,
        causation_id=None,
    )


def task_created(
    tid: str, deps: list[str], paths: list[str], epic: str = "E1", issue: int = 10
) -> Event:
    return ev(
        E.TASK_CREATED,
        "task",
        tid,
        {
            "epic_id": epic,
            "epic_title": epic,
            "title": f"task {tid}",
            "spec": "s",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": deps,
            "owned_paths": paths,
            "risk_tier": "T1",
            "issue_number": issue,
            "issue_url": None,
        },
    )


BOOTSTRAP = [
    ev(
        E.PROJECT_CREATED,
        "project",
        PID,
        {"name": "p", "repo": "org/demo", "default_branch": "main"},
    ),
    ev(E.GOAL_CREATED, "goal", GID, {"title": "g", "description": "d"}),
    ev(E.GOAL_PLAN_PROPOSED, "goal", GID, {"plan_discussion_number": 1, "revision": 1}),
    ev(E.GOAL_ACTIVATED, "goal", GID, {}),
    ev(
        E.EPIC_CREATED,
        "epic",
        "E1",
        {"goal_id": GID, "title": "E1", "order": 1, "milestone_number": 1},
    ),
    ev(
        E.EPIC_CREATED,
        "epic",
        "E2",
        {"goal_id": GID, "title": "E2", "order": 2, "milestone_number": 2},
    ),
]


class Harness:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        redis: Redis,
        *,
        max_workers: int = 4,
        launcher: FakeLauncher | None = None,
    ) -> None:
        self.factory = factory
        self.bus = EventBus(redis)
        self.relay = OutboxRelay(factory, redis, batch=100)
        self.projection = Projection(factory, self.bus)
        self.launcher = launcher or FakeLauncher()
        self.scheduler = Scheduler(
            factory,
            self.bus,
            self.launcher,
            projection=self.projection,
            max_workers=max_workers,
            repo_url="/tmp/remote.git",
            timeout_min=45,
        )

    async def publish(self, *events: Event) -> list[Event]:
        out = []
        async with self.factory() as s:
            for e in events:
                out.append(await self.bus.publish(s, e))
            await s.commit()
        return out

    async def pump(self) -> list[str]:
        """relay → projection+scheduler → 새 outbox 행이 생기면 다시 relay … 조용해질 때까지."""
        seen: list[str] = []

        async def both(d: Any) -> None:
            await self.projection.handle(d)
            await self.scheduler.handle(d)
            seen.append(d.event.type.value)

        for _ in range(20):
            relayed = 0
            while (n := await self.relay.relay_once()) > 0:
                relayed += n
            consumed = await self.bus.poll_once("sched", both, consumer="t", project_id=PID)
            if relayed == 0 and consumed == 0:
                break
        await self.projection.apply_retries(PID, now=datetime.now(UTC) + timedelta(hours=3))
        return seen

    async def task(self, tid: str) -> m.Task:
        async with self.factory() as s:
            t = await s.get(m.Task, tid)
            assert t is not None
            return t

    async def events(self, type_: str) -> list[m.Event]:
        async with self.factory() as s:
            return list(
                (
                    await s.execute(
                        select(m.Event)
                        .where(m.Event.project_id == PID, m.Event.type == type_)
                        .order_by(m.Event.seq)
                    )
                )
                .scalars()
                .all()
            )


# (b) 위상 정렬 / 사이클
def test_topo_order_and_cycle() -> None:
    assert topo_order({"D": ["B", "C"], "A": [], "C": ["A"], "B": ["A"]}) == ["A", "C", "B", "D"]
    with pytest.raises(SchedulerError, match="cycle"):
        topo_order({"A": ["B"], "B": ["A"]})


# (a)(e)(h) ready ∧ deps done만 배정, assigned 후 launch, 같은 Epic 두 Task → epic.activated 1건
async def test_assigns_ready_tasks_and_launches(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    h = Harness(factory, redis)
    await h.publish(
        *BOOTSTRAP,
        task_created("T1", [], ["src/a/**"]),
        task_created("T2", ["T1"], ["src/b/**"]),
        task_created("T3", [], ["src/c/**"]),
    )
    await h.pump()
    assigned = await h.events("task.assigned")
    assert [e.subject_id for e in assigned] == ["T1", "T3"]  # T2는 T1 대기
    assert (await h.task("T1")).status is TaskStatus.ASSIGNED and (
        await h.task("T2")
    ).status is TaskStatus.READY
    activated = await h.events("epic.activated")
    assert (
        len(activated) == 1 and activated[0].subject_id == "E1"
    )  # 두 Task 연속 배정에도 1건 (B11)
    async with factory() as s:
        epic = await s.get(m.Epic, "E1")
        assert epic is not None and epic.status is EpicStatus.ACTIVE
    specs = h.launcher.specs
    assert [sp.task_id for sp in specs] == ["T1", "T3"]
    spec: LaunchSpec = specs[0]
    assert spec.run_id == assigned[0].payload["run_id"] and spec.branch.startswith(
        "ai/e1/10-task-t1"
    )
    assert (
        spec.task_json["task"]["owned_paths"] == ["src/a/**"]
        and spec.task_json["run_id"] == spec.run_id
    )
    # P6.1 (리뷰 A3): repo는 projects 행(BOOTSTRAP의 org/demo)에서, 생성자 값은 폴백
    assert spec.repo_url == "org/demo" and spec.timeout_min == 45
    # task.assigned가 launch보다 먼저 (이벤트 seq < launch 순서 기록)
    assert h.launcher.order[0] == ("assigned_seen", "T1") or h.launcher.order[0][0] == "launch"


# (c) owned_paths 겹침 동시 배정 금지 → 하나만, 나머지는 대기
async def test_overlapping_owned_paths_not_concurrent(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    h = Harness(factory, redis)
    await h.publish(
        *BOOTSTRAP,
        task_created("T1", [], ["src/app/**"]),
        task_created("T2", [], ["src/app/users.py"]),
    )
    await h.pump()
    assert [e.subject_id for e in await h.events("task.assigned")] == ["T1"]
    assert (await h.task("T2")).status is TaskStatus.READY


# (d) max_workers 초과 대기, (f) run.finished → 슬롯 반환 → 다음 배정
async def test_slots_and_release(factory: async_sessionmaker[AsyncSession], redis: Redis) -> None:
    h = Harness(factory, redis, max_workers=1)
    await h.publish(
        *BOOTSTRAP, task_created("T1", [], ["src/a/**"]), task_created("T2", [], ["src/b/**"])
    )
    await h.pump()
    assert [e.subject_id for e in await h.events("task.assigned")] == ["T1"]
    run_id = h.launcher.specs[0].run_id
    # 워커가 실행을 마쳤다 (started → run.started → completed → run.finished)
    await h.publish(
        ev(
            E.TASK_STARTED,
            "task",
            "T1",
            {"run_id": run_id},
            actor=Actor(type="agent", id="coding-1"),
        ),
        ev(
            E.RUN_STARTED, "run", run_id, {"task_id": "T1", "agent_id": "coding-1", "model": "fake"}
        ),
        ev(E.TASK_COMPLETED, "task", "T1", {"run_id": run_id}),
        ev(
            E.RUN_FINISHED,
            "run",
            run_id,
            {
                "outcome": "success",
                "agent_outcome": "done",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.0,
                "duration_s": 1.0,
                "error": None,
            },
        ),
    )
    await h.pump()
    assert [e.subject_id for e in await h.events("task.assigned")] == ["T1", "T2"]
    assert h.scheduler.in_flight == {"T2"}


# (i) 기동 실패 → task.failed(launch_failed): 트리거라 ready로 돌아온 Task를 다시 배정하고,
#     attempt가 max(3)에 닿으면 blocked — 더 이상 배정하지 않는다 (D-28)
async def test_launch_failure(factory: async_sessionmaker[AsyncSession], redis: Redis) -> None:
    h = Harness(factory, redis, launcher=FakeLauncher(fail=True))
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    failed = await h.events("task.failed")
    assert [e.payload["attempt"] for e in failed] == [1, 2, 3]
    assert all(e.payload["reason"] == "launch_failed" for e in failed)
    t = await h.task("T1")
    assert t.status is TaskStatus.BLOCKED and t.attempt_count == 3
    assert h.scheduler.in_flight == set() and len(await h.events("task.assigned")) == 3
    with pytest.raises(LaunchError):
        await FakeLauncher(fail=True).launch(
            LaunchSpec(
                task_id="x",
                run_id="r",
                project_id=PID,
                goal_id=GID,
                branch="ai/e/1-x",
                repo_url="/r",
                task_json={},
                timeout_min=1,
            )
        )


# (g) ingest: 워커 미서명 이벤트 → append_signed(멱등) + projection 적용; tool_called는 tool_calls로
async def test_ingest_worker_events(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from worker.publish import RedisPublisher

    h = Harness(factory, redis)
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    run_id = h.launcher.specs[0].run_id
    worker_pub = RedisPublisher(redis)
    started = await worker_pub(
        ev(
            E.TASK_STARTED,
            "task",
            "T1",
            {"run_id": run_id},
            actor=Actor(type="agent", id="coding-1"),
        )
    )
    await worker_pub(
        ev(E.RUN_STARTED, "run", run_id, {"task_id": "T1", "agent_id": "coding-1", "model": "fake"})
    )
    await worker_pub(
        ev(E.RUN_TOOL_CALLED, "run", run_id, {"tool": "fs.read", "args_digest": "ab" * 32})
    )
    await worker_pub(started)  # 같은 이벤트 두 번 (at-least-once)
    await h.pump()
    rows = await h.events("task.started")
    assert len(rows) == 1 and rows[0].signature is not None and rows[0].projected_at is not None
    assert (await h.task("T1")).status is TaskStatus.RUNNING
    async with factory() as s:
        tools = (
            (await s.execute(select(m.ToolCall).where(m.ToolCall.run_id == run_id))).scalars().all()
        )
        assert len(tools) == 1
        run = await s.get(m.Run, run_id)
        assert run is not None and run.tool_call_count == 1
        from control_plane.events.chain import verify_chain_db

        assert await verify_chain_db(s, PID) is True


# PC-4 (기록): task.failed(attempt<max) 재배정 뒤 **이전 run**의 run.finished가 와도 슬롯을 돌려주면
# 안 된다 — 돌려주면 DB가 아직 ready(새 task.assigned 미반영)라 세 번째 배정이 난다
async def test_stale_run_finished_does_not_release_new_run(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from worker.publish import RedisPublisher

    h = Harness(factory, redis, max_workers=1)
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    run1 = h.launcher.specs[0].run_id
    worker = RedisPublisher(redis)
    agent = Actor(type="agent", id="coding-1")
    await worker(ev(E.TASK_STARTED, "task", "T1", {"run_id": run1}, actor=agent))
    await worker(
        ev(E.RUN_STARTED, "run", run1, {"task_id": "T1", "agent_id": "coding-1", "model": "fake"})
    )
    await worker(
        ev(
            E.TASK_FAILED,
            "task",
            "T1",
            {"run_id": run1, "reason": "scope_violation", "attempt": 1, "files": ["x"]},
            actor=agent,
        )
    )
    # 워커의 마지막 이벤트(run.finished)는 재배정 뒤에 소비된다
    await worker(
        ev(
            E.RUN_FINISHED,
            "run",
            run1,
            {
                "outcome": "failed",
                "agent_outcome": "failed",
                "tokens_in": 1,
                "tokens_out": 1,
                "cost_usd": 0.0,
                "duration_s": 1.0,
                "error": "scope",
            },
            actor=agent,
        )
    )
    await h.pump()
    assigned = await h.events("task.assigned")
    assert [e.subject_id for e in assigned] == ["T1", "T1"]  # 재배정 1회뿐
    run2 = h.launcher.specs[1].run_id
    assert h.scheduler.in_flight == {"T1"} and run2 != run1
    assert (await h.task("T1")).status is TaskStatus.ASSIGNED
    assert not any(e.projection_error for e in await h.events("task.assigned"))


# P6.6 (D-38): Scheduler는 repo_resolver로 프로젝트 repo를 로컬 경로로 바꾼다; 실패면 launch_failed
async def test_repo_resolver_and_failure(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from control_plane.repo_cache import RepoUnavailable

    calls: list[str] = []

    def resolver(repo: str) -> str:
        calls.append(repo)
        if repo == "org/demo":
            return "/cache/org/demo"
        raise RepoUnavailable(repo, "nope")

    h = Harness(factory, redis)
    h.scheduler._repo_resolver = resolver  # Harness는 생성자 인자를 안 받으므로 직접 주입
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    assert h.launcher.specs[0].repo_url == "/cache/org/demo" and calls == ["org/demo"]
    assert (
        h.launcher.specs[0].task_json["project_context"]["repo"] == "org/demo"
    )  # 워커 표시용 이름은 원본


# PC-6 발견: ingest의 워커 이벤트가 순서 역전(task.assigned 미반영)이면 예외 대신 D-30 재시도 큐
async def test_ingest_out_of_order_goes_to_retry_queue(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from datetime import UTC, datetime, timedelta

    from control_plane.events.bus import retry_stream_key

    h = Harness(factory, redis)
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    run_id = h.launcher.specs[
        0
    ].run_id  # task.assigned는 outbox에 있고 아직 projection 전일 수 있다
    # 워커가 바로 task.started를 XADD (미서명) → 아직 ready인 Task에 running 전이 = 순서 역전
    started = ev(
        E.TASK_STARTED, "task", "T1", {"run_id": run_id}, actor=Actor(type="agent", id="coding-1")
    )
    async with h.factory() as s:  # projection이 task.assigned를 못 본 상태를 강제: 직접 ready로
        t = await s.get(m.Task, "T1")
        assert t is not None
        t.status = TaskStatus.READY
        await s.commit()
    await h.scheduler.ingest(started)  # 예외 없이 돌아와야 한다
    assert await redis.exists(retry_stream_key(PID))  # 재시도 큐에 들어갔다
    assert (await h.task("T1")).status is TaskStatus.READY
    async with h.factory() as s:  # task.assigned 반영 후 재시도가 통과
        t = await s.get(m.Task, "T1")
        assert t is not None
        t.status = TaskStatus.ASSIGNED
        await s.commit()
    applied = await h.projection.apply_retries(PID, now=datetime.now(UTC) + timedelta(hours=3))
    assert applied == 1 and (await h.task("T1")).status is TaskStatus.RUNNING


# ------------------------------------------ P8.3 (D-44, F-5/F-5c) 죽은 워커 정리
# (b) launcher.is_alive — Fake는 dead 집합으로 제어
async def test_reap_dead_worker_publishes_failed_and_reassigns(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from datetime import UTC, datetime

    h = Harness(factory, redis, max_workers=1)
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    assert h.scheduler.in_flight == {"T1"}
    run1 = h.launcher.specs[0].run_id
    worker1 = f"fake-{run1}"
    # 워커가 아무 이벤트도 못 내고 죽음 (F-5a 상황)
    h.launcher.dead.add(worker1)
    reaped = await h.scheduler.reap(now=datetime.now(UTC))
    assert reaped == [("T1", run1, "worker_died")]
    await h.pump()
    failed = await h.events("task.failed")
    assert failed[0].payload == {"run_id": run1, "reason": "worker_died", "attempt": 1}
    assert failed[0].actor_id == "scheduler"
    fin = await h.events("run.finished")
    assert fin[0].subject_id == run1 and fin[0].payload["outcome"] == "failed"
    assert fin[0].payload["error"] == "worker died"
    # 슬롯 반환 → attempt<max이므로 재배정(새 run)
    assert [e.subject_id for e in await h.events("task.assigned")] == ["T1", "T1"]
    assert h.scheduler.in_flight == {"T1"} and h.launcher.specs[1].run_id != run1


# (c) 타임아웃: timeout_min + 5분 지나면 reason=timeout; 살아 있는 워커는 건드리지 않는다
async def test_reap_timeout_and_alive(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from datetime import UTC, datetime, timedelta

    h = Harness(factory, redis, max_workers=2)
    await h.publish(
        *BOOTSTRAP, task_created("T1", [], ["src/a/**"]), task_created("T2", [], ["src/b/**"])
    )
    await h.pump()
    assert await h.scheduler.reap(now=datetime.now(UTC)) == []  # 둘 다 살아 있음
    late = datetime.now(UTC) + timedelta(minutes=45 + 6)
    reaped = await h.scheduler.reap(now=late)
    assert sorted(r[0] for r in reaped) == ["T1", "T2"] and all(r[2] == "timeout" for r in reaped)
    await h.pump()
    assert all(e.payload["reason"] == "timeout" for e in await h.events("task.failed"))
    assert all(e.payload["outcome"] == "timeout" for e in await h.events("run.finished"))


# (d) 기동 시 DB에 assigned/running인데 in_flight에 없는 Task(이전 프로세스 잔재) → worker_died 처리
async def test_recover_orphans_on_startup(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    h = Harness(factory, redis, max_workers=1)
    await h.publish(*BOOTSTRAP, task_created("T1", [], ["src/a/**"]))
    await h.pump()
    run1 = h.launcher.specs[0].run_id
    await h.publish(
        ev(
            E.TASK_STARTED, "task", "T1", {"run_id": run1}, actor=Actor(type="agent", id="coding-1")
        ),
        ev(E.RUN_STARTED, "run", run1, {"task_id": "T1", "agent_id": "coding-1", "model": "fake"}),
    )
    await h.pump()
    assert (await h.task("T1")).status is TaskStatus.RUNNING
    # "새 프로세스": in_flight 비어 있는 새 Scheduler
    fresh = Harness(factory, redis, max_workers=1)
    orphans = await fresh.scheduler.recover_orphans()
    assert orphans == [("T1", run1)]
    await fresh.pump()
    failed = await fresh.events("task.failed")
    assert failed[-1].payload["reason"] == "worker_died" and failed[-1].payload["run_id"] == run1
    fin = [e for e in await fresh.events("run.finished") if e.subject_id == run1]
    assert len(fin) == 1
    assert (await fresh.task("T1")).status in (TaskStatus.READY, TaskStatus.ASSIGNED)
