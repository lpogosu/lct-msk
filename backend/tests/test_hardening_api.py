"""Хардненинг (05.10 вечер): сводка банка и справочник навыков, типизированная карточка кандидата в приглашении,
can_dispute по сроку, название города вакансии, управление демо на боевом стенде, режим жюри при сбросе, адрес возврата
ФСП ID, явное «реестр недоступен», Swagger без внешних CDN, клиентский IP за прокси."""

from __future__ import annotations

import re
from types import SimpleNamespace

import pytest
from fastapi import Request

from app.core.netutil import is_trusted_proxy, parse_trusted_proxies, resolve_client_ip
from app.core.rate_limit import client_ip
from app.core.settings import Settings
from app.services.demo import (
    models as _demo_models,  # noqa: F401 — таблицы демо должны попасть в схему тестовой БД
)
from tests.test_api_contact_support import (
    advance,
    assert_no_pii,
    invite,
    make_candidate,
    make_employer,
    make_need,
    open_card,
)

# --------------------------------------------------------------------------- п. 5: клиентский IP за прокси


@pytest.mark.parametrize(
    ("peer", "xff", "spec", "expected"),
    [
        ("203.0.113.9", "1.2.3.4", "127.0.0.1", "203.0.113.9"),  # сосед не прокси: заголовок подделан клиентом
        ("127.0.0.1", "198.51.100.7", "127.0.0.1", "198.51.100.7"),  # прокси на loopback
        ("172.29.80.5", "198.51.100.7", "172.29.80.0/24", "198.51.100.7"),  # Caddy в подсети compose
        ("172.29.80.5", "6.6.6.6, 198.51.100.7", "172.29.80.0/24", "198.51.100.7"),  # левое подделано клиентом: берём правое
        ("172.29.80.5", "198.51.100.7, 172.29.80.9", "172.29.80.0/24", "198.51.100.7"),  # цепочка прокси
        ("172.29.80.5", "мусор", "172.29.80.0/24", "172.29.80.5"),  # не IP — не верим заголовку
        ("172.29.80.5", None, "172.29.80.0/24", "172.29.80.5"),  # заголовка нет
        ("10.1.1.1", "2001:db8::1", "10.0.0.0/8", "2001:db8::1"),  # IPv6 клиент за IPv4-прокси
        ("203.0.113.9", "1.2.3.4", "*", "1.2.3.4"),  # «*» — доверять любому (dev)
        (None, "1.2.3.4", "*", "unknown"),
    ],
)
def test_resolve_client_ip(peer, xff, spec, expected):
    assert resolve_client_ip(peer, xff, spec) == expected


def test_trusted_proxy_spec_validation():
    assert parse_trusted_proxies("*") == (True, ())
    assert is_trusted_proxy("10.2.3.4", "10.0.0.0/8, 127.0.0.1") and not is_trusted_proxy("11.0.0.1", "10.0.0.0/8")
    assert not is_trusted_proxy("::1", "10.0.0.0/8")  # семейства адресов не смешиваются
    with pytest.raises(ValueError, match="не IP-адрес"):
        parse_trusted_proxies("не-адрес")
    with pytest.raises(ValueError):
        Settings(_env_file=None, forwarded_allow_ips="999.1.1.1")


def test_prod_rejects_wildcard_proxy_trust():
    secret = "x" * 40
    base = {"env": "prod", "jwt_secret": secret, "app_secret": secret + "a", "pseudonym_secret": secret + "b", "cookie_secure": True}
    assert Settings(_env_file=None, forwarded_allow_ips="172.29.80.0/24", **base).forwarded_allow_ips == "172.29.80.0/24"
    with pytest.raises(ValueError, match="FORWARDED_ALLOW_IPS"):
        Settings(_env_file=None, forwarded_allow_ips="*", **base)


def test_forwarded_allow_ips_env_name_like_uvicorn(monkeypatch):
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "10.0.0.0/8")
    assert Settings(_env_file=None).forwarded_allow_ips == "10.0.0.0/8"
    monkeypatch.setenv("VZ_FORWARDED_ALLOW_IPS", "192.168.0.0/16")
    assert Settings(_env_file=None).forwarded_allow_ips == "192.168.0.0/16"


def _request(peer: str, xff: str | None, allow: str) -> Request:
    headers = [(b"x-forwarded-for", xff.encode())] if xff else []
    scope = {
        "type": "http",
        "headers": headers,
        "client": (peer, 5000),
        "app": SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(forwarded_allow_ips=allow))),
    }
    return Request(scope)


