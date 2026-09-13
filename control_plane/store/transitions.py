"""상태 전이 표 — 유일한 정의 지점 (D-08). projection과 API 양쪽이 ``assert_transition``을 호출한다.

설계 §6.1(Task), §6.2(Decision), Goal/Epic은 §4.1 상태값 + ROADMAP P1.2 (e). D-28로 ``assigned`` 실패 경로 추가.
"""

from __future__ import annotations

from enum import StrEnum

from control_plane.store.enums import DecisionStatus, EpicStatus, GoalStatus, TaskStatus


class InvalidTransition(Exception):
    """불허 전이. 메시지에 출발/도착 상태 이름을 담는다."""

    def __init__(self, src: StrEnum, dst: StrEnum) -> None:
        self.src = src
        self.dst = dst
        super().__init__(f"{type(src).__name__}: {src.value} -> {dst.value} is not allowed")


_T = TaskStatus
_G = GoalStatus
_E = EpicStatus
_D = DecisionStatus

# §6.1 — "any → cancelled"에서 done/cancelled는 제외 (머지된 것은 취소할 수 없고, 자기 전이는 무의미).
TASK_ALLOWED: frozenset[tuple[TaskStatus, TaskStatus]] = frozenset(
    {
        (_T.DRAFT, _T.READY),
        (_T.READY, _T.READY),  # depends_on 미해소
        (_T.READY, _T.ASSIGNED),  # scheduler 배정
        (_T.ASSIGNED, _T.RUNNING),
        (_T.ASSIGNED, _T.READY),  # D-28 워커 기동 실패, attempt < max
        (_T.ASSIGNED, _T.BLOCKED),  # D-28 워커 기동 실패, attempt ≥ max
        (_T.RUNNING, _T.AWAITING_DECISION),  # needs_decision
        (_T.RUNNING, _T.IN_REVIEW),  # 구현 완료 → PR 단계
        (_T.RUNNING, _T.READY),  # fail, attempt < max
        (_T.RUNNING, _T.BLOCKED),  # fail, attempt ≥ max
        (_T.AWAITING_DECISION, _T.RUNNING),  # approved
        (_T.AWAITING_DECISION, _T.READY),  # changes → spec 갱신
        (_T.IN_REVIEW, _T.DONE),  # approved + merged
        (_T.BLOCKED, _T.READY),  # 사람 승인 / task.retried
    }
    | {(s, _T.CANCELLED) for s in _T if s not in (_T.DONE, _T.CANCELLED)}
)

GOAL_ALLOWED: frozenset[tuple[GoalStatus, GoalStatus]] = frozenset(
    {
        (_G.DRAFT, _G.PLANNING),
        (_G.PLANNING, _G.AWAITING_PLAN_APPROVAL),
        (_G.AWAITING_PLAN_APPROVAL, _G.ACTIVE),
        (_G.AWAITING_PLAN_APPROVAL, _G.PLANNING),  # /changes 재계획 (B6)
        (_G.ACTIVE, _G.BLOCKED),
        (_G.BLOCKED, _G.ACTIVE),
        (_G.ACTIVE, _G.DONE),
    }
    | {(s, _G.CANCELLED) for s in _G if s not in (_G.DONE, _G.CANCELLED)}
)

EPIC_ALLOWED: frozenset[tuple[EpicStatus, EpicStatus]] = frozenset(
    {
        (_E.PENDING, _E.ACTIVE),
        (_E.ACTIVE, _E.ACTIVE),  # epic.activated 멱등 (B11)
        (_E.ACTIVE, _E.DONE),
    }
)

# §6.2
DECISION_ALLOWED: frozenset[tuple[DecisionStatus, DecisionStatus]] = frozenset(
    {
        (_D.OPEN, _D.OPEN),  # agent votes 수집
        (_D.OPEN, _D.APPROVED),
        (_D.OPEN, _D.REJECTED),
        (_D.OPEN, _D.CHANGES_REQUESTED),
        (_D.CHANGES_REQUESTED, _D.OPEN),  # 재제출, new revision
        (_D.OPEN, _D.EXPIRED),
        (_D.EXPIRED, _D.OPEN),  # escalation: 알림 재발송 / 상위 승인자
        (_D.EXPIRED, _D.REJECTED),  # escalation: 자동 reject
    }
)

_TABLES: dict[type[StrEnum], frozenset[tuple[StrEnum, StrEnum]]] = {
    TaskStatus: TASK_ALLOWED,
    GoalStatus: GOAL_ALLOWED,
    EpicStatus: EPIC_ALLOWED,
    DecisionStatus: DECISION_ALLOWED,
}


def _table_for(src: StrEnum) -> frozenset[tuple[StrEnum, StrEnum]]:
    try:
        return _TABLES[type(src)]
    except KeyError as exc:
        raise TypeError(f"no transition table for {type(src).__name__}") from exc


def assert_transition[S: StrEnum](
    src: S, dst: S, table: frozenset[tuple[S, S]] | None = None
) -> None:
    """``src → dst``가 허용 전이가 아니면 ``InvalidTransition``. 서로 다른 Enum이면 ``TypeError``."""
    if type(src) is not type(dst):
        raise TypeError(f"mixed enums: {type(src).__name__} vs {type(dst).__name__}")
    allowed = table if table is not None else _table_for(src)
    if (src, dst) not in allowed:
        raise InvalidTransition(src, dst)


def allowed_targets[S: StrEnum](src: S) -> set[S]:
    """``src``에서 갈 수 있는 상태 집합."""
    return {dst for (s, dst) in _table_for(src) if s is src}  # type: ignore[misc]  # 표는 동종 Enum
