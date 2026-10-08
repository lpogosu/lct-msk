"""Жалобы кандидатов, очередь модерации, снижение квоты и автосигналы (docs/TRUST_AND_SAFETY.md)."""

from __future__ import annotations

import copy

import pytest

from app.models.contact import Invitation
from app.models.users import User
from app.trust import flags as trust_flags
from app.trust import verification as trust_verification
from tests.test_api_contact_support import (
    advance,
    invite,
    make_candidate,
    make_employer,
    make_need,
    open_card,
    shortlist,
)

SEVERNY_INN, SEVERNY_OGRN = "7720987658", "1057720987653"


def _moderator(make_account, db_session, role="methodist"):
    acct = make_account(role="candidate")
    db_session.get(User, acct.user_id).role = role
    db_session.flush()
    return acct


def _invite_one(client, make_account, db_session, emp, n):
    cand = make_candidate(make_account, db_session, n=n)
    sent = invite(client, emp, open_card(db_session, emp, cand).ref).json()["sent"][0]
    return cand, sent


def _complain(client, cand, inv_id, reason="collects_resume", comment=None):
    body = {"reason": reason, **({"comment": comment} if comment else {})}
    return client.post(f"/api/invitations/{inv_id}/report", json=body, headers=cand.headers)


def test_new_reasons_available_and_complaint_enters_moderation_queue(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, inv = _invite_one(client, make_account, db_session, emp, 1)
    detail = client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers).json()
    assert {"collects_resume", "salary_mismatch", "no_response"} <= set(detail["complaint_reasons"])
    assert _complain(client, cand, inv["id"], "salary_mismatch", "В письме другая сумма").status_code == 200

    mod = _moderator(make_account, db_session)
    queue = client.get("/api/admin/trust/complaints", headers=mod.headers).json()
    assert len(queue) == 1
    item = queue[0]
    assert item["status"] == "open" and item["reason_label"] == "Зарплата не та, что в приглашении"
    assert item["target_type"] == "invitation" and item["target_id"] == inv["id"] and item["comment"] == "В письме другая сумма"
    assert item["company_name"] == "ООО «Северный код»"
    # очередь закрыта для кандидата и работодателя
    assert client.get("/api/admin/trust/complaints", headers=cand.headers).status_code == 403
    assert client.get("/api/admin/trust/complaints", headers=emp.headers).status_code == 403
    # работодатель о жалобе не уведомляется
    assert not any("жалоб" in n["message"].lower() for n in client.get("/api/notifications", headers=emp.headers).json()["items"])


