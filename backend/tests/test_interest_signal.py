"""Сигнал интереса кандидата (DECISIONS 24): лимит 2 за окно, срок 60 дней, отзыв без возврата слота, отдельное
согласие, видимость работодателю только после открытия карточки (DECISIONS 7), Score не меняется."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contact import interest as rules
from app.contact.settings import get_contact_config
from app.models.users import User
from app.services.contact import interest as svc
from tests.test_api_contact_support import (
    advance,
    make_candidate,
    make_employer,
    make_need,
    open_card,
    shortlist,
)
from tests.test_api_needs_support import seed_demo_pool

CFG = get_contact_config().interest
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def test_config_matches_decision_24():
    assert (CFG.per_window, CFG.window_days, CFG.lifetime_days) == (2, 30, 60)


def test_rules_window_lifetime_and_no_refund():
    created = [T0, T0 + timedelta(days=3)]
    assert rules.remaining(created, T0 + timedelta(days=4), CFG) == 0
    assert rules.next_slot_at(created, T0 + timedelta(days=4), CFG) == T0 + timedelta(days=30)
    assert rules.remaining(created, T0 + timedelta(days=31), CFG) == 1  # первый вышел из окна
    rec = rules.SignalRecord(T0, rules.expires_at(T0, CFG))
    assert rules.is_active(rec, T0 + timedelta(days=59)) and not rules.is_active(rec, T0 + timedelta(days=60))
    revoked = rules.SignalRecord(T0, rec.expires_at, revoked_at=T0 + timedelta(days=1))
    assert not rules.is_active(revoked, T0 + timedelta(days=2))
    # лимит считается по created_at, а не по активности: отзыв слот не возвращает
    assert rules.remaining([T0], T0 + timedelta(days=2), CFG) == 1


@pytest.fixture
def world(client, make_account, db_session, frozen_clock):
    emps = [make_employer(client, make_account, db_session, d) for d in ("severny-kod.ru", "front-studio.ru", "volna.ru")]
    cand = make_candidate(make_account, db_session)
    return emps, cand


def _send(client, cand, **body):
    return client.post("/api/interest", json=body, headers=cand.headers)


def test_send_limit_consent_revoke_and_expiry(client, world):
    emps, cand = world
    st = client.get("/api/interest/me", headers=cand.headers).json()
    assert st["remaining"] == 2 and st["label"] == "осталось 2 из 2" and st["consent_given"] is False

    r = _send(client, cand, company_id=str(emps[0].company_id))
    assert r.status_code == 400 and r.json()["code"] == "consent_required"
    r = _send(client, cand, company_id=str(emps[0].company_id), consent=True)
    assert r.status_code == 201, r.text
    first = r.json()
    assert first["status"]["remaining"] == 1 and first["status"]["consent_given"] is True
    dup = _send(client, cand, company_id=str(emps[0].company_id))
    assert dup.status_code == 409 and dup.json()["code"] == "interest_exists"

    assert _send(client, cand, company_id=str(emps[1].company_id)).status_code == 201  # согласие уже есть
    over = _send(client, cand, company_id=str(emps[2].company_id))
    assert over.status_code == 409 and over.json()["code"] == "interest_limit"

    # отзыв сигнал гасит, слот не возвращает
    sid = first["signal"]["id"]
    st = client.post(f"/api/interest/{sid}/revoke", headers=cand.headers).json()
    assert st["remaining"] == 0 and len(st["active"]) == 1
    assert _send(client, cand, company_id=str(emps[2].company_id)).status_code == 409

    # через 31 день слот есть, второй сигнал ещё жив (60 дней), потом истекает
    advance(days=31)
    st = client.get("/api/interest/me", headers=cand.headers).json()
    assert st["remaining"] == 2 and len(st["active"]) == 1
    advance(days=30)
    assert client.get("/api/interest/me", headers=cand.headers).json()["active"] == []


def test_target_validation_and_roles(client, world):
    emps, cand = world
    assert _send(client, cand, consent=True).status_code == 400
    both = _send(client, cand, company_id=str(emps[0].company_id), vacancy_id=str(emps[0].company_id), consent=True)
    assert both.status_code == 400
    assert client.post("/api/interest", json={"company_id": str(emps[0].company_id)}, headers=emps[0].headers).status_code == 403


def test_revoking_consent_kills_signals(client, world):
    emps, cand = world
    assert _send(client, cand, company_id=str(emps[0].company_id), consent=True).status_code == 201
    body = client.get("/api/consents", headers=cand.headers).json()
    rows = body if isinstance(body, list) else next(v for v in body.values() if isinstance(v, list) and v and "purpose" in v[0])
    row = next(c for c in rows if c.get("purpose") == "interest_signal" and not c.get("revoked_at") and c.get("id"))
    assert client.post(f"/api/consents/{row['id']}/revoke", headers=cand.headers).status_code == 200
    assert client.get("/api/interest/me", headers=cand.headers).json()["active"] == []


def test_employer_sees_signal_only_after_card_opened(client, world, db_session):
    emps, cand = world
    emp, other = emps[0], emps[1]
    assert _send(client, cand, company_id=str(emp.company_id), consent=True).status_code == 201
    # слепой поток цел: карточку не открывали — сигнала нет
    assert svc.visible_for_company(db_session, emp.company_id, [cand.user_id]) == {}
    open_card(db_session, emp, cand)
    got = svc.visible_for_company(db_session, emp.company_id, [cand.user_id])[cand.user_id]
    assert got["text"] == "Кандидат отметил интерес к вашей компании" and got["label"] == "Взаимный интерес"
    # другая компания сигнала не видит, даже открыв карточку
    open_card(db_session, other, cand)
    assert svc.visible_for_company(db_session, other.company_id, [cand.user_id]) == {}
    # срок: через 60 дней бейдж исчезает
    advance(days=61)
    assert svc.visible_for_company(db_session, emp.company_id, [cand.user_id]) == {}


def test_pin_makes_signal_visible(client, world, db_session):
    emps, cand = world
    emp = emps[0]
    assert _send(client, cand, company_id=str(emp.company_id), consent=True).status_code == 201
    assert svc.visible_for_company(db_session, emp.company_id) == {}
    shortlist(db_session, make_need(db_session, emp), cand)  # шортлист = «открыл» (DECISIONS 7)
    assert cand.user_id in svc.visible_for_company(db_session, emp.company_id)


def test_bank_badge_filter_sort_and_score_unchanged(client, db_session, make_account, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    pool = seed_demo_pool(db_session)
    anna = db_session.get(User, pool["anna"])
    svc.send(db_session, anna, company_id=emp.company_id, vacancy_id=None, consent=True)
    db_session.flush()

    def bank(**params):
        r = client.get("/api/bank", params=params, headers=emp.headers)
        assert r.status_code == 200, r.text
        return r.json()["items"]

    base = bank()
    assert all(i["interest"] is None for i in base)  # карточку не открывали — сигнала не видно
    assert bank(interest="true") == []
    seen = None
    for it in base:  # открытие карточки = запись просмотра (DECISIONS 7)
        card = client.get(f"/api/bank/candidates/{it['ref']}", headers=emp.headers).json()
        if card.get("interest"):
            seen = it
            assert card["interest"]["label"] == "Взаимный интерес"
            break
    assert seen is not None
    after = bank()
    badge = [i for i in after if i["interest"]]
    assert len(badge) == 1 and badge[0]["ref"] == seen["ref"]
    # Score и порядок без флагов не меняются: сигнал не входит в Score
    assert [(i["ref"], i["score"]) for i in after] == [(i["ref"], i["score"]) for i in base]
    assert [i["ref"] for i in bank(interest="true")] == [seen["ref"]]
    assert bank(interest_first="true")[0]["ref"] == seen["ref"]


def test_vacancy_card_block_and_send_by_vacancy(client, world, db_session, monkeypatch):
    import app.services.contact.vacancies as vac_svc

    monkeypatch.setattr(vac_svc, "candidate_features", lambda db, uid: [])
    emps, cand = world
    need = make_need(db_session, emps[0])
    r = client.post("/api/vacancies", json={"need_id": str(need.id)}, headers=emps[0].headers)
    assert r.status_code == 201, r.text
    vid = r.json()["vacancy"]["id"]
    before = client.get(f"/api/vacancies/{vid}", headers=cand.headers).json()["interest"]
    assert before["sent"] is False and before["remaining"] == 2 and before["label"] == "осталось 2 из 2"
    assert _send(client, cand, vacancy_id=vid, consent=True).status_code == 201
    after = client.get(f"/api/vacancies/{vid}", headers=cand.headers).json()["interest"]
    assert after["sent"] is True and after["remaining"] == 1 and after["signal_id"]
