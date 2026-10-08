"""Кандидаты 15–17 лет: отказ младше 15, согласие законного представителя по ссылке из письма, видимость и стажировка."""

from __future__ import annotations

import re
from datetime import timedelta

import pytest

from app.models.candidate import CandidateProfile
from tests.test_api_contact_support import INVITE, invite, make_candidate, make_employer, make_need, open_card

REGISTER = {"email": "teen@example.com", "password": "Kod-2026-ok", "role": "candidate", "consent_personal_data": True}
GUARDIAN = {"guardian_name": "Ольга Петровна Тестова", "guardian_email": "olga.parent@example.com"}


def _token(mailer, email) -> str:
    mail = mailer.last_to(email)
    assert mail is not None, "письмо представителю не отправлено"
    m = re.search(r"guardian-confirm\?token=([\w-]+)", mail.text)
    assert m, mail.text
    return m.group(1)


def test_registration_refused_under_min_age(client, db_session, frozen_clock):
    year = frozen_clock.now().year
    r = client.post("/api/auth/register", json={**REGISTER, "birth_year": year - 14})
    assert r.status_code == 422
    detail = r.json()["details"][0]
    assert detail["field"] == "birth_year" and detail["type"] == "age_too_young" and "с 15 лет" in detail["message"]
    # год рождения, при котором возраст «14 или 15», тоже отклоняем (осторожно в пользу несовершеннолетних)
    assert client.post("/api/auth/register", json={**REGISTER, "birth_year": year - 15}).status_code == 422
    # аккаунт не создан: тот же email регистрируется без возрастных ограничений
    from sqlalchemy import select

    from app.models.users import User

    assert db_session.scalar(select(User).where(User.email == REGISTER["email"])) is None
    assert client.post("/api/auth/register", json={**REGISTER, "birth_year": year - 25}).status_code == 201


def test_employer_birth_year_is_ignored(client, db_session, frozen_clock):
    r = client.post("/api/auth/register", json={**REGISTER, "role": "employer", "birth_year": frozen_clock.now().year - 5})
    assert r.status_code == 201


def test_profile_birth_year_under_min_age_is_422(client, make_account, frozen_clock):
    acc = make_account()
    r = client.put("/api/candidates/me", json={"birth_year": frozen_clock.now().year - 14}, headers=acc.headers)
    assert r.status_code == 422 and r.json()["details"][0]["type"] == "age_too_young"


def test_minor_registration_flow_hidden_until_guardian_confirms(client, make_account, db_session, mailer, frozen_clock):
    year = frozen_clock.now().year
    acc = make_account()
    # взрослому согласие не нужно
    g = client.get("/api/candidates/me/guardian", headers=acc.headers).json()
    assert g["required"] is False and g["state"] == "not_required"
    assert client.post("/api/candidates/me/guardian", json=GUARDIAN, headers=acc.headers).status_code == 409

    r = client.put("/api/candidates/me", json={"birth_year": year - 17}, headers=acc.headers)  # 16–17 лет
    assert r.status_code == 200 and r.json()["guardian"]["state"] == "missing"
    prof = db_session.get(CandidateProfile, acc.user_id)
    assert prof.is_minor is True and prof.guardian_consent is False

    # нельзя указать себя представителем
    bad = client.post("/api/candidates/me/guardian", json={**GUARDIAN, "guardian_email": acc.email}, headers=acc.headers)
    assert bad.status_code == 400 and bad.json()["code"] == "guardian_email_same"
    short = client.post("/api/candidates/me/guardian", json={**GUARDIAN, "guardian_name": "Оля"}, headers=acc.headers)
    assert short.status_code == 422

    r = client.post("/api/candidates/me/guardian", json=GUARDIAN, headers=acc.headers)
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "pending" and body["guardian_email_masked"] == "o***@example.com"
    token = _token(mailer, GUARDIAN["guardian_email"])
    mail = mailer.last_to(GUARDIAN["guardian_email"])
    assert "Ольга Петровна Тестова" in mail.text and "Согласие законного представителя" in mail.subject
    assert acc.email not in mail.text  # email кандидата в письме только с маской
    # повторная отправка тому же адресу сразу — 429
    assert client.post("/api/candidates/me/guardian", json=GUARDIAN, headers=acc.headers).status_code == 429
    # пока не подтверждено, банк скрывает профиль и называет причину
    assert db_session.get(CandidateProfile, acc.user_id).guardian_consent is False
    # токен лежит в БД только хешем
    assert prof.guardian_token_hash and token not in prof.guardian_token_hash

    r = client.post("/api/guardian/confirm", json={"token": token})
    assert r.status_code == 200 and r.json()["confirmed"] is True
    db_session.refresh(prof)
    assert prof.guardian_consent is True and prof.guardian_confirmed_at is not None and prof.guardian_token_hash is None
    assert client.get("/api/candidates/me/guardian", headers=acc.headers).json()["state"] == "confirmed"
    # ссылка одноразовая
    again = client.post("/api/guardian/confirm", json={"token": token})
    assert again.status_code == 404 and again.json()["code"] == "guardian_token_invalid"


