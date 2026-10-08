"""Сигнал интереса кандидата (DECISIONS 24)

1. interest_signals — сигнал «мне интересна эта компания»: срок, отзыв, согласие.
2. consents.purpose — новая цель interest_signal (CHECK пересоздаётся).

Revision ID: 0012_interest_signals
Revises: 0011_trust_minors
Create Date: 2026-10-08 14:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_interest_signals"
down_revision: str | None = "0011_trust_minors"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TS = sa.DateTime(timezone=True)
_OLD = "'processing', 'blind_card', 'fsp_enrichment', 'contact_disclosure', 'test_logging', 'calibration'"
_NEW = _OLD + ", 'interest_signal'"


def upgrade() -> None:
    op.create_table(
        "interest_signals",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("vacancy_id", sa.Uuid(), nullable=True),
        sa.Column("consent_id", sa.Uuid(), nullable=True),
        sa.Column("expires_at", _TS, nullable=False),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["candidate_id"], ["users.id"], name=op.f("fk_interest_signals_candidate_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_interest_signals_company_id_companies"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["vacancy_id"], ["vacancies.id"], name=op.f("fk_interest_signals_vacancy_id_vacancies"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["consent_id"], ["consents.id"], name=op.f("fk_interest_signals_consent_id_consents"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_interest_signals")),
    )
    op.create_index(op.f("ix_interest_signals_candidate_id"), "interest_signals", ["candidate_id"], unique=False)
    op.create_index(op.f("ix_interest_signals_company_id"), "interest_signals", ["company_id"], unique=False)
    op.create_index("ix_interest_signals_company_candidate", "interest_signals", ["company_id", "candidate_id"], unique=False)
    op.drop_constraint(op.f("ck_consents_purpose"), "consents", type_="check")
    op.create_check_constraint(op.f("ck_consents_purpose"), "consents", f"purpose IN ({_NEW})")


def downgrade() -> None:
    op.execute("DELETE FROM consents WHERE purpose = 'interest_signal'")
    op.drop_constraint(op.f("ck_consents_purpose"), "consents", type_="check")
    op.create_check_constraint(op.f("ck_consents_purpose"), "consents", f"purpose IN ({_OLD})")
    op.drop_table("interest_signals")
