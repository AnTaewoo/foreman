"""SQLAlchemy 모델 (설계 §4.1 + D-29/D-30/D-31). 갱신 로직 없음 — 갱신은 events/projection.py만.

- JSON 컬럼은 Postgres에서 JSONB, sqlite에서 JSON.
- Enum 컬럼은 문자열 값으로 저장하고 ``validate_strings=True``로 Enum 밖 문자열을 거부한다.
- 모든 datetime은 ``UTCDateTime``: 저장 시 UTC 정규화, 읽을 때 tz-aware UTC 보장 (D-05).
- ``events``/``tool_calls``의 PK는 append 순번 ``seq`` (sqlite autoincrement는 INTEGER PK만).
  ULID ``id``는 unique. 커서·replay·검증 순서는 ``seq`` (D-29).
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

from control_plane.store.enums import (
    AgentStatus,
    DecisionStatus,
    DecisionType,
    EpicStatus,
    GoalStatus,
    RiskTier,
    Role,
    RunOutcome,
    TaskKind,
    TaskStatus,
)

# --------------------------------------------------------------------------- 타입


class UTCDateTime(TypeDecorator[datetime]):
    """tz-aware UTC datetime. naive 입력은 UTC로 간주, 읽을 때 항상 tzinfo=UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


JSONType = JSON().with_variant(JSONB(), "postgresql")
BigSeq = BigInteger().with_variant(Integer(), "sqlite")  # sqlite autoincrement는 INTEGER PK만


def enum_col(cls: type[StrEnum], name: str) -> Enum:
    """값 문자열 저장 + 제약 + Enum 밖 문자열 거부."""
    return Enum(
        cls,
        name=name,
        values_callable=lambda e: [m.value for m in e],
        create_constraint=True,
        validate_strings=True,
    )


def utc_now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONType, list[Any]: JSONType, datetime: UTCDateTime}


