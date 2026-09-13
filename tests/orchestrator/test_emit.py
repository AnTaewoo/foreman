"""P3.5 — orchestrator/emit.py (red a~e): 위상 정렬, 사이클, owned_paths 직렬화, Issue(dry), 멱등."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from control_plane.events.schema import Event, EventType
from control_plane.orchestrator.drafts import TaskDraft
from control_plane.orchestrator.emit import (
    CycleError,
    emit,
    paths_overlap,
    serialize_overlaps,
    toposort,
)
from control_plane.orchestrator.state import OrchestratorState, initial_state
from github_adapter.dry_run import DryRunGitHubClient


def td(
    title: str, deps: list[str] | None = None, paths: list[str] | None = None, epic: str = "E1"
) -> TaskDraft:
    return TaskDraft(
        title=title, spec=f"do {title}", kind="feature", role_required="coding",
        depends_on=deps or [], owned_paths=paths or [f"src/{title.lower()}.py"], estimated_tier="T1",
        epic=epic,
    )  # fmt: skip


class Sink:
    def __init__(self) -> None:
        self.events: list[Event] = []

    async def publish(self, event: Event) -> Event:
        self.events.append(event)
        return event

    def types(self) -> list[str]:
        return [e.type.value for e in self.events]


def state_with(
    tasks: list[TaskDraft], epics: list[str] | None = None, **extra: Any
) -> OrchestratorState:
    st = initial_state(
        project_id="P1", goal_id="G1", goal_title="goal", goal_description="d",
        repo_path="/tmp/x", repo_full_name="org/demo", last_event_id="EV0",
    )  # fmt: skip
    st["tasks"] = [t.model_dump() for t in tasks]
    st["epics"] = [
        {"title": e, "order": i + 1, "summary": ""} for i, e in enumerate(epics or ["E1"])
    ]
    st.update(extra)  # type: ignore[typeddict-item]
    return st


# toposort / paths_overlap / serialize_overlaps 단위
def test_toposort_keeps_input_order_for_ready_nodes() -> None:
    tasks = [td("D", ["B", "C"]), td("A"), td("C", ["A"]), td("B", ["A"])]
    assert [t.title for t in toposort(tasks)] == ["A", "C", "B", "D"]
    with pytest.raises(CycleError, match="A"):
        toposort([td("A", ["B"]), td("B", ["A"])])


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("src/app/users.py", "src/app/users.py", True),
        ("src/app/**", "src/app/users.py", True),
        ("src/**", "src/app/models.py", True),
        ("src/app/users.py", "src/app/user_store.py", False),  # 접두 문자열이 아니라 경로 구성요소
        ("src/app/*.py", "src/app/models/x.py", True),  # 보수적: 같은 디렉토리 접두
        ("tests/**", "src/**", False),
        ("docs/a.md", "docs/b.md", False),
    ],
)
def test_paths_overlap(a: str, b: str, expected: bool) -> None:
    assert paths_overlap(a, b) is expected and paths_overlap(b, a) is expected


def test_serialize_overlaps_adds_dependency_later_on_earlier() -> None:
    b = td("B", paths=["src/app/**"])
    c = td("C", paths=["src/app/users.py"])
    out = serialize_overlaps([b, c])
    assert out[1].depends_on == ["B"] and out[0].depends_on == []
    # 반대 방향 의존이 이미 있으면 유지
    b2 = td("B", ["C"], paths=["src/app/**"])
    out2 = serialize_overlaps([b2, c])
    assert out2[0].depends_on == ["C"] and out2[1].depends_on == []


# (a) 4 Task, Epic 2 → epic.created 2 → task.created 4 (위상 순), payload, dry Issue 4
async def test_emit_happy_path() -> None:
    sink = Sink()
    gh = DryRunGitHubClient()
    tasks = [
        td("A", epic="E1"),
        td("B", ["A"], epic="E1"),
        td("C", ["A"], epic="E2"),
        td("D", ["B", "C"], epic="E2"),
    ]
    st = state_with(tasks, epics=["E1", "E2"])
    result = await emit(st, github=gh, publish=sink.publish)
    assert result.get("error") is None
    assert sink.types() == ["epic.created", "epic.created"] + ["task.created"] * 4
    epics = sink.events[:2]
    assert [e.payload["title"] for e in epics] == ["E1", "E2"]
    assert all(
        e.payload["milestone_number"] in (1, 2) and e.payload["goal_id"] == "G1" for e in epics
    )
    created = sink.events[2:]
    assert [e.payload["title"] for e in created] == ["A", "B", "C", "D"]
    ids = {e.payload["title"]: e.subject.id for e in created}
    d = created[3].payload
    assert set(d) >= {"epic_id", "epic_title", "title", "spec", "kind", "role_required", "depends_on",
                      "owned_paths", "risk_tier", "issue_number", "issue_url"}  # fmt: skip
    assert (
        d["depends_on"] == [ids["B"], ids["C"]]
        and d["epic_title"] == "E2"
        and d["risk_tier"] == "T1"
    )
    assert d["issue_number"] == 4 and d["issue_url"].endswith("/issues/4")
    assert d["epic_id"] == epics[1].subject.id
    # causation 체인: EV0 → epic1 → epic2 → A → B → C → D
    prev = "EV0"
    for e in sink.events:
        assert e.causation_id == prev
        prev = e.id
    assert result["last_event_id"] == sink.events[-1].id
    assert [i["issue_number"] for i in result["issues"]] == [1, 2, 3, 4]
    snap = gh.snapshot()["repos"]["org/demo"]
    assert len(snap["issues"]) == 4 and len(snap["milestones"]) == 2 and len(snap["labels"]) == 22
    assert snap["issues"][1]["labels"] == [
        "ai:task",
        "role:coding",
        "kind:feature",
        "tier:T1",
        "epic:1",
    ]


# (b) 사이클 → goal.blocked, 이벤트 0
async def test_emit_cycle_blocks_goal() -> None:
    sink = Sink()
    gh = DryRunGitHubClient()
    st = state_with([td("A", ["B"]), td("B", ["A"])])
    result = await emit(st, github=gh, publish=sink.publish)
    assert sink.types() == ["goal.blocked"] and "cycle" in sink.events[0].payload["reason"]
    assert result["issues"] == [] and "cycle" in result["error"]
    assert result["last_event_id"] == sink.events[0].id
    assert gh.snapshot()["repos"] == {}  # Issue·milestone 없음


# (c) owned_paths 겹침 → C.depends_on에 B, 순서 B → C
async def test_emit_serializes_overlaps() -> None:
    sink = Sink()
    st = state_with([td("C", paths=["src/app/users.py"]), td("B", paths=["src/app/**"])])
    await emit(st, github=DryRunGitHubClient(), publish=sink.publish)
    created = [e for e in sink.events if e.type is EventType.TASK_CREATED]
    # 입력 순서는 C, B지만 겹침 직렬화로 B(먼저 나온 것)가 선행? — 규칙: 나중 Task가 앞 Task에 의존
    titles = [e.payload["title"] for e in created]
    ids = {e.payload["title"]: e.subject.id for e in created}
    assert titles == ["C", "B"]
    assert created[1].payload["depends_on"] == [ids["C"]]


# (d) owned_paths 빈 Task → ValidationError
def test_empty_owned_paths_rejected() -> None:
    with pytest.raises(ValidationError):
        TaskDraft(title="X", spec="s", owned_paths=[])


# (e) 같은 state로 재실행 → 동일 issues, 중복 없음
async def test_emit_is_idempotent() -> None:
    sink = Sink()
    gh = DryRunGitHubClient()
    st = state_with([td("A"), td("B", ["A"])])
    first = await emit(st, github=gh, publish=sink.publish)
    n_events = len(sink.events)
    st2: OrchestratorState = {
        **st,
        "issues": first["issues"],
        "last_event_id": first["last_event_id"],
    }
    second = await emit(st2, github=gh, publish=sink.publish)
    assert second["issues"] == first["issues"]
    assert len(sink.events) == n_events  # 이벤트 중복 없음
    assert len(gh.snapshot()["repos"]["org/demo"]["issues"]) == 2
