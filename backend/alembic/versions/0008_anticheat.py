"""«Антиподсказчик»: двойники экземпляров, улики по заданиям, песочница жюри

1. anticheat_twins — двойник экземпляра пула: данные подмены и шифрованные ключи (ключ двойника на клиент не уходит).
2. anticheat_evidence — ответ на задание с двойником и его классификация; решение методиста (без автобана).
3. anticheat_sandbox_runs — мини-тест «Попробуй списать» для жюри (DEMO_MODE), сессии demo_assisted.

Revision ID: 0008_anticheat
Revises: 0007_demo
Create Date: 2026-10-05 21:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0008_anticheat"
down_revision: str | None = "0007_demo"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_EMPTY_OBJ = sa.text("'{}'::jsonb")
_EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "anticheat_twins",
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("reason", sa.String(length=48), nullable=True),
        sa.Column("twin_version", sa.String(length=16), nullable=True),
        sa.Column("twin_key_hash", sa.String(length=64), nullable=True),
        sa.Column("data", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("secret_enc", sa.Text(), nullable=True),
        sa.Column("render_id", sa.String(length=32), nullable=True),
        sa.Column("shown_in_session_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["instance_id"], ["item_instances.id"], name=op.f("fk_anticheat_twins_instance_id_item_instances"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["shown_in_session_id"],
            ["test_sessions.id"],
            name=op.f("fk_anticheat_twins_shown_in_session_id_test_sessions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("instance_id", name=op.f("pk_anticheat_twins")),
    )
    op.create_index(op.f("ix_anticheat_twins_family_id"), "anticheat_twins", ["family_id"], unique=False)
    op.create_index(op.f("ix_anticheat_twins_twin_key_hash"), "anticheat_twins", ["twin_key_hash"], unique=False)
    op.create_index(
        op.f("ix_anticheat_twins_shown_in_session_id"), "anticheat_twins", ["shown_in_session_id"], unique=False
    )

    op.create_table(
        "anticheat_evidence",
        sa.Column("session_id", sa.Uuid(), nullable=False),
        sa.Column("response_id", sa.Uuid(), nullable=False),
        sa.Column("instance_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=False),
        sa.Column("variant_code", sa.String(length=8), nullable=False),
        sa.Column("answer", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("matched", sa.String(length=16), nullable=False),
        sa.Column("strength", sa.String(length=8), nullable=True),
        sa.Column("tag", sa.String(length=64), nullable=True),
        sa.Column("mutation_class", sa.String(length=32), nullable=False),
        sa.Column("mutation_subclass", sa.String(length=64), nullable=False),
        sa.Column("honest_p", sa.Float(), nullable=False),
        sa.Column("render_id", sa.String(length=32), nullable=True),
        sa.Column("review_status", sa.String(length=24), server_default="open", nullable=False),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", _TS, nullable=True),
        sa.Column("demo_assisted", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"], ["test_sessions.id"], name=op.f("fk_anticheat_evidence_session_id_test_sessions"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["response_id"],
            ["test_responses.id"],
            name=op.f("fk_anticheat_evidence_response_id_test_responses"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["instance_id"],
            ["item_instances.id"],
            name=op.f("fk_anticheat_evidence_instance_id_item_instances"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], name=op.f("fk_anticheat_evidence_reviewed_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_anticheat_evidence")),
        sa.UniqueConstraint("response_id", name=op.f("uq_anticheat_evidence_response_id")),
    )
    op.create_index(op.f("ix_anticheat_evidence_session_id"), "anticheat_evidence", ["session_id"], unique=False)
    op.create_index(op.f("ix_anticheat_evidence_kind"), "anticheat_evidence", ["kind"], unique=False)
    op.create_index(op.f("ix_anticheat_evidence_review_status"), "anticheat_evidence", ["review_status"], unique=False)
    op.create_index("ix_anticheat_evidence_created", "anticheat_evidence", ["created_at"], unique=False)

    op.create_table(
        "anticheat_sandbox_runs",
        sa.Column("test_id", sa.String(length=16), nullable=False),
        sa.Column("reviewer_seed", sa.String(length=32), nullable=False),
        sa.Column("items", _JSONB, server_default=_EMPTY_ARR, nullable=False),
        sa.Column("answers", _JSONB, nullable=True),
        sa.Column("copied", _JSONB, nullable=True),
        sa.Column("result", _JSONB, nullable=True),
        sa.Column("demo_assisted", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("review_status", sa.String(length=24), server_default="open", nullable=False),
        sa.Column("review_note", sa.String(length=500), nullable=True),
        sa.Column("reviewed_by", sa.Uuid(), nullable=True),
        sa.Column("reviewed_at", _TS, nullable=True),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("scored_at", _TS, nullable=True),
        sa.ForeignKeyConstraint(
            ["reviewed_by"], ["users.id"], name=op.f("fk_anticheat_sandbox_runs_reviewed_by_users"), ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("test_id", name=op.f("pk_anticheat_sandbox_runs")),
    )


def downgrade() -> None:
    op.drop_table("anticheat_sandbox_runs")
    op.drop_index("ix_anticheat_evidence_created", table_name="anticheat_evidence")
    op.drop_index(op.f("ix_anticheat_evidence_review_status"), table_name="anticheat_evidence")
    op.drop_index(op.f("ix_anticheat_evidence_kind"), table_name="anticheat_evidence")
    op.drop_index(op.f("ix_anticheat_evidence_session_id"), table_name="anticheat_evidence")
    op.drop_table("anticheat_evidence")
    op.drop_index(op.f("ix_anticheat_twins_shown_in_session_id"), table_name="anticheat_twins")
    op.drop_index(op.f("ix_anticheat_twins_twin_key_hash"), table_name="anticheat_twins")
    op.drop_index(op.f("ix_anticheat_twins_family_id"), table_name="anticheat_twins")
    op.drop_table("anticheat_twins")
