"""Вход жюри (DEMO_MODE): одноразовый кандидат, экспресс-тест, регистрация без письма, роли без пароля.

Экспресс — тот же движок и банк, но короче; категорию не присваивает; при недостатке уверенности честно говорит
«нужен полный тест»; сессия demo_assisted и в калибровку/метрики не входит (DECISIONS п. 4, 29)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from app.core.settings import Settings
from app.models.testing import TestSession as SessionRow
from app.services.demo import models as _demo_models  # noqa: F401 — регистрирует таблицы демо
from app.services.testing.keystore import decrypt_key
from tests import test_api_testing_support as support

api = support.api
bank = support.bank

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def demo_app(mailer, db_session) -> Iterator:
    from app.db.session import get_db
    from app.main import create_app

    settings = Settings(
        _env_file=None, env="test", demo_mode=True, argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1,
        mail_backend="console",
    )
    application = create_app(settings, mailer)
    application.state.demo_loop = False
    application.dependency_overrides[get_db] = lambda: db_session
    yield application


@pytest.fixture
def demo_client(demo_app):
    from fastapi.testclient import TestClient

    with TestClient(demo_app) as c:
        yield c


class Guest:
    def __init__(self, body: dict) -> None:
        self.body = body
        self.headers = {"Authorization": f"Bearer {body['access_token']}"}
        self.session_id = body["session_id"]


def start_guest(client, mode: str = "express", **kw) -> Guest:
    r = client.post("/api/demo/jury/start", json={"mode": mode, **kw})
    assert r.status_code == 201, r.text
    return Guest(r.json())


def play(client, db, g: Guest, correct) -> int:
    """Пройти сессию: correct(n) — верно ли отвечаем на n-е задание. Возвращает число отвеченных заданий."""
    n = 0
    for _ in range(14):
        nxt = client.get(f"/api/tests/sessions/{g.session_id}/next", headers=g.headers).json()
        if nxt["item"] is None:
            break
        inst = support.instance_for(db, g.session_id, nxt["item"]["seq"])
        key = decrypt_key(inst.key_enc)
        n += 1
        ans = key if correct(n) else support.wrong_answer(key, nxt["item"])
        client.post(
            f"/api/tests/sessions/{g.session_id}/answer",
            headers=g.headers,
            json={"seq": nxt["item"]["seq"], "answer": ans},
        )
    return n


def test_one_click_express_start_logs_in_and_marks_session(demo_client, bank, db_session):
    g = start_guest(demo_client)
    assert g.body["path"] == f"/candidate/test/{g.session_id}" and g.body["user"]["role"] == "candidate"
    assert "password" not in str(g.body).lower()
    s = demo_client.get(f"/api/tests/sessions/{g.session_id}", headers=g.headers).json()
    assert s["express"] is True and s["express_items_max"] == 8 and s["express_deadline_at"]
    assert "экспресс" in s["progress"]["text"]
    row = db_session.get(SessionRow, uuid.UUID(g.session_id))
    assert row.demo_assisted is True  # вне калибровки, экспозиции и метрик


def test_express_stops_at_cap_and_never_grants_category(demo_client, bank, db_session):
    g = start_guest(demo_client)
    # Кандидат «у порога» (верно через раз): уверенности за ≤ 8 заданий не набрать
    n = play(demo_client, db_session, g, lambda k: k % 2 == 1)
    assert 6 <= n <= 8
    res = demo_client.get(f"/api/tests/sessions/{g.session_id}/result", headers=g.headers).json()
    assert res["visible"] and res["express"] is not None
    ex = res["express"]
    assert ex["verdict"] == "inconclusive" and "нужен полный тест" in ex["headline"]
    assert res["confirmed_grade"] is None and res["category"] is None and res["offers"] == []
    assert res["blind_card_preview"] is None and ex["interval_width"] > 0 and ex["full_test"]
    # Попытка не сожжена: полный тест доступен сразу, без «через 14 дней»
    brief = demo_client.get("/api/tests/briefing?specialization=backend_python", headers=g.headers).json()
    assert brief["can_start"]["ok"], brief["can_start"]


def test_express_confident_result_is_estimate_not_category(demo_client, bank, db_session):
    g = start_guest(demo_client, grade="junior")
    play(demo_client, db_session, g, lambda k: True)
    res = demo_client.get(f"/api/tests/sessions/{g.session_id}/result", headers=g.headers).json()
    ex = res["express"]
    assert ex["verdict"] == "estimate_pass" and "или выше" in ex["headline"]
    assert res["confirmed_grade"] is None and res["category"] is None
    assert ex["n_items"] <= 8 and ex["stop_reason"] in ("confidence", "max_items")
    q = demo_client.get("/api/tests/qualification", headers=g.headers).json()
    assert all(x["status"] != "CONFIRMED" for x in q)


def test_express_time_budget_decides_on_given_answers(demo_client, bank, db_session, frozen_clock):
    g = start_guest(demo_client)
    n = 0
    for _ in range(4):
        nxt = demo_client.get(f"/api/tests/sessions/{g.session_id}/next", headers=g.headers).json()
        inst = support.instance_for(db_session, g.session_id, nxt["item"]["seq"])
        n += 1
        ans = decrypt_key(inst.key_enc) if n % 2 else support.wrong_answer(decrypt_key(inst.key_enc), nxt["item"])
        demo_client.post(
            f"/api/tests/sessions/{g.session_id}/answer",
            headers=g.headers,
            json={"seq": nxt["item"]["seq"], "answer": ans},
        )
    frozen_clock.advance(241)
    nxt = demo_client.get(f"/api/tests/sessions/{g.session_id}/next", headers=g.headers).json()
    assert nxt["item"] is None  # бюджет вышел, заданий уже достаточно — решение по данным ответам
    res = demo_client.get(f"/api/tests/sessions/{g.session_id}/result", headers=g.headers).json()
    assert res["express"]["stop_reason"] == "time_budget" and res["express"]["verdict"] == "inconclusive"


def test_express_unavailable_without_demo_mode(client, make_account, bank):
    acc = make_account()
    r = client.post(
        "/api/tests/sessions",
        headers=acc.headers,
        json={"specialization": "backend_python", "grade": "middle", "express": True, "consent_test_logging": True},
    )
    assert r.status_code == 404
    assert client.post("/api/demo/jury/start", json={"mode": "express"}).status_code == 404


def test_full_mode_guest_runs_regular_test(demo_client, bank, db_session):
    g = start_guest(demo_client, mode="full")
    s = demo_client.get(f"/api/tests/sessions/{g.session_id}", headers=g.headers).json()
    assert s["express"] is False and "от 8 до 20" in s["progress"]["text"]


def test_jury_paths_and_missing_personas(demo_client, bank):
    p = demo_client.get("/api/demo/jury/paths").json()
    assert p["express_items_max"] == 8 and p["express_minutes"] == 4
    assert any(x["code"] == "backend_python" for x in p["specializations"])
    # демо-мир в тесте не собран: роли недоступны, и это сказано честно (а не 500)
    assert p["employer_available"] is False
    assert p["invited_available"] is False
    r = demo_client.post("/api/demo/jury/persona", json={"persona": "employer"})
    assert r.status_code == 404 and r.json()["code"] == "persona_not_seeded"
    r = demo_client.post("/api/demo/jury/persona", json={"persona": "candidate_invited"})
    assert r.status_code == 404 and r.json()["code"] == "persona_not_seeded"


def test_persona_login_without_password(demo_client, db_session, test_settings):
    from app.services.demo import accounts
    from app.services.demo.cfg import section

    em = section("companies")[0]["hr_email"]
    accounts.ensure_user(db_session, test_settings, email=em, role="employer", password="Vz-whatever1")
    r = demo_client.post("/api/demo/jury/persona", json={"persona": "employer"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user"]["email"] == em and body["path"] == "/employer/needs" and "password" not in str(body).lower()


def test_candidate_with_invitation_persona_login(demo_client, db_session, test_settings):
    from app.services.demo import accounts
    from app.services.demo.cfg import section

    key = section("jury_entry")["invited_candidate"]
    em = next(x["email"] for x in section("staged") if x["key"] == key)
    accounts.ensure_user(db_session, test_settings, email=em, role="candidate", password="Vz-whatever1")
    assert demo_client.get("/api/demo/jury/paths").json()["invited_available"] is True
    r = demo_client.post("/api/demo/jury/persona", json={"persona": "candidate_invited"})
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == em and r.json()["path"] == "/candidate/invitations"


def test_register_without_email_code_in_demo_mode(demo_client):
    r = demo_client.post(
        "/api/auth/register",
        json={"email": "novy@example.com", "password": "Parol-2026", "role": "candidate", "consent_personal_data": True},
    )
    assert r.status_code == 201 and r.json()["status"] == "active" and r.json()["code_sent"] is False
    login = demo_client.post("/api/auth/login", json={"email": "novy@example.com", "password": "Parol-2026"})
    assert login.status_code == 200 and login.json()["user"]["email_verified"] is True


def test_register_requires_code_outside_demo_mode(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "prod@example.com", "password": "Parol-2026", "role": "candidate", "consent_personal_data": True},
    )
    assert r.status_code == 201 and r.json()["status"] == "pending_email" and r.json()["code_sent"] is True
    assert client.post("/api/auth/login", json={"email": "prod@example.com", "password": "Parol-2026"}).status_code == 403
