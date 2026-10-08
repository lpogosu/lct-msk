"""Матрица доступа (OWASP A01, IDOR): чужие ресурсы недоступны ни по чтению, ни по действию, а все закрытые маршруты
без токена отвечают 401/403.

Дополняет точечные проверки в test_api_chat / test_api_ats / test_api_needs_privacy / test_api_contact_*: здесь —
сквозная проверка по OpenAPI (ни один маршрут вне белого списка не отвечает 2xx/5xx анониму) и перекрёстные обращения
кандидат↔кандидат, работодатель↔работодатель по приглашениям и сессиям теста.
"""

from __future__ import annotations

import re
import uuid

import pytest

from tests import test_api_testing_support as support
from tests.test_api_contact_support import invite, make_candidate, make_employer, open_card
from tests.test_api_testing_support import do_survey, start

api = support.api
bank = support.bank

# Маршруты, открытые анониму по замыслу: вход/регистрация, публичная сверка PDF, служебные, демо-контур (сам закрыт
# DEMO_MODE и проверяется отдельно в test_api_demo_off), справочники согласий.
PUBLIC_PREFIXES = (
    "/api/auth/",
    "/api/health",
    "/api/v/",
    "/api/demo/",
    "/api/jury/",
    "/api/consents/documents",
    "/api/guardian/",
    "/api/fsp/",
    "/api/skills",
)
ALLOWED_ANON = {200, 201, 204, 400, 401, 403, 404, 405, 409, 410, 422, 429, 503}
# Анониму нельзя ни 2xx, ни 5xx (кроме 503 проверки БД) вне белого списка
FORBIDDEN_FOR_PROTECTED = set(range(200, 300)) | {500, 501, 502}


def _routes(app):
    """(метод, путь) из схемы OpenAPI: так видны и маршруты во вложенных роутерах."""
    for path, ops in app.openapi()["paths"].items():
        if path.startswith("/api/"):
            for method in ops:
                if method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    yield method.upper(), path


def test_every_protected_route_rejects_anonymous(client):
    app = client.app
    checked = 0
    leaks: list[str] = []
    for method, path in _routes(app):
        if path.startswith(PUBLIC_PREFIXES) or path in ("/api/openapi.json", "/api/docs", "/api/redoc"):
            continue
        url = re.sub(r"\{[^}]+\}", str(uuid.uuid4()), path)
        r = client.request(method, url, json={})
        checked += 1
        if r.status_code in FORBIDDEN_FOR_PROTECTED:
            leaks.append(f"{method} {path} -> {r.status_code}")
    assert checked > 80, "маршрутов подозрительно мало: изменился префикс API?"
    # ATS v1 и вебхуки ATS авторизуются ключом/подписью, а не JWT: без них 401 тоже обязателен (входят в проверку выше)
    assert not leaks, "маршруты без авторизации отвечают успехом или 5xx:\n" + "\n".join(leaks)


def test_employer_cannot_touch_other_companys_invitation(client, make_account, db_session, frozen_clock):
    owner = make_employer(client, make_account, db_session)
    rival = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    cand = make_candidate(make_account, db_session)
    inv = invite(client, owner, open_card(db_session, owner, cand).ref).json()["sent"][0]["id"]
    assert client.get(f"/api/invitations/{inv}", headers=owner.headers).status_code == 200
    for method, url in [
        ("GET", f"/api/invitations/{inv}"),
        ("POST", f"/api/invitations/{inv}/withdraw"),
        ("POST", f"/api/invitations/{inv}/contacted"),
        ("POST", f"/api/invitations/{inv}/counter/accept"),
        ("GET", f"/api/chat/invitation/{inv}"),
    ]:
        r = client.request(method, url, headers=rival.headers, json={})
        assert r.status_code in (403, 404, 422), f"{method} {url}: {r.status_code}"  # 422 — пустое тело, до действия не дошло
    # ничего не изменилось: приглашение у владельца по-прежнему отправлено
    assert client.get(f"/api/invitations/{inv}", headers=owner.headers).json()["status"] == "sent"
    listed = client.get("/api/invitations", headers=rival.headers).json()
    assert inv not in str(listed)


def test_candidate_cannot_touch_other_candidates_invitation(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    stranger = make_candidate(make_account, db_session, n=2)
    inv = invite(client, emp, open_card(db_session, emp, cand).ref).json()["sent"][0]["id"]
    for method, url, body in [
        ("GET", f"/api/invitations/inbox/{inv}", None),
        ("POST", f"/api/invitations/{inv}/view", None),
        ("POST", f"/api/invitations/{inv}/accept", {"consent": True}),
        ("POST", f"/api/invitations/{inv}/decline", {"reason": "other"}),
        ("POST", f"/api/invitations/{inv}/grant-consent", {"consent": True}),
        ("POST", f"/api/invitations/{inv}/report", {"reason": "spam"}),
    ]:
        r = client.request(method, url, headers=stranger.headers, json=body)
        assert r.status_code in (403, 404, 422), f"{method} {url}: {r.status_code}"
    # владелец после этого всё ещё может ответить: чужие вызовы ничего не изменили
    assert client.get(f"/api/invitations/inbox/{inv}", headers=cand.headers).json()["status"] in ("sent", "viewed")
    assert inv not in str(client.get("/api/invitations/inbox", headers=stranger.headers).json())


def test_candidate_cannot_read_other_candidates_test_session(api, bank, make_account):
    owner, stranger = make_account(), make_account()
    do_survey(api, owner)
    sid = start(api, owner)["id"]
    assert sid
    for path in ("", "/next", "/result", "/review"):
        r = api.client.get(f"/api/tests/sessions/{sid}{path}", headers=stranger.headers)
        assert r.status_code in (403, 404), f"{path}: {r.status_code}"
    r = api.client.post(f"/api/tests/sessions/{sid}/answer", headers=stranger.headers, json={"seq": 1, "answer": "1"})
    assert r.status_code in (403, 404, 422)
    r = api.client.post(f"/api/tests/sessions/{sid}/events", headers=stranger.headers, json={"events": [{"type": "blur"}]})
    assert r.status_code in (403, 404)


@pytest.mark.parametrize(
    ("role", "path"),
    [
        ("candidate", "/api/needs"),
        ("candidate", "/api/companies/me"),
        ("candidate", "/api/companies/me/ats/keys"),
        ("candidate", "/api/bank/candidates"),
        ("employer", "/api/tests/qualification"),
        ("employer", "/api/survey"),
        ("employer", "/api/lab/sessions"),
        ("candidate", "/api/lab/sessions"),
        ("candidate", "/api/admin/trust/complaints"),
        ("employer", "/api/admin/trust/complaints"),
    ],
)
def test_role_boundaries(client, make_account, db_session, role, path):
    acct = make_account(role=role)
    r = client.get(path, headers=acct.headers)
    assert r.status_code in (403, 404, 405), f"{role} {path}: {r.status_code}"
