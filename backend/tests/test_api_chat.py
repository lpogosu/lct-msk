"""Чат после «Взаимно»: доступ только участникам и только после взаимного «да», чтение после отзыва согласия,
лимиты длины и частоты, непрочитанные, уведомления и аудит (DECISIONS 29)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.services.demo.models import DemoPersona
from tests.test_api_contact_support import invite, make_candidate, make_employer, make_need, open_card


def _mutual(client, make_account, db):
    emp = make_employer(client, make_account, db)
    cand = make_candidate(make_account, db)
    alias = open_card(db, emp, cand)
    inv = invite(client, emp, alias.ref).json()["sent"][0]
    return emp, cand, inv["id"]


def _accept(client, cand, inv_id):
    client.get(f"/api/invitations/inbox/{inv_id}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv_id}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200, r.text


def _post(client, who, ref_id, body, kind="invitation"):
    return client.post(f"/api/chat/{kind}/{ref_id}/messages", json={"body": body}, headers=who.headers)


def test_chat_closed_before_mutual_yes(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    for who in (emp, cand):
        r = client.get(f"/api/chat/invitation/{inv_id}", headers=who.headers)
        assert r.status_code == 403 and r.json()["code"] == "chat_unavailable"
        assert _post(client, who, inv_id, "Привет").status_code == 403


def test_chat_flow_unread_notifications_and_audit(client, make_account, db_session, frozen_clock, mailer):
    from app.models.integration import ChatMessage
    from app.models.users import AuditLog

    emp, cand, inv_id = _mutual(client, make_account, db_session)
    _accept(client, cand, inv_id)

    r = client.get(f"/api/chat/invitation/{inv_id}", headers=emp.headers)
    assert r.status_code == 200 and r.json()["can_send"] is True and r.json()["messages"] == []
    assert r.json()["max_len"] == 1000

    r = _post(client, emp, inv_id, "Здравствуйте! Когда удобно созвониться?")
    assert r.status_code == 201, r.text
    assert r.json()["messages"][0]["mine"] is True and r.json()["messages"][0]["from"] == "employer"
    # письмо кандидату без текста сообщения
    mail = mailer.last_to(cand.acct.email)
    assert mail is not None and "Когда удобно" not in mail.text and "Взаимно" in mail.subject
    # непрочитанные у кандидата: счётчик и уведомление в кабинете
    assert client.get("/api/chat/unread", headers=cand.headers).json() == {"unread_total": 1}
    threads = client.get("/api/chat/threads", headers=cand.headers).json()
    assert threads["unread_total"] == 1 and threads["items"][0]["title"] == "ООО «Северный код»"
    notes = client.get("/api/notifications", headers=cand.headers).json()["items"]
    assert any("Новое сообщение в чате" in n["message"] and "Когда удобно" not in n["message"] for n in notes)

    # кандидат открывает чат — прочитано; отвечает; у работодателя 1 непрочитанное
    r = client.get(f"/api/chat/invitation/{inv_id}", headers=cand.headers)
    assert r.json()["marked_read"] == 1 and r.json()["messages"][0]["mine"] is False
    assert client.get("/api/chat/unread", headers=cand.headers).json()["unread_total"] == 0
    assert _post(client, cand, inv_id, "Завтра после 15:00 подойдёт").status_code == 201
    assert client.get("/api/chat/unread", headers=emp.headers).json()["unread_total"] == 1
    thread = client.get(f"/api/chat/invitation/{inv_id}", headers=emp.headers).json()
    assert [m["from"] for m in thread["messages"]] == ["employer", "candidate"] and thread["messages"][0]["read"] is True
    # работодатель не видит внутренних id кандидата
    assert str(cand.user_id) not in str(thread)
    # только новые: после первого сообщения — одно
    first = thread["messages"][0]["seq"]
    only_new = client.get(f"/api/chat/invitation/{inv_id}", params={"after": first}, headers=emp.headers).json()["messages"]
    assert [m["from"] for m in only_new] == ["candidate"]

    audits = db_session.scalars(select(AuditLog).where(AuditLog.action == "chat.message_sent")).all()
    assert len(audits) == 2 and all("Завтра" not in str(a.payload) for a in audits)
    assert len(db_session.scalars(select(ChatMessage)).all()) == 2


def test_chat_third_parties_and_roles(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    _accept(client, cand, inv_id)
    stranger = make_candidate(make_account, db_session, n=2)
    other_emp = make_employer(client, make_account, db_session, "drugaya-firma.ru")
    for who in (stranger, other_emp):
        assert client.get(f"/api/chat/invitation/{inv_id}", headers=who.headers).status_code == 404
        assert _post(client, who, inv_id, "Я чужой").status_code == 404
    assert client.get(f"/api/chat/invitation/{inv_id}").status_code == 401
    assert client.get("/api/chat/invitation/not-a-uuid", headers=emp.headers).status_code == 422
    assert client.get("/api/chat/threads", headers=stranger.headers).json()["items"] == []


def test_chat_limits_length_empty_and_rate(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    _accept(client, cand, inv_id)
    r = _post(client, emp, inv_id, "   ")
    assert r.status_code == 409 and r.json()["code"] == "chat_empty"
    r = _post(client, emp, inv_id, "я" * 1001)
    assert r.status_code == 409 and r.json()["code"] == "chat_too_long" and "1000" in r.json()["message"]
    codes = [_post(client, emp, inv_id, "я" * 1000 if i == 0 else f"сообщение {i}").status_code for i in range(12)]
    assert codes[0] == 201 and 429 in codes  # 10 в минуту (config/contact.yaml → chat.rate_per_minute)


def test_chat_readonly_after_close_and_revoke(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    _accept(client, cand, inv_id)
    assert _post(client, emp, inv_id, "Первое").status_code == 201
    # контакт завершён: обе стороны читают, писать нельзя
    assert client.post(f"/api/invitations/{inv_id}/contacted", headers=emp.headers).status_code == 200
    assert client.post(f"/api/invitations/{inv_id}/outcome", json={"outcome": "hired"}, headers=emp.headers).status_code == 200
    for who in (emp, cand):
        r = client.get(f"/api/chat/invitation/{inv_id}", headers=who.headers)
        assert r.status_code == 200 and r.json()["can_send"] is False and "завершён" in r.json()["readonly_reason"]
        w = _post(client, who, inv_id, "Ещё одно")
        assert w.status_code == 409 and w.json()["code"] == "chat_closed"
    # кандидат отзывает согласие: работодателю чат закрыт совсем, кандидат видит историю
    assert client.post(f"/api/invitations/{inv_id}/revoke-consent", headers=cand.headers).status_code == 200
    r = client.get(f"/api/chat/invitation/{inv_id}", headers=emp.headers)
    assert r.status_code == 403 and r.json()["code"] == "chat_closed"
    assert _post(client, emp, inv_id, "Вернитесь").status_code == 403
    r = client.get(f"/api/chat/invitation/{inv_id}", headers=cand.headers)
    assert r.status_code == 200 and r.json()["can_send"] is False and "отозвали" in r.json()["readonly_reason"]
    assert len(r.json()["messages"]) == 1
    assert client.get("/api/chat/threads", headers=emp.headers).json()["items"] == []


def test_chat_closes_when_consent_revoked_while_accepted(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    _accept(client, cand, inv_id)
    assert client.post(f"/api/invitations/{inv_id}/revoke-consent", headers=cand.headers).status_code == 200
    assert client.get(f"/api/chat/invitation/{inv_id}", headers=emp.headers).status_code == 403
    r = client.get(f"/api/chat/invitation/{inv_id}", headers=cand.headers)
    assert r.status_code == 200 and r.json()["can_send"] is False
    assert _post(client, cand, inv_id, "Передумала").status_code == 409


def test_chat_declined_invitation_never_opens(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    client.get(f"/api/invitations/inbox/{inv_id}", headers=cand.headers)
    client.post(f"/api/invitations/{inv_id}/decline", json={"reason": "stack"}, headers=cand.headers)
    for who in (emp, cand):
        assert client.get(f"/api/chat/invitation/{inv_id}", headers=who.headers).status_code == 403


def test_chat_demo_reply_for_synthetic_persona(client, make_account, db_session, frozen_clock):
    emp, cand, inv_id = _mutual(client, make_account, db_session)
    _accept(client, cand, inv_id)
    db_session.add(DemoPersona(user_id=cand.user_id, kind="synthetic", key=None, profile={}))
    db_session.flush()
    settings = client.app.state.settings
    prev = settings.demo_mode
    settings.demo_mode = True
    try:
        r = _post(client, emp, inv_id, "Добрый день!")
    finally:
        settings.demo_mode = prev
    assert r.status_code == 201, r.text
    assert [m["from"] for m in r.json()["messages"]] == ["employer", "candidate"]
    # вне DEMO_MODE автоответа нет
    r = _post(client, emp, inv_id, "Ещё вопрос")
    assert [m["from"] for m in r.json()["messages"]][-1] == "employer"


@pytest.fixture
def features(monkeypatch):
    import app.services.contact.vacancies as vac_svc
    from app.matching.scoring import CandidateFeatures, ModuleScore

    def fake(db, user_id):
        return [
            CandidateFeatures(
                candidate_id=str(user_id), spec="backend_python", grade="middle", theta=0.6, se=0.3,
                modules={"python_core": ModuleScore(0.8, 0.3, "confirmed")}, salary_min=230000,
                formats=frozenset({"hybrid", "remote"}),
            )
        ]

    monkeypatch.setattr(vac_svc, "candidate_features", fake)


def test_chat_application_thread(client, make_account, db_session, frozen_clock, features):
    """Отклик: чат открывается после «Есть интерес»; до этого — нет."""
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    need = make_need(db_session, emp, must=("python",))
    vac = client.post("/api/vacancies", json={"need_id": str(need.id)}, headers=emp.headers).json()["vacancy"]
    app = client.post("/api/applications", json={"vacancy_id": vac["id"], "consent": True}, headers=cand.headers).json()
    for who in (emp, cand):
        assert client.get(f"/api/chat/application/{app['id']}", headers=who.headers).status_code == 403
    client.get(f"/api/applications/{app['id']}", headers=emp.headers)  # просмотр
    assert client.post(f"/api/applications/{app['id']}/interest", headers=emp.headers).status_code == 200
    assert _post(client, emp, app["id"], "Интересно, расскажите об опыте", "application").status_code == 201
    assert _post(client, cand, app["id"], "С удовольствием", "application").status_code == 201
    assert client.get("/api/chat/unread", headers=emp.headers).json()["unread_total"] == 1
    # кандидат отзывает отклик → работодателю закрыто, кандидату только чтение
    assert client.post(f"/api/applications/{app['id']}/withdraw", headers=cand.headers).status_code == 200
    assert client.get(f"/api/chat/application/{app['id']}", headers=emp.headers).status_code == 403
    r = client.get(f"/api/chat/application/{app['id']}", headers=cand.headers)
    assert r.status_code == 200 and r.json()["can_send"] is False


def test_chat_and_ats_log_erased_with_candidate_account(client, make_account, db_session, frozen_clock):
    """152-ФЗ: удаление аккаунта кандидата стирает переписку и запросы с его контактами в «Журнале ATS»."""
    from app.models.integration import AtsLog, ChatMessage

    emp, cand, inv_id = _mutual(client, make_account, db_session)
    client.put("/api/companies/me/ats", json={"ats_type": "huntflow"}, headers=emp.headers)
    _accept(client, cand, inv_id)
    assert _post(client, emp, inv_id, "Здравствуйте").status_code == 201
    assert db_session.scalars(select(ChatMessage)).all() and db_session.scalars(select(AtsLog)).all()
    r = client.request("DELETE", "/api/candidates/me", json={"password": cand.acct.password}, headers=cand.headers)
    assert r.status_code == 200, r.text
    assert db_session.scalars(select(ChatMessage)).all() == []
    assert [x for x in db_session.scalars(select(AtsLog)).all() if x.entity_id == inv_id] == []