def test_client_ip_uses_app_settings():
    assert client_ip(_request("203.0.113.9", "1.1.1.1", "127.0.0.1")) == "203.0.113.9"
    assert client_ip(_request("127.0.0.1", "1.1.1.1", "127.0.0.1")) == "1.1.1.1"


def test_rate_limit_is_per_real_client_behind_trusted_proxy(client, app, test_settings):
    """За доверенным прокси разные клиенты не делят лимит, а подмена заголовка с чужого адреса его не обходит."""
    from fastapi.testclient import TestClient

    object.__setattr__(app.state.settings, "forwarded_allow_ips", "10.0.0.0/8")
    body = {"email": "nobody@example.com", "password": "неверный-пароль-1"}
    limit = app.state.settings.rate("login_ip").capacity
    # login_email лимитирует по email — меняем и его, чтобы упираться именно в лимит по IP
    with TestClient(app, client=("10.0.0.5", 4000)) as behind_proxy:
        for i in range(limit):
            r = behind_proxy.post("/api/auth/login", json={**body, "email": f"u{i}@example.com"}, headers={"X-Forwarded-For": "198.51.100.1"})
            assert r.status_code == 401
        r = behind_proxy.post("/api/auth/login", json={**body, "email": "z@example.com"}, headers={"X-Forwarded-For": "198.51.100.1"})
        assert r.status_code == 429  # лимит именно клиента 198.51.100.1
        other = behind_proxy.post("/api/auth/login", json={**body, "email": "y@example.com"}, headers={"X-Forwarded-For": "198.51.100.2"})
        assert other.status_code == 401  # другой клиент за тем же прокси — свой лимит
    with TestClient(app, client=("203.0.113.50", 4000)) as stranger:
        # недоверенный сосед подставляет чужой IP, но лимит считается по нему самому
        r = stranger.post("/api/auth/login", json={**body, "email": "w@example.com"}, headers={"X-Forwarded-For": "198.51.100.2"})
        assert r.status_code == 401
        for i in range(limit - 1):
            stranger.post("/api/auth/login", json={**body, "email": f"s{i}@example.com"}, headers={"X-Forwarded-For": f"9.9.9.{i}"})
        assert stranger.post("/api/auth/login", json={**body, "email": "t@example.com"}, headers={"X-Forwarded-For": "9.9.9.200"}).status_code == 429


# --------------------------------------------------------------------------- п. 6: документация без внешних CDN


def test_docs_pages_use_only_local_assets(client):
    for path in ("/api/docs", "/api/redoc"):
        html = client.get(path).text
        assert not re.findall(r"https?://", html), path  # ни одного внешнего адреса, включая CDN и шрифты
        for src in re.findall(r'(?:src|href)="(/[^"]+)"', html):
            r = client.get(src)
            assert r.status_code == 200, src
    js = client.get("/api/docs-assets/swagger-ui-bundle.js")
    assert js.headers["content-type"].startswith("text/javascript")  # при nosniff браузер не выполнит text/plain
    assert client.get("/api/docs-assets/redoc.standalone.js").status_code == 200
    assert client.get("/api/docs-assets/LICENSE.swagger-ui-dist.txt").status_code == 200  # лицензия лежит рядом
    assert "fonts.googleapis.com" not in client.get("/api/docs-assets/swagger-ui.css").text


# --------------------------------------------------------------------------- пп. 8, 16: адрес возврата ФСП ID


def test_fsp_redirect_uri_is_consistent_end_to_end():
    import json
    from pathlib import Path

    from app.fsp.settings import FSPSettings

    root = Path(__file__).resolve().parents[2]
    uri = Settings(_env_file=None).fsp_oidc_redirect_uri
    assert uri == FSPSettings().redirect_uri  # две настройки не расходятся
    assert uri.endswith("/candidate/fsp/callback")
    realm = json.loads((root / "deploy" / "keycloak" / "realm-fsp.json").read_text(encoding="utf-8"))
    assert uri in realm["clients"][0]["redirectUris"]
    routes = (root / "frontend" / "src" / "app" / "routes.tsx").read_text(encoding="utf-8")
    assert "path: 'candidate/fsp/callback'" in routes  # страница на этом адресе есть во фронтенде
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    assert "candidate/fsp/callback" in compose and "/auth/fsp/callback" not in compose


