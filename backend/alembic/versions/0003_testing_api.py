"""API тестирования: полное состояние доменной квалификации в qualifications.state

Квалификация (app.testing.session.Qualification) хранит попытки за 90 дней, цепочку сессий с доборами,
открытые проверки методистом и снимок для отката при аннулировании — это jsonb-снимок, колонки
таблицы остаются денормализацией для чтения (spec-testing §2.4, §4).

Revision ID: 0003_testing_api
Revises: 0002
Create Date: 2026-10-05 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_testing_api"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "qualifications",
        sa.Column(
            "state",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("qualifications", "state")
