"""Миграция 0010_chat_ats на чистой базе: цепочка 0001 → head даёт ровно схему моделей чата и интеграции с ATS
(chat_messages, company_api_keys, ats_settings, ats_log); downgrade до предыдущей ревизии убирает их, повторный upgrade проходит.

Модели лежат в app/models/integration.py вне app.models.__all__, поэтому согласованность миграции проверяется здесь."""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.base import Base
from app.models import integration as _integration  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
NEW_TABLES = {"chat_messages", "company_api_keys", "ats_settings", "ats_log"}


def _table_of(entry) -> str | None:
    item = entry[0] if isinstance(entry, list) else entry
    kind = item[0]
    if kind in ("add_column", "remove_column") or kind.startswith("modify_"):
        return item[2]
    obj = item[1]
    table = getattr(obj, "table", None)
    return table.name if table is not None else getattr(obj, "name", None)


def _ddl(conn) -> tuple[set, set]:
    idx = {
        (r.indexname, r.indexdef)
        for r in conn.execute(text("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"))
        if r.tablename in NEW_TABLES
    }
    cons = {
        (r.relname, r.conname, r.contype, r.def_)
        for r in conn.execute(
            text(
                "SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid) AS def_ FROM pg_constraint k "
                "JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public'"
            )
        )
        if r.relname in NEW_TABLES
    }
    return idx, cons


def test_migration_0010_matches_models_and_downgrades(pg_server, db_engine):
    url = pg_server.create_database("mig0010")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            migrated = _ddl(conn)
            assert NEW_TABLES <= set(inspect(conn).get_table_names())
        ours = [d for d in diff if _table_of(d) in NEW_TABLES]
        assert ours == [], f"Миграция расходится с моделями: {ours}"
        with db_engine.connect() as conn:  # то же, что даёт create_all в тестовой базе
            assert _ddl(conn) == migrated

        command.downgrade(cfg, ScriptDirectory.from_config(cfg).get_revision("0010_chat_ats").down_revision)
        with engine.connect() as conn:
            assert not NEW_TABLES & set(inspect(conn).get_table_names())
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert NEW_TABLES <= set(inspect(conn).get_table_names())
    finally:
        engine.dispose()
        pg_server.drop_database(url)
