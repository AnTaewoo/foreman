"""goals.plan_markdown — Plan 본문 (D-53, P9.1)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("goals", sa.Column("plan_markdown", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("goals", "plan_markdown")
