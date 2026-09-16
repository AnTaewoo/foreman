"""P6.1 — 상주 control plane (red a~e). 스크립트 pump 없이 Runtime만 돌려 Task가 assigned까지."""

from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.events.bus import EventBus, retry_stream_key
from control_plane.events.schema import Event, EventType
from control_plane.scheduler.launcher import DockerCliLauncher, FakeLauncher, LaunchSpec
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus
from tests.runtime.conftest import bootstrap, ev, task_created

ROOT = Path(__file__).resolve().parent.parent.parent
FIXTURES = ROOT / "tests" / "fixtures"


async def publish_all(
    factory: async_sessionmaker[AsyncSession], redis: Redis, events: list[Event]
) -> None:
    bus = EventBus(redis)
    async with factory() as s:
        for e in events:
            await bus.publish(s, e)
        await s.commit()


async def until(cond: Callable[[], Awaitable[bool]], timeout: float = 15.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while not await cond():
        if asyncio.get_running_loop().time() > deadline:
            raise AssertionError("timeout waiting for condition")
        await asyncio.sleep(0.1)


def settings_for(engine_url: str, **kw: Any) -> Settings:
    return Settings(_env_file=None, database_url=engine_url, **kw)


# (a) Runtime.start() 하나로 relay → projection → scheduler → launch. stop()으로 전부 내린다.
async def test_runtime_assigns_tasks_without_pump(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.runtime import Runtime

    launcher = FakeLauncher()
    rt = Runtime(settings_for("sqlite+aiosqlite://"), factory, redis, launcher=launcher)
    await publish_all(
        factory,
        redis,
        [
            *bootstrap("P1", "G1", str(tmp_path)),
            task_created("P1", "G1", "T1", ["a/**"], 1),
            task_created("P1", "G1", "T2", ["b/**"], 2),
        ],
    )
    await rt.start()
    try:

        async def all_assigned() -> bool:  # task.assigned도 relay → projection을 거친다
            async with factory() as s:
                rows = (await s.execute(select(m.Task))).scalars().all()
            return len(rows) == 2 and all(t.status is TaskStatus.ASSIGNED for t in rows)

        await until(all_assigned)
        assert {sp.task_id for sp in launcher.specs} == {"T1", "T2"}
    finally:
        await rt.stop()
    assert all(t.done() for t in rt.tasks)  # 백그라운드 Task 전부 종료


# (a-2) retry 루프: 모든 project의 :retry 스트림을 순회해 기한 지난 재시도를 적용
async def test_runtime_applies_due_retries_for_all_projects(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.runtime import Runtime

    clock = {"now": datetime.now(UTC)}
    launcher = FakeLauncher()
    rt = Runtime(
        settings_for("sqlite+aiosqlite://"),
        factory,
        redis,
        launcher=launcher,
        retry_interval=0.1,
        clock=lambda: clock["now"],
    )
    events = [*bootstrap("P1", "G1", str(tmp_path)), task_created("P1", "G1", "T1", ["a/**"], 1)]
    # task.completed가 task.assigned/started보다 먼저 온 상황 (ready→in_review 불허 → retry 스트림)
    completed = ev(
        "P1", EventType.TASK_COMPLETED, "task", "T1", {"run_id": "R1"}, correlation_id="G1"
    )
    await publish_all(factory, redis, [*events, completed])
    await rt.start()
    try:
        await until(lambda: redis.exists(retry_stream_key("P1")))  # projection이 retry에 넣었다
        # 워커 흐름 assigned → started(running) 뒤에는 task.completed 재시도가 통과해야 한다
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
                )
            ],
        )
        clock["now"] = datetime.now(UTC) + timedelta(hours=3)  # 재시도 기한 경과

        async def in_review() -> bool:
            async with factory() as s:
                t = await s.get(m.Task, "T1")
                return t is not None and t.status is TaskStatus.IN_REVIEW

        await until(in_review)
    finally:
        await rt.stop()


