"""Профиль компании (Р1) и уведомления через HTTP (концепция §3.2 шаг 2, §4.3; spec-contact §3.7)."""

from __future__ import annotations

import uuid

from tests.test_api_contact_support import COMPANY_PROFILE, invite, make_candidate, make_employer, open_card


def test_profile_completion_validation_and_preview(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session, profile=False)
    r = client.get("/api/companies/me", headers=emp.headers)
    assert r.status_code == 200
    me = r.json()
    assert me["profile_complete"] is False and me["email_domain"] == "severny-kod.ru"
    assert me["missing"] == ["название компании", "направление деятельности", "описание", "хотя бы один способ связи"]
    assert me["quota"]["label"] == "осталось 60/60"
    assert me["reliability"]["display"] == "Надёжность: мало данных" and me["reliability"]["r"] == 0.8

    for body, code in (
        ({"inn": "12345"}, "inn_invalid"),
        ({"contact_methods": {"email": "не-почта"}}, "contact_email_invalid"),
        ({"contact_methods": {"telegram": "@a"}}, "contact_telegram_invalid"),
        ({"contact_methods": {"phone": "12"}}, "contact_phone_invalid"),
        ({"website": "severny-kod.ru"}, "website_invalid"),
    ):
        r = client.put("/api/companies/me", json=body, headers=emp.headers)
        assert r.status_code == 400 and r.json()["code"] == code, (body, r.text)
    r = client.put("/api/companies/me", json={"unknown": 1}, headers=emp.headers)
    assert r.status_code == 422

    r = client.put(
        "/api/companies/me",
        json={**COMPANY_PROFILE, "inn": "7701234567", "contact_methods": {"telegram": "t.me/severny_hr", "phone": "+7 (495) 123-45-67"}},
        headers=emp.headers,
    )
    assert r.status_code == 200, r.text
    me = r.json()
    assert me["profile_complete"] is True and me["missing"] == [] and me["inn"] == "7701234567"
    assert me["contact_methods"] == {"telegram": "@severny_hr", "phone": "+7 (495) 123-45-67"}
    preview = client.get("/api/companies/me/preview", headers=emp.headers).json()
    assert preview["name"] == "ООО «Северный код»" and preview["personal_email"] is False
    assert [m["label"] for m in preview["contact_methods"]] == ["Telegram", "Телефон"]
    # Частичное обновление не трогает остальные поля; очистка способов связи снова закрывает отправку
    r = client.put("/api/companies/me", json={"field": "Финтех"}, headers=emp.headers)
    assert r.json()["name"] == "ООО «Северный код»" and r.json()["profile_complete"] is True
    r = client.put("/api/companies/me", json={"contact_methods": {}}, headers=emp.headers)
    assert r.json()["profile_complete"] is False and r.json()["missing"] == ["хотя бы один способ связи"]


def test_company_endpoints_roles_and_reliability(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    assert client.get("/api/companies/me", headers=cand.headers).status_code == 403
    assert client.get("/api/companies/me").status_code == 401
    r = client.get(f"/api/companies/{emp.company_id}/reliability", headers=cand.headers)
    assert r.status_code == 200 and r.json()["enough_data"] is False and r.json()["breakdown"] == []
    assert client.get(f"/api/companies/{uuid.uuid4()}/reliability", headers=cand.headers).status_code == 404


def test_notifications_list_read_and_counters(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    other = make_candidate(make_account, db_session, n=2)
    inv = invite(client, emp, open_card(db_session, emp, cand).ref).json()["sent"][0]
    r = client.get("/api/notifications/unread", headers=cand.headers)
    assert r.status_code == 200 and r.json() == {"total": 1, "by_kind": {"invitation": 1}}
    lst = client.get("/api/notifications", headers=cand.headers).json()
    n = lst["items"][0]
    assert n["kind_label"] == "Приглашения" and n["entity_type"] == "invitation" and n["entity_id"] == inv["id"]
    assert n["message"].startswith("Новое приглашение:") and n["read"] is False and n["created_text"] == "сейчас"
    # Чужое уведомление — 404 (IDOR)
    assert client.post(f"/api/notifications/{n['id']}/read", headers=other.headers).status_code == 404
    r = client.post(f"/api/notifications/{n['id']}/read", headers=cand.headers)
    assert r.status_code == 200 and r.json()["read"] is True
    assert client.get("/api/notifications/unread", headers=cand.headers).json()["total"] == 0
    # Ответ кандидата → уведомление работодателю; «прочитать все» по виду
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    client.post(f"/api/invitations/{inv['id']}/decline", json={"reason": "not_looking"}, headers=cand.headers)
    unread = client.get("/api/notifications/unread", headers=emp.headers).json()
    assert unread["by_kind"].get("invitation", 0) >= 1
    msg = client.get("/api/notifications?unread_only=true", headers=emp.headers).json()["items"][0]["message"]
    assert msg.startswith("Отказ от К-") and "сейчас не ищу" in msg
    r = client.post("/api/notifications/read-all", json={"kind": "invitation"}, headers=emp.headers)
    assert r.status_code == 200 and r.json()["marked"] >= 1 and r.json()["unread"]["total"] == 0
