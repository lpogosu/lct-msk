"""Миграция 0012_interest_signals: схема совпадает с моделями, CHECK согласий принимает interest_signal,
downgrade убирает таблицу, повторный upgrade проходит."""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from alembic import command
from app.db.base import Base
from tests.test_api_trust_migration import _table_of

BACKEND = Path(__file__).resolve().parents[1]


def test_migration_0012_matches_models_and_downgrades(pg_server):
    url = pg_server.create_database("mig0012")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            assert "interest_signals" in inspect(conn).get_table_names()
        ours = [d for d in diff if _table_of(d) in {"interest_signals", "consents"}]
        assert ours == [], f"Миграция расходится с моделями: {ours}"
        assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1
        command.downgrade(cfg, "0011_trust_minors")
        with engine.connect() as conn:
            assert "interest_signals" not in inspect(conn).get_table_names()
        command.upgrade(cfg, "head")
    finally:
        engine.dispose()
        pg_server.drop_database(url)
