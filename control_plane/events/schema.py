"""이벤트 스키마 (설계 §4.1 Event, §4.2). PC-1 이후 **추가만** — 필드·이름 삭제/변경 금지.

봉투 규약 (D-25, D-26, D-29):
- ``correlation_id`` 필수. Goal 스코프면 goal_id, Goal 밖(project/policy/budget/agent/control)은
  project_id.
- ``causation_id``는 직전 원인 이벤트 id. 루트(사람·API 명령이 원인)는 ``None``. 필드 누락은 오류.
- ``signature``는 발행자가 아니라 **저장 시점**에 ``events/chain.py``가 채운다. 발행자는 ``None``.
  서명 대상은 ``canonical_json()`` 텍스트, 체인 순서는 DB append 순번(``seq``).
- payload 값은 JSON 원시형(str/int/float/bool/None/list/dict)만. NaN/Infinity/Decimal/datetime
  금지 — JSONB 왕복 후에도 canonical 텍스트가 안정적이어야 한다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Literal, NotRequired, TypedDict, get_origin, get_type_hints

from pydantic import BaseModel, ConfigDict, Field, field_validator
from ulid import ULID

# --------------------------------------------------------------------------- EventType


class EventType(StrEnum):
    """``<domain>.<name>``. 설계 §4.2 + D-19/D-27/D-31 = 44개. 표의 행은 그룹, 프리픽스가 도메인."""

    # goal
    GOAL_CREATED = "goal.created"
    GOAL_PLAN_PROPOSED = "goal.plan_proposed"
    GOAL_ACTIVATED = "goal.activated"
    GOAL_DECOMPOSED = "goal.decomposed"  # D-56 (additive): 분해 결과 기록
    GOAL_BLOCKED = "goal.blocked"
    GOAL_COMPLETED = "goal.completed"
    GOAL_CANCELLED = "goal.cancelled"
    # epic (D-27)
    EPIC_CREATED = "epic.created"
    EPIC_ACTIVATED = "epic.activated"
    EPIC_COMPLETED = "epic.completed"
    # task
    TASK_CREATED = "task.created"
    TASK_ASSIGNED = "task.assigned"
    TASK_STARTED = "task.started"
    TASK_BLOCKED = "task.blocked"
    TASK_COMPLETED = "task.completed"
    TASK_FAILED = "task.failed"
    TASK_RETRIED = "task.retried"
    TASK_ESCALATED = "task.escalated"
    TASK_CANCELLED = "task.cancelled"  # D-27
    # run
    RUN_STARTED = "run.started"
    RUN_TOOL_CALLED = "run.tool_called"  # 감사 체인 밖 (D-31)
    RUN_TOOL_DENIED = "run.tool_denied"  # D-31
    RUN_ARTIFACT_PRODUCED = "run.artifact_produced"
    RUN_FINISHED = "run.finished"
    # decision
    DECISION_OPENED = "decision.opened"
    DECISION_AGENT_VOTED = "decision.agent_voted"
    DECISION_HUMAN_RESPONDED = "decision.human_responded"
    DECISION_RESOLVED = "decision.resolved"
    DECISION_EXPIRED = "decision.expired"
    # pr
    PR_OPENED = "pr.opened"
    PR_CHECKS_PASSED = "pr.checks_passed"
    PR_CHECKS_FAILED = "pr.checks_failed"
    PR_REVIEW_SUBMITTED = "pr.review_submitted"
    PR_MERGED = "pr.merged"
    PR_CLOSED = "pr.closed"
    # policy / budget
    POLICY_UPDATED = "policy.updated"
    POLICY_TIER_OVERRIDDEN = "policy.tier_overridden"
    BUDGET_WARNING = "budget.warning"
    BUDGET_EXCEEDED = "budget.exceeded"
    # control (project / agent / control)
    PROJECT_CREATED = "project.created"
    PROJECT_UPDATED = "project.updated"
    PROJECT_PAUSED = "project.paused"
    PROJECT_RESUMED = "project.resumed"
    AGENT_KILLED = "agent.killed"
    CONTROL_EMERGENCY_STOP = "control.emergency_stop"

    @property
    def domain(self) -> str:
        """프리픽스 = 도메인 (예: ``budget.warning`` → ``budget``)."""
        return self.value.partition(".")[0]


# 감사 체인 밖 타입: events 테이블이 아니라 tool_calls 테이블에만 저장, 서명 없음 (D-31)
UNCHAINED: frozenset[EventType] = frozenset({EventType.RUN_TOOL_CALLED})

# --------------------------------------------------------------------------- 값 집합

ActorType = Literal["agent", "human", "system", "github"]
EntityType = Literal["project", "goal", "epic", "task", "run", "decision", "pr", "agent", "policy"]

# Run.outcome (설계 §4.1) / AgentOutput.outcome (§5.1) — D-28. store/enums.py(P1.2)가 미러한다.
RUN_OUTCOMES: tuple[str, ...] = ("success", "failed", "timeout", "cancelled", "escalated")
AGENT_OUTCOMES: tuple[str, ...] = ("done", "needs_decision", "blocked", "failed", "timeout")
RunOutcomeValue = Literal["success", "failed", "timeout", "cancelled", "escalated"]
AgentOutcomeValue = Literal["done", "needs_decision", "blocked", "failed", "timeout"]

# --------------------------------------------------------------------------- 봉투


class Actor(BaseModel):
    """누가 일으켰나. id: agent=agent_id, human=user id, system=컴포넌트명, github=login."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    type: ActorType
    id: str


