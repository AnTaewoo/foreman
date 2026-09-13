"""PC-1 — 이벤트 한 바퀴 (진짜 Postgres + Redis).

project → goal → plan_proposed → activated → epic → task×3 → Task별
assigned → started → run.started → run.tool_called×2 → completed → run.finished → pr.merged
발행 → outbox relay → projection consumer → tasks 3행 done → verify_chain_db True
→ TRUNCATE(tasks, runs, epics, goals, tool_calls) → replay(seq 순) + apply(force=True) → 동일.

실행: HITL_DATABASE_URL=postgresql+asyncpg://… uv run python scripts/pc1_roundtrip.py
종료 코드 0 = pass.
"""

from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime, timedelta
from typing import Any

from redis.asyncio import Redis
from sqlalchemy import select, text
from ulid import ULID

from control_plane.config import Settings
from control_plane.events.bus import EventBus
from control_plane.events.chain import verify_chain_db
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.store import models as m
from control_plane.store import session as sess

E = EventType


def new_id() -> str:
    return str(ULID())


def build_sequence(pid: str, gid: str) -> list[Event]:
    """causation 체인이 이어지는 이벤트 목록. correlation은 Goal 스코프면 gid, 아니면 pid (D-25)."""
    events: list[Event] = []
    prev: str | None = None

    def emit(
        type_: EventType, entity: str, id_: str, payload: dict[str, Any], *, corr: str = gid
    ) -> Event:
        nonlocal prev
        e = Event(
            project_id=pid,
            actor=Actor(type="system", id="pc1"),
            type=type_,
            subject=Subject(entity=entity, id=id_),  # type: ignore[arg-type]
            payload=payload,
            correlation_id=corr,
            causation_id=prev,
        )
        if type_ is not E.RUN_TOOL_CALLED:  # 체인 밖 이벤트는 원인으로 삼지 않는다
            prev = e.id
        events.append(e)
        return e

    eid = new_id()
    emit(
        E.PROJECT_CREATED,
        "project",
        pid,
        {"name": "pc1", "repo": "org/pc1", "default_branch": "main"},
        corr=pid,
    )
    emit(E.GOAL_CREATED, "goal", gid, {"title": "PC-1 goal", "description": "roundtrip"})
    emit(E.GOAL_PLAN_PROPOSED, "goal", gid, {"plan_discussion_number": 1, "revision": 1})
    emit(E.GOAL_ACTIVATED, "goal", gid, {})
    emit(
        E.EPIC_CREATED,
        "epic",
        eid,
        {"goal_id": gid, "title": "epic", "order": 1, "milestone_number": 1},
    )
    tids = [new_id() for _ in range(3)]
    for i, tid in enumerate(tids, start=1):
        emit(
            E.TASK_CREATED,
            "task",
            tid,
            {
                "epic_id": eid,
                "epic_title": "epic",
                "title": f"task {i}",
                "spec": "do it",
                "kind": "feature",
                "role_required": "coding",
                "depends_on": [],
                "owned_paths": [f"src/t{i}/**"],
                "risk_tier": "T1",
                "issue_number": 100 + i,
                "issue_url": f"https://x/issues/{100 + i}",
            },
        )
    for i, tid in enumerate(tids, start=1):
        rid = new_id()
        emit(E.TASK_ASSIGNED, "task", tid, {"agent_id": "coding-1", "run_id": rid})
        if i == 1:
            emit(E.EPIC_ACTIVATED, "epic", eid, {})
        emit(E.TASK_STARTED, "task", tid, {"run_id": rid})
        emit(E.RUN_STARTED, "run", rid, {"task_id": tid, "agent_id": "coding-1", "model": "fake"})
        emit(
            E.RUN_TOOL_CALLED,
            "run",
            rid,
            {"tool": "fs.read", "args_digest": "ab" * 32, "duration_ms": 1},
        )
        emit(
            E.RUN_TOOL_CALLED,
            "run",
            rid,
            {"tool": "shell", "args_digest": "cd" * 32, "duration_ms": 900},
        )
        emit(
            E.PR_OPENED,
            "pr",
            str(200 + i),
            {
                "task_id": tid,
                "run_id": rid,
                "pr_number": 200 + i,
                "head": f"ai/epic/{100 + i}-task-{i}",
                "base": "main",
            },
        )
        emit(E.TASK_COMPLETED, "task", tid, {"run_id": rid, "pr_number": 200 + i})
        emit(
            E.RUN_FINISHED,
            "run",
            rid,
            {
                "outcome": "success",
                "agent_outcome": "done",
                "tokens_in": 10 * i,
                "tokens_out": 5 * i,
                "cost_usd": 0.001 * i,
                "duration_s": 3.0,
                "error": None,
            },
        )
        emit(E.PR_MERGED, "pr", str(200 + i), {"task_id": tid, "pr_number": 200 + i})
    return events


async def snapshot(factory: Any, pid: str, gid: str) -> dict[str, Any]:
    async with factory() as s:
        goal = await s.get(m.Goal, gid)
        epics = (await s.execute(select(m.Epic).where(m.Epic.goal_id == gid))).scalars().all()
        tasks = (
            (await s.execute(select(m.Task).where(m.Task.project_id == pid).order_by(m.Task.title)))
            .scalars()
            .all()
        )
        runs = (
            (await s.execute(select(m.Run).where(m.Run.project_id == pid).order_by(m.Run.task_id)))
            .scalars()
            .all()
        )
        return {
            "goal": None
            if goal is None
            else (goal.status.value, goal.plan_revision, goal.plan_discussion_id),
            "epics": [(e.title, e.status.value, e.milestone_number) for e in epics],
            "tasks": [
                (
                    t.title,
                    t.status.value,
                    t.issue_number,
                    t.pr_number,
                    t.attempt_count,
                    t.pr_merged_at is not None,
                    t.assignee_agent_id,
                    t.branch_name,
                )
                for t in tasks
            ],
            # tool_call_count 제외: tool_calls는 체인 밖(D-31)이라 TRUNCATE 후 replay로 안 돌아온다
            "runs": [
                (
                    r.task_id,
                    None if r.outcome is None else r.outcome.value,
                    r.agent_outcome,
                    r.tokens_in,
                    r.tokens_out,
                    r.cost_usd,
                    r.ended_at is not None,
                )
                for r in runs
            ],
        }


