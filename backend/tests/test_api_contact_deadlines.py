"""Сроки и отзывы через HTTP: истечение по замороженным часам, отзыв приглашения, отзыв согласия (оба пути),
просрочка первого контакта и «со мной не связались» (концепция §4.3, DECISIONS 19, 22)."""

from __future__ import annotations

from tests.test_api_contact_support import (
    advance,
    assert_no_pii,
    invite,
    make_candidate,
    make_employer,
    open_card,
    reliability_kinds,
)


def test_expiry_returns_quota_slot_and_sets_pause(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, emp, cand)
    r = invite(client, emp, a.ref, response_days=3)
    assert r.status_code == 201, r.text
    inv = r.json()["sent"][0]
    assert inv["timers"]["respond_by_text"] == "через 3 дня"
    assert client.get("/api/companies/me", headers=emp.headers).json()["quota"]["used"] == 1

    advance(days=3, hours=1)  # DECISIONS 19: сроки — по часам платформы, применяются при чтении
    r = client.get("/api/invitations/inbox", headers=cand.headers)
    item = r.json()["items"][0]
    assert item["status"] == "expired" and item["status_label"] == "Истекло"
    # Ответить на истёкшее нельзя; карточка открывается, но не «просматривается»
    r = client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    assert r.status_code == 200 and r.json()["status"] == "expired" and r.json()["actions"] == []
    r = client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 409 and "Истекло" in r.json()["message"]
    # Работодатель: событие истечения датировано самим сроком; слот квоты возвращён (не просмотрено)
    detail = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    expire = next(e for e in detail["events"] if e["to_status"] == "expired")
    assert expire["actor_role"] == "system" and expire["payload"]["quota_slot_returned"] is True
    assert expire["at"].startswith("2026-10-08T09:00")
    assert client.get("/api/companies/me", headers=emp.headers).json()["quota"]["used"] == 0
    # Пауза 14 дней после expired
    again = invite(client, emp, a.ref)
    assert again.status_code == 409 and "Пауза после истёкшего приглашения" in again.json()["message"]
    advance(days=14)
    assert invite(client, emp, a.ref).status_code == 201
    notes = client.get("/api/notifications?kind=invitation", headers=cand.headers).json()["items"]
    assert any("истекло" in n["message"] for n in notes)


