"""P6.3 — Dry 자동 머지 (D-36, red a~d): dry_run일 때만 pr.opened → pr.merged 1회. 실 모드는 0회."""

from __future__ import annotations

import asyncio
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from control_plane.config import Settings
from control_plane.events.bus import Delivery, EventBus
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Actor, Event, EventType
from control_plane.scheduler.launcher import FakeLauncher
from control_plane.store import models as m
from control_plane.store.enums import TaskStatus
from tests.runtime.conftest import bootstrap, ev, task_created
from tests.runtime.test_runtime import publish_all, settings_for, until

AGENT = Actor(type="agent", id="coding-1")


def pr_opened(pid: str, gid: str, task_id: str, pr: int, run_id: str = "R1") -> Event:
    return ev(
        pid, EventType.PR_OPENED, "pr", str(pr),
        {"task_id": task_id, "run_id": run_id, "pr_number": pr, "head": "ai/e/1-t", "base": "main"},
        correlation_id=gid, actor=AGENT,
    )  # fmt: skip


def delivery(event: Event, seq: int | None = 1) -> Delivery:
    return Delivery(event=event, message_id="1-0", attempt=1, group="g", consumer="c", seq=seq)


async def merged_events(factory: async_sessionmaker[AsyncSession]) -> list[m.Event]:
    async with factory() as s:
        rows = await s.execute(
            select(m.Event).where(m.Event.type == "pr.merged").order_by(m.Event.seq)
        )
        return list(rows.scalars().all())


