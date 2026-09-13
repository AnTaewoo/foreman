"""events append-only 트리거 (설계 §4.1 append-only, D-26/D-29)

UPDATE는 부기 컬럼(stream_id/published_at/projected_at/projection_error)만, DELETE 거부.
TRUNCATE는 행 트리거를 타지 않아 테스트/재구축(PC-1)에 쓸 수 있다. 다른 dialect는 no-op.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-13
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BOOKKEEPING = ("stream_id", "published_at", "projected_at", "projection_error")
BODY_COLUMNS = (
    "seq", "id", "project_id", "ts", "actor_type", "actor_id", "type", "subject_entity",
    "subject_id", "payload", "canonical_json", "correlation_id", "causation_id", "signature",
)  # fmt: skip

_FUNCTION = f"""
CREATE OR REPLACE FUNCTION events_append_only() RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'events is append-only (DELETE denied)';
    END IF;
    IF {" OR ".join(f"NEW.{c} IS DISTINCT FROM OLD.{c}" for c in BODY_COLUMNS)} THEN
        RAISE EXCEPTION 'events is append-only (only {", ".join(BOOKKEEPING)} may change)';
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;
"""

_TRIGGER = """
CREATE TRIGGER events_append_only
BEFORE UPDATE OR DELETE ON events
FOR EACH ROW EXECUTE FUNCTION events_append_only();
"""


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute(_FUNCTION)
    op.execute(_TRIGGER)


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    op.execute("DROP TRIGGER IF EXISTS events_append_only ON events")
    op.execute("DROP FUNCTION IF EXISTS events_append_only()")
