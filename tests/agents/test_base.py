"""P4.2 — AgentInput/Output(§5.1), assemble_context(§5.3), BaseAgent.run 이벤트(D-28) (red a~e)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from structlog.testing import capture_logs

from agents.base import (
    AgentInput,
    AgentMemory,
    AgentOutput,
    Artifact,
    BaseAgent,
    DecisionRequest,
    ProjectContext,
    RunBudget,
    TaskRef,
)
from agents.context import assemble_context
from agents.llm.base import estimate_tokens
from agents.llm.fake import FakeProvider
from tests.agents.conftest import Spy

ROOT = Path(__file__).resolve().parent.parent.parent


def make_input(
    context_md: str | None = "# CONTEXT\n- Flask app", role_notes: str = ""
) -> AgentInput:
    return AgentInput(
        task=TaskRef(
            id="01TASK",
            title="Add users route",
            spec="## Spec\nAdd GET /users",
            kind="feature",
            role_required="coding",
            owned_paths=["src/app/**", "tests/**"],
            issue_number=12,
            epic_slug="users-api",
            risk_tier="T1",
            depends_on=[],
            attempt=1,
            max_attempts=3,
        ),
        project_context=ProjectContext(
            project_id="P1",
            goal_id="G1",
            repo="org/demo",
            default_branch="main",
            context_md=context_md,
            policy_summary="",
            related_summaries=["Issue #11: models done"],
        ),
        memory=AgentMemory(role_notes=role_notes),
        budget=RunBudget(max_tokens=100_000, max_seconds=2700, max_cost_usd=2.0),
        run_id="01RUN",
        agent_id="coding-1",
    )


# (a) 계약 §5.1
def test_contract_shapes() -> None:
    inp = make_input()
    assert inp.task.owned_paths == ["src/app/**", "tests/**"] and inp.budget.max_seconds == 2700
    out = AgentOutput(
        outcome="done",
        artifacts=[
            Artifact(kind="branch", ref="ai/users-api/12-add-users"),
            Artifact(kind="pr", ref="42", url="https://gh/p/42"),
        ],
        decision_request=None,
        new_tasks=[],
        notes_for_memory="",
        summary="did it",
        tokens_in=10,
        tokens_out=5,
        cost_usd=0.0,
    )
    assert out.outcome == "done" and out.artifacts[1].url == "https://gh/p/42"
    with pytest.raises(ValueError):
        AgentOutput(outcome="maybe", summary="x")  # type: ignore[arg-type]
    dr = DecisionRequest(
        type="dependency",
        reason="adds redis",
        options=["A", "B"],
        recommendation="A",
        files=["pyproject.toml"],
    )
    assert (
        AgentOutput(outcome="needs_decision", decision_request=dr, summary="").decision_request
        is dr
    )


# (b) 조립 순서 + 예산 초과 시 뒤에서부터
def test_assemble_context_order_and_budget() -> None:
    inp = make_input(role_notes="- prefer dataclasses")
    files = {
        "src/app/main.py": "def create_app(): ...",
        "src/app/models.py": "class UserStore: ...",
    }
    ctx = assemble_context(inp, token_budget=100_000, system="SYSTEM PROMPT", related_files=files)
    names = [s.name for s in ctx.sections]
    assert names == [
        "system",
        "policy",
        "context_md",
        "role_notes",
        "task_spec",
        "related_summaries",
        "related_files",
    ]
    assert ctx.sections[1].text == ""  # policy: MVP 1 빈 문자열
    text = ctx.render()
    assert text.index("SYSTEM PROMPT") < text.index("# CONTEXT") < text.index("prefer dataclasses")
    assert (
        text.index("prefer dataclasses")
        < text.index("Add GET /users")
        < text.index("Issue #11")
        < text.index("class UserStore")
    )
    assert ctx.dropped == []

    # 예산 = system 토큰 수 → system만 남고 나머지는 뒤에서부터 비워진다
    tiny = assemble_context(
        inp,
        token_budget=estimate_tokens("SYSTEM PROMPT"),
        system="SYSTEM PROMPT",
        related_files=files,
    )
    assert [s.name for s in tiny.sections if s.text] == ["system"]
    assert tiny.dropped[0] == "related_files" and tiny.dropped[-1] == "context_md"
    # 중간 예산: 뒤쪽 섹션만 떨어진다
    mid_budget = (
        estimate_tokens("SYSTEM PROMPT")
        + estimate_tokens(inp.project_context.context_md or "")
        + estimate_tokens("- prefer dataclasses")
        + estimate_tokens(inp.task.spec)
        + 8
    )
    mid = assemble_context(
        inp, token_budget=mid_budget, system="SYSTEM PROMPT", related_files=files
    )
    assert "related_files" in mid.dropped and "context_md" not in mid.dropped
    assert mid.tokens <= mid_budget


# (c) CONTEXT.md 없으면 경고 + 계속
def test_missing_context_md_warns() -> None:
    inp = make_input(context_md=None)
    with capture_logs() as logs:
        ctx = assemble_context(inp, token_budget=10_000, system="S")
    assert any(
        e["event"] == "context.missing_context_md" and e["log_level"] == "warning" for e in logs
    )
    assert [s.name for s in ctx.sections if s.text] == ["system", "task_spec", "related_summaries"]


# (d) BaseAgent.run: task.started → run.started → execute → run.finished (D-28 매핑)
class OkAgent(BaseAgent):
    async def execute(self, input: AgentInput) -> AgentOutput:
        return AgentOutput(outcome="done", summary="ok", tokens_in=7, tokens_out=3, cost_usd=0.01)


class NeedsDecisionAgent(BaseAgent):
    async def execute(self, input: AgentInput) -> AgentOutput:
        return AgentOutput(
            outcome="needs_decision",
            summary="dep",
            decision_request=DecisionRequest(type="dependency", reason="r"),
        )


class BoomAgent(BaseAgent):
    async def execute(self, input: AgentInput) -> AgentOutput:
        raise RuntimeError("boom")


async def test_run_publishes_events_and_maps_outcome(spy: Spy) -> None:
    agent = OkAgent(publish=spy.publish, provider=FakeProvider(), model="fake")
    out = await agent.run(make_input())
    assert out.outcome == "done"
    assert spy.types() == ["task.started", "run.started", "run.finished"]
    started, run_started, finished = spy.events
    assert started.subject.id == "01TASK" and started.payload == {"run_id": "01RUN"}
    assert run_started.subject.id == "01RUN" and run_started.payload == {
        "task_id": "01TASK",
        "agent_id": "coding-1",
        "model": "fake",
    }
    assert run_started.causation_id == started.id and finished.causation_id == run_started.id
    p = finished.payload
    assert p["outcome"] == "success" and p["agent_outcome"] == "done"
    assert (p["tokens_in"], p["tokens_out"], p["cost_usd"]) == (7, 3, 0.01) and p["error"] is None
    assert (
        p["duration_s"] >= 0 and finished.correlation_id == "G1" and finished.actor.id == "coding-1"
    )


async def test_run_maps_needs_decision_to_escalated(spy: Spy) -> None:
    out = await NeedsDecisionAgent(publish=spy.publish, provider=FakeProvider()).run(make_input())
    assert out.outcome == "needs_decision"
    p = spy.events[-1].payload
    assert (p["outcome"], p["agent_outcome"]) == ("escalated", "needs_decision")


async def test_run_exception_becomes_failed(spy: Spy) -> None:
    out = await BoomAgent(publish=spy.publish, provider=FakeProvider()).run(make_input())
    assert out.outcome == "failed" and "boom" in (out.error or "")
    p = spy.events[-1].payload
    assert (p["outcome"], p["agent_outcome"]) == ("failed", "failed") and "boom" in p["error"]
    assert spy.types() == ["task.started", "run.started", "run.finished"]


# (e) agents/에 control_plane.config import 없음
def test_agents_do_not_import_config() -> None:
    for p in (ROOT / "agents").rglob("*.py"):
        tree = ast.parse(p.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom | ast.Import):
                names = (
                    [a.name for a in node.names]
                    if isinstance(node, ast.Import)
                    else [node.module or ""]
                )
                assert not any(n.startswith("control_plane.config") for n in names), p