def test_viewed_expiry_keeps_slot_and_counter_expiry_hits_reliability(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    c1 = make_candidate(make_account, db_session)
    c2 = make_candidate(make_account, db_session, n=2)
    a1, a2 = open_card(db_session, emp, c1), open_card(db_session, emp, c2)
    i1 = invite(client, emp, a1.ref, response_days=3).json()["sent"][0]
    i2 = invite(client, emp, a2.ref, response_days=7).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{i1['id']}", headers=c1.headers)  # просмотрено → слот не вернётся
    client.get(f"/api/invitations/inbox/{i2['id']}", headers=c2.headers)
    r = client.post(f"/api/invitations/{i2['id']}/counter", json={"salary_from": 280000}, headers=c2.headers)
    assert r.status_code == 200 and r.json()["counter"]["within_range"] is True
    advance(days=3, hours=2)  # и срок кандидата, и 3 дня на встречное — прошли
    kb = client.get("/api/invitations", headers=emp.headers).json()
    expired = next(col for col in kb["columns"] if col["status"] == "expired")
    assert expired["count"] == 2
    assert kb["quota"]["used"] == 2  # просмотренное истёкшее слот не возвращает
    assert reliability_kinds(db_session, emp.company_id) == ["counter_unanswered"]


def test_withdraw_sets_pause_decisions_22(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    a = open_card(db_session, emp, cand)
    inv = invite(client, emp, a.ref).json()["sent"][0]
    bad = client.post(f"/api/invitations/{inv['id']}/withdraw", json={"reason": "просто так"}, headers=emp.headers)
    assert bad.status_code == 400 and "Укажите причину отзыва" in bad.json()["message"]
    r = client.post(f"/api/invitations/{inv['id']}/withdraw", json={"reason": "chose_other"}, headers=emp.headers)
    assert r.status_code == 200 and r.json()["status"] == "withdrawn"
    again = invite(client, emp, a.ref)
    assert again.status_code == 409 and "Пауза после отозванного приглашения" in again.json()["message"]
    inbox = client.get("/api/invitations/inbox", headers=cand.headers).json()["items"]
    assert inbox[0]["status_label"] == "Отозвано"
    notes = client.get("/api/notifications", headers=cand.headers).json()["items"]
    assert any("отозвала приглашение: выбрали другого кандидата" in n["message"] for n in notes)


def _accepted(client, make_account, db_session, emp, n=1):
    cand = make_candidate(make_account, db_session, n=n)
    a = open_card(db_session, emp, cand)
    inv = invite(client, emp, a.ref).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200, r.text
    return cand, a, inv


def test_revoke_consent_hides_contacts_again(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, a, inv = _accepted(client, make_account, db_session, emp)
    view = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    assert view["candidate"]["view"] == "disclosed" and view["candidate"]["phone"] == cand.phone

    r = client.post(f"/api/invitations/{inv['id']}/revoke-consent", headers=cand.headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "consent_revoked" and r.json()["contacts_open"] is False
    view = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    assert view["status_label"] == "Согласие отозвано" and view["candidate"]["view"] == "blind"
    assert_no_pii(view, cand)
    consents = client.get("/api/consents", headers=cand.headers).json()
    assert [c["active"] for c in consents if c["purpose"] == "contact_disclosure"] == [False]
    again = invite(client, emp, a.ref)  # DECISIONS 22: пауза пары 14 дней
    assert again.status_code == 409 and "Пауза после отзыва согласия" in again.json()["message"]
    notes = client.get("/api/notifications", headers=emp.acct.headers).json()["items"]
    assert any("отозвано — контакты снова скрыты" in n["message"] for n in notes)
    assert_no_pii(notes, cand)


def test_revoke_via_consent_journal_moves_invitation(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, a, inv = _accepted(client, make_account, db_session, emp)
    consent = next(c for c in client.get("/api/consents", headers=cand.headers).json() if c["purpose"] == "contact_disclosure")
    r = client.post(f"/api/consents/{consent['id']}/revoke", headers=cand.headers)
    assert r.status_code == 200 and r.json()["active"] is False
    detail = client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers).json()
    assert detail["status"] == "consent_revoked" and detail["contacts_open"] is False
    view = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    assert view["candidate"]["view"] == "blind"
    assert_no_pii(view, cand)


def test_contact_overdue_and_dispute(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, _, inv = _accepted(client, make_account, db_session, emp)
    # Пн 09:00 + 3 рабочих дня = чт 09:00; до срока жаловаться рано
    early = client.post(f"/api/invitations/{inv['id']}/dispute", json={}, headers=cand.headers)
    assert early.status_code == 409 and "ещё есть время" in early.json()["message"]
    advance(days=3, hours=1)
    view = client.get(f"/api/invitations/{inv['id']}", headers=emp.headers).json()
    assert view["contact_overdue"] is True
    assert reliability_kinds(db_session, emp.company_id) == ["contact_overdue"]
    r = client.post(f"/api/invitations/{inv['id']}/dispute", json={"comment": "Никто не написал"}, headers=cand.headers)
    assert r.status_code == 200 and r.json()["can_dispute"] is False
    # Один гостинг — один штраф
    assert reliability_kinds(db_session, emp.company_id) == ["contact_overdue"]
    # «Связались» после срока — без плюса к надёжности; «не сложилось» с причиной уходит кандидату со ссылками
    assert client.post(f"/api/invitations/{inv['id']}/contacted", headers=emp.headers).status_code == 200
    r = client.post(
        f"/api/invitations/{inv['id']}/outcome",
        json={"outcome": "not_matched", "reason": "skills_missing"},
        headers=emp.headers,
    )
    assert r.status_code == 400 and "каких навыков" in r.json()["message"]
    r = client.post(
        f"/api/invitations/{inv['id']}/outcome",
        json={"outcome": "not_matched", "reason": "skills_missing", "skill_ids": ["docker", "kafka", "postgresql"]},
        headers=emp.headers,
    )
    assert r.status_code == 200 and r.json()["status"] == "closed"
    detail = client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers).json()
    fb = detail["feedback"][0]
    assert fb["label"] == "Не хватило навыков из брифа"
    kinds = {link["skill"]: link["kind"] for link in fb["links"]}
    # Docker проверяет модуль devops_core, PostgreSQL — sql: для них задача по модулю; Kafka тестом не проверяется
    assert kinds["postgresql"] == "task" and kinds["docker"] == "task" and kinds["kafka"] == "interview"
    assert reliability_kinds(db_session, emp.company_id) == ["contact_overdue", "outcome_reported"]


def test_sweep_applies_deadlines_without_reads(client, make_account, db_session, frozen_clock):
    """Фоновый проход (DEMO «+1 день»): сроки и уведомления — без захода пользователя."""
    from app.models.contact import Invitation
    from app.services.contact import sweep

    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    inv = invite(client, emp, open_card(db_session, emp, cand).ref, response_days=3).json()["sent"][0]
    advance(days=2, hours=1)
    out = sweep(db_session)
    assert out["invitations"] == 1
    row = db_session.get(Invitation, inv["id"])
    # При сроке 3 дня «через 48 ч» совпадает с «за 24 ч до конца» — одно напоминание
    assert row.status == "sent" and row.reminders_sent == ["candidate_before_deadline"]
    advance(days=1)
    sweep(db_session)
    db_session.refresh(row)
    assert row.status == "expired"