def test_backend_callback_forwards_browser_navigation_to_frontend_page(client):
    state = "s" * 24
    r = client.get(
        "/api/fsp/callback", params={"code": "c-1", "state": state}, headers={"Sec-Fetch-Mode": "navigate"}, follow_redirects=False
    )
    assert r.status_code == 303
    assert r.headers["location"] == f"http://localhost:5173/candidate/fsp/callback?code=c-1&state={state}"
    # отказ ФСП ID доезжает до страницы с пояснением
    r = client.get(
        "/api/fsp/callback",
        params={"error": "access_denied", "error_description": "Вход отклонён", "state": state},
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
    )
    assert r.status_code == 303 and "error=access_denied" in r.headers["location"]
    # запрос страницы (fetch) без кода — обычная ошибка API, а не редирект
    r = client.get("/api/fsp/callback", params={"state": state})
    assert r.status_code == 400 and r.json()["code"] == "fsp_callback_params"


# --------------------------------------------------------------------------- п. 17: «реестр недоступен»


def test_registry_outage_is_explicit_state_not_silent_linked_no_results(client, app, make_account, frozen_clock):
    from app.fsp.provider import FSPUnavailable
    from app.services.fsp_link import service as fsp

    class Down:
        def get_athlete(self, sub):
            raise FSPUnavailable("нет сети")

        get_achievements = get_competition = get_athlete

    app.dependency_overrides[fsp.get_provider] = lambda: Down()
    acc = make_account(role="candidate")
    sub = fsp.demo_registry().snapshot.athletes[0].sub
    r = client.post("/api/fsp/demo-login", json={"sub": sub, "mode": "link"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    me = client.get("/api/fsp/me", headers=acc.headers).json()
    assert me["state"] == "registry_unavailable" and me["linked"] is True
    assert "недоступен" in me["status_line"] and me["sync_error"]
    assert me["results"] == []  # данных нет, но это не «результатов нет» и не штраф: F = 0
    # согласие и обновление: явный 503, прежних данных нет — состояние по-прежнему «недоступен», а не «результатов нет»
    r = client.post("/api/fsp/me/enrichment", json={}, headers=acc.headers)
    assert r.status_code == 200 and r.json()["state"] == "registry_unavailable"
    assert client.post("/api/fsp/me/refresh", headers=acc.headers).status_code == 503


# --------------------------------------------------------------------------- п. 18: can_dispute по сроку


def _accepted(client, make_account, db_session, emp):
    cand = make_candidate(make_account, db_session)
    card = open_card(db_session, emp, cand)
    inv = invite(client, emp, card.ref).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200, r.text
    return cand, inv


def test_can_dispute_follows_contact_deadline(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, inv = _accepted(client, make_account, db_session, emp)
    url = f"/api/invitations/inbox/{inv['id']}"
    card = client.get(url, headers=cand.headers).json()
    # сразу после «да»: срок первого контакта не прошёл — кнопки нет, запрос дал бы 409
    assert card["can_dispute"] is False
    assert client.post(f"/api/invitations/{inv['id']}/dispute", json={}, headers=cand.headers).status_code == 409
    advance(days=3, hours=1)
    card = client.get(url, headers=cand.headers).json()
    assert card["can_dispute"] is True  # флаг и guard перехода согласованы
    r = client.post(f"/api/invitations/{inv['id']}/dispute", json={"comment": "Никто не написал"}, headers=cand.headers)
    assert r.status_code == 200 and r.json()["can_dispute"] is False


# --------------------------------------------------------------------------- п. 19: название города вакансии


def test_vacancy_city_name(client, make_account, db_session, frozen_clock, monkeypatch):
    emp = make_employer(client, make_account, db_session)
    need = make_need(db_session, emp)
    r = client.post("/api/vacancies", json={"need_id": str(need.id), "city": "moscow"}, headers=emp.headers)
    assert r.status_code == 201, r.text
    v = r.json()["vacancy"]
    assert v["city"] == "moscow" and v["city_name"] == "Москва"
    need2 = make_need(db_session, emp)
    r = client.post("/api/vacancies", json={"need_id": str(need2.id), "city": "Тверь"}, headers=emp.headers)
    assert r.status_code == 201, r.text
    assert r.json()["vacancy"]["city_name"] == "Тверь"  # свободный текст остаётся как есть
    mine = client.get("/api/vacancies/mine", headers=emp.headers).json()
    assert {v["city_name"] for v in mine} == {"Москва", "Тверь"}


# --------------------------------------------------------------------------- пп. 12, 13: типы ответа


def test_employer_invitation_detail_has_typed_candidate_and_snapshot(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, inv = _accepted(client, make_account, db_session, emp)
    detail = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    c = detail["candidate"]
    assert c["view"] == "disclosed" and c["phone"] == cand.phone and c["pseudonym"] == inv["pseudonym"]
    assert {"ref", "available", "category"} <= set(c)  # одна форма с карточкой панели подборки
    # слепая версия: ключей ПДн в ответе нет вообще (не «пустые», а отсутствующие)
    client.post(f"/api/invitations/{inv['id']}/revoke-consent", headers=cand.headers)
    blind = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    assert blind["candidate"]["view"] == "blind"
    assert_no_pii(blind, cand)


def test_openapi_types_candidate_card_and_marks_defaults_required(client):
    spec = client.get("/api/openapi.json").json()
    schemas = spec["components"]["schemas"]
    detail = schemas["InvitationEmployerDetail"] if "InvitationEmployerDetail" in schemas else schemas["InvitationEmployerDetail-Output"]
    cand = detail["properties"]["candidate"]
    refs = {o["$ref"].rsplit("/", 1)[-1] for o in cand["oneOf"]}
    assert refs == {"EmployerCandidateBlind", "EmployerCandidateDisclosed"} and cand["discriminator"]["propertyName"] == "view"
    snap = detail["properties"]["score_snapshot"]
    assert any(o.get("$ref", "").endswith("/ScoreSnapshotOut") for o in snap["anyOf"])
    # поля с умолчаниями в ответах обязательны (json_schema_serialization_defaults_required)
    card = schemas["CandidateCardOut"]
    assert set(card["properties"]) <= set(card["required"])
    assert "city_name" in schemas["VacancyOut"]["required"] and "city" in schemas["VacancyOut"]["required"]
    # тип запроса от этого не страдает: поле с умолчанием в теле остаётся необязательным
    assert "text" in schemas["BriefParseIn"]["required"] and "clear_city" not in schemas["BriefParseIn"]["required"]


# --------------------------------------------------------------------------- п. 10: управление демо на боевом стенде


@pytest.fixture
def demo_app_factory(mailer, db_session):
    def _make(**settings_kw):
        from fastapi.testclient import TestClient

        from app.db.session import get_db
        from app.main import create_app

        cfg = Settings(
            _env_file=None, env="test", demo_mode=True, argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1,
            mail_backend="console", **settings_kw,
        )
        application = create_app(cfg, mailer)
        application.state.demo_loop = False

        def _override():
            yield db_session

        application.dependency_overrides[get_db] = _override
        return TestClient(application), cfg

    return _make


def _login(client, email: str, password: str) -> dict[str, str]:
    r = client.post("/api/auth/login", json={"email": email, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _register(client, mailer, email: str, role: str) -> dict[str, str]:
    from tests.conftest import code_from_mail

    reg = client.post("/api/auth/register", json={"email": email, "password": "Parol-2026", "role": role, "consent_personal_data": True})
    assert reg.status_code == 201
    if reg.json()["status"] == "active":  # DEMO_MODE: регистрация без письма с кодом
        r = client.post("/api/auth/login", json={"email": email, "password": "Parol-2026"})
    else:
        r = client.post("/api/auth/verify-email", json={"email": email, "code": code_from_mail(mailer, email), "password": "Parol-2026"})
    assert r.status_code == 200, r.text
    client.cookies.clear()
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_demo_controls_open_to_any_role_outside_prod_and_admin_only_in_prod(demo_app_factory, mailer, db_session, frozen_clock):
    from app.models.users import User

    # вне prod — как раньше: любая роль управляет часами и режимом жюри
    c, _ = demo_app_factory()
    with c:
        cand = _register(c, mailer, "cand-open@example.com", "candidate")
        assert c.post("/api/demo/jury-mode", json={"enabled": True}, headers=cand).status_code == 200
        assert c.post("/api/demo/clock", json={"preset": "reset"}, headers=cand).status_code == 200

    # боевой стенд (demo_controls_admin_only; в VZ_ENV=prod это значение по умолчанию): только админ или ключ
    c, cfg = demo_app_factory(demo_controls_admin_only=True, demo_control_key="k" * 24)
    with c:
        assert cfg.demo_controls_restricted is True
        cand = _register(c, mailer, "cand-prod@example.com", "candidate")
        admin = _register(c, mailer, "adm@example.com", "candidate")
        db_session.query(User).filter(User.email == "adm@example.com").update({"role": "admin"})
        db_session.flush()
        for method, url, body in (
            ("post", "/api/demo/jury-mode", {"enabled": True}),
            ("post", "/api/demo/clock", {"preset": "plus_1d"}),
            ("post", "/api/demo/autoresponder", {"enabled": False}),
            ("post", "/api/demo/reset", None),
        ):
            r = getattr(c, method)(url, json=body, headers=cand)
            assert r.status_code == 403 and r.json()["code"] == "demo_controls_restricted", (url, r.text)
            assert getattr(c, method)(url, json=body).status_code == 401  # без входа и без ключа
        # ключ управления — без входа; неверный ключ не подходит
        assert c.post("/api/demo/jury-mode", json={"enabled": True}, headers={"X-Demo-Key": "k" * 24}).status_code == 200
        assert c.post("/api/demo/jury-mode", json={"enabled": False}, headers={"X-Demo-Key": "x" * 24}).status_code == 401
        # админ управляет без ключа
        assert c.post("/api/demo/clock", json={"preset": "reset"}, headers=admin).status_code == 200
        # открытое остаётся открытым: состояние и список аккаунтов — для экрана входа
        assert c.get("/api/demo/state").status_code == 200 and c.get("/api/demo/accounts").status_code == 200


def test_prod_default_restricts_demo_controls():
    base = {"jwt_secret": "x" * 40, "app_secret": "y" * 40, "pseudonym_secret": "z" * 40, "cookie_secure": True}
    assert Settings(_env_file=None, env="prod", **base).demo_controls_restricted is True
    assert Settings(_env_file=None, env="dev").demo_controls_restricted is False
    assert Settings(_env_file=None, env="prod", demo_controls_admin_only=False, **base).demo_controls_restricted is False


# --------------------------------------------------------------------------- п. 21: сброс демо не выключает режим жюри


def test_demo_reset_keeps_jury_mode(demo_app_factory, mailer, db_session, frozen_clock, monkeypatch):
    from app.services.demo import world
    from app.services.demo.state import get_state

    calls = []

    def fake_build(db, settings, say):
        calls.append(1)
        return {"scenario": {}}

    monkeypatch.setattr(world, "build_personas_and_scenario", fake_build)
    monkeypatch.setattr(world, "purge_contact_data", lambda db: {})
    monkeypatch.setattr(world, "drop_personas", lambda db: None)
    monkeypatch.setattr(world, "write_credentials_file", lambda settings: "var/demo_accounts.md")
    monkeypatch.setattr(world, "counts", lambda db: {})

    c, cfg = demo_app_factory()
    with c:
        who = _register(c, mailer, "jury-reset@example.com", "candidate")
        assert c.post("/api/demo/jury-mode", json={"enabled": True}, headers=who).json()["jury_mode"] is True
        since = get_state(db_session).jury_mode_since
        assert since is not None
        r = c.post("/api/demo/reset", headers=who)
        assert r.status_code == 200, r.text
        assert calls == [1]
        st = c.get("/api/demo/state").json()
        assert st["jury_mode"] is True  # раньше сброс выключал режим жюри
        assert get_state(db_session).jury_mode_since == since


# --------------------------------------------------------------------------- пп. 11, 14: сводка банка и справочник навыков


@pytest.fixture
def pool(db_session):
    from tests.test_api_needs_support import seed_demo_pool

    return seed_demo_pool(db_session)


def test_bank_summary_matches_bank_lists_and_guards_salary_quantiles(client, make_account, pool):
    emp = make_account("hr@severny-kod.ru", role="employer")
    r = client.get("/api/bank/summary", headers=emp.headers)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["salary_basis"] == "net" and s["privacy_min_n"] == 10
    grades = ["intern", "junior", "middle", "senior"]
    by_spec = {x["spec"]: x for x in s["specs"]}
    assert {"backend_python", "frontend_js", "fullstack", "data_ml"} <= set(by_spec)
    assert by_spec["backend_python"]["testable"] is True and by_spec["data_ml"]["testable"] is False
    total = 0
    for spec in s["specs"]:
        assert [c["grade"] for c in spec["cells"]] == grades
        for cell in spec["cells"]:
            if cell["n"] == 0:
                assert cell["fsp_share"] is None and cell["salary_min"] is None
            else:
                # список банка по категории с фильтром «есть ФСП»: total — с ФСП, hidden.count — без (никого не удаляет)
                bank = client.get(
                    "/api/bank", params={"category": f"{spec['spec']}:{cell['grade']}", "has_fsp": True}, headers=emp.headers
                ).json()
                assert cell["with_fsp"] == bank["total"], (spec["spec"], cell["grade"])
                assert cell["n"] == bank["total"] + bank["hidden"]["count"], (spec["spec"], cell["grade"])
                assert cell["fsp_share"] == pytest.approx(cell["with_fsp"] / cell["n"], abs=1e-3)
            q = cell["salary_min"]
            if q is not None:
                assert cell["n"] >= s["privacy_min_n"] and q["p25"] <= q["p50"] <= q["p75"]
                assert all(v % 10000 == 0 for v in q.values())  # округление до ступени: точных минимумов людей нет
        total += spec["n"]
    # в демо-пуле 16 видимых кандидатов; Fullstack-кандидат входит в несколько категорий, поэтому сумма ячеек больше
    assert s["total"] == 16 and total >= 16
    # маленькая ячейка не раскрывает квантили (Middle backend — 12–13 кандидатов; senior — 3)
    senior = next(c for c in by_spec["backend_python"]["cells"] if c["grade"] == "senior")
    assert senior["n"] == 3 and senior["salary_min"] is None
    # PII-ключей в сводке нет и она не требует кандидатской роли
    assert client.get("/api/bank/summary", headers={}).status_code == 401
    cand = make_account(role="candidate")
    assert client.get("/api/bank/summary", headers=cand.headers).status_code == 403


def test_bank_summary_has_own_rate_limit_and_does_not_eat_bank_limit(client, make_account, pool):
    from app.contact.settings import get_contact_config

    emp = make_account("hr@severny-kod.ru", role="employer")
    per_minute = int(get_contact_config().quotas.rate_limit_per_minute)
    for _ in range(per_minute):
        assert client.get("/api/bank/summary", headers=emp.headers).status_code == 200
    assert client.get("/api/bank/summary", headers=emp.headers).status_code == 429
    assert client.get("/api/bank", headers=emp.headers).status_code == 200  # список банка не пострадал


def test_bank_summary_survey_only_waiting_counts(client, make_account, db_session, pool):
    from datetime import UTC, datetime

    from app.models.candidate import SurveyResponse

    emp = make_account("hr@severny-kod.ru", role="employer")
    before = {x["spec"]: x["survey_only"] for x in client.get("/api/bank/summary", headers=emp.headers).json()["specs"]}
    waiting = [make_account(role="candidate") for _ in range(3)]
    for acc in waiting:
        db_session.add(
            SurveyResponse(
                candidate_id=acc.user_id, step=4, chosen_spec="data_ml", completed_at=datetime.now(UTC), answers={}
            )
        )
    db_session.flush()
    after = {x["spec"]: x["survey_only"] for x in client.get("/api/bank/summary", headers=emp.headers).json()["specs"]}
    assert after["data_ml"] == before["data_ml"] + 3  # «ждут теста»: опрос есть, подтверждённой категории нет


def test_skills_dictionary(client, make_account):
    emp = make_account(role="employer")
    assert client.get("/api/skills").status_code == 401
    items = client.get("/api/skills", headers=emp.headers).json()
    by_id = {s["id"]: s for s in items}
    assert {"python", "fastapi", "postgresql"} <= set(by_id)
    assert by_id["python"]["testable"] is True and by_id["python"]["modules"] and by_id["python"]["name"]
    testable = client.get("/api/skills", params={"testable": True}, headers=emp.headers).json()
    assert testable and all(s["testable"] for s in testable)
    untestable = client.get("/api/skills", params={"testable": False}, headers=emp.headers).json()
    assert all(not s["testable"] for s in untestable) and len(testable) + len(untestable) == len(items)
    backend = client.get("/api/skills", params={"spec": "backend_python"}, headers=emp.headers).json()
    assert backend and all("backend_python" in s["specs"] for s in backend)
    # навыки из словаря принимает фильтр банка
    assert client.get("/api/bank", params={"skill": ["python"]}, headers=emp.headers).status_code == 200
