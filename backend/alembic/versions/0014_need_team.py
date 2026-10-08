"""Бриф: необязательное поле «о команде» (E-02)

needs.team — JSONB {size, role, stack_context, about}; показывается кандидату, в Score не входит.

Revision ID: 0014_need_team
Revises: 0013_credentials
Create Date: 2026-10-08 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0014_need_team"
down_revision: str | None = "0013_credentials"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "needs",
        sa.Column("team", postgresql.JSONB(astext_type=sa.Text()), server_default=sa.text("'{}'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("needs", "team")