def test_three_distinct_reporters_cut_quota_and_dismissal_restores_it(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    sent = [_invite_one(client, make_account, db_session, emp, n) for n in range(1, 4)]
    before = client.get("/api/companies/me", headers=emp.headers).json()["quota"]
    assert before["reduced_reason"] is None
    base_limit = before["limit"]

    _complain(client, *[sent[0][0], sent[0][1]["id"]])
    _complain(client, sent[1][0], sent[1][1]["id"])
    assert client.get("/api/companies/me", headers=emp.headers).json()["quota"]["limit"] == base_limit  # порог 3
    _complain(client, sent[2][0], sent[2][1]["id"], "fake_vacancy")
    quota = client.get("/api/companies/me", headers=emp.headers).json()["quota"]
    assert quota["limit"] == base_limit // 2 and quota["reduced_reason"] == "жалобы кандидатов"

    mod = _moderator(make_account, db_session, "admin")
    queue = client.get("/api/admin/trust/complaints", headers=mod.headers).json()
    assert len(queue) == 3 and queue[0]["company_open_reporters"] == 3
    r = client.post(f"/api/admin/trust/complaints/{queue[0]['id']}/resolve", json={"decision": "dismissed", "note": "Недоразумение"}, headers=mod.headers)
    assert r.status_code == 200 and r.json()["status"] == "dismissed"
    # отклонённая жалоба не считается — квота возвращается
    quota = client.get("/api/companies/me", headers=emp.headers).json()["quota"]
    assert quota["limit"] == base_limit and quota["reduced_reason"] is None
    # повторный разбор — 409; пустая очередь по умолчанию показывает только открытые
    again = client.post(f"/api/admin/trust/complaints/{queue[0]['id']}/resolve", json={"decision": "upheld"}, headers=mod.headers)
    assert again.status_code == 409
    assert len(client.get("/api/admin/trust/complaints", headers=mod.headers).json()) == 2
    assert len(client.get("/api/admin/trust/complaints?status=dismissed", headers=mod.headers).json()) == 1


def test_upheld_serious_complaint_revokes_verification_label(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    assert client.post("/api/companies/me/verification", json={"inn": SEVERNY_INN, "ogrn": SEVERNY_OGRN}, headers=emp.headers).json()["level"] == "domain"
    cand, inv = _invite_one(client, make_account, db_session, emp, 1)
    _complain(client, cand, inv["id"], "payment_request")
    mod = _moderator(make_account, db_session)
    cid = client.get("/api/admin/trust/complaints", headers=mod.headers).json()[0]["id"]
    r = client.post(f"/api/admin/trust/complaints/{cid}/resolve", json={"decision": "upheld"}, headers=mod.headers)
    assert r.status_code == 200 and r.json()["status"] == "upheld" and r.json()["verification_level"] == "none"
    mine = client.get("/api/companies/me/verification", headers=emp.headers).json()
    assert mine["revoked"] is True and mine["revoke_reason"] == "Подтверждённые жалобы кандидатов"
    # мягкая причина метку не снимает
    emp2 = make_employer(client, make_account, db_session, domain="gmail.com")
    client.post("/api/companies/me/verification", json={"inn": "7701123451", "ogrn": "1027701123450"}, headers=emp2.headers)
    assert client.get("/api/companies/me/verification", headers=emp2.headers).json()["level"] == "none"  # название не совпало
    cand2, inv2 = _invite_one(client, make_account, db_session, emp2, 2)
    _complain(client, cand2, inv2["id"], "no_response")
    cid2 = client.get("/api/admin/trust/complaints", headers=mod.headers).json()[0]["id"]
    assert client.post(f"/api/admin/trust/complaints/{cid2}/resolve", json={"decision": "upheld"}, headers=mod.headers).status_code == 200


def test_vacancy_complaint(client, make_account, db_session, frozen_clock, monkeypatch):
    emp = make_employer(client, make_account, db_session)
    need = make_need(db_session, emp)
    vac = client.post("/api/vacancies", json={"need_id": str(need.id)}, headers=emp.headers).json()["vacancy"]
    cand = make_candidate(make_account, db_session)
    detail = client.get(f"/api/vacancies/{vac['id']}", headers=cand.headers).json()
    assert detail["reported"] is False and "collects_resume" in detail["complaint_reasons"]
    assert client.post(f"/api/vacancies/{vac['id']}/report", json={"reason": "нет"}, headers=cand.headers).status_code == 400
    r = client.post(f"/api/vacancies/{vac['id']}/report", json={"reason": "collects_resume"}, headers=cand.headers)
    assert r.status_code == 200 and r.json()["reported"] is True
    assert client.post(f"/api/vacancies/{vac['id']}/report", json={"reason": "other"}, headers=cand.headers).status_code == 409
    assert client.post(f"/api/vacancies/{vac['id']}/report", json={"reason": "other"}, headers=emp.headers).status_code == 403
    mod = _moderator(make_account, db_session)
    item = client.get("/api/admin/trust/complaints", headers=mod.headers).json()[0]
    assert item["target_type"] == "vacancy" and item["target_title"] == vac["title"]


def _cfg_with(monkeypatch, **flag_overrides):
    base = trust_verification.cfg()
    patched = copy.deepcopy(base)
    for name, values in flag_overrides.items():
        patched["flags"][name].update(values)
    monkeypatch.setattr(trust_flags, "trust_cfg", lambda: patched)


def test_flag_many_invitations_without_outcomes(client, make_account, db_session, frozen_clock, monkeypatch):
    _cfg_with(monkeypatch, many_invites_no_outcomes={"min_sent": 2})
    emp = make_employer(client, make_account, db_session)
    _invite_one(client, make_account, db_session, emp, 1)
    assert trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now()) == []
    _invite_one(client, make_account, db_session, emp, 2)
    found = trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now())
    assert [f["code"] for f in found] == ["many_invites_no_outcomes"]
    # один принятый — сигнал снимается
    inv = db_session.query(Invitation).filter(Invitation.company_id == emp.company_id).first()
    inv.accepted_at = frozen_clock.now()
    db_session.flush()
    assert trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now()) == []


def test_flag_idle_vacancy(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    need = make_need(db_session, emp)
    assert client.post("/api/vacancies", json={"need_id": str(need.id)}, headers=emp.headers).status_code == 201
    assert trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now()) == []
    advance(days=31)
    found = trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now())
    assert [f["code"] for f in found] == ["vacancy_idle"]
    # админская лента сигналов видит компанию
    mod = _moderator(make_account, db_session)
    flagged = client.get("/api/admin/trust/flags", headers=mod.headers).json()
    assert [c["company_id"] for c in flagged] == [str(emp.company_id)]


def test_flag_salary_below_brief(client, make_account, db_session, frozen_clock, monkeypatch):
    _cfg_with(monkeypatch, salary_below_brief={"min_invitations": 1})
    emp = make_employer(client, make_account, db_session)
    need = make_need(db_session, emp)  # вилка брифа 220–280 тыс. на руки
    cand = make_candidate(make_account, db_session)
    shortlist(db_session, need, cand)
    ref = open_card(db_session, emp, cand).ref
    ok = invite(client, emp, ref, need_id=str(need.id), salary_from=150000, salary_to=200000)
    assert ok.status_code == 201, ok.text
    found = trust_flags.company_flags(db_session, emp.company_id, frozen_clock.now())
    assert "salary_below_brief" in [f["code"] for f in found]


@pytest.mark.parametrize("target", ["invitation", "vacancy"])
def test_complaint_not_visible_to_other_candidate(client, make_account, db_session, frozen_clock, target):
    """Чужую приглашение-жалобу подать нельзя: 404 (защита от IDOR)."""
    emp = make_employer(client, make_account, db_session)
    cand, inv = _invite_one(client, make_account, db_session, emp, 1)
    stranger = make_candidate(make_account, db_session, n=2)
    if target == "invitation":
        r = client.post(f"/api/invitations/{inv['id']}/report", json={"reason": "other"}, headers=stranger.headers)
    else:
        import uuid

        r = client.post(f"/api/vacancies/{uuid.uuid4()}/report", json={"reason": "other"}, headers=stranger.headers)
    assert r.status_code == 404
