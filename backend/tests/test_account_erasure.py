"""Право на удаление (152-ФЗ, хардненинг п. 1): DELETE /api/candidates/me и /api/companies/me, сроки хранения,
минимизация данных реестра ФСП."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import func, select, text

from app.core.clock import clock
from app.db.base import Base
from app.models.candidate import CandidateProfile
from app.models.company import Company, ReliabilityEventRow, WebhookDelivery
from app.models.contact import Feedback, Invitation
from app.models.enums import UserStatus
from app.models.fsp import FspAuthState, FspLink
from app.models.matching import Need
from app.models.users import AuditLog, Consent, EmailCode, RefreshToken, User
from tests.test_api_contact_support import (
    advance,
    invite,
    make_candidate,
    make_employer,
    open_card,
)

PASSWORD = "Parol-2026"  # пароль make_account по умолчанию


def _delete(client, url: str, headers: dict, **body):
    return client.request("DELETE", url, json=body, headers=headers)


def _accepted(client, make_account, db_session, emp):
    cand = make_candidate(make_account, db_session)
    card = open_card(db_session, emp, cand)
    inv = invite(client, emp, card.ref).json()["sent"][0]
    client.get(f"/api/invitations/inbox/{inv['id']}", headers=cand.headers)
    r = client.post(f"/api/invitations/{inv['id']}/accept", json={"consent": True}, headers=cand.headers)
    assert r.status_code == 200, r.text
    return cand, inv


# --------------------------------------------------------------------------- схема БД: каскады


def test_every_foreign_key_to_users_and_companies_cascades_or_nulls():
    """Удаление users/companies одним DELETE возможно только если ни один внешний ключ не блокирует его (NO ACTION):
    на этом держится «удалили строку — удалили всё персональное»."""
    import app.models  # noqa: F401
    from app.services.demo import models as _demo  # noqa: F401

    blocking = [
        f"{t.name}.{fk.parent.name} → {fk.column.table.name}"
        for t in Base.metadata.sorted_tables
        for fk in t.foreign_keys
        if fk.column.table.name in ("users", "companies") and fk.ondelete not in ("CASCADE", "SET NULL")
    ]
    assert blocking == []


# --------------------------------------------------------------------------- кандидат


def test_candidate_deletion_requires_password_and_keeps_account_on_failure(client, make_account, db_session):
    acc = make_account(role="candidate")
    for body in ({}, {"password": "не-тот-пароль-1"}):
        r = _delete(client, "/api/candidates/me", acc.headers, **body)
        assert r.status_code == 403 and r.json()["code"] == "password_invalid", r.text
    assert db_session.get(User, acc.user_id) is not None
    # без токена и не той ролью — нельзя
    assert _delete(client, "/api/candidates/me", {}, password=PASSWORD).status_code == 401
    emp = make_account(role="employer")
    assert _delete(client, "/api/candidates/me", emp.headers, password=PASSWORD).status_code == 403


def test_candidate_deletion_wipes_personal_data_and_keeps_only_non_personal_traces(
    client, make_account, db_session, frozen_clock, mailer
):
    emp = make_employer(client, make_account, db_session)
    cand, inv = _accepted(client, make_account, db_session, emp)
    uid, email = cand.user_id, cand.acct.email
    db_session.add(Feedback(target_type="invitation", target_id=uuid.UUID(inv["id"]), direction="candidate_to_company", reason_code="x", comment="мой телефон +7 900"))
    # копия вебхука ATS с раскрытыми контактами кандидата
    company = db_session.get(Company, emp.company_id)
    db_session.add(
        WebhookDelivery(
            company_id=company.id,
            event_type="invitation.accepted",
            target_url="https://ats.example/hook",
            payload={"data": {"invitation_id": inv["id"], "pseudonym": inv["pseudonym"], "contacts": {"phone": cand.phone}}},
        )
    )
    db_session.flush()
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.actor_user_id == uid)) > 0

    r = _delete(client, "/api/candidates/me", cand.headers, password=PASSWORD)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["deleted"] is True and body["role"] == "candidate" and body["removed"] and body["kept"]
    assert "refresh" in r.headers.get("set-cookie", "").lower()  # cookie сессии очищена

    # аккаунт и всё, что на нём держалось, исчезло
    assert db_session.get(User, uid) is None
    assert db_session.get(CandidateProfile, uid) is None
    for model, col in ((Consent, Consent.user_id), (RefreshToken, RefreshToken.user_id), (EmailCode, EmailCode.user_id)):
        assert db_session.scalar(select(func.count()).select_from(model).where(col == uid)) == 0
    assert db_session.scalar(select(func.count()).select_from(Invitation).where(Invitation.candidate_id == uid)) == 0
    assert db_session.scalar(select(func.count()).select_from(Feedback).where(Feedback.target_id == uuid.UUID(inv["id"]))) == 0
    delivery = db_session.scalar(select(WebhookDelivery).where(WebhookDelivery.company_id == company.id))
    assert "contacts" not in delivery.payload["data"] and delivery.payload["data"]["pseudonym"] == inv["pseudonym"]
    # токен больше не работает
    assert client.get("/api/auth/me", headers=cand.headers).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 401

    # в журнале аудита нет ни IP, ни ссылок на человека; есть запись об удалении без ПДн
    rows = db_session.scalars(select(AuditLog)).all()
    assert not [a for a in rows if a.entity_id == str(uid) or (a.payload or {}).get("user_id") == str(uid)]
    erased = [a for a in rows if a.action == "account.erased"]
    assert len(erased) == 1 and erased[0].actor_user_id is None and erased[0].ip is None
    assert str(uid) not in str(erased[0].payload) and email not in str(erased[0].payload)
    # подтверждение ушло на почту
    mail = mailer.last_to(email)
    assert mail is not None and "удалён" in mail.subject.lower()
    # email можно использовать заново
    assert client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "role": "candidate", "consent_personal_data": True},
    ).status_code == 201


def test_reliability_counters_survive_candidate_deletion_without_link(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand, inv = _accepted(client, make_account, db_session, emp)
    advance(days=4)  # срок первого контакта прошёл → событие надёжности contact_overdue
    client.get(f"/api/invitations/{inv['id']}", headers=emp.headers)
    before = db_session.scalars(select(ReliabilityEventRow).where(ReliabilityEventRow.company_id == emp.company_id)).all()
    assert before
    r = _delete(client, "/api/candidates/me", cand.headers, password=PASSWORD)
    assert r.status_code == 200, r.text
    db_session.expire_all()  # тест делит сессию с приложением: удаление шло Core-запросом
    after = db_session.scalars(select(ReliabilityEventRow).where(ReliabilityEventRow.company_id == emp.company_id)).all()
    assert len(after) == len(before) and all(e.candidate_id is None and e.invitation_id is None for e in after)


def test_account_without_password_confirms_with_phrase(client, make_account, db_session):
    acc = make_account(role="candidate")
    db_session.execute(text("UPDATE users SET password_hash = NULL WHERE id = :i"), {"i": acc.user_id})
    db_session.expire_all()
    r = _delete(client, "/api/candidates/me", acc.headers)
    assert r.status_code == 400 and r.json()["code"] == "confirm_phrase_required"
    r = _delete(client, "/api/candidates/me", acc.headers, confirm="  удалить ")
    assert r.status_code == 200, r.text
    assert db_session.get(User, acc.user_id) is None


def test_deletion_attempts_are_rate_limited(client, make_account, db_session):
    acc = make_account(role="candidate")
    codes = [_delete(client, "/api/candidates/me", acc.headers, password="неверный-пароль-1").status_code for _ in range(7)]
    assert codes[:5] == [403] * 5 and codes[5:] == [429, 429]
    assert db_session.get(User, acc.user_id) is not None


# --------------------------------------------------------------------------- работодатель


def test_last_employer_deletion_closes_company_and_withdraws_invitations(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    cand = make_candidate(make_account, db_session)
    card = open_card(db_session, emp, cand)
    inv = invite(client, emp, card.ref).json()["sent"][0]
    company = db_session.get(Company, emp.company_id)
    company.webhook_url = "https://ats.example/hook"
    company.webhook_secret = "секрет"
    db_session.flush()

    assert _delete(client, "/api/companies/me", emp.headers, password="не-тот-пароль-1").status_code == 403
    r = _delete(client, "/api/companies/me", emp.headers, password=PASSWORD)
    assert r.status_code == 200, r.text
    assert r.json()["company_closed"] is True and r.json()["invitations_withdrawn"] == 1

    assert db_session.get(User, emp.acct.user_id) is None
    db_session.expire_all()
    company = db_session.get(Company, emp.company_id)
    assert company is not None and company.contact_methods == {} and company.webhook_url is None and company.webhook_secret is None
    row = db_session.get(Invitation, uuid.UUID(inv["id"]))
    assert row.status == "withdrawn"
    # кандидат видит отозванное приглашение
    item = client.get("/api/invitations/inbox", headers=cand.headers).json()["items"][0]
    assert item["status"] == "withdrawn"


def test_employer_deletion_keeps_company_when_colleagues_remain(client, make_account, db_session, frozen_clock):
    first = make_employer(client, make_account, db_session)
    # второй работодатель с того же корпоративного домена: общая компания
    second = make_employer(client, make_account, db_session, profile=False)
    assert second.company_id == first.company_id
    cand = make_candidate(make_account, db_session)
    card = open_card(db_session, first, cand)
    inv = invite(client, first, card.ref).json()["sent"][0]
    r = _delete(client, "/api/companies/me", second.headers, password=PASSWORD)
    assert r.status_code == 200 and r.json()["company_closed"] is False
    db_session.expire_all()
    company = db_session.get(Company, first.company_id)
    assert company.contact_methods  # профиль на месте
    assert db_session.get(Invitation, uuid.UUID(inv["id"])).status == "sent"
    assert db_session.scalars(select(Need).where(Need.company_id == first.company_id)).all() == []


# --------------------------------------------------------------------------- сроки хранения


def test_retention_sweep_removes_stale_unverified_and_anonymizes_old_ips(db_session, frozen_clock):
    from app.models.enums import ConsentPurpose
    from app.services.account import retention_sweep

    now = clock.now()

    def user(email: str, *, verified: bool, age_days: int) -> User:
        u = User(
            email=email,
            email_domain=email.split("@")[1],
            role="candidate",
            status=(UserStatus.active if verified else UserStatus.pending_email).value,
            email_verified=verified,
            created_at=now - timedelta(days=age_days),
        )
        db_session.add(u)
        db_session.flush()
        return u

    stale = user("old-unverified@example.com", verified=False, age_days=30)
    fresh = user("new-unverified@example.com", verified=False, age_days=1)
    active = user("old-active@example.com", verified=True, age_days=400)
    db_session.add(EmailCode(user_id=active.id, purpose="password_reset", email=active.email, code_hash="x", expires_at=now - timedelta(days=20)))
    db_session.add(AuditLog(at=now - timedelta(days=400), action="auth.login", actor_user_id=active.id, ip="203.0.113.7"))
    db_session.add(AuditLog(at=now - timedelta(days=1), action="auth.login", actor_user_id=active.id, ip="203.0.113.8"))
    db_session.add(
        Consent(user_id=active.id, purpose=ConsentPurpose.processing.value, text_version="v", granted_at=now - timedelta(days=500), ip="203.0.113.9", user_agent="UA")
    )
    db_session.add(FspAuthState(state_hash="s" * 64, nonce="n", code_verifier="v", redirect_uri="x", expires_at=now - timedelta(days=5)))
    db_session.flush()

    out = retention_sweep(db_session)
    assert out["unverified_accounts"] == 1 and out["email_codes"] >= 1 and out["fsp_auth_states"] == 1
    db_session.expunge_all()  # удаление шло Core-запросом, а тест делит сессию с задачей
    assert db_session.get(User, stale.id) is None
    assert db_session.get(User, fresh.id) is not None and db_session.get(User, active.id) is not None
    ips = {a.at: a.ip for a in db_session.scalars(select(AuditLog).where(AuditLog.actor_user_id == active.id))}
    assert sorted(v for v in ips.values() if v) == ["203.0.113.8"]  # старый IP обезличен, свежий остался
    consent = db_session.scalar(select(Consent).where(Consent.user_id == active.id))
    assert consent.ip is None and consent.user_agent is None and consent.text_version == "v"
    # повторный прогон ничего не меняет
    again = retention_sweep(db_session)
    assert again["unverified_accounts"] == 0 and again["audit_ip_anonymized"] == 0


# --------------------------------------------------------------------------- минимизация ответа реестра ФСП


def test_registry_response_minimization_keeps_only_used_fields():
    from datetime import date

    from app.fsp.models import Athlete
    from app.fsp.provider import InProcessFSPProvider, normalize_achievements
    from app.services.fsp_link.minimize import (
        athlete_of,
        birth_year_of,
        is_minimal,
        minimal_achievements,
        minimal_athlete,
    )

    reg = InProcessFSPProvider.from_file()
    # спортсмен с командным результатом
    sub = next(
        a.sub for a in reg.snapshot.athletes if any(r.team and r.team.members for r in reg.get_achievements(a.sub).results)
    )
    full = reg.get_achievements(sub)
    athlete = reg.get_athlete(sub)
    raw_athlete = minimal_athlete(athlete)
    assert set(raw_athlete) == {"sub", "birth_year"} and raw_athlete["birth_year"] == athlete.birth_date.year
    assert athlete_of(raw_athlete) is None and birth_year_of(raw_athlete) == athlete.birth_date.year
    assert athlete_of(athlete.model_dump(mode="json")) == athlete  # старые полные строки читаются как прежде

    mini = minimal_achievements(full)
    others = {m.athlete_sub for r in full.results if r.team for m in r.team.members} - {sub}
    assert others and not any(o in str(mini) for o in others)  # чужих ФСП ID участников команд нет
    assert is_minimal(raw_athlete, mini) and not is_minimal(athlete.model_dump(mode="json"), mini)
    # из минимального ответа профиль собирается так же (команда → размер состава, результаты, разряды)
    a = normalize_achievements(full, athlete=athlete)
    b = normalize_achievements(type(full).model_validate(mini), athlete=None)
    assert [r.model_dump() for r in a.results] == [r.model_dump() for r in b.results]
    assert [r.id for r in a.ranks] == [r.id for r in b.ranks]
    assert isinstance(athlete, Athlete) and isinstance(athlete.birth_date, date)
    # идемпотентность
    assert minimal_achievements(mini) == mini


def test_sync_stores_minimal_registry_response(client, make_account, db_session, frozen_clock):
    from app.services.fsp_link import service as fsp
    from app.services.fsp_link.minimize import is_minimal

    acc = make_account(role="candidate")
    sub = fsp.demo_registry().snapshot.athletes[0].sub
    assert client.post("/api/fsp/demo-login", json={"sub": sub, "mode": "link", "consent_fsp_enrichment": True}, headers=acc.headers).status_code == 200
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == acc.user_id))
    assert link.athlete and set(link.athlete) == {"sub", "birth_year"}
    assert link.achievements is not None and is_minimal(link.athlete, link.achievements)
    me = client.get("/api/fsp/me", headers=acc.headers).json()
    assert me["state"] in ("linked", "awaiting_protocol", "linked_no_results")
    # сохранённая запись не содержит ФИО из реестра
    athlete = fsp.demo_registry().get_athlete(sub)
    dumped = str(link.athlete) + str(link.achievements)
    assert athlete.last_name not in dumped and athlete.first_name not in dumped
