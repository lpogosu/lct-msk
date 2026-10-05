"""Миграция 0005_contact_api на чистой базе даёт ровно схему моделей раздела контакта.

Платформенный тест (test_platform_models) сравнивает только таблицы, экспортированные из app.models; таблицы,
которые добавил раздел контакта (notifications, application_events, reliability_events), и новые колонки
приглашений, пауз и задач сверяются здесь — вместе с индексами (включая частичные) и уникальностью.
Цепочка проверяется целиком: upgrade head → сравнение → downgrade до 0004 → снова head.
"""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.base import Base

BACKEND = Path(__file__).resolve().parents[1]

CONTACT_TABLES = {
    "invitations",
    "invitation_events",
    "offer_terms",
    "contact_disclosures",
    "cooldowns",
    "vacancies",
    "applications",
    "application_events",
    "notifications",
    "feedback",
    "outcome_checks",
    "companies",
    "webhook_deliveries",
    "reliability_events",
    "employer_tasks",
    "task_assignments",
}

_INDEXES_SQL = """
SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'
"""
_UNIQUE_SQL = """
SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint
WHERE contype IN ('u', 'c', 'f') AND connamespace = 'public'::regnamespace
"""


def _facts(conn) -> tuple[set, set]:
    idx = {(t, n, d) for t, n, d in conn.execute(text(_INDEXES_SQL)) if t in CONTACT_TABLES}
    cons = {(t, n, d) for t, n, d in conn.execute(text(_UNIQUE_SQL)) if t.split(".")[-1] in CONTACT_TABLES}
    return idx, cons


def _table_of(entry) -> str | None:
    item = entry[0] if isinstance(entry, list) else entry
    kind = item[0]
    if kind in ("add_column", "remove_column") or kind.startswith("modify_"):
        return item[2]
    obj = item[1]
    table = getattr(obj, "table", None)
    return table.name if table is not None else getattr(obj, "name", None)


def test_contact_migration_matches_models(pg_server, db_engine):
    url = pg_server.create_database("mig_contact")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            migrated = _facts(conn)
            tables = set(inspect(conn).get_table_names())
        ours = [d for d in diff if _table_of(d) in CONTACT_TABLES]
        assert ours == [], f"Миграция раздела контакта расходится с моделями: {ours}"
        assert {"notifications", "application_events", "reliability_events"} <= tables
        with db_engine.connect() as conn:  # эталон — create_all по моделям
            model = _facts(conn)
        assert migrated[0] == model[0], migrated[0] ^ model[0]
        assert migrated[1] == model[1], migrated[1] ^ model[1]
        # Сравнение не пустое: гарантии 0005 действительно на месте
        index_names = {n for _, n, _ in migrated[0]}
        assert {"ix_notifications_user_unread", "ix_task_assignments_candidate_finished"} <= index_names
        unread = next(d for _, n, d in migrated[0] if n == "ix_notifications_user_unread")
        assert "read_at IS NULL" in unread
        assert "uq_invitation_events_invitation_id_seq" in {n for _, n, _ in migrated[1]}

        # 0005 откатывается и накатывается снова (данных нет — проверяем DDL туда и обратно)
        command.downgrade(cfg, "0004_matching_api")
        with engine.connect() as conn:
            left = set(inspect(conn).get_table_names())
            cols = {c["name"] for c in inspect(conn).get_columns("invitations")}
        assert not ({"notifications", "application_events", "reliability_events"} & left)
        assert "counter" not in cols and "reminders_sent" not in cols
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()
        pg_server.drop_database(url)