def test_guardian_link_expires_and_new_guardian_resets_consent(client, make_account, db_session, mailer, frozen_clock):
    acc = make_account()
    client.put("/api/candidates/me", json={"birth_year": frozen_clock.now().year - 17}, headers=acc.headers)
    client.post("/api/candidates/me/guardian", json=GUARDIAN, headers=acc.headers)
    token = _token(mailer, GUARDIAN["guardian_email"])
    frozen_clock.advance(timedelta(hours=169).total_seconds())
    assert client.get("/api/candidates/me/guardian", headers=acc.headers).json()["state"] == "expired"
    r = client.post("/api/guardian/confirm", json={"token": token})
    assert r.status_code == 410 and r.json()["code"] == "guardian_token_expired"
    # после отправки нового письма новая ссылка работает, старая — нет
    assert client.post("/api/candidates/me/guardian", json=GUARDIAN, headers=acc.headers).status_code == 200
    new_token = _token(mailer, GUARDIAN["guardian_email"])
    assert new_token != token
    assert client.post("/api/guardian/confirm", json={"token": new_token}).status_code == 200
    # другой представитель — прежнее согласие не переносится
    other = {"guardian_name": "Иван Сергеевич Тестов", "guardian_email": "ivan.parent@example.com"}
    assert client.post("/api/candidates/me/guardian", json=other, headers=acc.headers).status_code == 200
    assert db_session.get(CandidateProfile, acc.user_id).guardian_consent is False


def test_turning_adult_clears_guardian_data(client, make_account, db_session, frozen_clock):
    acc = make_account()
    year = frozen_clock.now().year
    client.put("/api/candidates/me", json={"birth_year": year - 17}, headers=acc.headers)
    client.post("/api/candidates/me/guardian", json=GUARDIAN, headers=acc.headers)
    client.put("/api/candidates/me", json={"birth_year": year - 30}, headers=acc.headers)
    prof = db_session.get(CandidateProfile, acc.user_id)
    assert prof.is_minor is False and prof.guardian_email is None and prof.guardian_name is None


def test_bank_status_names_missing_guardian(client, make_account, db_session, frozen_clock):
    acc = make_account()
    client.put("/api/candidates/me", json={"birth_year": frozen_clock.now().year - 17}, headers=acc.headers)
    reasons = client.get("/api/candidates/me/privacy", headers=acc.headers).json()["bank"]["reasons"]
    assert any("законного представителя" in r for r in reasons)
    prof = db_session.get(CandidateProfile, acc.user_id)
    prof.guardian_consent = True
    db_session.flush()
    reasons = client.get("/api/candidates/me/privacy", headers=acc.headers).json()["bank"]["reasons"]
    assert not any("законного представителя" in r for r in reasons)


@pytest.mark.parametrize("consent", [False, True])
def test_invitation_to_minor_requires_consent_and_internship(client, make_account, db_session, frozen_clock, consent):
    year = frozen_clock.now().year
    emp = make_employer(client, make_account, db_session)
    teen = make_candidate(make_account, db_session, birth_year=year - 17, guardian_consent=consent)
    ref = open_card(db_session, emp, teen).ref
    r = invite(client, emp, ref)
    assert r.status_code in (200, 201, 400, 409), r.text
    text = r.text
    if not consent:
        assert "сейчас не принимает приглашения" in text
    else:
        # согласие есть, но это не стажировка: работодателю прямо говорят, что допустимо
        assert "только стажировку или лёгкий труд на неполный день" in text
        need = make_need(db_session, emp, is_internship=True)
        r2 = client.post("/api/invitations", json={**INVITE, "candidate": ref, "need_id": str(need.id)}, headers=emp.headers)
        assert r2.status_code == 201, r2.text
