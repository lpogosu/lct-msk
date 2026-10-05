"""Профиль кандидата, PDF-профиль со сверкой по QR и вход через ФСП ID

1. candidate_profiles: заявленные софт-скиллы, грейд «как заявлен», момент загрузки резюме (К7).
   Всё это — только серые «заявлено», в категорию и Score не входит (DECISIONS п. 1).
2. profile_snapshots: выданные стандартизированные PDF-профили — код сверки /v/{hash} (концепция §3.1.2).
3. fsp_links: сырой ответ реестра ФСП (из него без сети собирается профиль ФСП), дата данных реестра,
   источник и состояние последней синхронизации, скрытые кандидатом результаты (концепция §6.2, §6.4–6.5).
4. fsp_auth_states: одноразовые state/nonce/PKCE входа через ФСП ID (authorization code + PKCE).

Revision ID: 0006_profile_fsp
Revises: 0005_contact_api
Create Date: 2026-10-05 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0006_profile_fsp"
down_revision: str | None = "0005_contact_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # 1. Профиль кандидата
    op.add_column(
        "candidate_profiles",
        sa.Column("soft_skills", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
    )
    op.add_column("candidate_profiles", sa.Column("declared_grade", sa.String(length=16), nullable=True))
    op.add_column("candidate_profiles", sa.Column("resume_uploaded_at", sa.DateTime(timezone=True), nullable=True))

    # 2. Выданные PDF-профили
    op.create_table(
        "profile_snapshots",
        sa.Column("hash", sa.String(length=32), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("variant", sa.String(length=8), nullable=False),
        sa.Column("issued_on", sa.Date(), nullable=False),
        sa.Column("claims", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_profile_snapshots_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("hash", name=op.f("pk_profile_snapshots")),
    )
    op.create_index(op.f("ix_profile_snapshots_user_id"), "profile_snapshots", ["user_id"], unique=False)

    # 3. Данные реестра ФСП у привязки
    op.add_column("fsp_links", sa.Column("athlete", _JSONB, nullable=True))
    op.add_column("fsp_links", sa.Column("achievements", _JSONB, nullable=True))
    op.add_column("fsp_links", sa.Column("registry_as_of", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fsp_links", sa.Column("data_source", sa.String(length=16), nullable=True))
    op.add_column("fsp_links", sa.Column("fsp_state", sa.String(length=24), nullable=True))
    op.add_column(
        "fsp_links",
        sa.Column("hidden_result_ids", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False),
    )

    # 4. Вход через ФСП ID: state + nonce + PKCE
    op.create_table(
        "fsp_auth_states",
        sa.Column("state_hash", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("redirect_uri", sa.String(length=500), nullable=False),
        sa.Column("consent_personal_data", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("consent_fsp_enrichment", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_fsp_auth_states_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_fsp_auth_states")),
        sa.UniqueConstraint("state_hash", name=op.f("uq_fsp_auth_states_state_hash")),
    )
    op.create_index(op.f("ix_fsp_auth_states_user_id"), "fsp_auth_states", ["user_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_fsp_auth_states_user_id"), table_name="fsp_auth_states")
    op.drop_table("fsp_auth_states")
    for column in ("hidden_result_ids", "fsp_state", "data_source", "registry_as_of", "achievements", "athlete"):
        op.drop_column("fsp_links", column)
    op.drop_index(op.f("ix_profile_snapshots_user_id"), table_name="profile_snapshots")
    op.drop_table("profile_snapshots")
    for column in ("resume_uploaded_at", "declared_grade", "soft_skills"):
        op.drop_column("candidate_profiles", column)