async def main() -> int:
    settings = Settings()
    if not settings.database_url.startswith("postgresql"):
        print(f"FAIL: HITL_DATABASE_URL must be postgres, got {settings.database_url}")
        return 2
    engine = sess.create_engine(settings)
    factory = sess.create_session_factory(engine)
    redis: Redis = Redis.from_url(settings.redis_url, decode_responses=True)
    bus = EventBus(redis)
    projection = Projection(factory, bus)
    pid, gid = new_id(), new_id()
    print(f"project={pid} goal={gid}")
    ok = True

    def check(cond: bool, label: str) -> None:
        nonlocal ok
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    # 1. publish (outbox)
    events = build_sequence(pid, gid)
    async with factory() as s:
        published = [await bus.publish(s, e) for e in events]
        await s.commit()
    chained = [e for e in published if e.type is not E.RUN_TOOL_CALLED]
    print(
        f"1. published {len(events)} events "
        f"({len(chained)} chained, {len(events) - len(chained)} tool_called)"
    )

    # 2. relay
    relay = OutboxRelay(factory, redis, batch=50)
    relayed = 0
    while (n := await relay.relay_once()) > 0:
        relayed += n  # 다른 project의 미발행 행이 섞일 수 있어 스트림 길이로 판정
    stream_len = await redis.xlen(f"events:{pid}")
    check(
        stream_len == len(events),
        f"2. relayed {relayed}; stream events:{pid[-6:]} len {stream_len} == {len(events)}",
    )

    # 3. projection consumer (같은 스트림에서 tool_called는 relay보다 먼저 도착한다)
    handled = 0
    idle = 0
    while idle < 2:
        n = await bus.poll_once("projection", projection.handle, consumer="pc1", project_id=pid)
        handled += n
        idle = idle + 1 if n == 0 else 0
    retried = await projection.apply_retries(pid, now=datetime.now(UTC) + timedelta(seconds=10))
    check(handled == len(events), f"3. consumer handled {handled} == {len(events)}")
    print(f"   retries re-applied: {retried}")
    pending = await redis.xpending(f"events:{pid}", "projection")
    check(pending["pending"] == 0, "3. no pending messages (all acked)")

    # 4. state
    snap1 = await snapshot(factory, pid, gid)
    check(snap1["goal"] == ("active", 1, 1), f"4. goal {snap1['goal']}")
    check([e[1] for e in snap1["epics"]] == ["active"], f"4. epic {snap1['epics']}")
    check(
        len(snap1["tasks"]) == 3 and all(t[1] == "done" for t in snap1["tasks"]),
        f"4. tasks done: {[t[1] for t in snap1['tasks']]}",
    )
    check(
        len(snap1["runs"]) == 3 and all(r[1] == "success" for r in snap1["runs"]),
        "4. runs success ×3",
    )
    async with factory() as s:
        counts = (
            (await s.execute(select(m.Run.tool_call_count).where(m.Run.project_id == pid)))
            .scalars()
            .all()
        )
        n_events = await s.scalar(
            select(text("count(*)")).select_from(m.Event).where(m.Event.project_id == pid)
        )
        n_tools = await s.scalar(
            select(text("count(*)")).select_from(m.ToolCall).where(m.ToolCall.project_id == pid)
        )
        errors = (
            await s.execute(
                select(m.Event.id, m.Event.projection_error).where(
                    m.Event.project_id == pid, m.Event.projection_error.is_not(None)
                )
            )
        ).all()
        unprojected = await s.scalar(
            select(text("count(*)"))
            .select_from(m.Event)
            .where(m.Event.project_id == pid, m.Event.projected_at.is_(None))
        )
    check(list(counts) == [2, 2, 2], f"4. run.tool_call_count {list(counts)} == [2, 2, 2]")
    check(
        n_events == len(chained) and n_tools == 6,
        f"4. events rows {n_events}, tool_calls rows {n_tools}",
    )
    check(errors == [], f"4. projection_error rows: {errors}")
    check(unprojected == 0, f"4. unprojected events: {unprojected}")

    # 5. chain
    async with factory() as s:
        check(await verify_chain_db(s, pid) is True, "5. verify_chain_db True")

    # 6. truncate → replay → force apply
    async with engine.begin() as conn:
        await conn.execute(text("TRUNCATE tasks, runs, epics, goals, tool_calls CASCADE"))
    async with factory() as s:
        replayed = await bus.replay(s, pid)
    check(len(replayed) == len(chained), f"6. replayed {len(replayed)} == {len(chained)}")
    applied = 0
    for e in replayed:
        applied += 1 if await projection.apply(e, force=True) else 0
    check(applied == len(chained), f"6. force-applied {applied}")
    snap2 = await snapshot(factory, pid, gid)
    check(snap1 == snap2, "6. snapshot after replay == before")
    if snap1 != snap2:
        print("   before:", snap1)
        print("   after: ", snap2)

    await redis.aclose()
    await engine.dispose()
    print("PC-1 roundtrip:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
