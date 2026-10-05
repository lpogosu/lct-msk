"""API контакта: состояние машины приглашения, журнал откликов, уведомления, надёжность, регулярные задачи

1. invitations: поля доменной машины (app/contact/invitation.py), которые нужно хранить между запросами:
   встречное условие и срок ответа на него, причина отказа во встречном, просрочка первого контакта и какой
   «не связались» уже записан в надёжность (один раз), этап «собеседование», момент закрытия, причины плашки
   «Требует осторожности», отправленные напоминания (концепция §4.3, spec-contact §2.3).
2. invitation_events.seq — порядковый номер события: «{invitation_id}-{seq}» — идемпотентный event_id вебхука
   ATS; уникальность (invitation_id, seq) не даёт двум запросам записать один переход дважды (§8.2).
3. cooldowns.decline_reason — причина отказа: исключение «зарплата» снимает паузу при вилке от 1,15× (§4.3).
4. application_events — журнал переходов отклика (как у приглашения).
5. notifications — уведомления в кабинете.
6. reliability_events — события good/bad, из которых на чтении считается R (spec-contact §3.7).
7. employer_tasks / task_assignments — модуль семейства, «проверочный набор по вакансии» (DECISIONS 3) и итог,
   который читает подбор: балл, модуль, момент завершения (свежесть A, подтверждение модуля в K).

Revision ID: 0005_contact_api
Revises: 0004_matching_api
Create Date: 2026-10-05 12:00:00+00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005_contact_api"
down_revision: str | None = "0004_matching_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_JSONB = postgresql.JSONB(astext_type=sa.Text())
_TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    # 1. Приглашение: состояние доменной машины
    op.add_column("invitations", sa.Column("counter", _JSONB, nullable=True))
    op.add_column("invitations", sa.Column("countered_at", _TS, nullable=True))
    op.add_column("invitations", sa.Column("counter_decline_reason", sa.String(length=32), nullable=True))
    op.add_column(
        "invitations", sa.Column("contact_overdue", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.add_column("invitations", sa.Column("contact_penalty", sa.String(length=32), nullable=True))
    op.add_column("invitations", sa.Column("stage", sa.String(length=16), nullable=True))
    op.add_column("invitations", sa.Column("closed_at", _TS, nullable=True))
    op.add_column("invitations", sa.Column("caution", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column(
        "invitations", sa.Column("reminders_sent", _JSONB, server_default=sa.text("'[]'::jsonb"), nullable=False)
    )

    # 2. Номер события в приглашении: существующие события нумеруются по порядку записи
    op.add_column("invitation_events", sa.Column("seq", sa.SmallInteger(), nullable=True))
    op.execute(
        """
        UPDATE invitation_events e SET seq = n.rn
        FROM (SELECT id, row_number() OVER (PARTITION BY invitation_id ORDER BY at, id) AS rn
              FROM invitation_events) n
        WHERE e.id = n.id
        """
    )
    op.alter_column("invitation_events", "seq", nullable=False)
    op.create_unique_constraint(
        op.f("uq_invitation_events_invitation_id_seq"), "invitation_events", ["invitation_id", "seq"]
    )

    # 3. Пауза пары: причина отказа (исключение «зарплата»)
    op.add_column("cooldowns", sa.Column("decline_reason", sa.String(length=32), nullable=True))

    # 4. Журнал откликов
    op.create_table(
        "application_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("application_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.SmallInteger(), nullable=False),
        sa.Column("from_status", sa.String(length=24), nullable=True),
        sa.Column("to_status", sa.String(length=24), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("actor_role", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Uuid(), nullable=True),
        sa.Column("at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.Column("payload", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_id"], ["users.id"], name=op.f("fk_application_events_actor_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_application_events_application_id_applications"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_application_events")),
        sa.UniqueConstraint("application_id", "seq", name=op.f("uq_application_events_application_id_seq")),
    )
    op.create_index(op.f("ix_application_events_application_id"), "application_events", ["application_id"], unique=False)

    # 5. Уведомления
    op.create_table(
        "notifications",
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.String(length=64), nullable=True),
        sa.Column("payload", _JSONB, server_default=sa.text("'{}'::jsonb"), nullable=False),
        sa.Column("read_at", _TS, nullable=True),
        sa.Column("id", sa.Uuid(), server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("created_at", _TS, server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index("ix_notifications_user_created", "notifications", ["user_id", "created_at"], unique=False)
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id"],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )

    # 6. События надёжности компании
    op.create_table(
        "reliability_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("company_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("at", _TS, nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("invitation_id", sa.Uuid(), nullable=True),
        sa.Column("application_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["application_id"],
            ["applications.id"],
            name=op.f("fk_reliability_events_application_id_applications"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_id"], ["users.id"], name=op.f("fk_reliability_events_candidate_id_users"), ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["company_id"], ["companies.id"], name=op.f("fk_reliability_events_company_id_companies"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["invitation_id"],
            ["invitations.id"],
            name=op.f("fk_reliability_events_invitation_id_invitations"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reliability_events")),
    )
    op.create_index("ix_reliability_events_company_at", "reliability_events", ["company_id", "at"], unique=False)

    # 7. Регулярные задачи и проверочный набор
    op.add_column("employer_tasks", sa.Column("module", sa.String(length=32), nullable=True))
    op.add_column("employer_tasks", sa.Column("need_id", sa.Uuid(), nullable=True))
    op.add_column(
        "employer_tasks", sa.Column("is_check_set", sa.Boolean(), server_default=sa.false(), nullable=False)
    )
    op.create_index(op.f("ix_employer_tasks_need_id"), "employer_tasks", ["need_id"], unique=False)
    op.create_foreign_key(
        op.f("fk_employer_tasks_need_id_needs"), "employer_tasks", "needs", ["need_id"], ["id"], ondelete="SET NULL"
    )

    op.add_column("task_assignments", sa.Column("kind", sa.String(length=16), nullable=True))
    op.add_column("task_assignments", sa.Column("module", sa.String(length=32), nullable=True))
    op.add_column("task_assignments", sa.Column("family_id", sa.String(length=64), nullable=True))
    op.add_column("task_assignments", sa.Column("is_correct", sa.Boolean(), nullable=True))
    op.add_column("task_assignments", sa.Column("finished_at", _TS, nullable=True))
    op.add_column("task_assignments", sa.Column("skipped_at", _TS, nullable=True))
    op.add_column(
        "task_assignments",
        sa.Column("source", sa.String(length=16), server_default=sa.text("'offer'"), nullable=False),
    )
    op.add_column("task_assignments", sa.Column("need_id", sa.Uuid(), nullable=True))
    op.add_column("task_assignments", sa.Column("review_comment", sa.String(length=300), nullable=True))
    op.create_foreign_key(
        op.f("fk_task_assignments_need_id_needs"), "task_assignments", "needs", ["need_id"], ["id"], ondelete="SET NULL"
    )
    op.create_index(
        "ix_task_assignments_candidate_finished", "task_assignments", ["candidate_id", "finished_at"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_task_assignments_candidate_finished", table_name="task_assignments")
    op.drop_constraint(op.f("fk_task_assignments_need_id_needs"), "task_assignments", type_="foreignkey")
    for col in (
        "review_comment",
        "need_id",
        "source",
        "skipped_at",
        "finished_at",
        "is_correct",
        "family_id",
        "module",
        "kind",
    ):
        op.drop_column("task_assignments", col)
    op.drop_constraint(op.f("fk_employer_tasks_need_id_needs"), "employer_tasks", type_="foreignkey")
    op.drop_index(op.f("ix_employer_tasks_need_id"), table_name="employer_tasks")
    for col in ("is_check_set", "need_id", "module"):
        op.drop_column("employer_tasks", col)

    op.drop_index("ix_reliability_events_company_at", table_name="reliability_events")
    op.drop_table("reliability_events")
    op.drop_index("ix_notifications_user_unread", table_name="notifications", postgresql_where=sa.text("read_at IS NULL"))
    op.drop_index("ix_notifications_user_created", table_name="notifications")
    op.drop_table("notifications")
    op.drop_index(op.f("ix_application_events_application_id"), table_name="application_events")
    op.drop_table("application_events")

    op.drop_column("cooldowns", "decline_reason")
    op.drop_constraint(op.f("uq_invitation_events_invitation_id_seq"), "invitation_events", type_="unique")
    op.drop_column("invitation_events", "seq")
    for col in (
        "reminders_sent",
        "caution",
        "closed_at",
        "stage",
        "contact_penalty",
        "contact_overdue",
        "counter_decline_reason",
        "countered_at",
        "counter",
    ):
        op.drop_column("invitations", col)
