"""Признаки кандидата для подбора (контракт build_candidate_features) и кэш с инвалидацией; LLM-дополнение
разбора брифа (выключено по умолчанию, только с проверенной цитатой); миграция 0004."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text

from alembic import command
from app.core.clock import clock
from app.db.base import Base
from app.models.fsp import FspAchievementCache
from app.models.matching import MatchingFeatures
from app.services.matching import build_candidate_features, llm_brief, refresh_candidate_features
from app.services.testing.qualification import get_row, load_domain, save_domain
from tests.test_api_needs_support import FSP_STRONG, fsp_registry_data, seed_candidate

pytestmark = pytest.mark.usefixtures("frozen_clock")
BACKEND = Path(__file__).resolve().parents[1]


def test_fullstack_rows_grade_is_lower(db_session):
    cid = seed_candidate(db_session, theta=0.6, extra=[("frontend_js", "junior", -0.5)])
    rows = {f.spec: f for f in build_candidate_features(db_session, cid)}
    assert set(rows) == {"backend_python", "frontend_js", "fullstack"}
    assert rows["fullstack"].grade == "junior" and rows["backend_python"].grade == "middle"
    assert all(f.is_fullstack for f in rows.values())
    assert {"python_core", "js_core", "react"} <= set(rows["fullstack"].modules)
    assert rows["backend_python"].modules["python_core"].se == 0.35  # погрешность модуля — своя, из подбаллов
    assert rows["backend_python"].test_session_id  # id попытки — из строки квалификации (для ссылки-доказательства)
    assert rows["backend_python"].theta_age_days == pytest.approx(20.0)


def test_features_settings_tasks_and_declared(db_session):
    cid = seed_candidate(
        db_session,
        salary_min=250_000,
        salary_net=False,
        salary_visibility="exact",
        formats=("office",),
        declared=("kafka", "docker"),
        tasks=[("Очередь", 0.8, 6, ("python",), "python_core")],
    )
    (f,) = build_candidate_features(db_session, cid)
    assert (f.salary_min, f.salary_net, f.salary_visibility) == (250_000, False, "exact")
    assert f.formats == frozenset({"office"}) and f.city == "moscow" and f.industries == frozenset({"fintech"})
    assert f.declared_skills == frozenset({"kafka", "docker"})
    (t,) = f.tasks
    assert t.kind == "A" and t.module == "python_core" and t.q == pytest.approx(0.8) and t.days_ago == pytest.approx(6)
    assert f.vss_by_discipline == {} and f.fsp_summary is None  # без ФСП — F = 0, не штраф


def test_fsp_vss_hidden_results_and_consent(db_session):
    cid = seed_candidate(db_session, fsp_sub=FSP_STRONG)
    (f,) = build_candidate_features(db_session, cid)
    assert f.vss_by_discipline.get("product", 0) > 50 and "Призёр всероссийских" in (f.fsp_summary or "")
    before = f.vss_by_discipline["product"]
    # кандидат скрыл все результаты — в VSS они не идут (концепция §6.2), остаётся только оценка по разряду
    ach, _ = fsp_registry_data(FSP_STRONG)
    for r in ach["results"]:
        db_session.add(
            FspAchievementCache(user_id=cid, fsp_id=FSP_STRONG, external_id=r["id"], kind="result", hidden=True, payload={})
        )
    db_session.flush()
    (f2,) = build_candidate_features(db_session, cid)
    assert f2.vss_by_discipline.get("product", 0) < before and f2.fsp_summary is None
    # отзыв согласия на данные ФСП — профиль «не привязан»
    from app.core.consents import find_active_consent, revoke_consent

    revoke_consent(db_session, find_active_consent(db_session, cid, "fsp_enrichment"))
    db_session.flush()
    (f3,) = build_candidate_features(db_session, cid)
    assert f3.vss_by_discipline == {}


def test_cache_invalidated_by_qualification_hook(db_session):
    cid = seed_candidate(db_session, theta=0.4)
    assert refresh_candidate_features(db_session, cid) == 1
    row = db_session.scalars(select(MatchingFeatures).where(MatchingFeatures.candidate_id == cid)).one()
    assert row.fingerprint and row.visible and row.grade == "middle"
    q = get_row(db_session, cid, "backend_python")
    dom = load_domain(q)
    dom.grade, dom.theta = "senior", 1.3
    save_domain(db_session, q, dom, notify=True)  # хук модуля тестирования → кэш помечен устаревшим
    db_session.expire_all()
    row = db_session.scalars(select(MatchingFeatures).where(MatchingFeatures.candidate_id == cid)).one()
    assert row.fingerprint is None
    from app.services.matching.features import ensure_fresh

    assert ensure_fresh(db_session) >= 1
    db_session.expire_all()
    row = db_session.scalars(select(MatchingFeatures).where(MatchingFeatures.candidate_id == cid)).one()
    assert row.grade == "senior" and row.fingerprint
    # TTL кэша: через 8 дней без изменений строка пересобирается (свежесть VSS, истечение разрядов)
    clock.advance(timedelta(days=8).total_seconds())
    assert ensure_fresh(db_session) >= 1


class _FakeLLM:
    name = "yandexgpt"

    def complete(self, system: str, user: str, timeout_s: float) -> str:
        return json.dumps(
            {
                "items": [
                    {"field": "skill", "value": "Kafka", "quote": "очередью сообщений"},
                    {"field": "skill", "value": "Go", "quote": "пишем на Go"},  # цитаты в тексте нет — отброс
                    {"field": "skill", "value": "Python", "quote": "Python"},  # уже найдено правилами
                    {"field": "skill", "value": "Телепатия", "quote": "сервис"},  # нет в справочнике
                ]
            },
            ensure_ascii=False,
        )


def test_llm_only_adds_skills_with_verified_quotes(client, make_account, monkeypatch):
    from app.llm.gateway import LLMGateway
    from app.llm.settings import LLMSettings

    emp = make_account("hr@severny-kod.ru", role="employer")
    brief = "Строим сервис платежей на Python с очередью сообщений. 200–250 тыс. на руки"
    off = client.post("/api/needs/parse", json={"text": brief}, headers=emp.headers).json()
    assert off["llm_used"] is False and [c["skill_id"] for c in off["skills"]] == ["python"]
    gw = LLMGateway(settings=LLMSettings(provider="auto"), providers=[_FakeLLM()])
    monkeypatch.setattr(llm_brief, "get_gateway", lambda: gw)
    on = client.post("/api/needs/parse", json={"text": brief}, headers=emp.headers).json()
    assert on["llm_used"] is True
    chips = {c["skill_id"]: c for c in on["skills"]}
    assert set(chips) == {"python", "kafka"}
    assert chips["python"]["source"] == "правило"
    k = chips["kafka"]
    assert k["source"] == "YandexGPT" and k["kind"] == "nice" and k["reason"] == "llm"
    assert brief[k["quote"]["start"] : k["quote"]["end"]] == "очередью сообщений"
    created = client.post("/api/needs", json={"text": brief}, headers=emp.headers)
    assert created.status_code == 201 and "kafka" in created.json()["brief"]["nice"]
    assert created.json()["parse"]["llm_used"] is True


def test_migration_0004_creates_tables_and_downgrades(pg_server, db_engine):
    url = pg_server.create_database("mig4")
    engine = create_engine(url)
    try:
        cfg = Config(str(BACKEND / "alembic.ini"))
        cfg.attributes["url"] = url.render_as_string(hide_password=False)
        cfg.attributes["configure_logger"] = False
        command.upgrade(cfg, "0004_matching_api")
        ours = {"matching_features", "candidate_aliases"}
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)

            def table_of(entry) -> str | None:
                item = entry[0] if isinstance(entry, list) else entry
                kind = item[0]
                if kind in ("add_column", "remove_column") or kind.startswith("modify_"):
                    return item[2]
                obj = item[1]
                table = getattr(obj, "table", None)
                return table.name if table is not None else getattr(obj, "name", None)

            assert [d for d in diff if table_of(d) in ours] == []
            idx = {i["name"]: i for i in inspect(conn).get_indexes("matching_features")}
            assert "visible" in str(idx["ix_matching_features_category"].get("dialect_options", {}))
            comp = dict(
                conn.execute(
                    text(
                        "SELECT attname, attcompression FROM pg_attribute "
                        "WHERE attrelid = 'need_versions'::regclass AND attname IN ('items', 'stats')"
                    )
                ).all()
            )
            assert comp == {"items": "l", "stats": "l"}
        command.downgrade(cfg, "0003_testing_api")
        with engine.connect() as conn:
            assert not (ours & set(inspect(conn).get_table_names()))
    finally:
        engine.dispose()
        pg_server.drop_database(url)
