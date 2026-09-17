"""goals.llm_profile — Goal마다 고른 LLM 프로파일 (D-57, P9)

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("goals", sa.Column("llm_profile", sa.String(length=40), nullable=True))


def downgrade() -> None:
    op.drop_column("goals", "llm_profile")
