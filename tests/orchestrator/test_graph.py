"""P3.4 — orchestrator graph (red a~f): interrupt, approve/reject, checkpointer 재빌드."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from agents.llm.fake import FakeProvider
from control_plane.config import Settings
from control_plane.events.schema import Event, EventType
from control_plane.orchestrator import graph as graph_mod
from control_plane.orchestrator.drafts import PLAN_SECTIONS
from control_plane.orchestrator.graph import (
    OrchestratorDeps,
    PlanError,
    build_graph,
    get_checkpointer,
    open_postgres_checkpointer,
    postgres_conn_string,
)
from control_plane.orchestrator.state import OrchestratorState, initial_state
from github_adapter.dry_run import DryRunDiscussionsClient, DryRunGitHubClient

SAMPLE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"

PLAN_JSON: dict[str, Any] = {
    "understanding": "Flask app with in-memory store.",
    "acceptance_criteria": ["GET /users lists users", "tests pass"],
    "epics": [
        {"title": "Users API", "order": 1, "summary": "CRUD", "task_count": 2, "risk_tier": "T1"}
    ],
    "task_graph": "T-1 → T-2",
    "decisions_expected": ["none"],
    "budget_estimate": "~$1, ~2 runs",
}
DECOMPOSE_JSON: dict[str, Any] = {
    "epics": [{"title": "Users API", "order": 1, "summary": "CRUD"}],
    "tasks": [
        # P9: 코드 Task는 테스트 파일을 같이 소유한다 (테스트만 있는 Task는 구현 Task로 합쳐진다)
        {
            "title": "Add users route",
            "spec": "s",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": [],
            "owned_paths": ["src/app/users.py", "tests/test_users.py"],
            "estimated_tier": "T1",
            "epic": "Users API",
        },
        {
            "title": "Add users validation",
            "spec": "s",
            "kind": "feature",
            "role_required": "coding",
            "depends_on": ["Add users route"],
            "owned_paths": ["src/app/validation.py", "tests/test_validation.py"],
            "estimated_tier": "T1",
            "epic": "Users API",
        },
    ],
}


class Sink:
    """publish 스파이: 이벤트를 모으고 그대로 돌려준다 (서명은 여기서 불필요)."""

    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def types(self) -> list[str]:
        return [e.type.value for e in self.events]


async def stub_emit(state: OrchestratorState, *, sink: Sink, publish: Any) -> dict[str, Any]:
    """P3.5 전까지의 emit 대역: epic.created → task.created를 causation 체인으로 발행."""
    from control_plane.events.schema import Actor, Subject

    prev = state.get("last_event_id")
    issues: list[dict[str, Any]] = []
    for i, epic in enumerate(state.get("epics") or [], start=1):
        e = Event(
            project_id=state["project_id"],
            actor=Actor(type="agent", id="orchestrator"),
            type=EventType.EPIC_CREATED,
            subject=Subject(entity="epic", id=f"E{i}"),
            payload={
                "goal_id": state["goal_id"],
                "title": epic["title"],
                "order": i,
                "milestone_number": None,
            },
            correlation_id=state["goal_id"],
            causation_id=prev,
        )
        prev = (await publish(e)).id
    for i, task in enumerate(state.get("tasks") or [], start=1):
        e = Event(
            project_id=state["project_id"],
            actor=Actor(type="agent", id="orchestrator"),
            type=EventType.TASK_CREATED,
            subject=Subject(entity="task", id=f"T{i}"),
            payload={"title": task["title"]},
            correlation_id=state["goal_id"],
            causation_id=prev,
        )
        prev = (await publish(e)).id
        issues.append({"task_id": f"T{i}", "issue_number": i})
    return {"issues": issues, "last_event_id": prev}


def make(
    script: list[Any], sink: Sink | None = None
) -> tuple[Any, Sink, FakeProvider, OrchestratorDeps]:
    sink = sink or Sink()
    provider = FakeProvider(script=script)

    async def emit(state: OrchestratorState) -> dict[str, Any]:
        return await stub_emit(state, sink=sink, publish=sink.publish)

    deps = OrchestratorDeps(
        provider=provider,
        github=DryRunGitHubClient(),
        discussions=DryRunDiscussionsClient(),
        publish=sink.publish,
        emit=emit,
        model=None,
    )
    return deps, sink, provider, deps


def state0() -> OrchestratorState:
    return initial_state(
        project_id="P1",
        goal_id="G1",
        goal_title="Add users endpoint",
        goal_description="CRUD for users",
        repo_path=str(SAMPLE),
        repo_full_name="org/demo",
    )


CFG = {"configurable": {"thread_id": "G1"}}


# (a) interrupt at wait_plan_approval, plan + discussion + goal.plan_proposed
async def test_runs_to_interrupt() -> None:
    deps, sink, provider, _ = make([PLAN_JSON])
    graph = build_graph(deps, checkpointer=MemorySaver())
    out = await graph.ainvoke(state0(), CFG)
    assert "__interrupt__" in out
    snap = await graph.aget_state(CFG)
    assert snap.next == ("wait_plan_approval",)
    values = snap.values
    assert values["plan"].startswith("## Plan for Goal")
    assert all(f"### {s}" in values["plan"] for s in PLAN_SECTIONS)
    assert values["plan_discussion_number"] == 1 and values["plan_revision"] == 1
    assert "## Repository" in values["repo_summary"]
    assert sink.types() == ["goal.plan_proposed"]
    ev = sink.events[0]
    # P9.1 (D-53): Plan 본문이 이벤트에 실린다 (Discussion 본문과 동일)
    assert ev.payload == {
        "plan_discussion_number": 1,
        "revision": 1,
        "plan_markdown": values["plan"],
    }
    assert ev.correlation_id == "G1" and ev.causation_id is None and ev.actor.id == "orchestrator"
    assert values["last_event_id"] == ev.id
    assert len(provider.calls) == 1 and provider.calls[0].system  # analyze.md 시스템 프롬프트
    assert "Add users endpoint" in provider.calls[0].messages[0].content


# P9 발견: 제목이 "Plan #<rev>: <goal title>"뿐이라 같은 제목의 Goal(같은 예시 버튼)이
# 이전 Goal의 Discussion을 재사용했다(멱등 키 = 제목). Goal 짧은 id를 제목에 넣는다
async def test_plan_discussion_title_is_unique_per_goal() -> None:
    deps1, sink1, _, _ = make([PLAN_JSON])
    shared = deps1.discussions
    graph1 = build_graph(deps1, checkpointer=MemorySaver())
    await graph1.ainvoke(state0(), CFG)
    deps2, sink2, _, _ = make([PLAN_JSON])
    deps2.discussions = shared  # 같은 repo의 Discussions
    graph2 = build_graph(deps2, checkpointer=MemorySaver())
    s2 = state0()
    s2["goal_id"] = "G2"
    await graph2.ainvoke(s2, {"configurable": {"thread_id": "G2"}})
    n1 = sink1.events[0].payload["plan_discussion_number"]
    n2 = sink2.events[0].payload["plan_discussion_number"]
    assert n1 != n2
    titles = {d["title"] for d in shared.snapshot()["repos"]["org/demo"]["discussions"].values()}
    assert len(titles) == 2
    assert any("G1" in t and "Add users endpoint" in t and t.startswith("Plan #1") for t in titles)
    assert any("G2" in t for t in titles)


async def test_plan_retry_once_then_error() -> None:
    deps, sink, provider, _ = make(["garbage", PLAN_JSON])
    graph = build_graph(deps, checkpointer=MemorySaver())
    await graph.ainvoke(state0(), CFG)
    assert len(provider.calls) == 2 and "garbage" in provider.calls[1].messages[-2].content
    deps2, _, provider2, _ = make(["garbage", "still garbage"])
    graph2 = build_graph(deps2, checkpointer=MemorySaver())
    with pytest.raises(PlanError):
        await graph2.ainvoke(state0(), {"configurable": {"thread_id": "G2"}})
    assert len(provider2.calls) == 2


# (b) approve → goal.activated → decompose → emit → END, causation chain
async def test_resume_approved() -> None:
    deps, sink, provider, _ = make([PLAN_JSON, DECOMPOSE_JSON])
    graph = build_graph(deps, checkpointer=MemorySaver())
    await graph.ainvoke(state0(), CFG)
    out = await graph.ainvoke(Command(resume={"approved": True, "by": "alice"}), CFG)
    assert "__interrupt__" not in out
    assert (await graph.aget_state(CFG)).next == ()
    assert sink.types() == [
        "goal.plan_proposed",
        "goal.activated",
        "goal.decomposed",  # D-56: 분해 원문 꼬리·정규화 기록·Task 제목 보존
        "epic.created",
        "task.created",
        "task.created",
    ]
    # causation 체인: 각 이벤트의 causation = 직전 이벤트 id
    for prev, cur in zip(sink.events, sink.events[1:], strict=False):
        assert cur.causation_id == prev.id
    assert sink.events[1].payload == {"by": "alice"}
    decomposed = sink.events[2].payload
    assert decomposed["tasks"] == ["Add users route", "Add users validation"]
    assert isinstance(decomposed["changes"], list) and '"tasks"' in decomposed["raw_tail"]
    assert [t["title"] for t in out["tasks"]] == ["Add users route", "Add users validation"]
    assert out["epics"][0]["title"] == "Users API"
    assert out["issues"] == [
        {"task_id": "T1", "issue_number": 1},
        {"task_id": "T2", "issue_number": 2},
    ]
    assert out.get("error") is None and out["approval"] == {"approved": True, "by": "alice"}
    assert (
        len(provider.calls) == 2
        and provider.calls[1].messages[0].content.count("## Plan for Goal") == 1
    )


# (c) reject → goal.cancelled, decompose 호출 없음
async def test_resume_rejected() -> None:
    deps, sink, provider, _ = make([PLAN_JSON, DECOMPOSE_JSON])
    graph = build_graph(deps, checkpointer=MemorySaver())
    await graph.ainvoke(state0(), CFG)
    out = await graph.ainvoke(
        Command(resume={"approved": False, "by": "bob", "reason": "too big"}), CFG
    )
    assert sink.types() == ["goal.plan_proposed", "goal.cancelled"]
    assert sink.events[1].payload == {"by": "bob", "reason": "too big"}
    assert len(provider.calls) == 1  # decompose 안 부름
    assert out.get("tasks") in (None, []) and (await graph.aget_state(CFG)).next == ()


# (d) 같은 체크포인터로 재빌드 → analyze/draft 재실행 없음
async def test_rebuild_with_same_checkpointer_resumes() -> None:
    saver = MemorySaver()
    deps, sink, provider, _ = make([PLAN_JSON, DECOMPOSE_JSON])
    await build_graph(deps, checkpointer=saver).ainvoke(state0(), CFG)
    calls_before = len(provider.calls)
    graph2 = build_graph(deps, checkpointer=saver)  # 새 그래프 객체, 같은 저장소
    out = await graph2.ainvoke(Command(resume={"approved": True, "by": "alice"}), CFG)
    assert len(provider.calls) == calls_before + 1  # decompose 1회만
    assert sink.types().count("goal.plan_proposed") == 1
    assert len(out["issues"]) == 2


# (e) goal.created는 발행하지 않는다
async def test_no_goal_created_event() -> None:
    deps, sink, _, _ = make([PLAN_JSON, DECOMPOSE_JSON])
    graph = build_graph(deps, checkpointer=MemorySaver())
    await graph.ainvoke(state0(), CFG)
    await graph.ainvoke(Command(resume={"approved": True, "by": "a"}), CFG)
    assert "goal.created" not in sink.types()


# decompose 실패 → goal.blocked, error
async def test_decompose_failure_blocks_goal() -> None:
    deps, sink, provider, _ = make([PLAN_JSON, "bad", "bad"])
    graph = build_graph(deps, checkpointer=MemorySaver())
    await graph.ainvoke(state0(), CFG)
    out = await graph.ainvoke(Command(resume={"approved": True, "by": "a"}), CFG)
    assert sink.types() == ["goal.plan_proposed", "goal.activated", "goal.blocked"]
    assert "decompose" in out["error"] and out.get("issues") in (None, [])


# (f) checkpointer 선택
def test_get_checkpointer_and_conn_string(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(
        get_checkpointer(Settings(_env_file=None, database_url="sqlite+aiosqlite:///x.db")),
        MemorySaver,
    )
    assert (
        postgres_conn_string("postgresql+asyncpg://u:p@h:5432/db") == "postgresql://u:p@h:5432/db"
    )
    assert postgres_conn_string("postgresql://u:p@h/db") == "postgresql://u:p@h/db"
    called: list[str] = []

    class FakeSaver:
        @classmethod
        def from_conn_string(cls, conn: str) -> str:
            called.append(conn)
            return "cm"

    monkeypatch.setattr(graph_mod, "AsyncPostgresSaver", FakeSaver)
    pg = Settings(_env_file=None, database_url="postgresql+asyncpg://u:p@h:5432/db")
    assert open_postgres_checkpointer(pg) == "cm" and called == ["postgresql://u:p@h:5432/db"]
    with pytest.raises(ValueError, match="postgres"):
        get_checkpointer(pg)
