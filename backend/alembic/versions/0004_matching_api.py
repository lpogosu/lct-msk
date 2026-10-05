"""API подбора: кэш признаков кандидата и псевдонимы «в рамках компании»

1. matching_features — признаки кандидата для подбора и банка, строка на категорию (Fullstack — своей строкой).
   payload — сериализованный CandidateFeatures с абсолютными датами; visible — кандидат виден работодателям.
   Пересобирается по хукам (квалификация, отзыв согласия) и по отпечатку источников (fingerprint).
2. candidate_aliases — псевдоним «К-4821» = HMAC(компания‖кандидат) и непрозрачная ссылка ref для API
   работодателя (DECISIONS п. 20): свои у каждой компании, сохраняются при первом показе.
3. need_versions.items / stats — сжатие TOAST lz4 (версия подборки — до ~1 МБ JSON; запись вдвое дешевле pglz).

Revision ID: 0004_matching_api
Revises: 0003_testing_api
Create Date: 2026-10-05 20:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004_matching_api"
down_revision: str | None = "0003_testing_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "matching_features",
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("specialization", sa.String(length=32), nullable=False),
        sa.Column("grade", sa.String(length=16), nullable=False),
        sa.Column("visible", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("has_fsp", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("payload", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["users.id"],
            name=op.f("fk_matching_features_candidate_id_users"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("candidate_id", "specialization", name=op.f("pk_matching_features")),
    )
    op.create_index(
        "ix_matching_features_category",
        "matching_features",
        ["specialization", "grade"],
        unique=False,
        postgresql_where=sa.text("visible"),
    )
    op.create_table(
        "candidate_aliases",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("ref", sa.String(length=32), nullable=False),
        sa.Column("pseudonym", sa.String(length=16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["users.id"],
            name=op.f("fk_candidate_aliases_candidate_id_users"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name=op.f("fk_candidate_aliases_company_id_companies"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("company_id", "candidate_id", name=op.f("pk_candidate_aliases")),
        sa.UniqueConstraint("company_id", "pseudonym", name=op.f("uq_candidate_aliases_company_id_pseudonym")),
        sa.UniqueConstraint("company_id", "ref", name=op.f("uq_candidate_aliases_company_id_ref")),
    )
    # 3. Версия подборки — до ~1 МБ JSON: сжатие lz4 вместо pglz вдвое удешевляет запись (как в модели).
    op.execute(_LZ4.format(method="lz4"))


_LZ4 = (
    "DO $$ BEGIN "
    "ALTER TABLE need_versions ALTER COLUMN items SET COMPRESSION {method}; "
    "ALTER TABLE need_versions ALTER COLUMN stats SET COMPRESSION {method}; "
    "EXCEPTION WHEN feature_not_supported OR invalid_parameter_value OR undefined_object THEN NULL; "
    "END $$"
)


def downgrade() -> None:
    op.execute(_LZ4.format(method="default"))
    op.drop_table("candidate_aliases")
    op.drop_index("ix_matching_features_category", table_name="matching_features", postgresql_where=sa.text("visible"))
    op.drop_table("matching_features")
