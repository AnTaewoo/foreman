"""e2e dry-run (P5.4, D-18): repo 경로 + Goal → Orchestrator(Plan → 자동 승인 → TaskDraft → Issue)
→ Scheduler → (프로세스 내 워커) Coding Agent → tmp bare remote에 ai/* 브랜치 → 토큰 합계.

    uv run python scripts/e2e_dry_run.py --fake tests/fixtures/sample_repo "goal"   # 네트워크 0
    uv run python scripts/e2e_dry_run.py tests/fixtures/sample_repo "goal"          # 실 LLM (D-33)
    옵션: --no-coding (Plan + Issue까지) --remote DIR --workdir DIR

GitHub는 항상 Dry(DryRunGitHubClient/DryRunDiscussionsClient). 이벤트는 outbox → 진짜 Redis(DB 14)
→ projection → Scheduler. 종료 코드 0 = 완주(코딩까지면 Task 전부 done).
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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
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
from control_plane.orchestrator import emit as emit_mod
from control_plane.orchestrator.graph import OrchestratorDeps, build_graph
from control_plane.orchestrator.state import OrchestratorState, initial_state
from control_plane.scheduler.launcher import FakeLauncher, LaunchSpec
from control_plane.scheduler.scheduler import Scheduler
from control_plane.store import models as m
from control_plane.store import session as sess
from control_plane.store.models import Base
from control_plane.store.session import get_session
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient
from worker.entrypoint import prepare_worktree
from worker.publish import RedisPublisher

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
GIT_ENV = {
    "GIT_AUTHOR_NAME": "seed",
    "GIT_AUTHOR_EMAIL": "seed@x",
    "GIT_COMMITTER_NAME": "seed",
    "GIT_COMMITTER_EMAIL": "seed@x",
    "PATH": "/usr/bin:/bin:/usr/local/bin",
}
IGNORE = shutil.ignore_patterns(
    ".git", "dot_git_stub", ".venv", "node_modules", "__pycache__", ".pytest_cache"
)

# --fake 스크립트: Plan 1회 + decompose 1회. Task 2개는 coding_scripts/pass.json이 만드는 파일 소유
# (같은 owned_paths → depends_on으로 직렬화되어 Scheduler 순서 검증도 겸한다).
FAKE_PLAN: dict[str, Any] = {
    "understanding": "Small Flask app with an in-memory UserStore; tests use pytest.",
    "acceptance_criteria": ["users module lists users", "tests pass"],
    "epics": [
        {
            "title": "Users API",
            "order": 1,
            "summary": "users module",
            "task_count": 2,
            "risk_tier": "T1",
        }
    ],
    "task_graph": "T-1 → T-2",
    "decisions_expected": ["none"],
    "budget_estimate": "~$1, ~2 runs",
}
FAKE_DECOMPOSE: dict[str, Any] = {
    "epics": [{"title": "Users API", "order": 1, "summary": "users module"}],
    "tasks": [
        {
            "title": "Add users module",
            "spec": "Create src/app/users.py with list_users() and tests/test_users.py",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": [],
            "owned_paths": ["src/app/users.py", "tests/test_users.py"],
            "estimated_tier": "T1",
            "epic": "Users API",
        },
        {
            "title": "Harden users tests",
            "spec": "Extend tests/test_users.py (same files, runs after the first task)",
            "kind": "test",
            "role_required": "coding",
            "depends_on": ["Add users module"],
            "owned_paths": ["src/app/users.py", "tests/test_users.py"],
            "estimated_tier": "T0",
            "epic": "Users API",
        },
    ],
}


class Counting:
    def __init__(self, inner: ModelProvider) -> None:
        self.inner, self.calls, self.tin, self.tout = inner, 0, 0, 0

    async def complete(self, messages: Any, **kw: Any) -> Completion:
        c = await self.inner.complete(messages, **kw)
        self.calls += 1
        self.tin += c.tokens_in
        self.tout += c.tokens_out
        return c


@dataclass
class Summary:
    exit_code: int = 1
    provider: str = ""
    dry_run: bool = True
    task_count: int = 0
    issue_count: int = 0
    coding: list[dict[str, Any]] = field(default_factory=list)
    branches: list[str] = field(default_factory=list)
    tokens: dict[str, int] = field(default_factory=dict)
    remote: str = ""


def seed_remote(repo_path: Path, remote: Path, workdir: Path) -> None:
    """repo_path 사본을 git init → bare remote의 main으로 push (repo_path 자체는 안 건드린다)."""
    subprocess.run([str(FIXTURES / "make_remote.sh"), str(remote)], check=True, capture_output=True)
    seed = workdir / "seed"
    shutil.copytree(repo_path, seed, ignore=IGNORE)
    for cmd in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "add", "-A"],
        ["git", "commit", "-q", "-m", "seed: e2e"],
        ["git", "remote", "add", "origin", str(remote)],
        ["git", "push", "-q", "origin", "main"],
    ):
        subprocess.run(cmd, cwd=seed, check=True, capture_output=True, env=GIT_ENV)


def _provider_label(settings: Settings) -> str:
    if settings.llm_provider == "anthropic":
        return f"anthropic:{settings.anthropic_model}"
    return f"{settings.llm_provider}:{settings.llm_model}"


async def run(argv: list[str]) -> Summary:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("goal")
    ap.add_argument("--fake", action="store_true", help="FakeProvider (네트워크 0)")
    ap.add_argument("--no-coding", action="store_true", help="Plan + Issue까지만")
    ap.add_argument("--remote", default=None, help="bare remote 경로 (기본 workdir/remote.git)")
    ap.add_argument("--workdir", default=None, help="작업 디렉토리 (기본 임시)")
    ap.add_argument(
        "--model", default=None, help="openai_compat 모델 덮어쓰기 (예: qwen2.5-coder:14b)"
    )
    args = ap.parse_args(argv)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stderr),
    )
    settings = Settings()
    if args.model:
        settings = settings.model_copy(update={"llm_model": args.model})
    summary = Summary(provider="fake" if args.fake else _provider_label(settings), dry_run=True)
    workdir = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="e2e-"))
    workdir.mkdir(parents=True, exist_ok=True)
    repo_path = Path(args.repo_path).resolve()
    remote = Path(args.remote) if args.remote else workdir / "remote.git"
    summary.remote = str(remote)

    # ---- 인프라: sqlite(tmp) + Redis DB 14 (테스트 DB 15와 분리), Dry GitHub
    engine = sess.create_engine(
        Settings(_env_file=None, database_url=f"sqlite+aiosqlite:///{workdir / 'e2e.db'}")
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = sess.create_session_factory(engine)
    redis: Redis = Redis.from_url(
        settings.redis_url.rsplit("/", 1)[0] + "/14", decode_responses=True
    )
    await redis.flushdb()
    bus = EventBus(redis)
    relay = OutboxRelay(factory, redis, batch=100)
    projection = Projection(factory, bus)
    github = DryRunGitHubClient()
    discussions = DryRunDiscussionsClient()

    async def publish(event: Event) -> Event:
        async with get_session(factory) as s:
            return await bus.publish(s, event)

    async def pump() -> None:
        for _ in range(50):
            relayed = 0
            while (n := await relay.relay_once()) > 0:
                relayed += n
            consumed = await bus.poll_once("e2e", projection.handle, consumer="e2e")
            if relayed == 0 and consumed == 0:
                break

    # ---- 0. Project / Goal 루트 이벤트
    pid, gid = str(ULID()), str(ULID())
    human = Actor(type="human", id="e2e")
    await publish(
        Event(
            project_id=pid,
            actor=human,
            type=EventType.PROJECT_CREATED,
            subject=Subject(entity="project", id=pid),
            payload={"name": repo_path.name, "repo": str(repo_path), "default_branch": "main"},
            correlation_id=pid,
            causation_id=None,
        )
    )
    created = await publish(
        Event(
            project_id=pid,
            actor=human,
            type=EventType.GOAL_CREATED,
            subject=Subject(entity="goal", id=gid),
            payload={"title": args.goal, "description": args.goal},
            correlation_id=gid,
            causation_id=None,
        )
    )

    # ---- 1~3. Orchestrator
    orch_base: ModelProvider = (
        FakeProvider(script=[FAKE_PLAN, FAKE_DECOMPOSE]) if args.fake else get_provider(settings)
    )
    orch = Counting(orch_base)

    async def do_emit(state: OrchestratorState) -> dict[str, Any]:
        return await emit_mod.emit(state, github=github, publish=publish)

    deps = OrchestratorDeps(
        provider=orch,
        github=github,
        discussions=discussions,
        publish=publish,
        emit=do_emit,
        model=None if args.fake or settings.llm_provider == "anthropic" else settings.llm_model,
    )
    graph = build_graph(deps, checkpointer=MemorySaver())
    cfg = {"configurable": {"thread_id": gid}}
    state = initial_state(
        project_id=pid,
        goal_id=gid,
        goal_title=args.goal,
        goal_description=args.goal,
        repo_path=str(repo_path),
        repo_full_name=f"local/{repo_path.name}",
        last_event_id=created.id,
    )
    print(f"e2e dry-run  provider={summary.provider}  repo={repo_path}  remote={remote}")
    out = await graph.ainvoke(state, cfg)
    print("\n=== 1. RepoSummary ===")
    print(str(out.get("repo_summary", "")).rstrip())
    print("\n=== 2. Plan ===")
    print(str(out.get("plan", "")).rstrip())
    if "__interrupt__" not in out:
        print("!! interrupt 없음 — Plan 단계 실패:", out.get("error"))
        await _close(redis, engine)
        return summary
    print("\n(auto-approve) by e2e")
    final = await graph.ainvoke(Command(resume={"approved": True, "by": "e2e"}), cfg)
    tasks = list(final.get("tasks") or [])
    issues = list(final.get("issues") or [])
    summary.task_count, summary.issue_count = len(tasks), len(issues)
    print("\n=== 3. TaskDrafts ===")
    print(json.dumps({"epics": final.get("epics"), "tasks": tasks}, ensure_ascii=False, indent=2))
    print()
    by_task = {t.get("title"): t for t in tasks}
    for issue in issues:
        title = issue.get("title") or next((t for t in by_task if issue.get("task_id") and t), "")
        print(f"would create issue #{issue.get('issue_number')}: {title or issue.get('task_id')}")
    if final.get("error"):
        print("!! emit 오류:", final["error"])
    await pump()
    if args.no_coding:
        summary.tokens = _tokens(orch, [])
        _print_tokens(summary.tokens)
        summary.exit_code = 0 if issues and not final.get("error") else 1
        await _close(redis, engine)
        return summary

    # ---- 4. Scheduler + 프로세스 내 Coding Agent
    seed_remote(repo_path, remote, workdir)
    coding_counts: list[Counting] = []
    workers: set[asyncio.Task[None]] = set()

    async def launch(spec: LaunchSpec) -> None:
        while await relay.relay_once() > 0:
            pass
        t = asyncio.create_task(worker(spec))
        workers.add(t)
        t.add_done_callback(workers.discard)

    async def worker(spec: LaunchSpec) -> None:
        wd = workdir / "work" / spec.run_id
        repo = prepare_worktree(
            spec.repo_url, spec.branch, wd, {"PATH": GIT_ENV["PATH"], "HOME": str(workdir)}
        )
        base: ModelProvider = (
            FakeProvider(script=json.loads((FIXTURES / "coding_scripts" / "pass.json").read_text()))
            if args.fake
            else get_provider(settings)
        )
        counting = Counting(base)
        coding_counts.append(counting)
        agent = CodingAgent(
            publish=RedisPublisher(redis),
            provider=counting,
            github=github,
            repo=f"local/{repo_path.name}",
            worktree=repo,
            model=None if args.fake or settings.llm_provider == "anthropic" else settings.llm_model,
            test_command="pytest -q",
            shell_timeout=300,
        )
        started = time.monotonic()
        title = spec.task_json["task"]["title"]
        try:
            output = await asyncio.wait_for(
                agent.run(AgentInput.model_validate(spec.task_json)), timeout=spec.timeout_min * 60
            )
        except Exception as exc:  # noqa: BLE001 — 워커 크래시는 기록하고 슬롯을 비운다
            summary.coding.append({"task": title, "outcome": "crashed", "error": repr(exc)})
            scheduler.in_flight.discard(spec.task_id)
            return
        summary.coding.append(
            {
                "task": title,
                "outcome": output.outcome,
                "attempts": (agent.last_state or {}).get("attempt"),
                "calls": counting.calls,
                "tokens_in": counting.tin,
                "tokens_out": counting.tout,
                "seconds": round(time.monotonic() - started, 1),
                "error": output.error,
            }
        )

    scheduler = Scheduler(
        factory,
        bus,
        FakeLauncher(on_launch=launch),
        projection=projection,
        max_workers=1,
        repo_url=str(remote),
        timeout_min=20,
    )

    async def both(d: Delivery) -> None:
        await projection.handle(d)
        await scheduler.handle(d)

    merged: set[str] = set()
    deadline = time.monotonic() + 60 * 60
    while time.monotonic() < deadline:
        relayed = 0
        while (n := await relay.relay_once()) > 0:
            relayed += n
        consumed = await bus.poll_once("e2e-sched", both, consumer="s", project_id=pid)
        await projection.apply_retries(pid, now=datetime.now(UTC) + timedelta(hours=3))
        if relayed == 0 and consumed == 0:
            async with factory() as s:
                rows = (
                    (await s.execute(select(m.Task).where(m.Task.project_id == pid)))
                    .scalars()
                    .all()
                )
                review = [
                    (t.id, t.pr_number, t.title)
                    for t in rows
                    if t.status.value == "in_review" and t.pr_number and t.id not in merged
                ]
                settled = all(t.status.value in ("done", "blocked", "cancelled") for t in rows)
            if review:
                # MVP 1은 사람이 머지한다(§8) — e2e는 Dry PR을 사람 대신 머지해 의존 Task를 풀어준다
                for task_id, pr_number, title in review:
                    merged.add(task_id)
                    await publish(
                        Event(
                            project_id=pid,
                            actor=Actor(type="github", id="e2e"),
                            type=EventType.PR_MERGED,
                            subject=Subject(entity="pr", id=str(pr_number)),
                            payload={
                                "task_id": task_id,
                                "pr_number": pr_number,
                                "merged_by": "e2e",
                            },
                            correlation_id=gid,
                            causation_id=None,
                        )
                    )
                    print(f"(auto-merge) PR #{pr_number} → {title}")
                continue
            if not scheduler.in_flight and (settled or not await scheduler.tick(pid)):
                break
            if workers:
                await asyncio.wait(workers, timeout=1.0)
            else:
                await asyncio.sleep(0.2)

    print("\n=== 4. Coding Agent 결과 ===")
    for c in summary.coding:
        print(
            f"  {c['outcome']:<10} attempts={c.get('attempts')} calls={c.get('calls')} "
            f"tokens={c.get('tokens_in')}/{c.get('tokens_out')} {c.get('seconds')}s  {c['task']}"
            + (f"  error={str(c['error'])[:100]}" if c.get("error") else "")
        )
    heads = subprocess.run(
        ["git", "branch", "--list"], cwd=remote, capture_output=True, text=True, env=GIT_ENV
    ).stdout
    summary.branches = [b.strip("* ").strip() for b in heads.splitlines() if "ai/" in b]
    print("\n=== 5. bare remote 브랜치 ===")
    for b in summary.branches:
        subject = subprocess.run(
            ["git", "log", "--format=%s", "-n", "1", b],
            cwd=remote, capture_output=True, text=True, env=GIT_ENV,
        ).stdout.strip()  # fmt: skip
        print(f"  {b}: {subject}")
    async with factory() as s:
        chain_ok = await verify_chain_db(s, pid)
        pr_opened = len(
            (
                await s.execute(
                    select(m.Event.id).where(m.Event.project_id == pid, m.Event.type == "pr.opened")
                )
            ).all()
        )
    print(f"  pr.opened={pr_opened} verify_chain={chain_ok}")
    summary.tokens = _tokens(orch, coding_counts)
    _print_tokens(summary.tokens)
    all_done = bool(summary.coding) and all(c["outcome"] == "done" for c in summary.coding)
    summary.exit_code = 0 if all_done and chain_ok and len(summary.branches) >= len(tasks) else 1
    await _close(redis, engine)
    return summary


def _tokens(orch: Counting, coding: list[Counting]) -> dict[str, int]:
    return {
        "orchestrator_calls": orch.calls,
        "orchestrator_tokens_in": orch.tin,
        "orchestrator_tokens_out": orch.tout,
        "coding_calls": sum(c.calls for c in coding),
        "coding_tokens_in": sum(c.tin for c in coding),
        "coding_tokens_out": sum(c.tout for c in coding),
        "calls": orch.calls + sum(c.calls for c in coding),
        "tokens_in": orch.tin + sum(c.tin for c in coding),
        "tokens_out": orch.tout + sum(c.tout for c in coding),
    }


def _print_tokens(t: dict[str, int]) -> None:
    print("\n=== 6. 토큰 합계 ===")
    print(
        f"  orchestrator: {t['orchestrator_calls']} calls, "
        f"{t['orchestrator_tokens_in']}/{t['orchestrator_tokens_out']}"
    )
    print(
        f"  coding:       {t['coding_calls']} calls, "
        f"{t['coding_tokens_in']}/{t['coding_tokens_out']}"
    )
    print(f"  total:        {t['calls']} calls, in {t['tokens_in']} / out {t['tokens_out']}")


async def _close(redis: Redis, engine: Any) -> None:
    await redis.aclose()
    await engine.dispose()


def main() -> int:
    summary = asyncio.run(run(sys.argv[1:]))
    print("\ne2e:", "PASS" if summary.exit_code == 0 else "FAIL")
    return summary.exit_code


if __name__ == "__main__":
    sys.exit(main())
