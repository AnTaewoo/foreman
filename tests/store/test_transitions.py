"""P1.2 — 상태 전이 표 (설계 §6.1 Task, §6.2 Decision, Goal/Epic; D-28). 리터럴 양방향 비교."""

from __future__ import annotations

import pytest

from control_plane.events import schema
from control_plane.store.enums import (
    AgentOutcome,
    DecisionStatus,
    EpicStatus,
    GoalStatus,
    RunOutcome,
    TaskStatus,
)
from control_plane.store.transitions import (
    DECISION_ALLOWED,
    EPIC_ALLOWED,
    GOAL_ALLOWED,
    TASK_ALLOWED,
    InvalidTransition,
    allowed_targets,
    assert_transition,
)

T = TaskStatus
G = GoalStatus
E = EpicStatus
D = DecisionStatus

# 설계 §6.1의 화살표 전부 (D-28 assigned→ready/blocked 포함). "any → cancelled"는 done/cancelled 제외.
TASK_ARROWS: set[tuple[TaskStatus, TaskStatus]] = {
    (T.DRAFT, T.READY),
    (T.READY, T.READY),  # depends_on 미해소 자기 전이
    (T.READY, T.ASSIGNED),  # scheduler 배정
    (T.ASSIGNED, T.RUNNING),
    (T.ASSIGNED, T.READY),  # D-28 워커 기동 실패 (attempt < max)
    (T.ASSIGNED, T.BLOCKED),  # D-28 (attempt ≥ max)
    (T.RUNNING, T.AWAITING_DECISION),  # needs_decision
    (T.RUNNING, T.IN_REVIEW),  # done → PR 단계
    (T.RUNNING, T.READY),  # fail (attempt < max)
    (T.RUNNING, T.BLOCKED),  # fail (attempt ≥ max)
    (T.AWAITING_DECISION, T.RUNNING),  # approved
    (T.AWAITING_DECISION, T.READY),  # changes → spec 갱신
    (T.IN_REVIEW, T.DONE),  # approved + merged
    (T.BLOCKED, T.READY),  # 사람 승인 / task.retried
} | {(s, T.CANCELLED) for s in T if s not in (T.DONE, T.CANCELLED)}

GOAL_ARROWS: set[tuple[GoalStatus, GoalStatus]] = {
    (G.DRAFT, G.PLANNING),
    (G.PLANNING, G.AWAITING_PLAN_APPROVAL),
    (G.AWAITING_PLAN_APPROVAL, G.ACTIVE),
    (G.AWAITING_PLAN_APPROVAL, G.PLANNING),  # /changes 재계획
    (G.ACTIVE, G.BLOCKED),
    (G.BLOCKED, G.ACTIVE),
    (G.ACTIVE, G.DONE),
} | {(s, G.CANCELLED) for s in G if s not in (G.DONE, G.CANCELLED)}

EPIC_ARROWS: set[tuple[EpicStatus, EpicStatus]] = {
    (E.PENDING, E.ACTIVE),
    (E.ACTIVE, E.ACTIVE),  # epic.activated 멱등 (B11)
    (E.ACTIVE, E.DONE),
}

# §6.2
DECISION_ARROWS: set[tuple[DecisionStatus, DecisionStatus]] = {
    (D.OPEN, D.OPEN),  # agent votes 수집
    (D.OPEN, D.APPROVED),
    (D.OPEN, D.REJECTED),
    (D.OPEN, D.CHANGES_REQUESTED),
    (D.CHANGES_REQUESTED, D.OPEN),  # 재제출 (new revision)
    (D.OPEN, D.EXPIRED),
    (D.EXPIRED, D.OPEN),  # escalation: 알림 재발송 / 상위 승인자
    (D.EXPIRED, D.REJECTED),  # escalation: 자동 reject
}


