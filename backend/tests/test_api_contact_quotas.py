"""Квоты, лимиты, пачка, вилка, антифейк и слабое соответствие через HTTP (концепция §4.3, DECISIONS 6, 8)."""

from __future__ import annotations

import pytest

from tests.test_api_contact_support import (
    INVITE,
    invite,
    make_candidate,
    make_employer,
    make_need,
    open_card,
)


@pytest.fixture
def small_quota(monkeypatch):
    """Квота 2 новых за 7 дней вместо 60 (правило то же, что в config/contact.yaml)."""
    import app.services.contact.common as common
    from app.contact.settings import get_contact_config

    base = get_contact_config()
    patched = base.model_copy(update={"quotas": base.quotas.model_copy(update={"new_per_owner": 2})})
    monkeypatch.setattr(common, "get_contact_config", lambda: patched)
    return patched


def test_company_quota_and_batch(client, make_account, db_session, frozen_clock, small_quota):
    emp = make_employer(client, make_account, db_session)
    cands = [make_candidate(make_account, db_session, n=i) for i in range(3)]
    refs = [open_card(db_session, emp, c).ref for c in cands]
    stranger = make_candidate(make_account, db_session, n=9)
    from app.services.matching.privacy import get_alias

    stranger_ref = get_alias(db_session, emp.company_id, stranger.user_id).ref

    r = client.post(
        "/api/invitations/batch",
        json={**INVITE, "candidates": [*refs, stranger_ref], "why_you": {refs[0]: ["Тест по Python в верхних 10%"]}},
        headers=emp.headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["sent"]) == 2  # квота 2: третьему — понятный отказ
    reasons = {x["ref"]: x["errors"][0]["code"] for x in body["rejected"]}
    assert reasons[refs[2]] == "owner_quota" and reasons[stranger_ref] == "card_not_opened"
    assert "Квота исчерпана: 2 из 2" in next(x for x in body["rejected"] if x["ref"] == refs[2])["errors"][0]["message"]
    assert body["quota"]["remaining"] == 0 and body["quota"]["next_slot_in"] == "через 7 дней"
    detail = client.get(f"/api/invitations/inbox/{body['sent'][0]['id']}", headers=cands[0].headers).json()
    assert detail["why_you"] == ["Тест по Python в верхних 10%"]

    r = client.post("/api/invitations/batch", json={**INVITE, "candidates": [f"К-{i}" for i in range(11)]}, headers=emp.headers)
    assert r.status_code == 400 and r.json()["code"] == "batch_too_large"


def test_candidate_weekly_limit_and_personal_email(client, make_account, db_session, frozen_clock):
    cand = make_candidate(make_account, db_session, weekly_invite_limit=1)
    e1 = make_employer(client, make_account, db_session, "gmail.com")  # личная почта: своя компания и квота
    e2 = make_employer(client, make_account, db_session, "gmail.com")
    assert e1.company_id != e2.company_id
    r = invite(client, e1, open_card(db_session, e1, cand).ref)
    assert r.status_code == 201
    assert any("личный email" in n for n in r.json()["notes"])
    r = invite(client, e2, open_card(db_session, e2, cand).ref)
    assert r.status_code == 409 and "исчерпан лимит приглашений на эту неделю" in r.json()["message"]
    item = client.get("/api/invitations/inbox", headers=cand.headers).json()["items"][0]
    assert item["company"]["personal_email_label"] == "личный email"
    # Квоты двух аккаунтов с gmail независимы (DECISIONS 8)
    q1 = client.get("/api/companies/me", headers=e1.headers).json()["quota"]
    q2 = client.get("/api/companies/me", headers=e2.headers).json()["quota"]
    assert (q1["used"], q2["used"]) == (1, 0)


