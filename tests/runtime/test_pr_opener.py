"""P6.7 — PrOpener (D-37, red b~c, f): control plane이 task.completed를 받아 PR을 열고 pr.opened."""

from __future__ import annotations

import asyncio
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.events.bus import Delivery, EventBus
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Actor, Event, EventType
from control_plane.scheduler.launcher import FakeLauncher
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus
from github_adapter.dry_run import DryRunGitHubClient
from tests.runtime.conftest import bootstrap, ev, task_created
from tests.runtime.test_runtime import publish_all, settings_for, until

AGENT = Actor(type="agent", id="coding-1")


def completed(pid: str, gid: str, tid: str, run_id: str, branch: str | None) -> Event:
    payload: dict[str, object] = {"run_id": run_id, "summary": "Added users module; tests pass."}
    if branch:
        payload["branch"] = branch
    return ev(pid, EventType.TASK_COMPLETED, "task", tid, payload, correlation_id=gid, actor=AGENT)


def delivery(event: Event, seq: int | None = 1) -> Delivery:
    return Delivery(event=event, message_id="1-0", attempt=1, group="g", consumer="c", seq=seq)


async def project_all(factory: async_sessionmaker[AsyncSession], redis: Redis, pid: str) -> None:
    bus = EventBus(redis)
    while await OutboxRelay(factory, redis).relay_once() > 0:
        pass
    await bus.poll_once("t", Projection(factory, bus).handle, consumer="t", project_id=pid)


async def events_of(factory: async_sessionmaker[AsyncSession], type_: str) -> list[m.Event]:
    async with factory() as s:
        rows = await s.execute(select(m.Event).where(m.Event.type == type_).order_by(m.Event.seq))
        return list(rows.scalars().all())


# (b) task.completed{branch, summary} → open_pr(draft, 메타) → pr.opened + Issue 코멘트; 두 번 → 1건
async def test_pr_opener_opens_once(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.pr_opener import PrOpener

    repo = str(tmp_path)
    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", repo, "main"), task_created("P1", "G1", "T1", ["a/**"], 12)],
    )
    await project_all(factory, redis, "P1")
    github = DryRunGitHubClient()
    opener = PrOpener(factory, EventBus(redis), github)
    done = completed("P1", "G1", "T1", "R1", "ai/e1/12-t1")
    await opener.handle(delivery(done, seq=None))  # 워커 미서명 전달
    await opener.handle(delivery(done, seq=9))  # relay 서명본 — 두 번째
    opened = await events_of(factory, "pr.opened")
    assert len(opened) == 1
    e = opened[0]
    assert e.actor_type == "system" and e.actor_id == "pr-opener" and e.causation_id == done.id
    assert e.subject_entity == "pr" and e.correlation_id == "G1"
    assert (
        e.payload["task_id"] == "T1" and e.payload["run_id"] == "R1" and e.payload["draft"] is True
    )
    assert e.payload["head"] == "ai/e1/12-t1" and e.payload["base"] == "main"
    pr_number = e.payload["pr_number"]
    snap = github.snapshot()["repos"][repo]
    pr = snap["pulls"][pr_number]
    assert pr["head"] == "ai/e1/12-t1" and pr["base"] == "main" and pr["draft"] is True
    assert pr["title"] == "[T-12] T1" and pr["body"].startswith(
        "<!-- ai-platform:meta task=T1 run=R1"
    )
    assert snap["issues"][12]["comments"][0]["key"] == "summary:R1"
    assert opener.opened == [("P1", "T1")]


# (b-2) branch 없는 task.completed(실패 경로 등) → no-op
async def test_pr_opener_ignores_without_branch(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.pr_opener import PrOpener

    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", str(tmp_path)), task_created("P1", "G1", "T1", ["a/**"], 1)],
    )
    await project_all(factory, redis, "P1")
    opener = PrOpener(factory, EventBus(redis), DryRunGitHubClient())
    await opener.handle(delivery(completed("P1", "G1", "T1", "R1", None)))
    assert await events_of(factory, "pr.opened") == [] and opener.opened == []


# (c) Runtime 체인: task.completed → PrOpener → pr.opened → DryMerger → pr.merged → done
async def test_runtime_opens_pr_and_dry_merges(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.pr_opener import PrOpener
    from control_plane.runtime import Runtime

    launcher = FakeLauncher()
    github = DryRunGitHubClient()
    rt = Runtime(
        settings_for("sqlite+aiosqlite://", dry_run=True),
        factory,
        redis,
        launcher=launcher,
        github=github,
    )
    assert any(getattr(h, "__self__", None).__class__ is PrOpener for h in rt.handlers)
    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", str(tmp_path)), task_created("P1", "G1", "T1", ["a/**"], 1)],
    )
    await rt.start()
    try:
        await until(lambda: asyncio.sleep(0, result=len(launcher.specs) >= 1))
        run_id = launcher.specs[0].run_id
        await publish_all(
            factory,
            redis,
            [
                ev(
                    "P1",
                    EventType.TASK_STARTED,
                    "task",
                    "T1",
                    {"run_id": run_id},
                    correlation_id="G1",
                    actor=AGENT,
                ),
                ev(
                    "P1",
                    EventType.RUN_ARTIFACT_PRODUCED,
                    "run",
                    run_id,
                    {"kind": "branch", "ref": "ai/e1/1-t1"},
                    correlation_id="G1",
                    actor=AGENT,
                ),
                completed("P1", "G1", "T1", run_id, "ai/e1/1-t1"),
                ev(
                    "P1",
                    EventType.RUN_FINISHED,
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
                    correlation_id="G1",
                    actor=AGENT,
                ),
            ],
        )

        async def is_done() -> bool:
            async with factory() as s:
                t = await s.get(m.Task, "T1")
                return t is not None and t.status is TaskStatus.DONE and t.pr_number is not None

        await until(is_done)
    finally:
        await rt.stop()
    assert (
        len(await events_of(factory, "pr.opened")) == 1
        and len(await events_of(factory, "pr.merged")) == 1
    )
    assert len(github.snapshot()["repos"][str(tmp_path)]["pulls"]) == 1
