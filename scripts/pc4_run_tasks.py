"""PC-4 — Task 3개 → Scheduler → (프로세스 내 워커) Coding Agent → bare remote에 ai/* 브랜치 3개.

D-34: 기본은 실 LLM(Settings.llm_provider), Fake 스크립트는 --fake 옵션으로만.

    uv run python scripts/pc4_run_tasks.py [--fake] [--remote DIR]

sqlite(임시 파일) + 진짜 Redis(HITL_REDIS_URL, DB 14 — 테스트의 DB 15와 분리). GitHub은 Dry.
워커는 FakeLauncher.on_launch에서
프로세스 내로 실행되며 이벤트는 실제 워커처럼 Redis XADD(미서명) → Scheduler.ingest → projection.
판정: 브랜치 ≥3 push, would open_pr ≥3, pr.opened ≥3, verify_chain_db True. 종료 코드 0 = PASS.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from redis.asyncio import Redis
from sqlalchemy import select
from ulid import ULID

from agents.base import AgentInput
from agents.coding import CodingAgent
from agents.llm import get_provider
from agents.llm.base import Completion, ModelProvider
from agents.llm.fake import FakeProvider
from control_plane.config import Settings
from control_plane.events.bus import Delivery, EventBus
from control_plane.events.chain import verify_chain_db
from control_plane.events.outbox import OutboxRelay
from control_plane.events.projection import Projection
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.scheduler.launcher import FakeLauncher, LaunchSpec
from control_plane.scheduler.scheduler import Scheduler
from control_plane.store import models as m
from control_plane.store import session as sess
from control_plane.store.models import Base
from github_adapter.dry_run import DryRunGitHubClient
from worker.entrypoint import prepare_worktree
from worker.publish import RedisPublisher

E = EventType
ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "seed",
    "GIT_AUTHOR_EMAIL": "seed@x",
    "GIT_COMMITTER_NAME": "seed",
    "GIT_COMMITTER_EMAIL": "seed@x",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}

# PC-3 fake 결과 형태의 Task 3개 (sample_repo 기준, owned_paths 겹침 없음).
# 사용자 결정 2026-09-13 (D-35): 7B 로컬 모델 기준의 "간단한 수준" — 새 파일 위주, 기대값 명시.
# 기존 파일을 크게 고치는 Task(UserStore.update/delete)는 7B 편차 → PC-5(Anthropic)에서.
TASKS: list[dict[str, Any]] = [
    {
        "title": "Add maths helpers module",
        "spec": (
            "Create `src/app/maths.py` with `add(a: int, b: int) -> int` and "
            "`mul(a: int, b: int) -> int`. Add `tests/test_maths.py` with exactly these checks: "
            "`add(2, 3) == 5`, `add(-1, 1) == 0`, `mul(2, 3) == 6`, `mul(0, 9) == 0`. "
            "Import as `from app.maths import add, mul`. Do not touch other files."
        ),
        "owned_paths": ["src/app/maths.py", "tests/test_maths.py"],
        "depends_on": [],
    },
    {
        "title": "Add users module with list_users and add_user",
        "spec": (
            "Create `src/app/users.py` with a module-level `store = UserStore()` and functions "
            "`list_users() -> list[dict]` (each dict has id and name) and "
            "`add_user(name: str) -> dict`. Import UserStore with "
            "`from app.models import UserStore`. "
            "Add `tests/test_users.py` (import `from app.users import list_users, add_user`) "
            "with ONE test function `test_add_then_list` that asserts, in this order: "
            '`add_user("ann") == {"id": 1, "name": "ann"}` then '
            '`list_users() == [{"id": 1, "name": "ann"}]`. The store is shared, so do not write '
            "a second test that adds users. Do not modify main.py or models.py."
        ),
        "owned_paths": ["src/app/users.py", "tests/test_users.py"],
        "depends_on": [],
    },
    {
        "title": "Add greet_all helper to main",
        "spec": (
            "In `src/app/main.py`, add `greet_all(names: list[str]) -> list[str]` that returns "
            "`greet(name)` for each name, keeping every existing line unchanged. Add "
            "`tests/test_greet_all.py` (import `from app.main import greet_all`) with: "
            '`greet_all([]) == []` and `greet_all(["a", "b"]) == ["hello, a", "hello, b"]`.'
        ),
        "owned_paths": ["src/app/main.py", "tests/test_greet_all.py"],
        "depends_on": [],
    },
]


# --fake: pass.json 스크립트는 users 모듈 하나만 만든다 → 같은 owned_paths의 변형 3개(직렬 배정)
FAKE_TASKS: list[dict[str, Any]] = [
    {**TASKS[1], "title": f"{TASKS[1]['title']} (fake {i})"} for i in (1, 2, 3)
]


def seed_remote(remote: Path) -> None:
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = remote.parent / "seed"
    shutil.copytree(
        FIXTURES / "sample_repo",
        seed,
        ignore=shutil.ignore_patterns(
            "dot_git_stub", ".venv", "node_modules", "__pycache__", ".pytest_cache"
        ),
    )
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed: sample_repo"],
        ["git", "remote", "add", "origin", str(remote)],
        ["git", "push", "-q", "origin", "main"],
    ):
        subprocess.run(cmd, cwd=seed, check=True, capture_output=True, env=GIT_ENV)


class Counting:
    def __init__(self, inner: ModelProvider, dump: Path | None = None) -> None:
        self.inner, self.calls, self.tin, self.tout = inner, 0, 0, 0
        self.dump = dump

    async def complete(self, messages: Any, **kw: Any) -> Completion:
        c = await self.inner.complete(messages, **kw)
        self.calls += 1
        self.tin += c.tokens_in
        self.tout += c.tokens_out
        if self.dump is not None:
            with self.dump.open("a", encoding="utf-8") as fh:
                rec = {
                    "call": self.calls,
                    "schema": getattr(kw.get("schema"), "__name__", None),
                    "prompt": [m.content for m in messages],
                    "text": c.text,
                    "parsed": c.parsed is not None,
                }
                fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return c


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--fake",
        action="store_true",
        help="FakeProvider(pass.json 스크립트)로 — D-34 기본은 실 LLM",
    )
    ap.add_argument("--remote", default=None, help="bare remote 경로 (기본 임시 디렉토리)")
    ap.add_argument(
        "--only", type=int, nargs="*", default=None, help="실행할 Task 번호(1..3) 부분집합"
    )
    ap.add_argument("--dump", default=None, help="LLM 요청/응답을 JSON-lines로 남길 디렉토리")
    args = ap.parse_args()
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
    settings = Settings()
    tmp = Path(tempfile.mkdtemp(prefix="pc4-"))
    remote = Path(args.remote) if args.remote else tmp / "remote.git"
    if not remote.exists():
        seed_remote(remote)
    db = tmp / "pc4.db"
    engine = sess.create_engine(Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{db}"))
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sess.create_session_factory(engine)
    # DB 14: 테스트(DB 15, FLUSHDB)와 겹치지 않게
    redis: Redis = Redis.from_url(
        settings.redis_url.rsplit("/", 1)[0] + "/14", decode_responses=True
    )
    await redis.flushdb()
    bus = EventBus(redis)
    relay = OutboxRelay(factory, redis, batch=100)
    projection = Projection(factory, bus)
    github = DryRunGitHubClient()
    model_name = (
        settings.llm_model if settings.llm_provider != "anthropic" else settings.anthropic_model
    )
    mode = "fake" if args.fake else f"{settings.llm_provider}:{model_name}"
    stats: dict[str, Any] = {"runs": []}

    workers: set[asyncio.Task[None]] = set()

    async def launch_inprocess(spec: LaunchSpec) -> None:
        """실 워커처럼 launch는 즉시 반환하고 워커는 백그라운드로. 기동 전에 outbox를 한 번 흘려
        task.assigned가 워커 이벤트보다 먼저 스트림에 들어가게 한다(운영에서는 relay 상시 동작)."""
        while await relay.relay_once() > 0:
            pass
        t = asyncio.create_task(run_worker_inprocess(spec))
        workers.add(t)
        t.add_done_callback(workers.discard)

    async def run_worker_inprocess(spec: LaunchSpec) -> None:
        """실제 워커 대신 프로세스 내에서: clone → CodingAgent.run → Redis XADD(미서명)."""
        workdir = tmp / "work" / spec.run_id
        repo = prepare_worktree(
            spec.repo_url, spec.branch, workdir, {"PATH": GIT_ENV["PATH"], "HOME": str(tmp)}
        )
        provider: ModelProvider
        if args.fake:
            provider = FakeProvider(
                script=json.loads((FIXTURES / "coding_scripts" / "pass.json").read_text())
            )
        else:
            provider = get_provider(settings)
        dump = None
        if args.dump:
            Path(args.dump).mkdir(parents=True, exist_ok=True)
            dump = Path(args.dump) / f"{spec.task_json['task']['issue_number']}.jsonl"
        counting = Counting(provider, dump)
        agent = CodingAgent(
            publish=RedisPublisher(redis),
            provider=counting,
            github=github,
            repo="local/sample-repo",
            worktree=repo,
            model=None if args.fake else settings.llm_model,
            test_command="pytest -q",
            shell_timeout=300,
        )
        started = time.monotonic()
        output = await asyncio.wait_for(
            agent.run(AgentInput.model_validate(spec.task_json)), timeout=spec.timeout_min * 60
        )
        stats["runs"].append(
            {
                "task": spec.task_json["task"]["title"],
                "outcome": output.outcome,
                "calls": counting.calls,
                "tokens_in": counting.tin,
                "tokens_out": counting.tout,
                "seconds": round(time.monotonic() - started, 1),
                "attempts": (agent.last_state or {}).get("attempt"),
                "error": output.error,
                "test_output": str((agent.last_state or {}).get("test_output") or "")[-600:],
            }
        )
        print(
            f"  ↳ worker done: {output.outcome} ({counting.calls} calls, "
            f"{round(time.monotonic() - started)}s) — {spec.task_json['task']['title']}"
        )

    launcher = FakeLauncher(on_launch=launch_inprocess)
    scheduler = Scheduler(
        factory,
        bus,
        launcher,
        projection=projection,
        max_workers=1,
        repo_url=str(remote),
        timeout_min=20,
    )

    pid, gid, eid = str(ULID()), str(ULID()), str(ULID())
    tasks_spec = FAKE_TASKS if args.fake else TASKS
    if args.only:
        tasks_spec = [t for i, t in enumerate(tasks_spec, start=1) if i in args.only]
    tids = [str(ULID()) for _ in tasks_spec]

    def ev(type_: EventType, entity: str, id_: str, payload: dict[str, Any]) -> Event:
        return Event(
            project_id=pid,
            actor=Actor(type="system", id="pc4"),
            type=type_,
            subject=Subject(entity=entity, id=id_),
            payload=payload,
            correlation_id=gid,
            causation_id=None,
        )  # type: ignore[arg-type]

    boot = [
        ev(
            E.PROJECT_CREATED,
            "project",
            pid,
            {"name": "pc4", "repo": "local/sample-repo", "default_branch": "main"},
        ),
        ev(E.GOAL_CREATED, "goal", gid, {"title": "Users CRUD + helpers", "description": "PC-4"}),
        ev(E.GOAL_PLAN_PROPOSED, "goal", gid, {"plan_discussion_number": 1, "revision": 1}),
        ev(E.GOAL_ACTIVATED, "goal", gid, {}),
        ev(
            E.EPIC_CREATED,
            "epic",
            eid,
            {"goal_id": gid, "title": "Users API", "order": 1, "milestone_number": 1},
        ),
    ]
    for i, (tid, t) in enumerate(zip(tids, tasks_spec, strict=True), start=1):
        boot.append(
            ev(
                E.TASK_CREATED,
                "task",
                tid,
                {
                    "epic_id": eid,
                    "epic_title": "Users API",
                    "title": t["title"],
                    "spec": t["spec"],
                    "kind": "feature",
                    "role_required": "coding",
                    "depends_on": [],
                    "owned_paths": t["owned_paths"],
                    "risk_tier": "T1",
                    "issue_number": 100 + i,
                    "issue_url": None,
                },
            )
        )
    async with factory() as s:
        for e in boot:
            await bus.publish(s, e)
        await s.commit()
    print(f"=== PC-4 (provider={mode}) remote={remote} ===")

    async def both(d: Delivery) -> None:
        await projection.handle(d)
        await scheduler.handle(d)

    deadline = time.monotonic() + 60 * 60
    while time.monotonic() < deadline:
        relayed = 0
        while (n := await relay.relay_once()) > 0:
            relayed += n
        consumed = await bus.poll_once("pc4", both, consumer="pc4", project_id=pid)
        await projection.apply_retries(pid, now=datetime.now(UTC) + timedelta(hours=3))
        if relayed == 0 and consumed == 0:
            async with factory() as s:
                statuses = (
                    (await s.execute(select(m.Task.status).where(m.Task.project_id == pid)))
                    .scalars()
                    .all()
                )
            if not scheduler.in_flight and all(
                st.value in ("in_review", "done", "blocked", "cancelled") for st in statuses
            ):
                break
            if not scheduler.in_flight and not await scheduler.tick(pid):
                break
            if workers:
                await asyncio.wait(workers, timeout=1.0)
            else:
                await asyncio.sleep(0.2)

    ok = True

    def check(cond: bool, label: str) -> None:
        nonlocal ok
        print(f"  [{'ok' if cond else 'FAIL'}] {label}")
        ok = ok and cond

    heads = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True, env=GIT_ENV
    ).stdout
    branches = [b.strip("* ").strip() for b in heads.splitlines() if b.strip().startswith("ai/")]
    print("\n=== branches on remote ===")
    for b in branches:
        log = subprocess.run(
            ["git", "log", "--format=%s%n%b", "-n", "1", b],
            cwd=remote,
            capture_output=True,
            text=True,
            env=GIT_ENV,
        ).stdout
        print(
            f"  {b}: {log.strip().splitlines()[0] if log.strip() else '(no commits)'}"
            f"  trailer={'Task #' in log}"
        )
    check(len(branches) >= 3, f"branches pushed: {len(branches)} >= 3")
    snap = github.snapshot()["repos"].get("local/sample-repo", {})
    pulls = snap.get("pulls", {})
    print("=== would open_pr ===")
    for n, pr in pulls.items():
        print(f"  #{n} {pr['title']} draft={pr['draft']} head={pr['head']}")
    check(len(pulls) >= 3, f"would open_pr: {len(pulls)} >= 3")
    async with factory() as s:
        tasks = (await s.execute(select(m.Task).where(m.Task.project_id == pid))).scalars().all()
        pr_opened = (
            (
                await s.execute(
                    select(m.Event).where(m.Event.project_id == pid, m.Event.type == "pr.opened")
                )
            )
            .scalars()
            .all()
        )
        runs = (await s.execute(select(m.Run).where(m.Run.project_id == pid))).scalars().all()
        errors = (
            await s.execute(
                select(m.Event.type, m.Event.projection_error).where(
                    m.Event.project_id == pid, m.Event.projection_error.is_not(None)
                )
            )
        ).all()
        chain_ok = await verify_chain_db(s, pid)
    print("=== tasks ===")
    for t in tasks:
        print(f"  {t.title}: {t.status.value} attempts={t.attempt_count} pr={t.pr_number}")
    check(len(pr_opened) >= 3, f"pr.opened events: {len(pr_opened)} >= 3")
    check(chain_ok, "verify_chain_db True")
    check(errors == [], f"projection_error rows: {errors}")
    print("=== runs ===")
    for r in stats["runs"]:
        print(
            f"  {r['outcome']:<14} attempts={r['attempts']} calls={r['calls']} "
            f"tokens={r['tokens_in']}/{r['tokens_out']} {r['seconds']}s  {r['task']}"
            + (f"  error={r['error'][:80]}" if r["error"] else "")
        )
    print(
        f"  runs in db: {len(runs)}, "
        f"outcomes={[r.outcome.value if r.outcome else None for r in runs]}"
    )
    await redis.aclose()
    await engine.dispose()
    print("PC-4:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