class Subject(BaseModel):
    """무엇에 대한 이벤트인가. ``pr``의 id는 PR 번호 문자열이고 payload에 ``task_id``를 동반한다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entity: EntityType
    id: str


def _check_json_value(value: object, path: str) -> None:
    """JSON 원시형만 허용. bool은 int의 서브클래스라 isinstance(int)에 걸리지만 그것도 허용 대상."""
    if value is None or isinstance(value, str | bool | int):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"payload{path}: non-finite float not allowed")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json_value(item, f"{path}[{i}]")
        return
    if isinstance(value, dict):
        for k, item in value.items():
            if not isinstance(k, str):
                raise ValueError(f"payload{path}: non-str key {k!r}")
            _check_json_value(item, f"{path}.{k}")
        return
    raise ValueError(f"payload{path}: {type(value).__name__} is not a JSON value")


def _new_ulid() -> str:
    return str(ULID())


def _utc_now() -> datetime:
    return datetime.now(UTC)


class Event(BaseModel):
    """append-only 이벤트 (설계 §4.1). 불변. 서명은 ``signed()``로 새 객체를 만든다."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(default_factory=_new_ulid)
    project_id: str
    ts: datetime = Field(default_factory=_utc_now)
    actor: Actor
    type: EventType
    subject: Subject
    # payload 형태는 PAYLOAD_TYPES의 TypedDict로 문서화 (D-04). 값 타입은 JSON 원시형으로 제한.
    payload: dict[str, Any] = Field(default_factory=dict)  # Any: 이벤트 타입별 형태가 다름
    correlation_id: str
    causation_id: str | None
    signature: str | None = None

    @field_validator("ts")
    @classmethod
    def _normalize_utc(cls, v: datetime) -> datetime:
        if v.tzinfo is None:
            return v.replace(tzinfo=UTC)
        return v.astimezone(UTC)

    @field_validator("payload")
    @classmethod
    def _json_only(cls, v: dict[str, Any]) -> dict[str, Any]:
        _check_json_value(v, "")
        return v


# ------------------------------------------------------------ canonical / sign / verify


def canonical_json(event: Event) -> str:
    """서명 대상 텍스트. 키 정렬, 공백 없음, 비ASCII 유지, ``signature`` 제외, NaN 금지."""
    body = event.model_dump(mode="json", exclude={"signature"})
    return json.dumps(
        body, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )


def sign(prev_signature: str | None, canonical: str) -> str:
    """SHA-256(prev ‖ "\\n" ‖ canonical). 루트는 prev=None(빈 문자열)."""
    data = (prev_signature or "") + "\n" + canonical
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def signed(event: Event, signature: str) -> Event:
    """서명이 채워진 사본."""
    return event.model_copy(update={"signature": signature})


def verify_chain(events: Sequence[Event]) -> bool:
    """주어진 순서대로 체인을 재계산해 전부 일치하면 True. 미서명 이벤트가 있으면 False."""
    prev: str | None = None
    for event in events:
        if event.signature is None:
            return False
        if sign(prev, canonical_json(event)) != event.signature:
            return False
        prev = event.signature
    return True


# --------------------------------------------------------------------------- payload 형태 (D-04)
# TypedDict는 문서화 + append 시 필수 키 검사에만. 값 타입은 강제하지 않는다(강타입화는 "추가").


class ProjectCreatedPayload(TypedDict):
    name: str
    repo: str  # repo_full_name 또는 로컬 경로
    default_branch: str
    members: NotRequired[list[dict[str, str]]]  # P5.2 추가(additive): [{user_id, role}] §4.1


class ProjectUpdatedPayload(TypedDict):
    """P9 (D-54): 프로젝트 보관/복원. 행·이벤트는 지우지 않는다(append-only). 키는 선택."""

    archived: NotRequired[bool]
    by: NotRequired[str]


class GoalDecomposedPayload(TypedDict):
    """D-56: 분해 결과 기록 — Task 제목, 정규화 변경 목록, 원문 꼬리 (관측용, projection noop)."""

    tasks: list[str]
    changes: NotRequired[list[str]]
    raw_tail: NotRequired[str]
    parsed: NotRequired[
        list[dict[str, Any]]
    ]  # 정규화 전 Task {title, kind, owned_paths, depends_on}


class GoalCreatedPayload(TypedDict):
    title: str
    description: str


