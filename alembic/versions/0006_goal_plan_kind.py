"""goals.plan_kind — Plan이 Issue로 게시됐는지 (P9.11, Plans 카테고리 없는 repo)

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("goals", sa.Column("plan_kind", sa.String(length=20), nullable=True))


def downgrade() -> None:
    op.drop_column("goals", "plan_kind")