def test_salary_rules_and_preview(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    ref = open_card(db_session, emp, cand).ref
    r = invite(client, emp, ref, salary_from=100000, salary_to=250000)
    assert r.status_code == 400 and "шире 2,0×" in r.json()["message"]
    r = invite(client, emp, ref, salary_from=20000, salary_to=30000)
    assert r.status_code == 400 and "не меньше 30 000 ₽" in r.json()["message"]
    r = invite(client, emp, ref, description="Коротко")
    assert r.status_code == 400 and "не короче 50 символов" in r.json()["message"]
    # Превью: «так увидит кандидат» + предупреждение о широкой вилке, ничего не отправлено
    r = client.post(
        "/api/invitations/preview",
        json={**INVITE, "salary_from": 200000, "salary_to": 360000, "candidates": [ref]},
        headers=emp.headers,
    )
    assert r.status_code == 200, r.text
    pv = r.json()
    assert pv["company"]["name"] == "ООО «Северный код»" and pv["salary"]["text"] == "200 000–360 000 ₽ на руки"
    check = pv["checks"][0]
    assert check["ok"] is True and check["warnings"][0]["code"] == "salary_wide"
    assert client.get("/api/invitations/inbox", headers=cand.headers).json()["items"] == []
    r = invite(client, emp, ref, salary_from=200000, salary_to=360000)
    assert r.status_code == 201 and any("Широкая вилка" in w for w in r.json()["warnings"])


def test_caution_badge_and_weak_match(client, make_account, db_session, frozen_clock, monkeypatch):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    need = make_need(db_session, emp)
    ref = open_card(db_session, emp, cand).ref
    import app.services.contact.identity as identity

    monkeypatch.setattr(
        identity,
        "score_snapshot",
        lambda db, need_id, cid: {"score": 35.4, "rank": 12, "category": "Backend · Middle", "parts": {}, "why_you": ["Ваш тест подтвердил SQL"]},
    )
    r = invite(
        client,
        emp,
        ref,
        need_id=str(need.id),
        description="Стажировка с обучением. Для оформления пришлите скан паспорта до собеседования, мы всё подготовим.",
    )
    assert r.status_code == 201, r.text
    assert any("Слабое соответствие брифу: 35 из 100" in w for w in r.json()["warnings"])
    sent = r.json()["sent"][0]
    assert "weak_match" in sent["flags"] and "caution" in sent["flags"] and sent["score"] == 35.4
    item = client.get("/api/invitations/inbox", headers=cand.headers).json()["items"][0]
    assert item["caution"] == "Требует осторожности"
    detail = client.get(f"/api/invitations/inbox/{sent['id']}", headers=cand.headers).json()
    assert any("паспорт" in reason for reason in detail["caution_reasons"])
    assert detail["why_you"] == ["Ваш тест подтвердил SQL"]


def test_profile_gate_blocks_invitations(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session, profile=False)
    cand = make_candidate(make_account, db_session)
    ref = open_card(db_session, emp, cand).ref
    r = invite(client, emp, ref)
    assert r.status_code == 409 and r.json()["code"] == "company_profile_incomplete"
    assert "название компании" in r.json()["message"] and "хотя бы один способ связи" in r.json()["message"]


def test_complaint_recorded_once_and_counted(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    inv = invite(client, emp, open_card(db_session, emp, cand).ref).json()["sent"][0]
    detail = client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers).json()
    assert detail["reported"] is False and "fake_vacancy" in detail["complaint_reasons"]
    bad = client.post(f"/api/invitations/{inv['id']}/report", json={"reason": "скучно"}, headers=cand.headers)
    assert bad.status_code == 400 and bad.json()["code"] == "complaint_reason"
    r = client.post(
        f"/api/invitations/{inv['id']}/report",
        json={"reason": "payment_request", "comment": "Просили оплатить обучение"},
        headers=cand.headers,
    )
    assert r.status_code == 200 and r.json()["reported"] is True and r.json()["status"] == "viewed"
    again = client.post(f"/api/invitations/{inv['id']}/report", json={"reason": "other"}, headers=cand.headers)
    assert again.status_code == 409
    assert client.get("/api/companies/me", headers=emp.headers).json()["complaints_90d"] == 1
    # Компания о жалобе не уведомляется
    notes = client.get("/api/notifications", headers=emp.headers).json()["items"]
    assert not any("жалоб" in n["message"].lower() for n in notes)