# (b) 프로젝트별 repo/default_branch는 projects 행에서 (리뷰 A3)
async def test_scheduler_reads_repo_per_project(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.runtime import Runtime

    launcher = FakeLauncher()
    rt = Runtime(settings_for("sqlite+aiosqlite://"), factory, redis, launcher=launcher)
    one, two = tmp_path / "repo-one", tmp_path / "repo-two"
    one.mkdir()
    two.mkdir()
    await publish_all(
        factory,
        redis,
        [
            *bootstrap("P1", "G1", str(one), "main"),
            task_created("P1", "G1", "T1", ["a/**"], 1),
            *bootstrap("P2", "G2", str(two), "develop"),
            task_created("P2", "G2", "T2", ["a/**"], 1),
        ],
    )
    await rt.start()
    try:
        await until(lambda: asyncio.sleep(0, result=len(launcher.specs) >= 2))
    finally:
        await rt.stop()
    by_task = {sp.task_id: sp for sp in launcher.specs}
    assert by_task["T1"].repo_url == str(one.resolve()) and by_task["T2"].repo_url == str(
        two.resolve()
    )
    assert by_task["T1"].task_json["project_context"]["default_branch"] == "main"
    assert by_task["T2"].task_json["project_context"]["default_branch"] == "develop"


# (c) 설정으로 launcher 선택
def test_settings_and_launcher_selection(redis: Redis) -> None:
    from control_plane.runtime import build_launcher

    s = Settings(_env_file=None)
    assert s.worker_launcher == "docker" and s.worker_image == "foreman-worker:dev"
    assert s.scheduler_max_workers == 4
    docker = build_launcher(Settings(_env_file=None, redis_url="redis://localhost:6379/3"), redis)
    assert isinstance(docker, DockerCliLauncher) and docker.image == "foreman-worker:dev"
    # 컨테이너에서 호스트 Redis: localhost → host.docker.internal
    assert docker.redis_url == "redis://host.docker.internal:6379/3"
    inproc = build_launcher(
        Settings(_env_file=None, worker_launcher="inprocess", llm_provider="openai_compat"), redis
    )
    assert type(inproc).__name__ == "InProcessLauncher"
    with pytest.raises(ValueError):
        Settings(_env_file=None, worker_launcher="bogus")  # type: ignore[arg-type]


# (d) InProcessLauncher: 이 프로세스 안에서 CodingAgent 실행 → 브랜치 push + run.finished XADD
async def test_inprocess_launcher_runs_coding_agent(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from agents.llm.fake import FakeProvider
    from control_plane.scheduler.launcher import InProcessLauncher

    remote = tmp_path / "remote.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    shutil.copytree(
        FIXTURES / "sample_repo",
        seed,
        ignore=shutil.ignore_patterns("dot_git_stub", ".venv", "node_modules"),
    )
    genv = {
        "GIT_AUTHOR_NAME": "s",
        "GIT_AUTHOR_EMAIL": "s@x",
        "GIT_COMMITTER_NAME": "s",
        "GIT_COMMITTER_EMAIL": "s@x",
        "PATH": "/usr/bin:/bin",
    }
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed"],
        ["git", "remote", "add", "origin", str(remote)],
        ["git", "push", "-q", "origin", "main"],
    ):
        subprocess.run(cmd, cwd=seed, check=True, capture_output=True, env=genv)
    script = json.loads((FIXTURES / "coding_scripts" / "pass.json").read_text())
    launcher = InProcessLauncher(
        redis, provider_factory=lambda: FakeProvider(script=script), workdir=tmp_path / "work"
    )
    task_json = {
        "task": {
            "id": "T1",
            "title": "Add users module",
            "spec": "s",
            "kind": "feature",
            "role_required": "coding",
            "owned_paths": ["src/app/**", "tests/**"],
            "issue_number": 12,
            "epic_slug": "users-api",
            "risk_tier": "T1",
            "attempt": 1,
            "max_attempts": 3,
        },
        "project_context": {
            "project_id": "P1",
            "goal_id": "G1",
            "repo": str(remote),
            "default_branch": "main",
        },
        "run_id": "01RUN",
        "agent_id": "coding-1",
    }
    spec = LaunchSpec(
        task_id="T1",
        run_id="01RUN",
        project_id="P1",
        goal_id="G1",
        branch="ai/users-api/12-add-users-module",
        repo_url=str(remote),
        task_json=task_json,
        timeout_min=5,
    )
    worker_id = await launcher.launch(spec)
    assert worker_id.startswith("inprocess-")
    await launcher.wait_idle()
    heads = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True, env=genv
    ).stdout
    assert "ai/users-api/12-add-users-module" in heads
    entries = await redis.xrange("events:P1")
    types = [fields["type"] for _, fields in entries]
    assert types[0] == "task.started" and types[-1] == "run.finished" and "task.completed" in types


# (e) python -m control_plane: Runtime을 띄우고 stop 신호에 정리
async def test_main_runs_until_stop(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from control_plane.__main__ import serve
    from control_plane.runtime import Runtime

    rt = Runtime(settings_for("sqlite+aiosqlite://"), factory, redis, launcher=FakeLauncher())
    stop = asyncio.Event()

    async def trigger() -> None:
        await asyncio.sleep(0.3)
        stop.set()

    asyncio.create_task(trigger())
    await serve(rt, stop)
    assert all(t.done() for t in rt.tasks)


# P6.5: Settings 단가 → 워커 env (D-39)
def test_worker_env_carries_prices() -> None:
    from control_plane.runtime import worker_llm_env

    s = Settings(_env_file=None, llm_provider="openai_compat")
    assert s.llm_price_in_per_mtok == 0.0 and s.llm_price_out_per_mtok == 0.0
    env = worker_llm_env(
        Settings(
            _env_file=None,
            llm_provider="openai_compat",
            llm_price_in_per_mtok=3.0,
            llm_price_out_per_mtok=15.0,
        )
    )
    assert (
        env["WORKER_LLM_PRICE_IN_PER_MTOK"] == "3.0"
        and env["WORKER_LLM_PRICE_OUT_PER_MTOK"] == "15.0"
    )


# P8.1: Runtime의 RepoCache는 settings.dry_run을 받는다
def test_runtime_repo_cache_is_dry_in_dry_mode(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from control_plane.runtime import Runtime

    rt = Runtime(
        settings_for("sqlite+aiosqlite://", dry_run=True), factory, redis, launcher=FakeLauncher()
    )
    assert rt.repo_cache.dry_run is True
