"""Доверие и безопасность, несовершеннолетние

1. company_verifications — метка «Проверенная компания» (ИНН/ОГРН, проверки, отзыв).
2. trust_complaints — жалобы кандидатов на приглашения и вакансии, очередь модерации; существующие жалобы
   (feedback.direction = 'complaint') переносятся.
3. candidate_profiles: законный представитель кандидата 15–17 лет (имя, email, хеш одноразовой ссылки, отметки времени).

Revision ID: 0011_trust_minors
Revises: 0010_chat_ats
Create Date: 2026-10-08 10:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_trust_minors"
down_revision: str | None = "0010_chat_ats"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_EMPTY_OBJ = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "company_verifications",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("inn", sa.String(length=12), nullable=False),
        sa.Column("ogrn", sa.String(length=15), nullable=False),
        sa.Column("level", sa.String(length=16), nullable=False),
        sa.Column("checks", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("registry_name", sa.String(length=300), nullable=True),
        sa.Column("submitted_at", _TS, nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.Column("revoked_by", sa.Uuid(), nullable=True),
        sa.Column("revoke_reason", sa.String(length=32), nullable=True),
        sa.Column("revoke_note", sa.String(length=300), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_company_verifications_company_id_companies"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["revoked_by"], ["users.id"], name=op.f("fk_company_verifications_revoked_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("company_id", name=op.f("pk_company_verifications")),
    )
    op.create_index(op.f("ix_company_verifications_inn"), "company_verifications", ["inn"], unique=False)

    op.create_table(
        "trust_complaints",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("reporter_id", sa.Uuid(), nullable=True),
        sa.Column("target_type", sa.String(length=16), nullable=False),
        sa.Column("target_id", sa.Uuid(), nullable=False),
        sa.Column("reason", sa.String(length=32), nullable=False),
        sa.Column("comment", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("resolved_by", sa.Uuid(), nullable=True),
        sa.Column("resolved_at", _TS, nullable=True),
        sa.Column("resolution_note", sa.String(length=300), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_trust_complaints_company_id_companies"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["reporter_id"], ["users.id"], name=op.f("fk_trust_complaints_reporter_id_users"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["resolved_by"], ["users.id"], name=op.f("fk_trust_complaints_resolved_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trust_complaints")),
        sa.UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_trust_complaints_once"),
    )
    op.create_index(op.f("ix_trust_complaints_company_id"), "trust_complaints", ["company_id"], unique=False)
    op.create_index("ix_trust_complaints_status_created", "trust_complaints", ["status", "created_at"], unique=False)

    # Перенос прежних жалоб (хранились в feedback): компания и автор — из приглашения
    op.execute(
        """
        INSERT INTO trust_complaints (company_id, reporter_id, target_type, target_id, reason, comment, status, created_at)
        SELECT i.company_id, i.candidate_id, 'invitation', f.target_id, f.reason_code, f.comment, 'open', f.created_at
        FROM feedback f JOIN invitations i ON i.id = f.target_id
        WHERE f.target_type = 'invitation' AND f.direction = 'complaint'
        ON CONFLICT DO NOTHING
        """
    )
    op.execute("DELETE FROM feedback WHERE target_type = 'invitation' AND direction = 'complaint'")

    op.add_column("candidate_profiles", sa.Column("guardian_name", sa.String(length=200), nullable=True))
    op.add_column("candidate_profiles", sa.Column("guardian_email", sa.String(length=254), nullable=True))
    op.add_column("candidate_profiles", sa.Column("guardian_token_hash", sa.String(length=64), nullable=True))
    op.add_column("candidate_profiles", sa.Column("guardian_requested_at", _TS, nullable=True))
    op.add_column("candidate_profiles", sa.Column("guardian_confirmed_at", _TS, nullable=True))
    op.create_index(
        op.f("ix_candidate_profiles_guardian_token_hash"), "candidate_profiles", ["guardian_token_hash"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_candidate_profiles_guardian_token_hash"), table_name="candidate_profiles")
    for col in ("guardian_confirmed_at", "guardian_requested_at", "guardian_token_hash", "guardian_email", "guardian_name"):
        op.drop_column("candidate_profiles", col)
    op.drop_index("ix_trust_complaints_status_created", table_name="trust_complaints")
    op.drop_index(op.f("ix_trust_complaints_company_id"), table_name="trust_complaints")
    op.drop_table("trust_complaints")
    op.drop_index(op.f("ix_company_verifications_inn"), table_name="company_verifications")
    op.drop_table("company_verifications")