# (a) 리터럴 양방향
def test_task_table_matches_design_arrows_exactly() -> None:
    assert set(TASK_ALLOWED) == TASK_ARROWS
    assert {d for (s, d) in TASK_ALLOWED if s is T.IN_REVIEW} == {T.DONE, T.CANCELLED}
    assert (T.RUNNING, T.DONE) not in TASK_ALLOWED  # done은 in_review를 거친다


def test_goal_epic_decision_tables_match() -> None:
    assert set(GOAL_ALLOWED) == GOAL_ARROWS
    assert set(EPIC_ALLOWED) == EPIC_ARROWS
    assert set(DECISION_ALLOWED) == DECISION_ARROWS


# (b) 불허 → InvalidTransition (메시지에 두 상태 이름)
@pytest.mark.parametrize(
    ("src", "dst"),
    [(T.READY, T.RUNNING), (T.DRAFT, T.ASSIGNED), (T.DONE, T.READY), (T.CANCELLED, T.READY)],
)
def test_assert_transition_rejects(src: TaskStatus, dst: TaskStatus) -> None:
    with pytest.raises(InvalidTransition) as exc:
        assert_transition(src, dst)
    assert src.value in str(exc.value) and dst.value in str(exc.value)


def test_assert_transition_allows_and_returns_none() -> None:
    assert assert_transition(T.READY, T.ASSIGNED) is None
    assert assert_transition(G.DRAFT, G.PLANNING) is None
    assert assert_transition(E.PENDING, E.ACTIVE) is None
    assert assert_transition(D.OPEN, D.APPROVED) is None
    assert assert_transition(T.READY, T.ASSIGNED, table=TASK_ALLOWED) is None


def test_assert_transition_mixed_enums_is_error() -> None:
    with pytest.raises(TypeError):
        assert_transition(T.READY, G.PLANNING)  # type: ignore[type-var]


# (c) any → cancelled
@pytest.mark.parametrize("src", [s for s in T if s not in (T.DONE, T.CANCELLED)])
def test_any_task_state_can_be_cancelled(src: TaskStatus) -> None:
    assert_transition(src, T.CANCELLED)


def test_done_and_cancelled_cannot_be_cancelled() -> None:
    for src in (T.DONE, T.CANCELLED):
        with pytest.raises(InvalidTransition):
            assert_transition(src, T.CANCELLED)


# (d) blocked 탈출은 ready, cancelled뿐
def test_blocked_exits_only_to_ready_or_cancelled() -> None:
    assert allowed_targets(T.BLOCKED) == {T.READY, T.CANCELLED}
    assert allowed_targets(G.BLOCKED) == {G.ACTIVE, G.CANCELLED}
    assert allowed_targets(E.DONE) == set()


# (e) Goal 전이 세부
def test_goal_transitions_detail() -> None:
    assert_transition(G.AWAITING_PLAN_APPROVAL, G.PLANNING)
    with pytest.raises(InvalidTransition):
        assert_transition(G.AWAITING_PLAN_APPROVAL, G.BLOCKED)  # B5: activated 먼저
    with pytest.raises(InvalidTransition):
        assert_transition(G.DRAFT, G.ACTIVE)


# enums가 schema.py의 값 집합을 미러 (D-28)
def test_enums_mirror_schema_outcome_values() -> None:
    assert {m.value for m in RunOutcome} == set(schema.RUN_OUTCOMES)
    assert {m.value for m in AgentOutcome} == set(schema.AGENT_OUTCOMES)


def test_task_status_values_are_design_6_1() -> None:
    assert {m.value for m in TaskStatus} == {
        "draft", "ready", "assigned", "running", "awaiting_decision", "in_review", "done",
        "blocked", "cancelled",
    }  # fmt: skip
    assert {m.value for m in GoalStatus} == {
        "draft", "planning", "awaiting_plan_approval", "active", "blocked", "done", "cancelled"
    }
    assert {m.value for m in EpicStatus} == {"pending", "active", "done"}
    assert {m.value for m in DecisionStatus} == {
        "open", "approved", "rejected", "changes_requested", "expired"
    }
