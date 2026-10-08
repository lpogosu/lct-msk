"""Цифровой паспорт навыков: реестр выданных подписанных учётных данных (W3C VC / Open Badges 3.0)

issued_credentials — подписанный документ целиком, код для /v/{code}, номер в списке отзыва (последовательность
credential_status_idx), срок действия и отметка отзыва.

Revision ID: 0013_credentials
Revises: 0012_interest_signals
Create Date: 2026-10-08 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013_credentials"
down_revision: str | None = "0012_interest_signals"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.execute(sa.schema.CreateSequence(sa.Sequence("credential_status_idx")))
    op.create_table(
        "issued_credentials",
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=40), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("credential_id", sa.String(length=120), nullable=False),
        sa.Column("status_index", sa.Integer(), nullable=False),
        sa.Column("document", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("include_name", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("valid_until", _TS, nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_issued_credentials_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_issued_credentials")),
        sa.UniqueConstraint("code", name=op.f("uq_issued_credentials_code")),
        sa.UniqueConstraint("credential_id", name=op.f("uq_issued_credentials_credential_id")),
        sa.UniqueConstraint("status_index", name=op.f("uq_issued_credentials_status_index")),
    )
    op.create_index(op.f("ix_issued_credentials_user_id"), "issued_credentials", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_issued_credentials_user_id"), table_name="issued_credentials")
    op.drop_table("issued_credentials")
    op.execute(sa.schema.DropSequence(sa.Sequence("credential_status_idx")))
