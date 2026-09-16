"""P8.3 — Runtime reaper 루프 + 기동 시 잔재 복구 (D-44)."""

from __future__ import annotations

import asyncio
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.scheduler.launcher import FakeLauncher
from control_plane.store import models as m
from tests.runtime.conftest import bootstrap, task_created
from tests.runtime.test_runtime import publish_all, settings_for, until


async def test_runtime_reaps_dead_worker(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.runtime import Runtime

    launcher = FakeLauncher()
    rt = Runtime(
        settings_for("sqlite+aiosqlite://"), factory, redis, launcher=launcher, reap_interval=0.2
    )
    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", str(tmp_path)), task_created("P1", "G1", "T1", ["a/**"], 1)],
    )
    await rt.start()
    try:
        await until(lambda: asyncio.sleep(0, result=len(launcher.specs) >= 1))
        launcher.dead.add(f"fake-{launcher.specs[0].run_id}")
        await until(lambda: asyncio.sleep(0, result=len(launcher.specs) >= 2))  # 재배정됨
    finally:
        await rt.stop()
    async with factory() as s:
        failed = (
            (await s.execute(select(m.Event).where(m.Event.type == "task.failed"))).scalars().all()
        )
    assert failed and failed[0].payload["reason"] == "worker_died"
