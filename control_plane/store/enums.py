"""상태·분류 Enum (설계 §4.1, §6). 값은 DB에 문자열로 저장된다. Run/Agent outcome은 schema.py를 미러(D-28)."""

from __future__ import annotations

from enum import StrEnum


class TaskStatus(StrEnum):
    """설계 §6.1."""

    DRAFT = "draft"
    READY = "ready"
    ASSIGNED = "assigned"
    RUNNING = "running"
    AWAITING_DECISION = "awaiting_decision"
    IN_REVIEW = "in_review"
    DONE = "done"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class GoalStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    ACTIVE = "active"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class EpicStatus(StrEnum):
    PENDING = "pending"
    ACTIVE = "active"
    DONE = "done"


class RunOutcome(StrEnum):
    """Run.outcome (§4.1). schema.RUN_OUTCOMES와 동치 — 테스트로 강제."""

    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


class AgentOutcome(StrEnum):
    """AgentOutput.outcome (§5.1) + 워커 timeout. schema.AGENT_OUTCOMES와 동치."""

    DONE = "done"
    NEEDS_DECISION = "needs_decision"
    BLOCKED = "blocked"
    FAILED = "failed"
    TIMEOUT = "timeout"


class DecisionStatus(StrEnum):
    """설계 §6.2."""

    OPEN = "open"
    APPROVED = "approved"
    REJECTED = "rejected"
    CHANGES_REQUESTED = "changes_requested"
    EXPIRED = "expired"


class DecisionType(StrEnum):
    PLAN = "plan"
    ARCHITECTURE = "architecture"
    DEPENDENCY = "dependency"
    SCHEMA = "schema"
    SECURITY = "security"
    DEPLOY = "deploy"
    COST = "cost"
    PR_MERGE = "pr_merge"


class AgentStatus(StrEnum):
    IDLE = "idle"
    WORKING = "working"
    WAITING_APPROVAL = "waiting_approval"
    BLOCKED = "blocked"
    PAUSED = "paused"
    TERMINATED = "terminated"


class TaskKind(StrEnum):
    FEATURE = "feature"
    BUGFIX = "bugfix"
    TEST = "test"
    REFACTOR = "refactor"
    RESEARCH = "research"
    FIX_FROM_REVIEW = "fix_from_review"
    FIX_FROM_TEST = "fix_from_test"


class Role(StrEnum):
    CODING = "coding"
    ARCHITECT = "architect"
    RESEARCH = "research"
    TEST = "test"
    REVIEW = "review"


class RiskTier(StrEnum):
    """설계 §8.1."""

    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"
