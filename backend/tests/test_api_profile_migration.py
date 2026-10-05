"""Миграция 0006_profile_fsp на чистой базе: цепочка 0001 → head даёт ровно схему моделей для таблиц профиля
и ФСП; downgrade до 0005 убирает их, повторный upgrade проходит.

Общий тест платформы (test_platform_models) сравнивает только таблицы из app.models.__all__, а
profile_snapshots и fsp_auth_states туда не входят (их модели — в app/models/candidate.py и fsp.py), поэтому
согласованность миграции с моделями для них проверяется здесь.
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
OUR_TABLES = {"profile_snapshots", "fsp_auth_states", "candidate_profiles", "fsp_links"}
NEW_TABLES = {"profile_snapshots", "fsp_auth_states"}


def _table_of(entry) -> str | None:
    item = entry[0] if isinstance(entry, list) else entry  # modify_* приходят списком
    kind = item[0]
    if kind in ("add_column", "remove_column") or kind.startswith("modify_"):
        return item[2]
    obj = item[1]  # Table | Index | Constraint
    table = getattr(obj, "table", None)
    return table.name if table is not None else getattr(obj, "name", None)


def _ddl(conn, tables: set[str]) -> tuple[set, set]:
    """Индексы и ограничения наших таблиц — как их видит PostgreSQL (имена и определения)."""
    idx = {
        (r.indexname, r.indexdef)
        for r in conn.execute(text("SELECT tablename, indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"))
        if r.tablename in tables
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
        if r.relname in tables
    }
    return idx, cons


def test_migration_0006_matches_models_and_downgrades(pg_server, db_engine):
    url = pg_server.create_database("mig0006")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
            migrated = _ddl(conn, OUR_TABLES)
            assert NEW_TABLES <= set(inspect(conn).get_table_names())
        ours = [d for d in diff if _table_of(d) in OUR_TABLES]
        assert ours == [], f"Миграция расходится с моделями: {ours}"
        # Индексы и ограничения (UNIQUE state_hash, FK с ON DELETE CASCADE) — те же, что даёт create_all
        with db_engine.connect() as conn:
            assert _ddl(conn, OUR_TABLES) == migrated

        command.downgrade(cfg, "0005_contact_api")
        with engine.connect() as conn:
            insp = inspect(conn)
            assert not NEW_TABLES & set(insp.get_table_names())
            assert "hidden_result_ids" not in {c["name"] for c in insp.get_columns("fsp_links")}
            assert "soft_skills" not in {c["name"] for c in insp.get_columns("candidate_profiles")}
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert NEW_TABLES <= set(inspect(conn).get_table_names())
    finally:
        engine.dispose()
        pg_server.drop_database(url)
