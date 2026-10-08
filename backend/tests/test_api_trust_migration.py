"""Миграция 0011_trust_minors на чистой базе: схема совпадает с моделями (метка компании, жалобы, поля представителя),
downgrade до предыдущей ревизии убирает их, повторный upgrade проходит; прежние жалобы из feedback переносятся."""

from __future__ import annotations

from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from alembic import command
from app.db.base import Base
from app.models import trust as _trust  # noqa: F401

BACKEND = Path(__file__).resolve().parents[1]
NEW_TABLES = {"company_verifications", "trust_complaints"}
GUARDIAN_COLUMNS = {"guardian_name", "guardian_email", "guardian_token_hash", "guardian_requested_at", "guardian_confirmed_at"}


def _table_of(entry) -> str | None:
    item = entry[0] if isinstance(entry, list) else entry
    kind = item[0]
    if kind in ("add_column", "remove_column") or kind.startswith("modify_"):
        return item[2]
    obj = item[1]
    table = getattr(obj, "table", None)
    return table.name if table is not None else getattr(obj, "name", None)


def test_migration_0011_matches_models_and_downgrades(pg_server):
    url = pg_server.create_database("mig0011")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
            insp = inspect(conn)
            assert NEW_TABLES <= set(insp.get_table_names())
            assert GUARDIAN_COLUMNS <= {c["name"] for c in insp.get_columns("candidate_profiles")}
        ours = [d for d in diff if _table_of(d) in NEW_TABLES | {"candidate_profiles"}]
        assert ours == [], f"Миграция расходится с моделями: {ours}"
        assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1

        command.downgrade(cfg, ScriptDirectory.from_config(cfg).get_revision("0011_trust_minors").down_revision)
        with engine.connect() as conn:
            insp = inspect(conn)
            assert not NEW_TABLES & set(insp.get_table_names())
            assert not GUARDIAN_COLUMNS & {c["name"] for c in insp.get_columns("candidate_profiles")}
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            assert NEW_TABLES <= set(inspect(conn).get_table_names())
    finally:
        engine.dispose()
        pg_server.drop_database(url)


def test_migration_0011_moves_old_complaints(pg_server):
    """Жалобы, записанные до миграции в feedback, попадают в очередь модерации с автором и компанией из приглашения."""
    url = pg_server.create_database("mig0011b")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "0010_chat_ats")
        with engine.begin() as conn:
            conn.execute(text("INSERT INTO companies (id, email_domain, personal_domain) VALUES ('00000000-0000-0000-0000-0000000000c1', 'x.ru', false)"))
            for uid, email, role in (("00000000-0000-0000-0000-0000000000a1", "c@x.ru", "candidate"),):
                conn.execute(
                    text("INSERT INTO users (id, email, email_domain, role, status, email_verified) VALUES (:i, :e, 'x.ru', :r, 'active', true)"),
                    {"i": uid, "e": email, "r": role},
                )
            conn.execute(
                text(
                    "INSERT INTO invitations (id, company_id, candidate_id, pseudonym, status, respond_by) VALUES "
                    "('00000000-0000-0000-0000-0000000000b1', '00000000-0000-0000-0000-0000000000c1', "
                    "'00000000-0000-0000-0000-0000000000a1', 'К-1', 'sent', now())"
                )
            )
            conn.execute(
                text(
                    "INSERT INTO feedback (target_type, target_id, direction, reason_code, comment) VALUES "
                    "('invitation', '00000000-0000-0000-0000-0000000000b1', 'complaint', 'fake_vacancy', 'странно')"
                )
            )
        command.upgrade(cfg, "head")
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT company_id, reporter_id, target_type, reason, comment, status FROM trust_complaints")).all()
            assert len(rows) == 1
            r = rows[0]
            assert str(r.company_id).endswith("c1") and str(r.reporter_id).endswith("a1")
            assert (r.target_type, r.reason, r.comment, r.status) == ("invitation", "fake_vacancy", "странно", "open")
            assert conn.execute(text("SELECT count(*) FROM feedback WHERE direction = 'complaint'")).scalar() == 0
    finally:
        engine.dispose()
        pg_server.drop_database(url)