# --------------------------------------------------------------------------- 엔티티 (§4.1)


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    repo_full_name: Mapped[str] = mapped_column(String(300))  # "owner/repo" 또는 로컬 경로(MVP 1)
    installation_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    policy: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    budget: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    protected_paths: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    members: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # D-54 (0004)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Goal(Base):
    __tablename__ = "goals"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[GoalStatus] = mapped_column(
        enum_col(GoalStatus, "goal_status"), default=GoalStatus.DRAFT
    )
    acceptance_criteria: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    plan_discussion_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    plan_revision: Mapped[int] = mapped_column(Integer, default=0)
    plan_markdown: Mapped[str | None] = mapped_column(Text, nullable=True)  # D-53 (0003)
    llm_profile: Mapped[str | None] = mapped_column(String(40), nullable=True)  # D-57 (0005)
    # P9.11 (0006): Plan이 게시된 곳. None=Discussion, "issue"=Plans 카테고리가 없어 Issue
    plan_kind: Mapped[str | None] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Epic(Base):
    __tablename__ = "epics"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    order: Mapped[int] = mapped_column(Integer, default=0)
    milestone_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[EpicStatus] = mapped_column(
        enum_col(EpicStatus, "epic_status"), default=EpicStatus.PENDING
    )


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    epic_id: Mapped[str] = mapped_column(ForeignKey("epics.id"), index=True)
    # 조회 편의용 비정규화 (scheduler가 project 단위로 ready Task를 찾는다)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    goal_id: Mapped[str] = mapped_column(ForeignKey("goals.id"), index=True)
    issue_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    spec: Mapped[str] = mapped_column(Text)
    kind: Mapped[TaskKind] = mapped_column(enum_col(TaskKind, "task_kind"))
    role_required: Mapped[Role] = mapped_column(enum_col(Role, "role"))
    depends_on: Mapped[list[Any]] = mapped_column(JSONType, default=list)  # task ids
    blocks: Mapped[list[Any]] = mapped_column(JSONType, default=list)  # 역방향, projection이 유지
    owned_paths: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    risk_tier: Mapped[RiskTier] = mapped_column(enum_col(RiskTier, "risk_tier"))
    assignee_agent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    branch_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_merged_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # D-30 (b)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    status: Mapped[TaskStatus] = mapped_column(
        enum_col(TaskStatus, "task_status"), default=TaskStatus.DRAFT, index=True
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now, onupdate=utc_now)


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    agent_id: Mapped[str] = mapped_column(String(64))
    worker_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    started_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    outcome: Mapped[RunOutcome | None] = mapped_column(
        enum_col(RunOutcome, "run_outcome"), nullable=True
    )
    agent_outcome: Mapped[str | None] = mapped_column(String(32), nullable=True)  # D-28 원값
    input_snapshot: Mapped[str | None] = mapped_column(String(128), nullable=True)  # 프롬프트 해시
    tool_call_count: Mapped[int] = mapped_column(Integer, default=0)
    denied_count: Mapped[int] = mapped_column(Integer, default=0)  # run.tool_denied 수
    artifacts: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    log_ref: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Decision(Base):
    """MVP 3에서 사용. 테이블만 먼저 만든다."""

    __tablename__ = "decisions"

    id: Mapped[str] = mapped_column(String(26), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    discussion_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    type: Mapped[DecisionType] = mapped_column(enum_col(DecisionType, "decision_type"))
    risk_tier: Mapped[RiskTier] = mapped_column(enum_col(RiskTier, "risk_tier"))
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    agent_votes: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    quorum: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    human_responses: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    related_task_ids: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    related_pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    escalation_policy: Mapped[str | None] = mapped_column(String(64), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[DecisionStatus] = mapped_column(
        enum_col(DecisionStatus, "decision_status"), default=DecisionStatus.OPEN
    )
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utc_now)


class Agent(Base):
    """Agent 인스턴스. MVP 1은 단일 Coding Agent라 거의 비어 있다."""

    __tablename__ = "agents"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    project_id: Mapped[str] = mapped_column(ForeignKey("projects.id"), index=True)
    role: Mapped[Role] = mapped_column(enum_col(Role, "role"))
    display_name: Mapped[str] = mapped_column(String(100))
    model_settings: Mapped[dict[str, Any]] = mapped_column("model_config", JSONType, default=dict)
    tool_allowlist: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    capabilities: Mapped[list[Any]] = mapped_column(JSONType, default=list)
    status: Mapped[AgentStatus] = mapped_column(
        enum_col(AgentStatus, "agent_status"), default=AgentStatus.IDLE
    )


# --------------------------------------------------------------------------- 이벤트 (append-only)


class Event(Base):
    """append-only. UPDATE는 부기 컬럼(stream_id/published_at/projected_at/projection_error)뿐."""

    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("id", name="uq_events_id"),)

    seq: Mapped[int] = mapped_column(BigSeq, primary_key=True, autoincrement=True)  # D-29
    id: Mapped[str] = mapped_column(String(26))  # ULID, unique
    project_id: Mapped[str] = mapped_column(String(26), index=True)
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
    actor_type: Mapped[str] = mapped_column(String(16))
    actor_id: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(64), index=True)
    subject_entity: Mapped[str] = mapped_column(String(16))
    subject_id: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)  # 쿼리용 사본
    canonical_json: Mapped[str] = mapped_column(Text)  # 서명·검증 대상 (D-29)
    correlation_id: Mapped[str] = mapped_column(String(26), index=True)
    causation_id: Mapped[str | None] = mapped_column(String(26), nullable=True)
    signature: Mapped[str | None] = mapped_column(String(64), nullable=True)  # 저장 시점 (D-26)
    # 부기 컬럼 (append-only 예외)
    stream_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    projected_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    projection_error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ToolCall(Base):
    """``run.tool_called`` 저장소 — 감사 체인 밖, 서명 없음 (D-31)."""

    __tablename__ = "tool_calls"
    __table_args__ = (UniqueConstraint("id", name="uq_tool_calls_id"),)

    seq: Mapped[int] = mapped_column(BigSeq, primary_key=True, autoincrement=True)
    id: Mapped[str] = mapped_column(String(26))  # 이벤트 ULID
    run_id: Mapped[str] = mapped_column(String(26), index=True)
    project_id: Mapped[str] = mapped_column(String(26), index=True)
    tool: Mapped[str] = mapped_column(String(64))
    args_digest: Mapped[str] = mapped_column(String(64))
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    denied: Mapped[bool] = mapped_column(Boolean, default=False)
    ts: Mapped[datetime] = mapped_column(UTCDateTime)
