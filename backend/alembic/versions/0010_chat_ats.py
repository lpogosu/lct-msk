"""Чат после «Взаимно» и интеграция с ATS

1. chat_messages — переписка по приглашению/отклику (доступна после взаимного «да»).
2. company_api_keys — API-ключи компании для /api/ats/v1 (хеш, области доступа).
3. ats_settings — тип ATS (none | webhook | huntflow), адрес, зашифрованный токен.
4. ats_log — «Журнал ATS»: запросы адаптера (эмулятор/реальный режим) и входящие статусы; идемпотентность.

Revision ID: 0010_chat_ats
Revises: 0008_anticheat
Create Date: 2026-10-07 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0010_chat_ats"
down_revision: str | None = "0008_anticheat"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)
_EMPTY_OBJ = sa.text("'{}'::jsonb")
_EMPTY_ARR = sa.text("'[]'::jsonb")


def upgrade() -> None:
    op.create_table(
        "chat_messages",
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("ref_id", sa.Uuid(), nullable=False),
        sa.Column("sender_role", sa.String(length=10), nullable=False),
        sa.Column("sender_id", sa.Uuid(), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("read_at", _TS, nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("kind IN ('invitation', 'application')", name=op.f("ck_chat_messages_kind")),
        sa.CheckConstraint("sender_role IN ('employer', 'candidate')", name=op.f("ck_chat_messages_sender_role")),
        sa.ForeignKeyConstraint(["sender_id"], ["users.id"], name=op.f("fk_chat_messages_sender_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_messages")),
        sa.UniqueConstraint("seq", name=op.f("uq_chat_messages_seq")),
    )
    op.create_index("ix_chat_messages_thread", "chat_messages", ["kind", "ref_id", "seq"], unique=False)

    op.create_table(
        "company_api_keys",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("key_id", sa.String(length=16), nullable=False),
        sa.Column("key_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", _JSONB, server_default=_EMPTY_ARR, nullable=False),
        sa.Column("last_used_at", _TS, nullable=True),
        sa.Column("revoked_at", _TS, nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_company_api_keys_company_id_companies"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], name=op.f("fk_company_api_keys_created_by_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_company_api_keys")),
        sa.UniqueConstraint("key_id", name=op.f("uq_company_api_keys_key_id")),
    )
    op.create_index(op.f("ix_company_api_keys_company_id"), "company_api_keys", ["company_id"], unique=False)

    op.create_table(
        "ats_settings",
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("ats_type", sa.String(length=16), server_default=sa.text("'none'"), nullable=False),
        sa.Column("base_url", sa.String(length=300), nullable=True),
        sa.Column("account_id", sa.String(length=32), nullable=True),
        sa.Column("token_enc", sa.Text(), nullable=True),
        sa.Column("updated_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_ats_settings_company_id_companies"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("company_id", name=op.f("pk_ats_settings")),
    )

    op.create_table(
        "ats_log",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("direction", sa.String(length=3), nullable=False),
        sa.Column("mode", sa.String(length=10), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("method", sa.String(length=8), nullable=True),
        sa.Column("url", sa.String(length=500), nullable=True),
        sa.Column("request", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("status", sa.String(length=12), nullable=False),
        sa.Column("response_code", sa.Integer(), nullable=True),
        sa.Column("response", _JSONB, server_default=_EMPTY_OBJ, nullable=False),
        sa.Column("entity_type", sa.String(length=16), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("idempotency_key", sa.String(length=80), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["companies.id"], name=op.f("fk_ats_log_company_id_companies"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_ats_log")),
    )
    op.create_index("ix_ats_log_company_created", "ats_log", ["company_id", "created_at"], unique=False)
    op.create_index(
        "uq_ats_log_idem",
        "ats_log",
        ["company_id", "idempotency_key"],
        unique=True,
        postgresql_where=sa.text("idempotency_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_ats_log_idem", table_name="ats_log")
    op.drop_index("ix_ats_log_company_created", table_name="ats_log")
    op.drop_table("ats_log")
    op.drop_table("ats_settings")
    op.drop_index(op.f("ix_company_api_keys_company_id"), table_name="company_api_keys")
    op.drop_table("company_api_keys")
    op.drop_index("ix_chat_messages_thread", table_name="chat_messages")
    op.drop_table("chat_messages")
