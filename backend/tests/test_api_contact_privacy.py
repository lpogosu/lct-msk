"""Приватность и вебхуки ATS через HTTP (концепция §4.5, §8.2, DECISIONS 20).

Контакты кандидата не появляются ни в одном ответе работодателю (ни ключом, ни значением) до раскрытия;
вебхук подписан HMAC-SHA256, идемпотентен по event_id и несёт контакты только при действующем согласии.
"""

from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from tests.test_api_contact_support import (
    assert_no_pii,
    dump,
    invite,
    make_candidate,
    make_employer,
    open_card,
)


@pytest.fixture
def sink():
    """Приёмник вебхуков вместо HTTP: запоминает тело и заголовки, отвечает 200 (или заданным кодом)."""
    from app.services.contact import webhooks

    received: list[dict] = []
    state = {"code": 200}

    def transport(url, body, headers, timeout):
        received.append({"url": url, "body": body, "headers": dict(headers)})
        return state["code"], "ok"

    prev = webhooks.set_transport(transport)
    yield received, state
    webhooks.set_transport(prev)


def test_contacts_hidden_until_disclosure_everywhere(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, emp, cand)
    responses = []
    r = invite(client, emp, a.ref)
    responses.append(r.json())
    inv = r.json()["sent"][0]
    responses.append(client.get("/api/invitations", headers=emp.headers).json())
    responses.append(client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json())
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    client.post(
        f"/api/invitations/{inv['id']}/counter",
        json={"salary_from": 250000, "auto_disclose": False, "comment": "Хочу удалёнку позже обсудить"},
        headers=cand.headers,
    )
    responses.append(client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json())
    # Работодатель принял встречное, но кандидат снял галочку автораскрытия — контакты ещё закрыты
    r = client.post(f"/api/invitations/{inv['id']}/counter/accept", headers=emp.headers)
    assert r.status_code == 200 and r.json()["status"] == "accepted" and r.json()["contacts_open"] is False
    responses.append(r.json())
    responses.append(client.get("/api/notifications", headers=emp.headers).json())
    for body in responses:
        assert_no_pii(body, cand)
    # Профиль компании содержит её собственные email/Telegram/ИНН — проверяем только значения кандидата
    company_me = dump(client.get("/api/companies/me", headers=emp.headers).json())
    assert not any(v in company_me for v in cand.pii())
    # Свой id кандидат видит, работодатель — нет; в журнале событий нет id участников
    assert str(cand.user_id) not in dump(responses)
    # Отдельное согласие — и контакты открыты
    r = client.post(f"/api/invitations/{inv['id']}/grant-consent", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200 and r.json()["contacts_open"] is True
    view = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    assert view["candidate"]["email"] == cand.email and view["timers"]["contact_due_text"] == "через 3 дня"
    assert str(cand.user_id) not in dump(view)


def test_webhook_signed_idempotent_and_contacts_only_after_consent(client, make_account, db_session, frozen_clock, sink):
    received, state = sink
    emp = make_employer(client, make_account, db_session)
    r = client.put("/api/companies/me", json={"webhook_url": "https://ats.example.com/hooks/vzaimno"}, headers=emp.headers)
    assert r.status_code == 200
    secret = r.json()["webhook"]["secret"]
    assert secret and r.json()["webhook"]["signature_header"] == "X-Vzaimno-Signature"
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, emp, cand)
    inv = invite(client, emp, a.ref).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    assert [json.loads(x["body"])["type"] for x in received] == ["invitation.sent", "invitation.viewed"]
    for x in received:  # до согласия — ни контактов, ни внутреннего id
        assert "contacts" not in json.loads(x["body"])["data"]
        assert str(cand.user_id) not in x["body"].decode() and cand.email not in x["body"].decode()

    r = client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200
    accepted = received[-1]
    payload = json.loads(accepted["body"])
    assert payload["type"] == "invitation.accepted"
    assert payload["data"]["contacts"]["email"] == cand.email and payload["data"]["pseudonym"] == a.pseudonym
    # Подпись: HMAC-SHA256(секрет компании, тело) в заголовке; event_id — в заголовке и в теле
    expected = "sha256=" + hmac.new(secret.encode(), accepted["body"], hashlib.sha256).hexdigest()
    assert accepted["headers"]["X-Vzaimno-Signature"] == expected
    assert accepted["headers"]["X-Vzaimno-Event-Id"] == payload["event_id"]
    assert accepted["headers"]["X-Vzaimno-Event"] == "invitation.accepted"

    # Идемпотентность: повторная постановка того же события не создаёт новую доставку
    from app.models.company import Company
    from app.services.contact import webhooks

    company = db_session.get(Company, emp.company_id)
    again = webhooks.enqueue(db_session, company, "invitation.accepted", {}, f"{inv['id']}-003")
    assert str(again.event_id) == payload["event_id"]

    # Журнал доставок: последние 20 с кодом ответа
    log = client.get("/api/companies/me/webhooks", headers=emp.headers).json()
    assert log["url"].startswith("https://ats.example.com") and len(log["deliveries"]) == 3
    assert {d["status"] for d in log["deliveries"]} == {"delivered"} and log["deliveries"][0]["response_code"] == 200

    # Отказ приёмника → повтор с задержкой (30 с, 60 с, …), затем доставка
    state["code"] = 503
    client.post(f"/api/invitations/{inv['id']}/contacted", headers=emp.headers)
    log = client.get("/api/companies/me/webhooks", headers=emp.headers).json()
    failed = next(d for d in log["deliveries"] if d["event_type"] == "invitation.mark_contacted")
    assert failed["status"] == "pending" and failed["attempts"] == 1 and failed["response_code"] == 503
    assert failed["next_attempt_text"] is not None
    state["code"] = 200
    from tests.test_api_contact_support import advance

    advance(hours=0.01)  # 36 с > первой задержки 30 с
    log = client.get("/api/companies/me/webhooks", headers=emp.headers).json()
    retried = next(d for d in log["deliveries"] if d["event_type"] == "invitation.mark_contacted")
    assert retried["status"] == "delivered" and retried["attempts"] == 2


def test_webhook_test_ping_and_validation(client, make_account, db_session, frozen_clock, sink):
    received, _ = sink
    emp = make_employer(client, make_account, db_session)
    r = client.post("/api/companies/me/webhooks/test", headers=emp.headers)
    assert r.status_code == 409 and r.json()["code"] == "webhook_not_configured"
    bad = client.put("/api/companies/me", json={"webhook_url": "ftp://ats.example.com"}, headers=emp.headers)
    assert bad.status_code == 400 and bad.json()["code"] == "webhook_url_invalid"
    creds = client.put("/api/companies/me", json={"webhook_url": "https://u:p@ats.example.com/h"}, headers=emp.headers)
    assert creds.status_code == 400
    client.put("/api/companies/me", json={"webhook_url": "https://ats.example.com/h"}, headers=emp.headers)
    r = client.post("/api/companies/me/webhooks/test", headers=emp.headers)
    assert r.status_code == 200 and r.json()["status"] == "delivered" and r.json()["event_type"] == "ping"
    old = client.get("/api/companies/me", headers=emp.headers).json()["webhook"]["secret"]
    new = client.post("/api/companies/me/webhooks/rotate-secret", headers=emp.headers).json()["secret"]
    assert new and new != old
    client.post("/api/companies/me/webhooks/test", headers=emp.headers)
    body = received[-1]["body"]
    assert received[-1]["headers"]["X-Vzaimno-Signature"] == "sha256=" + hmac.new(new.encode(), body, hashlib.sha256).hexdigest()
