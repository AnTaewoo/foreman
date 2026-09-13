"""PC-3 — Orchestrator 그래프 완주 (dry GitHub, MemorySaver).

    uv run python scripts/pc3_plan_dryrun.py --fake tests/fixtures/sample_repo "goal"
    uv run python scripts/pc3_plan_dryrun.py tests/fixtures/sample_repo "goal"   # 실 LLM

analyze → draft_plan(Plan + Discussion dry) → interrupt → 자동 approve → decompose(TaskDraft JSON)
→ emit(Dry Issue, epic/task 이벤트). 출력: Plan, TaskDraft JSON, would 로그, 이벤트, 토큰 합계.
종료 코드 0 = 완주. 실 LLM은 `--fake`가 없을 때 Settings.llm_provider가 고른다 (D-33).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path
from typing import Any

import structlog
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command
from ulid import ULID

from agents.llm import get_provider
from agents.llm.base import Completion, ModelProvider
from agents.llm.fake import FakeProvider
from control_plane.config import Settings
from control_plane.events.schema import Event
from control_plane.orchestrator import emit as emit_mod
from control_plane.orchestrator.graph import OrchestratorDeps, build_graph
from control_plane.orchestrator.state import OrchestratorState, initial_state
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient

FAKE_PLAN: dict[str, Any] = {
    "understanding": "Small Flask app with an in-memory UserStore; tests use pytest.",
    "acceptance_criteria": ["GET /users lists users", "POST /users creates a user", "tests pass"],
    "epics": [
        {
            "title": "Users API",
            "order": 1,
            "summary": "CRUD routes",
            "task_count": 4,
            "risk_tier": "T1",
        }
    ],
    "task_graph": "T-1 → T-2 → T-4, T-3 → T-4",
    "decisions_expected": ["none"],
    "budget_estimate": "~$2, ~4 runs",
}
FAKE_DECOMPOSE: dict[str, Any] = {
    "epics": [{"title": "Users API", "order": 1, "summary": "CRUD routes"}],
    "tasks": [
        {
            "title": "Add UserStore.update/delete",
            "spec": "Extend models.UserStore",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": [],
            "owned_paths": ["src/app/models.py"],
            "estimated_tier": "T1",
            "epic": "Users API",
        },
        {
            "title": "Add GET/POST /users routes",
            "spec": "In main.create_app",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": ["Add UserStore.update/delete"],
            "owned_paths": ["src/app/main.py"],
            "estimated_tier": "T1",
            "epic": "Users API",
        },
        {
            "title": "Add PUT/DELETE /users/<id>",
            "spec": "In main.create_app",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": ["Add UserStore.update/delete"],
            "owned_paths": ["src/app/main.py"],
            "estimated_tier": "T1",
            "epic": "Users API",
        },
        {
            "title": "Tests for /users",
            "spec": "tests/test_users.py",
            "kind": "test",
            "role_required": "coding",
            "depends_on": ["Add GET/POST /users routes", "Add PUT/DELETE /users/<id>"],
            "owned_paths": ["tests/test_users.py"],
            "estimated_tier": "T0",
            "epic": "Users API",
        },
    ],
}


class CountingProvider:
    """토큰 합계를 세는 래퍼."""

    def __init__(self, inner: ModelProvider) -> None:
        self.inner = inner
        self.tokens_in = 0
        self.tokens_out = 0
        self.calls = 0

    async def complete(self, messages: Any, **kwargs: Any) -> Completion:
        c = await self.inner.complete(messages, **kwargs)
        self.calls += 1
        self.tokens_in += c.tokens_in
        self.tokens_out += c.tokens_out
        return c


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_path")
    ap.add_argument("goal")
    ap.add_argument("--fake", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stdout)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.dev.ConsoleRenderer(colors=False),
        ],
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
    )
    settings = Settings()
    if args.fake:
        base: ModelProvider = FakeProvider(script=[FAKE_PLAN, FAKE_DECOMPOSE])
        mode = "fake"
    else:
        base = get_provider(settings)
        mode = f"anthropic:{settings.anthropic_model}"
    provider = CountingProvider(base)
    github = DryRunGitHubClient()
    discussions = DryRunDiscussionsClient()
    events: list[Event] = []

    async def publish(event: Event) -> Event:
        events.append(event)
        return event

    async def do_emit(state: OrchestratorState) -> dict[str, Any]:
        return await emit_mod.emit(state, github=github, publish=publish)

    deps = OrchestratorDeps(
        provider=provider, github=github, discussions=discussions, publish=publish, emit=do_emit
    )
    graph = build_graph(deps, checkpointer=MemorySaver())
    goal_id = str(ULID())
    cfg = {"configurable": {"thread_id": goal_id}}
    state = initial_state(
        project_id=str(ULID()),
        goal_id=goal_id,
        goal_title=args.goal,
        goal_description=args.goal,
        repo_path=str(Path(args.repo_path).resolve()),
        repo_full_name="local/sample-repo",
    )
    print(f"=== PC-3 plan dry-run (provider={mode}) ===")
    out = await graph.ainvoke(state, cfg)
    if "__interrupt__" not in out:
        print("FAIL: graph did not interrupt at wait_plan_approval")
        return 1
    snap = await graph.aget_state(cfg)
    print("\n=== 1. Plan (Discussion body) ===")
    print(snap.values["plan"])
    disc = snap.values["plan_discussion_number"]
    print(f"\n[interrupt] next={snap.next} discussion=#{disc} → auto-approve")
    final = await graph.ainvoke(Command(resume={"approved": True, "by": "pc3-auto"}), cfg)
    print("\n=== 2. TaskDraft JSON ===")
    print(
        json.dumps(
            {"epics": final.get("epics"), "tasks": final.get("tasks")}, ensure_ascii=False, indent=2
        )
    )
    print("\n=== 3. Issues (dry) ===")
    for issue in final.get("issues", []):
        print(
            f"  #{issue['issue_number']} {issue['title']}  "
            f"(epic: {issue['epic_title']}, milestone {issue['milestone_number']})"
        )
    print("\n=== 4. Events ===")
    for e in events:
        print(
            f"  {e.type.value:<20} subject={e.subject.entity}:{e.subject.id[-6:]} "
            f"causation={str(e.causation_id)[-6:]}"
        )
    print(f"\n=== 5. Tokens === calls={provider.calls}", end=" ")
    print(f"in={provider.tokens_in} out={provider.tokens_out}")
    ok = not final.get("error") and len(final.get("issues", [])) >= 3
    if final.get("error"):
        print("error:", final["error"])
    print("PC-3:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
