"""DEMO_MODE: часы «+1 день / +3 дня / сброс» двигают сроки приглашений, список аккаунтов без паролей, режим жюри
(подсказка + demo_assisted), вебхук-приёмник, ручной ответ автоответчика."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import timedelta

import pytest

from app.core.clock import clock
from app.core.settings import Settings
from app.models.candidate import CandidateProfile
from app.models.system import DemoClock
from app.models.testing import TestSession
from app.models.users import User
from app.services.contact import webhooks
from app.services.demo import accounts, sink
from app.services.demo import models as _demo_models  # noqa: F401
from tests import test_api_testing_support as support
from tests.test_api_contact_support import invite, make_candidate, make_employer, open_card

api = support.api
bank = support.bank


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None, env="test", demo_mode=True, argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1,
        mail_backend="console",
    )


@pytest.fixture
def app(test_settings, mailer, db_session):
    from app.db.session import get_db
    from app.main import create_app

    application = create_app(test_settings, mailer)
    application.state.demo_loop = False  # фоновый цикл в тестах не нужен: проходы вызываются явно

    def _override() -> Iterator:
        yield db_session

    application.dependency_overrides[get_db] = _override
    return application


@pytest.fixture
def sink_transport():
    previous = sink.install()
    yield
    sink.uninstall(previous)


pytestmark = pytest.mark.usefixtures("frozen_clock")


def test_state_and_accounts_are_public_and_never_contain_passwords(client):
    r = client.get("/api/demo/state")
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["demo_mode"] is True and st["jury_mode"] is False and st["clock"]["shifted"] is False
    assert {p["code"] for p in st["presets"]} == {"plus_1d", "plus_3d", "reset"}

    r = client.get("/api/demo/accounts")
    assert r.status_code == 200, r.text
    body = r.json()
    roles = {a["role"] for a in body["accounts"]}
    assert roles == {"candidate", "employer", "methodist", "admin"}
    keys = {a["key"] for a in body["accounts"]}
    assert {"anna", "ilya", "registered", "survey", "midtest", "category", "jury", "methodist", "admin"} <= keys
    assert any(a["email"].startswith("hr@severny-kod") for a in body["accounts"])
    text = r.text.lower()
    assert "password" not in text and "vz-" not in text  # пароли — только в файле на сервере
    assert body["registration_email"] == "maria@demo.example.com"
    assert "demo_accounts.md" in body["hint"]


def test_passwords_are_derived_from_secret_and_stable(test_settings):
    a = accounts.password_for(test_settings, "persona:anna")
    assert a == accounts.password_for(test_settings, "persona:anna")
    assert a != accounts.password_for(test_settings, "persona:ilya")
    assert any(c.isdigit() for c in a) and any(c.isalpha() for c in a) and len(a) >= 8
    other = test_settings.model_copy(update={"app_secret": type(test_settings.app_secret)("другой-секрет-для-проверки-1234567890")})
    assert accounts.password_for(other, "persona:anna") != a


def test_clock_requires_login(client):
    assert client.post("/api/demo/clock", json={"preset": "plus_1d"}).status_code == 401
    assert client.post("/api/demo/reset").status_code == 401
    assert client.post("/api/demo/jury-mode", json={"enabled": True}).status_code == 401


def test_clock_shift_moves_invitation_deadlines_and_resets(client, make_account, db_session):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session, salary_min=230_000)
    alias = open_card(db_session, emp, cand)
    r = invite(client, emp, alias.ref)
    assert r.status_code == 201, r.text
    inv_id = r.json()["sent"][0]["id"]
    before = client.get(f"/api/invitations/{inv_id}", headers=emp.headers).json()
    assert before["status"] == "sent" and before["timers"]["respond_by_text"] == "через 5 дней"

    r = client.post("/api/demo/clock", json={"preset": "plus_3d"}, headers=emp.headers)
    assert r.status_code == 200, r.text
    assert r.json()["offset_seconds"] == 3 * 86400 and r.json()["shifted"] is True
    mid = client.get(f"/api/invitations/{inv_id}", headers=emp.headers).json()
    assert mid["status"] == "sent" and mid["timers"]["respond_by_text"] == "через 2 дня"  # срок ответа приблизился
    assert db_session.get(DemoClock, 1).offset_seconds == 3 * 86400  # переживёт перезапуск

    r = client.post("/api/demo/clock", json={"preset": "plus_3d"}, headers=emp.headers)
    assert r.json()["offset_seconds"] == 6 * 86400
    after = client.get(f"/api/invitations/{inv_id}", headers=emp.headers).json()
    assert after["status"] == "expired"  # 5 дней на ответ прошли

    r = client.post("/api/demo/clock", json={"preset": "reset"}, headers=emp.headers)
    assert r.json()["offset_seconds"] == 0 and r.json()["shifted"] is False
    assert db_session.get(DemoClock, 1).offset_seconds == 0
    assert clock.offset_seconds == 0

    one = client.post("/api/demo/clock", json={"preset": "plus_1d"}, headers=emp.headers).json()
    assert one["offset_seconds"] == 86400
    assert client.post("/api/demo/clock", json={"preset": "plus_9d"}, headers=emp.headers).status_code == 422


def test_autoresponder_endpoint_answers_now_and_toggles(client, make_account, db_session):
    from app.services.demo.state import get_state

    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session, salary_min=230_000)
    accounts.mark(db_session, db_session.get(User, cand.user_id), "synthetic", "c9998", decline_reason="stack")
    get_state(db_session).scenario_built_at = clock.now() - timedelta(hours=1)
    alias = open_card(db_session, emp, cand)
    r = invite(client, emp, alias.ref)
    inv_id = r.json()["sent"][0]["id"]
    r = client.post("/api/demo/autoresponder", json={"respond_now": True}, headers=emp.headers)
    assert r.status_code == 200, r.text
    assert r.json()["answered"] == 1 and r.json()["accepted"] == 1
    assert client.get(f"/api/invitations/{inv_id}", headers=emp.headers).json()["status"] == "accepted"
    off = client.post("/api/demo/autoresponder", json={"enabled": False}, headers=emp.headers).json()
    assert off["enabled"] is False
    assert client.get("/api/demo/state").json()["autoresponder"] is False


def test_jury_mode_hint_marks_session_demo_assisted(client, api, bank, make_account, db_session):  # noqa: ARG001
    acc = make_account()
    support.do_survey(api, acc, months=40)
    s = support.start(api, acc, "middle")
    nxt = api.get(f"/api/tests/sessions/{s['id']}/next", acc)
    item = nxt["item"]

    # режим выключен — подсказки нет, ключи наружу не выходят
    r = client.get("/api/demo/jury/hint", params={"session_id": s["id"]}, headers=acc.headers)
    assert r.status_code == 409 and r.json()["code"] == "jury_mode_off"

    r = client.post("/api/demo/jury-mode", json={"enabled": True}, headers=acc.headers)
    assert r.status_code == 200 and r.json()["jury_mode"] is True
    row = db_session.get(TestSession, uuid.UUID(s["id"]))
    db_session.refresh(row)
    assert row.demo_assisted is True  # сессия, начатая в режиме жюри, помечена сразу

    r = client.get("/api/demo/jury/hint", params={"session_id": s["id"]}, headers=acc.headers)
    assert r.status_code == 200, r.text
    hint = r.json()
    assert hint["seq"] == item["seq"] and hint["demo_assisted"] is True and hint["answer"] is not None
    res = api.post(f"/api/tests/sessions/{s['id']}/answer", acc, {"seq": item["seq"], "answer": hint["answer"]})
    assert res["finished"] is False  # ответ принят в формате задания (подсказка — ключ, сохранённый на сервере)

    # чужая сессия неотличима от несуществующей
    other = make_account()
    r = client.get("/api/demo/jury/hint", params={"session_id": s["id"]}, headers=other.headers)
    assert r.status_code == 404
    off = client.post("/api/demo/jury-mode", json={"enabled": False}, headers=acc.headers).json()
    assert off["jury_mode"] is False


def test_webhook_sink_shows_platform_deliveries_and_direct_posts(client, make_account, db_session, sink_transport):
    emp = make_employer(client, make_account, db_session)
    host = sink.sink_host()
    r = client.put("/api/companies/me", json={"webhook_url": f"https://{host}/api/demo/webhook-sink"}, headers=emp.headers)
    assert r.status_code == 200, r.text
    secret = r.json()["webhook"]["secret"]
    assert secret

    r = client.post("/api/companies/me/webhooks/test", headers=emp.headers)
    assert r.status_code == 200 and r.json()["status"] == "delivered" and r.json()["response_code"] == 200

    lst = client.get("/api/demo/webhook-sink").json()
    assert lst["host"] == host and len(lst["items"]) == 1
    ev = lst["items"][0]
    assert ev["event_type"] == "ping" and ev["source"] == "platform" and ev["signature_valid"] is True
    assert ev["signature"].startswith("sha256=") and ev["payload"]["type"] == "ping"

    # прямой POST внешней ATS: подпись проверяется ключом компании по известному event_id
    body = webhooks.body_bytes(ev["payload"])
    wc = webhooks.cfg().webhooks
    headers = {wc.event_header: "ping", wc.event_id_header: ev["event_id"], wc.signature_header: webhooks.sign(secret, body)}
    r = client.post("/api/demo/webhook-sink", content=body, headers=headers)
    assert r.status_code == 200 and r.json() == {"ok": True, "event_id": ev["event_id"], "signature_valid": True}
    bad = dict(headers, **{wc.signature_header: "sha256=" + "0" * 64})
    r = client.post("/api/demo/webhook-sink", content=body, headers=bad)
    assert r.json()["signature_valid"] is False
    items = client.get("/api/demo/webhook-sink").json()["items"]
    assert [i["source"] for i in items].count("direct") == 2 and len(items) <= 20


def test_sink_keeps_only_last_20(client):
    for i in range(25):
        client.post("/api/demo/webhook-sink", content=b'{"type":"x","n":%d}' % i)
    items = client.get("/api/demo/webhook-sink").json()["items"]
    assert len(items) == 20 and items[0]["payload"]["n"] == 24
    sink.clear()
    assert client.get("/api/demo/webhook-sink").json()["items"] == []


def test_sink_transport_passes_other_hosts_through():
    seen = []
    previous = lambda url, body, headers, timeout: (seen.append(url) or (204, "x"))  # noqa: E731
    t = sink.make_transport(previous)
    assert t(f"https://{sink.sink_host()}/api/demo/webhook-sink", b"{}", {}, 1.0) == (200, "ok")
    assert t("https://ats.partner.example/hook", b"{}", {}, 1.0) == (204, "x") and seen == ["https://ats.partner.example/hook"]


def test_synthetic_refs_label_only_demo_candidates(client, make_account, db_session):
    emp = make_employer(client, make_account, db_session)
    fake = make_candidate(make_account, db_session, salary_min=200_000)
    human = make_candidate(make_account, db_session, salary_min=200_000)
    accounts.mark(db_session, db_session.get(User, fake.user_id), "synthetic", "c0042", decline_reason="stack")
    a_fake, a_human = open_card(db_session, emp, fake), open_card(db_session, emp, human)
    r = client.get("/api/demo/synthetic", headers=emp.headers)
    assert r.status_code == 200, r.text
    assert r.json()["refs"] == [a_fake.ref] and a_human.ref not in r.json()["refs"]
    assert "Синтетический" in r.json()["label"]
    cand_only = make_account()
    assert client.get("/api/demo/synthetic", headers=cand_only.headers).status_code == 403


def test_persona_profile_flags_visible_in_state(client, db_session):
    cand_user = User(email=f"c-{uuid.uuid4().hex[:6]}@cand.example.org", email_domain="cand.example.org", role="candidate",
                     status="active", email_verified=True)
    db_session.add(cand_user)
    db_session.flush()
    db_session.add(CandidateProfile(user_id=cand_user.id))
    accounts.mark(db_session, cand_user, "synthetic", "c0001", decline_reason="stack")
    assert client.get("/api/demo/state").json()["synthetic_candidates"] == 1


def test_lifespan_installs_sink_and_runs_demo_loop_only_in_demo_mode(test_settings, mailer, db_session, monkeypatch):
    """DEMO_MODE: при старте приложения подключается приёмник вебхуков и запускается фоновый цикл автоответчика, при
    остановке транспорт возвращается. Выключенный режим ничего не запускает."""
    import threading

    from fastapi.testclient import TestClient

    from app.main import create_app
    from app.services.demo import loop

    started = threading.Event()

    async def fake_run(app, factory):  # noqa: ARG001
        started.set()
        import asyncio

        await asyncio.sleep(3600)

    monkeypatch.setattr(loop, "run", fake_run)
    before = webhooks.set_transport(None)
    webhooks.set_transport(before)

    app = create_app(test_settings, mailer)
    app.state.demo_session_factory = lambda: db_session
    with TestClient(app):
        assert started.wait(2), "демо-цикл не запущен"
        assert webhooks._transport is not before  # noqa: SLF001 — приёмник подключён
    assert webhooks._transport is before  # noqa: SLF001 — при остановке прежний транспорт возвращён

    started.clear()
    off = create_app(test_settings.model_copy(update={"demo_mode": False}), mailer)
    with TestClient(off):
        assert not started.wait(0.3)
        assert webhooks._transport is before  # noqa: SLF001