# (a) dry_run → pr.merged 1건 (actor system:dry-merge, causation=pr.opened id); 같은 PR 두 번 → 1건
async def test_dry_merger_publishes_once(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from control_plane.dry_merge import DryMerger

    bus = EventBus(redis)
    await publish_all(
        factory, redis, [*bootstrap("P1", "G1", "/r"), task_created("P1", "G1", "T1", ["a/**"], 1)]
    )
    merger = DryMerger(factory, bus, enabled=True)
    opened = pr_opened("P1", "G1", "T1", 7)
    await merger.handle(delivery(opened, seq=None))  # 워커가 XADD한 미서명 전달
    await merger.handle(delivery(opened, seq=42))  # relay를 거친 서명본 — 두 번째
    rows = await merged_events(factory)
    assert len(rows) == 1
    e = rows[0]
    assert e.actor_type == "system" and e.actor_id == "dry-merge"
    assert e.causation_id == opened.id and e.correlation_id == "G1" and e.subject_id == "7"
    assert e.payload == {"task_id": "T1", "pr_number": 7, "merged_by": "dry-run"}
    assert merger.merged == [("P1", 7)]


# (a-2) 이미 머지된 Task(pr_merged_at)면 새 프로세스의 DryMerger도 no-op
async def test_dry_merger_skips_already_merged(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from control_plane.dry_merge import DryMerger
    from control_plane.events.outbox import OutboxRelay
    from control_plane.events.projection import Projection

    bus = EventBus(redis)
    await publish_all(
        factory, redis, [*bootstrap("P1", "G1", "/r"), task_created("P1", "G1", "T1", ["a/**"], 1)]
    )
    while await OutboxRelay(factory, redis).relay_once() > 0:  # projection에 Task 행을 만든다
        pass
    await bus.poll_once("t", Projection(factory, bus).handle, consumer="t", project_id="P1")
    async with factory() as s:
        t = await s.get(m.Task, "T1")
        assert t is not None
        t.pr_merged_at = datetime.now(UTC)
        await s.commit()
    await DryMerger(factory, bus, enabled=True).handle(delivery(pr_opened("P1", "G1", "T1", 7)))
    assert await merged_events(factory) == []


# (b) 실 모드(dry_run=false) → 발행 0
async def test_dry_merger_disabled_in_real_mode(
    factory: async_sessionmaker[AsyncSession], redis: Redis
) -> None:
    from control_plane.dry_merge import DryMerger

    await DryMerger(factory, EventBus(redis), enabled=False).handle(
        delivery(pr_opened("P1", "G1", "T1", 7))
    )
    assert await merged_events(factory) == []


class _FakeTokens:
    def token_nowait(self, repo: str) -> str:  # P9.9: repo별 (RepoRouter 인터페이스)
        return "ghs_test"

    async def refresh_all(self) -> None:
        return None


# (c) Runtime: dry_run이면 체인에 DryMerger가 붙고, in_review Task가 done까지 간다 → 의존 Task 배정
async def test_runtime_dry_merge_unblocks_dependents(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.dry_merge import DryMerger
    from control_plane.runtime import Runtime

    launcher = FakeLauncher()
    rt = Runtime(
        settings_for("sqlite+aiosqlite://", dry_run=True), factory, redis, launcher=launcher
    )
    assert any(getattr(h, "__self__", None).__class__ is DryMerger for h in rt.handlers)
    from github_adapter.dry_run import DryRunGitHubClient

    real = Runtime(
        settings_for("sqlite+aiosqlite://", dry_run=False),
        factory,
        redis,
        launcher=FakeLauncher(),
        github=DryRunGitHubClient(),  # 실 client는 App 자격 증명이 필요 — 배선만 확인
        token_provider=_FakeTokens(),  # P7.2: 실 모드는 토큰 제공자도 필요
    )
    assert not any(getattr(h, "__self__", None).__class__ is DryMerger for h in real.handlers)

    t2 = task_created("P1", "G1", "T2", ["b/**"], 2)
    t2.payload["depends_on"] = ["T1"]
    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", str(tmp_path)), task_created("P1", "G1", "T1", ["a/**"], 1), t2],
    )
    await rt.start()
    try:
        await until(lambda: asyncio.sleep(0, result=len(launcher.specs) >= 1))
        run_id = launcher.specs[0].run_id
        # 워커 흐름 (서명 발행으로 흉내): started → pr.opened → completed → run.finished
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
                pr_opened("P1", "G1", "T1", 3, run_id),
                ev(
                    "P1",
                    EventType.TASK_COMPLETED,
                    "task",
                    "T1",
                    {"run_id": run_id, "pr_number": 3},
                    correlation_id="G1",
                    actor=AGENT,
                ),
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

        async def t1_done_t2_assigned() -> bool:
            async with factory() as s:
                a = await s.get(m.Task, "T1")
                b = await s.get(m.Task, "T2")
                return (
                    a is not None
                    and b is not None
                    and a.status is TaskStatus.DONE
                    and b.status is TaskStatus.ASSIGNED
                )

        await until(t1_done_t2_assigned)
    finally:
        await rt.stop()
    assert [sp.task_id for sp in launcher.specs] == ["T1", "T2"]
    assert len(await merged_events(factory)) == 1


# (d) 헬퍼: settings_for가 dry_run을 받는다 (Settings 필드 확인)
def test_settings_dry_run_flag(_: Callable[..., Settings] = settings_for) -> None:
    assert settings_for("sqlite+aiosqlite://", dry_run=False).dry_run is False


# ------------------------------------------ PC-6 발견: Dry 머지는 git main도 옮겨야 한다

GENV = {
    "GIT_AUTHOR_NAME": "s",
    "GIT_AUTHOR_EMAIL": "s@x",
    "GIT_COMMITTER_NAME": "s",
    "GIT_COMMITTER_EMAIL": "s@x",
    "PATH": "/usr/bin:/bin",
}
FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, env=GENV
    ).stdout.strip()


def make_remote_with_branch(
    tmp_path: Path, branch: str, filename: str, diverge: bool = False
) -> Path:
    remote = tmp_path / "remote.git"
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = tmp_path / "seed"
    shutil.copytree(
        FIXTURES / "sample_repo",
        seed,
        ignore=shutil.ignore_patterns("dot_git_stub", ".venv", "node_modules"),
    )
    git(seed, "init", "-q", "-b", "main")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "seed")
    git(seed, "remote", "add", "origin", str(remote))
    git(seed, "push", "-q", "origin", "main")
    git(seed, "checkout", "-q", "-b", branch)
    (seed / filename).write_text("x = 1\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", f"feat: {filename}")
    git(seed, "push", "-q", "origin", branch)
    if diverge:  # main에도 다른 파일 커밋 → ff 불가, 충돌 없음
        git(seed, "checkout", "-q", "main")
        (seed / "OTHER.md").write_text("o\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "main moves")
        git(seed, "push", "-q", "origin", "main")
    return remote


# (e) pr.opened{head} → bare remote의 main이 브랜치까지 fast-forward → 그 다음 pr.merged
async def test_dry_merge_moves_git_main_fast_forward(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.dry_merge import DryMerger

    remote = make_remote_with_branch(tmp_path, "ai/e/1-t", "NEW.py")
    bus = EventBus(redis)
    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", str(remote)), task_created("P1", "G1", "T1", ["a/**"], 1)],
    )
    while await OutboxRelay(factory, redis).relay_once() > 0:
        pass
    await bus.poll_once("t", Projection(factory, bus).handle, consumer="t", project_id="P1")
    merger = DryMerger(factory, bus, enabled=True, repo_path_for=lambda repo: Path(repo))
    before = git(remote, "rev-parse", "main")
    opened = ev(
        "P1",
        EventType.PR_OPENED,
        "pr",
        "7",
        {"task_id": "T1", "run_id": "R1", "pr_number": 7, "head": "ai/e/1-t", "base": "main"},
        correlation_id="G1",
        actor=AGENT,
    )
    await merger.handle(delivery(opened))
    assert git(remote, "rev-parse", "main") == git(remote, "rev-parse", "ai/e/1-t") != before
    assert len(await merged_events(factory)) == 1 and merger.git_merges == [
        ("P1", 7, "fast-forward")
    ]


# (f) main이 갈라졌지만 충돌 없음 → worktree merge 커밋 → pr.merged; 충돌이면 pr.merged 없음 + 기록
async def test_dry_merge_diverged_and_conflict(
    factory: async_sessionmaker[AsyncSession], redis: Redis, tmp_path: Path
) -> None:
    from control_plane.dry_merge import DryMerger

    remote = make_remote_with_branch(tmp_path, "ai/e/1-t", "NEW.py", diverge=True)
    bus = EventBus(redis)
    await publish_all(
        factory,
        redis,
        [*bootstrap("P1", "G1", str(remote)), task_created("P1", "G1", "T1", ["a/**"], 1)],
    )
    while await OutboxRelay(factory, redis).relay_once() > 0:
        pass
    await bus.poll_once("t", Projection(factory, bus).handle, consumer="t", project_id="P1")
    merger = DryMerger(factory, bus, enabled=True, repo_path_for=lambda repo: Path(repo))
    opened = ev(
        "P1",
        EventType.PR_OPENED,
        "pr",
        "7",
        {"task_id": "T1", "run_id": "R1", "pr_number": 7, "head": "ai/e/1-t", "base": "main"},
        correlation_id="G1",
        actor=AGENT,
    )
    await merger.handle(delivery(opened))
    assert merger.git_merges == [("P1", 7, "merge")]
    assert "NEW.py" in git(remote, "ls-tree", "--name-only", "main")
    assert "OTHER.md" in git(remote, "ls-tree", "--name-only", "main")
    assert len(await merged_events(factory)) == 1
    # 충돌: 같은 파일을 main과 브랜치가 다르게 바꿈 → 머지 실패 → pr.merged 발행 없음
    remote2 = make_remote_with_branch(tmp_path / "c", "ai/e/2-t", "README.md", diverge=False)
    seed = tmp_path / "c" / "seed"
    git(seed, "checkout", "-q", "main")
    (seed / "README.md").write_text("conflict on main\n")
    git(seed, "add", "-A")
    git(seed, "commit", "-q", "-m", "main edits README")
    git(seed, "push", "-q", "origin", "main")
    await publish_all(
        factory,
        redis,
        [*bootstrap("P2", "G2", str(remote2)), task_created("P2", "G2", "T2", ["a/**"], 1)],
    )
    while await OutboxRelay(factory, redis).relay_once() > 0:
        pass
    await bus.poll_once("t", Projection(factory, bus).handle, consumer="t", project_id="P2")
    opened2 = ev(
        "P2",
        EventType.PR_OPENED,
        "pr",
        "8",
        {"task_id": "T2", "run_id": "R2", "pr_number": 8, "head": "ai/e/2-t", "base": "main"},
        correlation_id="G2",
        actor=AGENT,
    )
    await merger.handle(delivery(opened2))
    assert merger.git_merges[-1] == ("P2", 8, "conflict")
    assert len(await merged_events(factory)) == 1  # P2의 pr.merged는 없다
