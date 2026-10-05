"""Демо-режим и админ-инструменты жюри

1. demo_personas — роль аккаунта в демо-мире (персона сценария, ступенчатый кандидат, служебный логин,
   синтетический кандидат автоответчика с причиной «Нет»; G2, DECISIONS п. 4).
2. demo_state — одна строка: режим жюри, автоответчик, отметки сборки мира (seed/reset).
3. jury_batches / jury_candidates / jury_briefs / jury_pairs — импорт жюри (POST /api/admin/import, DECISIONS п. 5):
   брифы, кандидаты (с тестом и без: «категория по опросу, не подтверждена») и размеченные пары для метрик.

Revision ID: 0007_demo
Revises: 0006_profile_fsp
Create Date: 2026-10-05 18:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_demo"
down_revision: str | None = "0006_profile_fsp"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_EMPTY_OBJ = sa.text("'{}'::jsonb")


def upgrade() -> None:
    op.create_table(
        "demo_personas",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=True),
        sa.Column("profile", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "kind IN ('persona', 'staged', 'synthetic', 'employer', 'staff')", name=op.f("ck_demo_personas_kind")
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_demo_personas_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_demo_personas")),
    )
    op.create_index(op.f("ix_demo_personas_kind"), "demo_personas", ["kind"], unique=False)
    op.create_index(op.f("ix_demo_personas_key"), "demo_personas", ["key"], unique=False)

    op.create_table(
        "demo_state",
        sa.Column("id", sa.SmallInteger(), nullable=False),
        sa.Column("jury_mode", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("jury_mode_since", _TS, nullable=True),
        sa.Column("autoresponder", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("seeded_at", _TS, nullable=True),
        sa.Column("scenario_built_at", _TS, nullable=True),
        sa.Column("seed_version", sa.String(length=32), nullable=True),
        sa.Column("meta", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("updated_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("id = 1", name=op.f("ck_demo_state_singleton")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_demo_state")),
    )

    op.create_table(
        "jury_batches",
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("n_briefs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_candidates", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_unconfirmed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("n_pairs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("meta", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_jury_batches_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_jury_batches")),
    )

    op.create_table(
        "jury_candidates",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("payload", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["jury_batches.id"], name=op.f("fk_jury_candidates_batch_id_jury_batches"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("batch_id", "candidate_id", name=op.f("pk_jury_candidates")),
    )

    op.create_table(
        "jury_briefs",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.String(length=64), nullable=False),
        sa.Column("payload", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.ForeignKeyConstraint(["batch_id"], ["jury_batches.id"], name=op.f("fk_jury_briefs_batch_id_jury_batches"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("batch_id", "brief_id", name=op.f("pk_jury_briefs")),
    )

    op.create_table(
        "jury_pairs",
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("brief_id", sa.String(length=64), nullable=False),
        sa.Column("candidate_id", sa.String(length=64), nullable=False),
        sa.Column("rel", sa.SmallInteger(), nullable=False),
        sa.CheckConstraint("rel BETWEEN 0 AND 3", name=op.f("ck_jury_pairs_rel")),
        sa.ForeignKeyConstraint(["batch_id"], ["jury_batches.id"], name=op.f("fk_jury_pairs_batch_id_jury_batches"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("batch_id", "brief_id", "candidate_id", name=op.f("pk_jury_pairs")),
    )
    op.create_index("ix_jury_pairs_batch_brief", "jury_pairs", ["batch_id", "brief_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jury_pairs_batch_brief", table_name="jury_pairs")
    op.drop_table("jury_pairs")
    op.drop_table("jury_briefs")
    op.drop_table("jury_candidates")
    op.drop_table("jury_batches")
    op.drop_table("demo_state")
    op.drop_index(op.f("ix_demo_personas_key"), table_name="demo_personas")
    op.drop_index(op.f("ix_demo_personas_kind"), table_name="demo_personas")
    op.drop_table("demo_personas")
