"""Платформа: схема БД — таблицы, инварианты на уровне PostgreSQL, миграция Alembic совпадает с моделями."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command
from app import models as m
from app.db.base import Base

BACKEND = Path(__file__).resolve().parents[1]
MODEL_TABLES = {getattr(m, name).__tablename__ for name in m.__all__ if hasattr(getattr(m, name), "__tablename__")}


def _violates(db, obj_or_sql) -> bool:
    """True, если вставка нарушает ограничение БД (проверка в SAVEPOINT, сессия остаётся рабочей)."""
    try:
        with db.begin_nested():
            if isinstance(obj_or_sql, str):
                db.execute(text(obj_or_sql))
            else:
                db.add(obj_or_sql)
                db.flush()
    except IntegrityError:
        return True
    return False


def _user(db, email=None, role="candidate") -> m.User:
    email = email or f"u-{uuid.uuid4().hex[:8]}@example.com"
    u = m.User(email=email, email_domain=email.split("@")[1], role=role, status="active", email_verified=True)
    db.add(u)
    db.flush()
    return u


def _company(db, domain=None, personal=False) -> m.Company:
    c = m.Company(email_domain=domain or f"{uuid.uuid4().hex[:6]}.ru", personal_domain=personal)
    db.add(c)
    db.flush()
    return c


def test_all_model_tables_exist(db_engine):
    tables = set(inspect(db_engine).get_table_names())
    assert MODEL_TABLES <= tables
    assert len(MODEL_TABLES) == 42  # +credentials (цифровой паспорт навыков)
    expected = {
        "users", "email_codes", "refresh_tokens", "consents", "audit_log", "companies", "webhook_deliveries",
        "candidate_profiles", "survey_responses", "candidate_features", "item_family_params", "item_instances",
        "test_sessions", "test_responses", "session_events", "flags", "qualifications", "grade_changes",
        "needs", "need_versions", "shortlist_pins", "invitations", "invitation_events", "offer_terms",
        "contact_disclosures", "applications", "vacancies", "employer_tasks", "task_assignments",
        "fsp_links", "fsp_achievements", "validation_runs",
    }
    assert expected <= MODEL_TABLES


def test_user_constraints(db_session):
    assert _violates(db_session, m.User(email="x@example.com", email_domain="example.com", role="superuser"))
    assert _violates(db_session, m.User(email="Upper@example.com", email_domain="example.com", role="candidate"))
    _user(db_session, "dup@example.com")
    assert _violates(db_session, m.User(email="dup@example.com", email_domain="example.com", role="candidate"))
    assert not _violates(db_session, m.User(email="ok@example.com", email_domain="example.com", role="methodist"))


def test_consent_purpose_checked_and_jsonb_defaults(db_session):
    u = _user(db_session)
    assert _violates(db_session, m.Consent(user_id=u.id, purpose="marketing", text_version="v1"))
    # Сырой INSERT без scope: серверные умолчания (seed, импорт) не падают на NOT NULL
    db_session.execute(
        text("INSERT INTO consents (user_id, purpose, text_version) VALUES (:u, 'blind_card', 'v1')"), {"u": u.id}
    )
    row = db_session.execute(text("SELECT id, scope, granted_at FROM consents WHERE user_id = :u"), {"u": u.id}).one()
    assert isinstance(row.id, uuid.UUID) and row.scope == {} and row.granted_at is not None


def test_one_active_test_session_per_candidate(db_session):
    from app.testing.session import SessionStatus as DomainStatus

    u = _user(db_session)

    def session(status, kind="initial"):
        return m.TestSession(candidate_id=u.id, specialization="backend_python", kind=kind, claimed_grade="middle", status=status, seed=1)

    # Слой хранения пишет значения доменного модуля app/testing как есть — индекс должен их видеть
    db_session.add(session(DomainStatus.IN_PROGRESS.value))
    db_session.flush()
    assert _violates(db_session, session(DomainStatus.CREATED.value))
    assert _violates(db_session, session(DomainStatus.PAUSED.value))
    assert not _violates(db_session, session(DomainStatus.FINAL.value))  # завершённых может быть сколько угодно
    assert not _violates(db_session, session("IN_PROGRESS", kind="preview"))  # превью/симуляции не мешают
    # Другой регистр не обходит гарантию молча: CHECK отклоняет запись
    assert _violates(db_session, m.TestSession(candidate_id=_user(db_session).id, specialization="backend_python", kind="initial", claimed_grade="middle", status="in_progress", seed=1))


def test_status_vocabularies_match_domain_modules():
    """Значения enum платформы = значения доменных модулей (их и будет писать слой хранения)."""
    from app.contact.statuses import PAIR_ACTIVE_STATUSES, InvitationStatus
    from app.models import enums as e
    from app.testing.session import QualStatus, SessionStatus

    assert {s.value for s in e.SessionStatus} == {s.value for s in SessionStatus}
    assert {s.value for s in e.QualificationStatus} == {s.value for s in QualStatus}
    from app.testing import cat

    assert {s.value for s in e.SessionDecision} == {cat.PASSED, cat.FAILED, cat.BORDERLINE_PASS, cat.BORDERLINE_FAIL}
    assert set(e.EXCLUSIVE_SESSION_KINDS) == {cat.KIND_INITIAL, cat.KIND_RETAKE, cat.KIND_UPGRADE, cat.KIND_EXT_UP, cat.KIND_EXT_DOWN}
    assert {s.value for s in e.FamilyStatus} >= {cat.OPERATIONAL, cat.QUARANTINE, cat.RETIRED, cat.PRETEST}
    from app.fsp.models import ResultStatus

    assert {s.value for s in e.AchievementStatus} == {s.value for s in ResultStatus}
    from app.contact.statuses import ApplicationStatus

    assert {s.value for s in e.ApplicationStatus} == {s.value for s in ApplicationStatus}
    assert {s.value for s in e.InvitationStatus} == {s.value for s in InvitationStatus}
    assert set(e.PAIR_ACTIVE_INVITATION_STATUSES) == {str(s) for s in PAIR_ACTIVE_STATUSES}


def test_one_active_invitation_per_pair(db_session, frozen_clock):
    cand, company = _user(db_session), _company(db_session)

    def inv(status):
        return m.Invitation(company_id=company.id, candidate_id=cand.id, pseudonym="К-0001", status=status, respond_by=frozen_clock.now())

    db_session.add(inv("sent"))
    db_session.flush()
    assert _violates(db_session, inv("countered"))
    assert not _violates(db_session, inv("declined"))
    # Открытый контакт (accepted) тоже занимает пару — как PAIR_ACTIVE_STATUSES модуля контакта
    cand2 = _user(db_session)
    db_session.add(m.Invitation(company_id=company.id, candidate_id=cand2.id, pseudonym="К-0003", status="accepted", respond_by=frozen_clock.now()))
    db_session.flush()
    assert _violates(db_session, m.Invitation(company_id=company.id, candidate_id=cand2.id, pseudonym="К-0003", status="sent", respond_by=frozen_clock.now()))
    assert not _violates(db_session, m.Invitation(company_id=company.id, candidate_id=cand2.id, pseudonym="К-0003", status="closed", respond_by=frozen_clock.now()))
    other = _company(db_session)
    assert not _violates(
        db_session,
        m.Invitation(company_id=other.id, candidate_id=cand.id, pseudonym="К-0002", status="sent", respond_by=frozen_clock.now()),
    )


def test_company_domain_uniqueness(db_session):
    _company(db_session, "severny-kod.ru")
    assert _violates(db_session, m.Company(email_domain="severny-kod.ru", personal_domain=False))
    _company(db_session, "gmail.com", personal=True)
    assert not _violates(db_session, m.Company(email_domain="gmail.com", personal_domain=True))


def test_need_salary_range_and_version_cycle(db_session):
    company = _company(db_session)
    assert _violates(db_session, m.Need(company_id=company.id, raw_text="т", salary_from=200_000, salary_to=150_000))
    need = m.Need(company_id=company.id, raw_text="Бэкенд", salary_from=220_000, salary_to=280_000)
    db_session.add(need)
    db_session.flush()
    v1 = m.NeedVersion(need_id=need.id, n=1, scoring_config_version="scoring@1")
    db_session.add(v1)
    db_session.flush()
    need.current_version_id = v1.id  # цикл needs ↔ need_versions
    db_session.flush()
    assert _violates(db_session, m.NeedVersion(need_id=need.id, n=1, scoring_config_version="scoring@1"))


def test_model_defaults_use_clock(db_session, frozen_clock):
    u = _user(db_session)
    assert u.created_at == frozen_clock.now() == datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
    profile = m.CandidateProfile(user_id=u.id)
    db_session.add(profile)
    db_session.flush()
    assert profile.formats == [] and profile.blocked_company_ids == [] and profile.field_visibility["full_name"] is True
    assert profile.field_visibility["email"] is True
    # Лимит приглашений по умолчанию — 5 из config/contact.yaml, а не NULL (= «без ограничений»)
    assert profile.weekly_invite_limit == 5
    unlimited = m.CandidateProfile(user_id=_user(db_session).id, weekly_invite_limit=None)  # явный выбор «∞»
    db_session.add(unlimited)
    db_session.flush()
    db_session.refresh(unlimited)
    assert unlimited.weekly_invite_limit is None


def test_field_visibility_speaks_contact_module_vocabulary():
    """К13 → ContactDisclosure: ключи видимости профиля — те же, что раскрывает модуль контакта,
    и каждому соответствует колонка профиля (или явно None)."""
    from app.contact.disclosure import chosen_fields, disclosable_fields

    assert set(m.DEFAULT_FIELD_VISIBILITY) == set(disclosable_fields())
    allowed = [k for k, on in m.DEFAULT_FIELD_VISIBILITY.items() if on]
    assert set(chosen_fields(allowed)) == set(allowed)  # ничего не отбрасывается молча
    assert "email" in allowed  # канал связи раскрывается по умолчанию
    assert set(m.DISCLOSURE_FIELD_COLUMNS) == set(m.DEFAULT_FIELD_VISIBILITY)
    columns = set(m.CandidateProfile.__table__.columns.keys())
    assert all(col is None or col in columns for col in m.DISCLOSURE_FIELD_COLUMNS.values())


def test_disclosure_consent_version_matches_contact_module():
    """Журнал согласий и ContactDisclosure ссылаются на одну редакцию текста согласия на раскрытие."""
    from app.contact.settings import get_contact_config
    from app.core.consents import current_version

    assert current_version("contact_disclosure") == get_contact_config().disclosure.consent_text_version


_CHECKS_SQL = """
SELECT conrelid::regclass::text AS tbl, conname, pg_get_constraintdef(oid) AS def
FROM pg_constraint WHERE contype = 'c' AND connamespace = 'public'::regnamespace
"""
_INDEXES_SQL = "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public'"


def _ddl_facts(conn) -> tuple[set, set]:
    """CHECK-ограничения и определения индексов (с предикатами частичных индексов): автогенерация
    Alembic их не сравнивает, а именно в них живут гарантии «одна активная сессия/приглашение»."""
    checks = {tuple(r) for r in conn.execute(text(_CHECKS_SQL))}
    indexes = {tuple(r) for r in conn.execute(text(_INDEXES_SQL))}
    return checks, indexes


def test_alembic_migration_matches_models(pg_server, db_engine):
    """Миграции на чистой базе дают ровно схему моделей (включая CHECK и предикаты частичных
    индексов); downgrade убирает всё."""
    url = pg_server.create_database("mig")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "head")

        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
            assert conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).scalar() == 1
            fks = {fk["name"] for fk in inspect(conn).get_foreign_keys("needs")}
            assert "fk_needs_current_version_id_need_versions" in fks
            migrated_checks, migrated_indexes = _ddl_facts(conn)
        # Эталон — схема из моделей (create_all в базе тестового прогона)
        with db_engine.connect() as conn:
            model_checks, model_indexes = _ddl_facts(conn)
        def checks_of_ours(rows: set) -> set:
            return {r for r in rows if r[0].split(".")[-1] in MODEL_TABLES}

        def indexes_of_ours(rows: set) -> set:
            return {r for r in rows if r[1].split(" ON public.")[1].split(" ")[0] in MODEL_TABLES}

        assert checks_of_ours(migrated_checks) == checks_of_ours(model_checks)
        assert indexes_of_ours(migrated_indexes) == indexes_of_ours(model_indexes)
        # Сравнение не пустое: гарантии из 0002 действительно на месте
        pair = next(d for name, d in migrated_indexes if name == "uq_invitations_active_pair")
        assert "accepted" in pair
        session_idx = next(d for name, d in migrated_indexes if name == "uq_test_sessions_one_active")
        assert "IN_PROGRESS" in session_idx
        assert any(name == "ck_test_sessions_status" for _, name, _ in migrated_checks)

        def table_of(entry) -> str | None:
            item = entry[0] if isinstance(entry, list) else entry  # modify_* приходят списком
            kind = item[0]
            if kind in ("add_column", "remove_column") or kind.startswith("modify_"):
                return item[2]
            obj = item[1]  # Table | Index | Constraint
            table = getattr(obj, "table", None)
            return table.name if table is not None else getattr(obj, "name", None)

        # Учитываем только таблицы платформы: модели других модулей (если появятся) мигрируют сами
        ours = [d for d in diff if table_of(d) in MODEL_TABLES or table_of(d) is None]
        assert ours == [], f"Миграция расходится с моделями: {ours}"

        command.downgrade(cfg, "base")
        with engine.connect() as conn:
            left = set(inspect(conn).get_table_names()) - {"alembic_version"}
        assert left == set()
    finally:
        engine.dispose()
        pg_server.drop_database(url)


@pytest.mark.parametrize("name", ["Need", "Invitation", "TestSession", "Consent", "ContactDisclosure"])
def test_key_models_have_uuid_pk(name):
    pk = getattr(m, name).__table__.primary_key.columns
    assert len(pk) == 1 and next(iter(pk)).name == "id"
