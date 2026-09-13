"""initial schema — 설계 §4.1 8 엔티티 + tool_calls (D-29, D-31)

Postgres에서는 enum 타입을 한 번만 만든다: ``role``과 ``risk_tier``는 두 테이블이 공유하므로
``_pg_enums(create=True)``로 먼저 생성하고 컬럼은 ``create_type=False`` variant를 쓴다.

Revision ID: 0001
Revises:
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from control_plane.store.models import UTCDateTime

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ENUMS: dict[str, list[str]] = {
    "role": ["coding", "architect", "research", "test", "review"],
    "agent_status": ["idle", "working", "waiting_approval", "blocked", "paused", "terminated"],
    "decision_type": [
        "plan",
        "architecture",
        "dependency",
        "schema",
        "security",
        "deploy",
        "cost",
        "pr_merge",
    ],
    "risk_tier": ["T0", "T1", "T2", "T3"],
    "decision_status": ["open", "approved", "rejected", "changes_requested", "expired"],
    "goal_status": [
        "draft",
        "planning",
        "awaiting_plan_approval",
        "active",
        "blocked",
        "done",
        "cancelled",
    ],
    "epic_status": ["pending", "active", "done"],
    "task_kind": [
        "feature",
        "bugfix",
        "test",
        "refactor",
        "research",
        "fix_from_review",
        "fix_from_test",
    ],
    "task_status": [
        "draft",
        "ready",
        "assigned",
        "running",
        "awaiting_decision",
        "in_review",
        "done",
        "blocked",
        "cancelled",
    ],
    "run_outcome": ["success", "failed", "timeout", "cancelled", "escalated"],
}


def _enum(name: str) -> sa.types.TypeEngine[str]:
    """sqlite: CHECK 제약이 붙은 VARCHAR. postgresql: 미리 만든 enum 타입 참조(create_type=False)."""
    values = ENUMS[name]
    return sa.Enum(*values, name=name, create_constraint=True).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False), "postgresql"
    )


def _pg_enums(create: bool) -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name, values in ENUMS.items():
        typ = postgresql.ENUM(*values, name=name)
        if create:
            typ.create(op.get_bind(), checkfirst=True)
        else:
            typ.drop(op.get_bind(), checkfirst=True)


def upgrade() -> None:
    _pg_enums(create=True)
    op.create_table(
        "events",
        sa.Column(
            "seq",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("ts", UTCDateTime(), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("subject_entity", sa.String(length=16), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column(
            "payload", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("canonical_json", sa.Text(), nullable=False),
        sa.Column("correlation_id", sa.String(length=26), nullable=False),
        sa.Column("causation_id", sa.String(length=26), nullable=True),
        sa.Column("signature", sa.String(length=64), nullable=True),
        sa.Column("stream_id", sa.String(length=32), nullable=True),
        sa.Column("published_at", UTCDateTime(), nullable=True),
        sa.Column("projected_at", UTCDateTime(), nullable=True),
        sa.Column("projection_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id", name="uq_events_id"),
    )
    op.create_index(op.f("ix_events_correlation_id"), "events", ["correlation_id"], unique=False)
    op.create_index(op.f("ix_events_project_id"), "events", ["project_id"], unique=False)
    op.create_index(op.f("ix_events_type"), "events", ["type"], unique=False)
    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("repo_full_name", sa.String(length=300), nullable=False),
        sa.Column("installation_id", sa.BigInteger(), nullable=True),
        sa.Column(
            "policy", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "budget", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("default_branch", sa.String(length=200), nullable=False),
        sa.Column(
            "protected_paths",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "members", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "tool_calls",
        sa.Column(
            "seq",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            autoincrement=True,
            nullable=False,
        ),
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("run_id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("args_digest", sa.String(length=64), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("denied", sa.Boolean(), nullable=False),
        sa.Column("ts", UTCDateTime(), nullable=False),
        sa.PrimaryKeyConstraint("seq"),
        sa.UniqueConstraint("id", name="uq_tool_calls_id"),
    )
    op.create_index(op.f("ix_tool_calls_project_id"), "tool_calls", ["project_id"], unique=False)
    op.create_index(op.f("ix_tool_calls_run_id"), "tool_calls", ["run_id"], unique=False)
    op.create_table(
        "agents",
        sa.Column("id", sa.String(length=64), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("role", _enum("role"), nullable=False),
        sa.Column("display_name", sa.String(length=100), nullable=False),
        sa.Column(
            "model_config", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "tool_allowlist",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "capabilities", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("status", _enum("agent_status"), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_agents_project_id"), "agents", ["project_id"], unique=False)
    op.create_table(
        "decisions",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("discussion_number", sa.Integer(), nullable=True),
        sa.Column("type", _enum("decision_type"), nullable=False),
        sa.Column("risk_tier", _enum("risk_tier"), nullable=False),
        sa.Column(
            "proposal", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "agent_votes", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "quorum", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "human_responses",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "related_task_ids",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("related_pr_number", sa.Integer(), nullable=True),
        sa.Column("expires_at", UTCDateTime(), nullable=True),
        sa.Column("escalation_policy", sa.String(length=64), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("status", _enum("decision_status"), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_decisions_project_id"), "decisions", ["project_id"], unique=False)
    op.create_table(
        "goals",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("status", _enum("goal_status"), nullable=False),
        sa.Column(
            "acceptance_criteria",
            sa.JSON().with_variant(postgresql.JSONB(), "postgresql"),
            nullable=False,
        ),
        sa.Column("plan_discussion_id", sa.Integer(), nullable=True),
        sa.Column("plan_revision", sa.Integer(), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_goals_project_id"), "goals", ["project_id"], unique=False)
    op.create_table(
        "epics",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("goal_id", sa.String(length=26), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("order", sa.Integer(), nullable=False),
        sa.Column("milestone_number", sa.Integer(), nullable=True),
        sa.Column("status", _enum("epic_status"), nullable=False),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goals.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_epics_goal_id"), "epics", ["goal_id"], unique=False)
    op.create_table(
        "tasks",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("epic_id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("goal_id", sa.String(length=26), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=True),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("spec", sa.Text(), nullable=False),
        sa.Column("kind", _enum("task_kind"), nullable=False),
        sa.Column("role_required", _enum("role"), nullable=False),
        sa.Column(
            "depends_on", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "blocks", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column(
            "owned_paths", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("risk_tier", _enum("risk_tier"), nullable=False),
        sa.Column("assignee_agent_id", sa.String(length=64), nullable=True),
        sa.Column("branch_name", sa.String(length=300), nullable=True),
        sa.Column("pr_number", sa.Integer(), nullable=True),
        sa.Column("pr_merged_at", UTCDateTime(), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("status", _enum("task_status"), nullable=False),
        sa.Column("created_at", UTCDateTime(), nullable=False),
        sa.Column("updated_at", UTCDateTime(), nullable=False),
        sa.ForeignKeyConstraint(
            ["epic_id"],
            ["epics.id"],
        ),
        sa.ForeignKeyConstraint(
            ["goal_id"],
            ["goals.id"],
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_tasks_epic_id"), "tasks", ["epic_id"], unique=False)
    op.create_index(op.f("ix_tasks_goal_id"), "tasks", ["goal_id"], unique=False)
    op.create_index(op.f("ix_tasks_project_id"), "tasks", ["project_id"], unique=False)
    op.create_index(op.f("ix_tasks_status"), "tasks", ["status"], unique=False)
    op.create_table(
        "runs",
        sa.Column("id", sa.String(length=26), nullable=False),
        sa.Column("task_id", sa.String(length=26), nullable=False),
        sa.Column("project_id", sa.String(length=26), nullable=False),
        sa.Column("agent_id", sa.String(length=64), nullable=False),
        sa.Column("worker_id", sa.String(length=128), nullable=True),
        sa.Column("started_at", UTCDateTime(), nullable=False),
        sa.Column("ended_at", UTCDateTime(), nullable=True),
        sa.Column("outcome", _enum("run_outcome"), nullable=True),
        sa.Column("agent_outcome", sa.String(length=32), nullable=True),
        sa.Column("input_snapshot", sa.String(length=128), nullable=True),
        sa.Column("tool_call_count", sa.Integer(), nullable=False),
        sa.Column("denied_count", sa.Integer(), nullable=False),
        sa.Column(
            "artifacts", sa.JSON().with_variant(postgresql.JSONB(), "postgresql"), nullable=False
        ),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=True),
        sa.Column("log_ref", sa.String(length=500), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["tasks.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_runs_project_id"), "runs", ["project_id"], unique=False)
    op.create_index(op.f("ix_runs_task_id"), "runs", ["task_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_runs_task_id"), table_name="runs")
    op.drop_index(op.f("ix_runs_project_id"), table_name="runs")
    op.drop_table("runs")
    op.drop_index(op.f("ix_tasks_status"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_project_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_goal_id"), table_name="tasks")
    op.drop_index(op.f("ix_tasks_epic_id"), table_name="tasks")
    op.drop_table("tasks")
    op.drop_index(op.f("ix_epics_goal_id"), table_name="epics")
    op.drop_table("epics")
    op.drop_index(op.f("ix_goals_project_id"), table_name="goals")
    op.drop_table("goals")
    op.drop_index(op.f("ix_decisions_project_id"), table_name="decisions")
    op.drop_table("decisions")
    op.drop_index(op.f("ix_agents_project_id"), table_name="agents")
    op.drop_table("agents")
    op.drop_index(op.f("ix_tool_calls_run_id"), table_name="tool_calls")
    op.drop_index(op.f("ix_tool_calls_project_id"), table_name="tool_calls")
    op.drop_table("tool_calls")
    op.drop_table("projects")
    op.drop_index(op.f("ix_events_type"), table_name="events")
    op.drop_index(op.f("ix_events_project_id"), table_name="events")
    op.drop_index(op.f("ix_events_correlation_id"), table_name="events")
    op.drop_table("events")
    _pg_enums(create=False)