class GoalPlanProposedPayload(TypedDict):
    plan_discussion_number: int
    revision: int  # /changes 재제출이면 2 이상 (B6)
    plan_markdown: NotRequired[str]  # Plan 본문 (D-53, additive; Discussion 본문과 동일)


class EpicCreatedPayload(TypedDict):
    goal_id: str
    title: str
    order: int
    milestone_number: int | None


class TaskCreatedPayload(TypedDict):
    epic_id: str
    epic_title: str
    title: str
    spec: str
    kind: str
    role_required: str
    depends_on: list[str]  # task id (제목이 아님)
    owned_paths: list[str]
    risk_tier: str
    issue_number: int | None  # 있으면 draft→ready (D-20)
    issue_url: str | None
    max_attempts: NotRequired[int]


class TaskAssignedPayload(TypedDict):
    agent_id: str
    run_id: str


class TaskStartedPayload(TypedDict):
    run_id: str


class TaskCompletedPayload(TypedDict):
    run_id: str
    pr_number: NotRequired[
        int
    ]  # D-37 이전: 워커가 PR을 열던 시절의 키. 지금은 PrOpener가 pr.opened로
    branch: NotRequired[str]  # D-37 (additive): push한 브랜치 — PrOpener가 head로 쓴다
    summary: NotRequired[str]  # D-37 (additive): LLM 요약 — PR 본문·Issue 코멘트


class RunArtifactProducedPayload(TypedDict):
    kind: str  # branch | pr | comment | log
    ref: str


class TaskFailedPayload(TypedDict):
    run_id: str | None  # 기동 실패도 run_id가 있다(Scheduler 발급). 없을 때만 None
    reason: str  # launch_failed | scope_violation | tests_failed | timeout | ...
    attempt: int  # 이 run의 번호 (Scheduler 기준)
    files: NotRequired[list[str]]
    edit_rounds: NotRequired[int]  # P9: run 안의 편집→테스트 반복 횟수 (additive)
    branch: NotRequired[str]  # P9: WIP 브랜치 (control plane이 GitHub에 push, additive)
    test_output: NotRequired[str]  # P9: 마지막 테스트 출력 꼬리 ≤ 2000자 (additive)


class RunStartedPayload(TypedDict):
    task_id: str
    agent_id: str
    model: str


class RunToolCalledPayload(TypedDict):
    tool: str
    args_digest: str  # 인자 sha256 — 비밀값·파일 내용은 싣지 않는다
    duration_ms: NotRequired[int]


class RunToolDeniedPayload(TypedDict):
    tool: str
    reason: str
    args_digest: str


class RunFinishedPayload(TypedDict):
    outcome: RunOutcomeValue  # Run.outcome (D-28)
    agent_outcome: AgentOutcomeValue  # AgentOutput.outcome 원값
    tokens_in: int
    tokens_out: int
    cost_usd: float
    duration_s: float
    error: str | None


class PrOpenedPayload(TypedDict):
    task_id: str
    run_id: str
    pr_number: int
    head: str
    base: str
    draft: NotRequired[bool]
    url: NotRequired[str]


PAYLOAD_TYPES: dict[EventType, type[Any]] = {  # Any: TypedDict 클래스들
    EventType.PROJECT_CREATED: ProjectCreatedPayload,
    EventType.PROJECT_UPDATED: ProjectUpdatedPayload,
    EventType.GOAL_CREATED: GoalCreatedPayload,
    EventType.GOAL_PLAN_PROPOSED: GoalPlanProposedPayload,
    EventType.GOAL_DECOMPOSED: GoalDecomposedPayload,
    EventType.EPIC_CREATED: EpicCreatedPayload,
    EventType.TASK_CREATED: TaskCreatedPayload,
    EventType.TASK_ASSIGNED: TaskAssignedPayload,
    EventType.TASK_STARTED: TaskStartedPayload,
    EventType.TASK_COMPLETED: TaskCompletedPayload,
    EventType.RUN_ARTIFACT_PRODUCED: RunArtifactProducedPayload,  # D-37 (additive)
    EventType.TASK_FAILED: TaskFailedPayload,
    EventType.RUN_STARTED: RunStartedPayload,
    EventType.RUN_TOOL_CALLED: RunToolCalledPayload,
    EventType.RUN_TOOL_DENIED: RunToolDeniedPayload,
    EventType.RUN_FINISHED: RunFinishedPayload,
    EventType.PR_OPENED: PrOpenedPayload,
}


def required_payload_keys(event_type: EventType) -> frozenset[str]:
    """등록된 TypedDict의 필수 키. 미등록 타입은 빈 집합(검사 안 함).

    ``from __future__ import annotations`` 때문에 클래스 생성 시점의 ``__required_keys__``는
    ``NotRequired``를 못 본다. 힌트를 실제로 해석해 ``NotRequired``를 골라낸다.
    """
    td = PAYLOAD_TYPES.get(event_type)
    if td is None:
        return frozenset()
    hints = get_type_hints(td, include_extras=True)
    return frozenset(k for k, h in hints.items() if get_origin(h) is not NotRequired)
