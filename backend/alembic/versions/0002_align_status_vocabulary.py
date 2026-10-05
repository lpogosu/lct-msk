"""Словари статусов совпадают с доменными модулями; «одно активное приглашение» включает accepted

1. test_sessions / qualifications: статусы и решения — заглавными, как в app/testing (CREATED, IN_PROGRESS,
   PASSED, CONFIRMED …). Частичный индекс «одна активная сессия на аккаунт» сравнивает строки буквально:
   со строчными значениями в предикате он не срабатывал бы для записей слоя хранения. Плюс CHECK на
   test_sessions.status — запись в другом регистре падает сразу, а не обходит индекс молча.
2. invitations: «одно активное приглашение на пару» (концепция §4.3) — открытый контакт (accepted) тоже
   занимает пару, как PAIR_ACTIVE_STATUSES в app/contact/statuses.py.

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-05 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SESSION_STATUSES = (
    "CREATED",
    "IN_PROGRESS",
    "PAUSED",
    "DECIDED",
    "EXTENSION_OFFERED",
    "FINAL",
    "ABANDONED",
    "UNDER_REVIEW",
    "INVALIDATED",
)
_EXCLUSIVE_KINDS = "'initial', 'retake', 'upgrade', 'ext_up', 'ext_down'"


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{v}'" for v in values)


def upgrade() -> None:
    # Данные, записанные до выравнивания (строчными), приводим к словарю домена
    op.execute("UPDATE test_sessions SET status = upper(status), decision = upper(decision)")
    op.execute("UPDATE qualifications SET status = upper(status)")

    op.drop_index("uq_test_sessions_one_active", table_name="test_sessions")
    op.create_index(
        "uq_test_sessions_one_active",
        "test_sessions",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text(
            f"status IN ('CREATED', 'IN_PROGRESS', 'PAUSED') AND kind IN ({_EXCLUSIVE_KINDS})"
        ),
    )
    # op.f: имя уже итоговое — иначе соглашение об именах добавило бы префикс ck_ ещё раз
    op.create_check_constraint(op.f("ck_test_sessions_status"), "test_sessions", f"status IN ({_in(_SESSION_STATUSES)})")

    op.drop_index("uq_invitations_active_pair", table_name="invitations")
    op.create_index(
        "uq_invitations_active_pair",
        "invitations",
        ["company_id", "candidate_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('sent', 'viewed', 'countered', 'accepted')"),
    )


def downgrade() -> None:
    op.drop_index("uq_invitations_active_pair", table_name="invitations")
    op.create_index(
        "uq_invitations_active_pair",
        "invitations",
        ["company_id", "candidate_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('sent', 'viewed', 'countered')"),
    )

    op.drop_constraint(op.f("ck_test_sessions_status"), "test_sessions", type_="check")
    op.drop_index("uq_test_sessions_one_active", table_name="test_sessions")
    op.create_index(
        "uq_test_sessions_one_active",
        "test_sessions",
        ["candidate_id"],
        unique=True,
        postgresql_where=sa.text(
            f"status IN ('created', 'in_progress', 'paused') AND kind IN ({_EXCLUSIVE_KINDS})"
        ),
    )
    op.execute("UPDATE test_sessions SET status = lower(status), decision = lower(decision)")
    op.execute("UPDATE qualifications SET status = lower(status)")
