"""MVP 1 Orchestrator 그래프 (설계 §15.1, §3.3):

    analyze_repo → draft_plan → wait_plan_approval
        ├─ approved → activate_and_decompose → emit_issues → END
        └─ rejected → reject → END

- 부작용(Discussion, 이벤트)은 ``draft_plan``에, ``wait_plan_approval``은 ``interrupt``만 한다.
  resume 시 interrupt 노드가 처음부터 다시 실행되므로 거기엔 부작용을 두지 않는다 (D-13).
- ``goal.activated``는 resume 직후, decompose **전**에 발행한다 (B5: 사이클이면 active→blocked).
- ``emit_issues``는 주입된 ``deps.emit(state)`` 호출 (P3.5). 결과 키는 issues/error/last_event_id.
- 체크포인터: 단위 테스트는 MemorySaver, 운영은 Postgres (D-12).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import interrupt
from pydantic import ValidationError

from agents.llm.base import Message, ModelProvider
from control_plane.events.schema import Actor, Event, EventType, Subject
from control_plane.orchestrator.context import build_summary, render_summary
from control_plane.orchestrator.drafts import (
    DecomposeError,
    PlanDraft,
    decompose_with_retry,
    missing_plan_sections,
    render_prompt,
    system_prompt,
)
from control_plane.orchestrator.state import OrchestratorState
from github_adapter.discussions import DiscussionRef
from github_adapter.protocol import GitHubClient

log = structlog.get_logger(__name__)

PLAN_CATEGORY = "Plans"
ORCHESTRATOR_ACTOR = Actor(type="agent", id="orchestrator")


class PlanError(Exception):
    """Plan 초안을 두 번 요청했는데도 유효하지 않다."""


class DiscussionsLike(Protocol):
    async def create_discussion(
        self, repo: str, category: str, title: str, body: str
    ) -> DiscussionRef: ...


Publish = Callable[[Event], Awaitable[Event]]
Emit = Callable[[OrchestratorState], Awaitable[dict[str, Any]]]


async def dry_emit(state: OrchestratorState) -> dict[str, Any]:
    """P3.5 전 기본값: 로그만."""
    log.info("would emit_issues", tasks=len(state.get("tasks") or []))
    return {"issues": [], "last_event_id": state.get("last_event_id")}


@dataclass
class OrchestratorDeps:
    provider: ModelProvider
    github: GitHubClient
    discussions: DiscussionsLike
    publish: Publish
    emit: Emit = dry_emit
    model: str | None = None
    min_tasks: int = 1  # X.2: 운영은 3 (runner/e2e), 단위 테스트는 1


def _event(
    state: OrchestratorState,
    type_: EventType,
    payload: dict[str, Any],
    *,
    entity: Literal["goal"] = "goal",
) -> Event:
    return Event(
        project_id=state["project_id"],
        actor=ORCHESTRATOR_ACTOR,
        type=type_,
        subject=Subject(entity=entity, id=state["goal_id"]),
        payload=payload,
        correlation_id=state["goal_id"],
        causation_id=state.get("last_event_id"),
    )


def build_graph(
    deps: OrchestratorDeps, *, checkpointer: BaseCheckpointSaver[Any] | None = None
) -> CompiledStateGraph[OrchestratorState, None, OrchestratorState, OrchestratorState]:
    # ------------------------------------------------------------------ nodes
    async def analyze_repo(state: OrchestratorState) -> dict[str, Any]:
        if state.get("repo_summary"):
            return {}
        summary = build_summary(state["repo_path"])
        return {"repo_summary": render_summary(summary)}

    async def draft_plan(state: OrchestratorState) -> dict[str, Any]:
        goal_text = f"{state['goal_title']}\n\n{state.get('goal_description', '')}".strip()
        messages = [
            Message(
                role="user",
                content=render_prompt("plan", goal=goal_text, repo_summary=state["repo_summary"]),
            )
        ]
        plan: PlanDraft | None = None
        last_error = ""
        for _ in range(2):
            completion = await deps.provider.complete(
                messages, system=system_prompt(), schema=PlanDraft, model=deps.model
            )
            parsed = completion.parsed
            if not isinstance(parsed, PlanDraft):
                try:
                    parsed = PlanDraft.model_validate_json(completion.text)
                except (ValidationError, ValueError) as exc:
                    last_error = str(exc)[:500]
                    parsed = None
            if parsed is not None:
                md = parsed.to_markdown(
                    goal_title=state["goal_title"], goal_number=state["goal_id"][-6:]
                )
                missing = missing_plan_sections(md)
                if not missing:
                    plan = parsed
                    break
                last_error = f"missing sections: {missing}"
            messages = [
                *messages,
                Message(role="assistant", content=completion.text or "(empty)"),
                Message(
                    role="user",
                    content=f"Rejected: {last_error}\nReturn the corrected JSON object only.",
                ),
            ]
        if plan is None:
            raise PlanError(f"plan draft invalid after 2 attempts: {last_error}")

        revision = int(state.get("plan_revision") or 0) + 1
        markdown = plan.to_markdown(
            goal_title=state["goal_title"], goal_number=state["goal_id"][-6:]
        )
        # 제목이 멱등 키라 Goal마다 유일해야 한다 (같은 제목 Goal의 Discussion 재사용, P9)
        title = f"Plan #{revision} (Goal #{state['goal_id'][-6:]}): {state['goal_title']}"
        discussion = await deps.discussions.create_discussion(
            state["repo_full_name"], PLAN_CATEGORY, title, markdown
        )
        event = await deps.publish(
            _event(
                state,
                EventType.GOAL_PLAN_PROPOSED,
                {
                    "plan_discussion_number": discussion.number,
                    "revision": revision,
                    "plan_markdown": markdown,  # D-53: API가 Plan 본문을 보여준다
                },
            )
        )
        return {
            "plan": markdown,
            "plan_json": plan.model_dump(),
            "plan_discussion_number": discussion.number,
            "plan_discussion_id": discussion.id,
            "plan_revision": revision,
            "last_event_id": event.id,
        }

    async def wait_plan_approval(state: OrchestratorState) -> dict[str, Any]:
        decision = interrupt(
            {
                "plan_discussion_number": state.get("plan_discussion_number"),
                "plan_revision": state.get("plan_revision"),
            }
        )
        return {"approval": dict(decision)}

    def route_after_approval(state: OrchestratorState) -> str:
        return "activate_and_decompose" if state.get("approval", {}).get("approved") else "reject"

    async def reject(state: OrchestratorState) -> dict[str, Any]:
        approval = state.get("approval", {})
        event = await deps.publish(
            _event(
                state,
                EventType.GOAL_CANCELLED,
                {"by": approval.get("by", "unknown"), "reason": approval.get("reason", "")},
            )
        )
        return {"last_event_id": event.id}

    async def activate_and_decompose(state: OrchestratorState) -> dict[str, Any]:
        approval = state.get("approval", {})
        activated = await deps.publish(
            _event(state, EventType.GOAL_ACTIVATED, {"by": approval.get("by", "unknown")})
        )
        working: OrchestratorState = {**state, "last_event_id": activated.id}
        goal_text = f"{state['goal_title']}\n\n{state.get('goal_description', '')}".strip()
        try:
            result = await decompose_with_retry(
                deps.provider,
                repo_summary=state["repo_summary"],
                plan=state["plan"],
                goal=goal_text,
                model=deps.model,
                min_tasks=deps.min_tasks,
            )
        except DecomposeError as exc:
            blocked = await deps.publish(
                _event(working, EventType.GOAL_BLOCKED, {"reason": f"decompose: {exc}"})
            )
            return {"error": f"decompose failed: {exc}", "last_event_id": blocked.id}
        return {
            "epics": [e.model_dump() for e in result.epics],
            "tasks": [t.model_dump() for t in result.tasks],
            "error": None,
            "last_event_id": activated.id,
        }

    def route_after_decompose(state: OrchestratorState) -> str:
        return END if state.get("error") else "emit_issues"

    async def emit_issues(state: OrchestratorState) -> dict[str, Any]:
        result = await deps.emit(state)
        out: dict[str, Any] = {
            "issues": result.get("issues", []),
            "last_event_id": result.get("last_event_id", state.get("last_event_id")),
        }
        if result.get("error"):
            out["error"] = result["error"]
        return out

    # ------------------------------------------------------------------ graph
    g: StateGraph[OrchestratorState, None, OrchestratorState, OrchestratorState] = StateGraph(
        OrchestratorState
    )
    g.add_node("analyze_repo", analyze_repo)
    g.add_node("draft_plan", draft_plan)
    g.add_node("wait_plan_approval", wait_plan_approval)
    g.add_node("activate_and_decompose", activate_and_decompose)
    g.add_node("reject", reject)
    g.add_node("emit_issues", emit_issues)
    g.add_edge(START, "analyze_repo")
    g.add_edge("analyze_repo", "draft_plan")
    g.add_edge("draft_plan", "wait_plan_approval")
    g.add_conditional_edges(
        "wait_plan_approval",
        route_after_approval,
        {"activate_and_decompose": "activate_and_decompose", "reject": "reject"},
    )
    g.add_conditional_edges(
        "activate_and_decompose", route_after_decompose, {"emit_issues": "emit_issues", END: END}
    )
    g.add_edge("reject", END)
    g.add_edge("emit_issues", END)
    return g.compile(checkpointer=checkpointer)


# ---------------------------------------------------------------------- checkpointer (D-12)


class _DbSettings(Protocol):
    database_url: str


def postgres_conn_string(url: str) -> str:
    """SQLAlchemy URL(``postgresql+asyncpg://``) → psycopg용 ``postgresql://``."""
    scheme, sep, rest = url.partition("://")
    return f"{scheme.split('+', 1)[0]}{sep}{rest}"


def get_checkpointer(settings: _DbSettings) -> BaseCheckpointSaver[Any]:
    """sqlite(단위 테스트)면 MemorySaver. Postgres는 ``open_postgres_checkpointer``로 연다."""
    if settings.database_url.startswith("sqlite"):
        return MemorySaver()
    raise ValueError("postgres checkpointer must be opened with open_postgres_checkpointer()")


def open_postgres_checkpointer(settings: _DbSettings) -> Any:  # Any: async context manager
    """``async with open_postgres_checkpointer(settings) as saver: await saver.setup()``."""
    return AsyncPostgresSaver.from_conn_string(postgres_conn_string(settings.database_url))
