"""Платформа: регистрация с кодом из письма, вход, refresh-ротация, выход, лимиты (концепция §3.1 шаги 1–2, §6.5)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from app.core.security import create_access_token, hash_token
from app.models import AuditLog, CandidateProfile, Company, Consent, EmailCode, RefreshToken, User

PASSWORD = "Parol-2026"


def _register(client, email, role="candidate", **extra):
    payload = {"email": email, "password": PASSWORD, "role": role, "consent_personal_data": True, **extra}
    return client.post("/api/auth/register", json=payload)


def _user(db, email) -> User:
    return db.scalar(select(User).where(User.email == email.lower()))


# --- Регистрация и подтверждение ---


def test_full_registration_flow(client, mailer, read_code, db_session, frozen_clock, test_settings):
    email = "Maria.Ivanova@Example.com"
    r = _register(client, email)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["email"] == "maria.ivanova@example.com"
    assert body["status"] == "pending_email" and body["role"] == "candidate"
    assert body["code_sent"] is True and body["code_ttl_s"] == 900 and body["resend_available_in_s"] == 60

    # Письмо: код в теме и тексте, ссылка на экран подтверждения, русский текст
    mail = mailer.last_to("maria.ivanova@example.com")
    code = read_code("maria.ivanova@example.com")
    assert code in mail.text and code in mail.html
    assert "Код действует 15 минут" in mail.text
    assert f"/verify-email?email=maria.ivanova%40example.com&code={code}" in mail.text

    user = _user(db_session, email)
    assert user.status == "pending_email" and not user.email_verified
    assert user.password_hash.startswith("$argon2id$")
    # Код в БД только в виде HMAC
    row = db_session.scalar(select(EmailCode).where(EmailCode.user_id == user.id))
    assert row.code_hash != code and len(row.code_hash) == 64
    assert row.expires_at == frozen_clock.now() + timedelta(minutes=15)

    # До подтверждения войти нельзя — экран О3
    r = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert r.status_code == 403
    assert r.json()["code"] == "email_not_verified"
    assert r.json()["details"]["email"] == "maria.ivanova@example.com"

    r = client.post("/api/auth/verify-email", json={"email": email, "code": code, "password": PASSWORD})
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["token_type"] == "bearer" and tokens["expires_in"] == 900
    assert tokens["user"]["status"] == "active" and tokens["user"]["email_verified"] is True
    set_cookie = r.headers["set-cookie"]
    assert f"{test_settings.refresh_cookie_name}=" in set_cookie
    assert "HttpOnly" in set_cookie and "Path=/api/auth" in set_cookie and "samesite=lax" in set_cookie.lower()
    assert r.headers["cache-control"] == "no-store"

    db_session.expire_all()
    user = _user(db_session, email)
    assert user.status == "active" and user.email_verified and user.email_verified_at == frozen_clock.now()
    profile = db_session.get(CandidateProfile, user.id)
    assert profile is not None and profile.weekly_invite_limit == 5
    assert profile.search_mode == "open" and profile.salary_visibility == "signal"

    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 200 and r.json()["email"] == "maria.ivanova@example.com"

    # Повторное подтверждение — 409
    r = client.post("/api/auth/verify-email", json={"email": email, "code": code, "password": PASSWORD})
    assert r.status_code == 409 and r.json()["code"] == "already_verified"


def test_registration_records_separate_pd_consent(client, db_session):
    r = _register(client, "consent@example.com", consent_text_version="2026-10-05")
    assert r.status_code == 201
    user = _user(db_session, "consent@example.com")
    consents = db_session.scalars(select(Consent).where(Consent.user_id == user.id)).all()
    assert len(consents) == 1
    c = consents[0]
    assert c.purpose == "processing" and c.text_version == "2026-10-05"
    assert c.revoked_at is None and c.ip == "testclient" and c.scope == {"context": "registration"}


def test_registration_requires_consent_and_valid_fields(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "no-consent@example.com", "password": PASSWORD, "role": "candidate", "consent_personal_data": False},
    )
    assert r.status_code == 422
    body = r.json()
    assert body["code"] == "validation_error" and body["message"] == "Проверьте заполнение полей"
    assert {"field": "consent_personal_data", "message": "Нужно согласие на обработку персональных данных", "type": "value_error"} in body["details"]

    r = client.post("/api/auth/register", json={"email": "не-почта", "password": PASSWORD, "role": "admin"})
    assert r.status_code == 422
    fields = {d["field"]: d["message"] for d in r.json()["details"]}
    assert fields["email"] == "Некорректный адрес электронной почты"
    assert fields["role"].startswith("Допустимые значения")
    assert fields["consent_personal_data"] == "Обязательное поле"


def test_registration_outdated_consent_version(client):
    r = _register(client, "old-consent@example.com", consent_text_version="2020-01-01")
    assert r.status_code == 409 and r.json()["code"] == "consent_outdated"
    assert r.json()["details"]["actual_version"] == "2026-10-05"


def test_registration_password_policy(client):
    r = client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "password": "abc", "role": "candidate", "consent_personal_data": True},
    )
    assert r.status_code == 422
    messages = [d["message"] for d in r.json()["details"] if d["field"] == "password"]
    assert "Минимум 8 символов" in messages and "Хотя бы одна цифра" in messages


def test_registration_conflict_for_verified_email(client, make_account):
    acc = make_account("taken@example.com")
    r = _register(client, acc.email.upper())
    assert r.status_code == 409
    assert r.json() == {
        "code": "email_taken",
        "message": "Этот email уже зарегистрирован. Войдите в аккаунт",
        "details": {"email": "taken@example.com"},
    }


def test_concurrent_registration_race_is_409_not_500(client, make_account, monkeypatch):
    from app.core import auth_service

    acc = make_account("race@example.com")
    # Имитируем гонку: проверка «email свободен» прошла, а строку уже вставил параллельный запрос
    monkeypatch.setattr(auth_service, "get_user_by_email", lambda db, email: None)
    r = _register(client, acc.email)
    assert r.status_code == 409 and r.json()["code"] == "email_taken"


def test_reregistration_of_unverified_email_respects_send_limit(client, mailer, frozen_clock, db_session):
    assert _register(client, "again@example.com").status_code == 201
    r = _register(client, "again@example.com", role="employer")
    assert r.status_code == 429 and r.json()["code"] == "resend_too_soon"
    frozen_clock.advance(61)
    r = _register(client, "again@example.com", role="employer")
    assert r.status_code == 201 and r.json()["role"] == "employer"
    assert len([m for m in mailer.outbox if m.to == "again@example.com"]) == 2


# --- Код: попытки, срок, повторная отправка ---


def test_wrong_code_attempts_burn_code(client, mailer, read_code, frozen_clock):
    email = "attempts@example.com"
    _register(client, email)
    code = read_code(email)
    wrong = "000000" if code != "000000" else "111111"
    for left in (4, 3, 2, 1):
        r = client.post("/api/auth/verify-email", json={"email": email, "code": wrong, "password": PASSWORD})
        assert r.status_code == 400
        assert r.json()["code"] == "code_invalid" and r.json()["details"] == {"attempts_left": left}
    r = client.post("/api/auth/verify-email", json={"email": email, "code": wrong, "password": PASSWORD})
    assert r.status_code == 400 and r.json()["code"] == "code_attempts_exceeded"
    # Правильный код после 5 ошибок уже не действует
    r = client.post("/api/auth/verify-email", json={"email": email, "code": code, "password": PASSWORD})
    assert r.status_code == 400 and r.json()["code"] == "code_not_found"
    # Новый код работает
    frozen_clock.advance(61)
    assert client.post("/api/auth/resend-code", json={"email": email}).status_code == 200
    r = client.post("/api/auth/verify-email", json={"email": email, "code": read_code(email), "password": PASSWORD})
    assert r.status_code == 200


def test_code_expires_after_15_minutes(client, mailer, read_code, frozen_clock):
    email = "expire@example.com"
    _register(client, email)
    code = read_code(email)
    frozen_clock.advance(15 * 60)
    r = client.post("/api/auth/verify-email", json={"email": email, "code": code, "password": PASSWORD})
    assert r.status_code == 400 and r.json()["code"] == "code_expired"
    assert r.json()["message"] == "Срок действия кода истёк. Запросите новый код"


def test_verify_unknown_email_does_not_reveal_existence(client):
    r = client.post("/api/auth/verify-email", json={"email": "ghost@example.com", "code": "123456", "password": PASSWORD})
    assert r.status_code == 400 and r.json()["code"] == "code_invalid"


def test_resend_cooldown_60s_and_5_per_hour(client, mailer, read_code, frozen_clock):
    email = "resend@example.com"
    _register(client, email)  # отправка №1
    first_code = read_code(email)

    r = client.post("/api/auth/resend-code", json={"email": email})
    assert r.status_code == 429
    assert r.json()["code"] == "resend_too_soon" and r.json()["details"] == {"retry_after_s": 60}
    assert r.headers["retry-after"] == "60"
    assert r.json()["message"] == "Отправить код ещё раз можно через 60 с"

    frozen_clock.advance(30)
    r = client.post("/api/auth/resend-code", json={"email": email})
    assert r.status_code == 429 and r.json()["details"] == {"retry_after_s": 30}

    for n in (2, 3, 4, 5):
        frozen_clock.advance(61)
        r = client.post("/api/auth/resend-code", json={"email": email})
        assert r.status_code == 200, r.text
        assert r.json()["sends_left_this_hour"] == 5 - n

    # Шестое письмо в течение часа — нельзя, ждать до выхода первой отправки из окна
    frozen_clock.advance(61)
    r = client.post("/api/auth/resend-code", json={"email": email})
    assert r.status_code == 429 and r.json()["code"] == "resend_hourly_limit"
    elapsed = 30 + 61 * 5
    assert r.json()["details"]["retry_after_s"] == 3600 - elapsed

    frozen_clock.advance(3600 - elapsed)
    assert client.post("/api/auth/resend-code", json={"email": email}).status_code == 200
    assert len([m for m in mailer.outbox if m.to == email]) == 6

    # Старые коды погашены: работает только последний
    r = client.post("/api/auth/verify-email", json={"email": email, "code": first_code, "password": PASSWORD})
    assert r.status_code == 400
    r = client.post("/api/auth/verify-email", json={"email": email, "code": read_code(email), "password": PASSWORD})
    assert r.status_code == 200


def test_resend_unknown_and_verified(client, make_account):
    r = client.post("/api/auth/resend-code", json={"email": "nobody@example.com"})
    assert r.status_code == 404 and r.json()["code"] == "user_not_found"
    acc = make_account()
    r = client.post("/api/auth/resend-code", json={"email": acc.email})
    assert r.status_code == 409 and r.json()["code"] == "already_verified"


def test_mail_failure_does_not_consume_send_limit(client, mailer, frozen_clock):
    mailer.fail_next = True
    r = _register(client, "smtp-down@example.com")
    assert r.status_code == 201 and r.json()["code_sent"] is False and r.json()["resend_available_in_s"] == 0
    # Неотправленное письмо не блокирует повтор
    r = client.post("/api/auth/resend-code", json={"email": "smtp-down@example.com"})
    assert r.status_code == 200 and r.json()["sent"] is True
    mailer.fail_next = True
    frozen_clock.advance(61)
    r = client.post("/api/auth/resend-code", json={"email": "smtp-down@example.com"})
    assert r.status_code == 503 and r.json()["code"] == "mail_unavailable"


# --- Работодатель: компания по домену ---


def test_employer_company_by_domain(client, make_account, db_session):
    a = make_account("hr@severny-kod.ru", role="employer")
    b = make_account("cto@Severny-Kod.ru", role="employer")
    p1 = make_account("boss.one@gmail.com", role="employer")
    p2 = make_account("boss.two@gmail.com", role="employer")
    users = {acc.email.lower(): db_session.get(User, acc.user_id) for acc in (a, b, p1, p2)}
    corp = users["hr@severny-kod.ru"].company_id
    assert corp is not None and users["cto@severny-kod.ru"].company_id == corp
    company = db_session.get(Company, corp)
    assert company.email_domain == "severny-kod.ru" and company.personal_domain is False and company.name is None
    # Личная почта: своя компания на аккаунт с пометкой «личный email»
    c1, c2 = (db_session.get(Company, users[e].company_id) for e in ("boss.one@gmail.com", "boss.two@gmail.com"))
    assert c1.id != c2.id and c1.personal_domain and c2.personal_domain
    # У кандидата компании нет
    cand = make_account(role="candidate")
    assert db_session.get(User, cand.user_id).company_id is None


# --- Вход, refresh, выход ---


def test_login_refresh_rotation_logout(client, make_account, test_settings, frozen_clock, db_session):
    acc = make_account("login@example.com")
    cookie_name = test_settings.refresh_cookie_name

    r = client.post("/api/auth/login", json={"email": "LOGIN@example.com", "password": PASSWORD})
    assert r.status_code == 200, r.text
    first = r.cookies.get(cookie_name)
    assert first and r.json()["user"]["id"] == str(acc.user_id)

    frozen_clock.advance(5)
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200, r.text
    second = r.cookies.get(cookie_name)
    assert second and second != first
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200

    # Старый токен в пределах окна гонки вкладок → 409, семья не отзывается
    client.cookies.clear()
    r = client.post("/api/auth/refresh", headers={"Cookie": f"{cookie_name}={first}"})
    assert r.status_code == 409 and r.json()["code"] == "refresh_superseded"

    # Тот же старый токен позже → кража: вся семья отозвана, второй токен тоже мёртв
    frozen_clock.advance(test_settings.refresh_reuse_grace_s + 1)
    r = client.post("/api/auth/refresh", headers={"Cookie": f"{cookie_name}={first}"})
    assert r.status_code == 401 and r.json()["code"] == "refresh_reused"
    r = client.post("/api/auth/refresh", headers={"Cookie": f"{cookie_name}={second}"})
    assert r.status_code == 401 and r.json()["code"] == "refresh_invalid"
    assert db_session.scalar(select(AuditLog).where(AuditLog.action == "auth.refresh_reuse_detected")) is not None

    # Новый вход → выход: cookie удаляется, refresh больше не работает
    client.cookies.clear()
    r = client.post("/api/auth/login", json={"email": acc.email, "password": PASSWORD})
    third = r.cookies.get(cookie_name)
    r = client.post("/api/auth/logout")
    assert r.status_code == 204
    assert f'{cookie_name}=""' in r.headers["set-cookie"] and "Max-Age=0" in r.headers["set-cookie"]
    client.cookies.clear()
    r = client.post("/api/auth/refresh", headers={"Cookie": f"{cookie_name}={third}"})
    assert r.status_code == 401 and r.json()["code"] == "refresh_invalid"
    # Выход без cookie — идемпотентен
    assert client.post("/api/auth/logout").status_code == 204

    tokens = db_session.scalars(select(RefreshToken).where(RefreshToken.user_id == acc.user_id)).all()
    # Жива только сессия из make_account (другое «устройство»): выход и отзыв касаются своей цепочки
    alive = [t for t in tokens if t.revoked_at is None]
    assert len(alive) == 1 and alive[0].token_hash == hash_token(acc.refresh_cookie)
    assert {t.revoked_reason for t in tokens} >= {"rotated", "reuse_detected", "logout"}
    rotated = next(t for t in tokens if t.revoked_reason == "rotated")
    assert rotated.replaced_by_id is not None


def test_refresh_errors(client, make_account, test_settings, frozen_clock):
    assert client.post("/api/auth/refresh").json()["code"] == "refresh_missing"
    r = client.post("/api/auth/refresh", headers={"Cookie": f"{test_settings.refresh_cookie_name}=garbage-token"})
    assert r.status_code == 401
    acc = make_account()
    frozen_clock.advance(test_settings.refresh_token_ttl_s + 1)
    r = client.post("/api/auth/refresh", headers={"Cookie": f"{test_settings.refresh_cookie_name}={acc.refresh_cookie}"})
    assert r.status_code == 401 and r.json()["code"] == "refresh_expired"


def test_login_wrong_password_and_unknown_email_look_the_same(client, make_account):
    acc = make_account()
    bad_pw = client.post("/api/auth/login", json={"email": acc.email, "password": "Wrong-2026"})
    unknown = client.post("/api/auth/login", json={"email": "unknown@example.com", "password": "Wrong-2026"})
    assert bad_pw.status_code == unknown.status_code == 401
    assert bad_pw.json() == unknown.json() == {"code": "invalid_credentials", "message": "Неверный email или пароль", "details": None}
    assert bad_pw.headers["www-authenticate"] == "Bearer"


def test_login_rate_limit_per_email(client, make_account):
    acc = make_account()
    for _ in range(5):
        assert client.post("/api/auth/login", json={"email": acc.email, "password": "Wrong-2026"}).status_code == 401
    r = client.post("/api/auth/login", json={"email": acc.email, "password": PASSWORD})
    assert r.status_code == 429  # даже верный пароль: лимит проверяется до пароля
    assert r.json()["code"] == "rate_limited" and int(r.headers["retry-after"]) == 60
    assert r.json()["message"].startswith("Слишком много запросов")
    # Другой email с того же IP ещё может войти
    other = make_account()
    assert client.post("/api/auth/login", json={"email": other.email, "password": PASSWORD}).status_code == 200


def test_login_rate_limit_per_ip(client, app, make_account, test_settings):
    acc = make_account()
    cap = test_settings.rate("login_ip").capacity
    app.state.rate_limiter.reset()
    for i in range(cap):
        client.post("/api/auth/login", json={"email": f"x{i}@example.com", "password": "Wrong-2026"})
    r = client.post("/api/auth/login", json={"email": acc.email, "password": PASSWORD})
    assert r.status_code == 429


def test_verify_rate_limit_per_email(client, mailer):
    email = "brute@example.com"
    _register(client, email)
    statuses = [client.post("/api/auth/verify-email", json={"email": email, "code": "999999", "password": PASSWORD}).status_code for _ in range(11)]
    assert statuses[-1] == 429 and 429 not in statuses[:10]


# --- Access-токен ---


def test_access_token_checks(client, make_account, test_settings, db_session):
    acc = make_account()
    assert client.get("/api/auth/me").json()["code"] == "not_authenticated"
    r = client.get("/api/auth/me", headers={"Authorization": "Bearer abc.def.ghi"})
    assert r.status_code == 401 and r.json()["code"] == "token_invalid"
    expired, _ = create_access_token(acc.user_id, "candidate", test_settings, now=datetime.now(UTC) - timedelta(hours=1))
    r = client.get("/api/auth/me", headers={"Authorization": f"Bearer {expired}"})
    assert r.status_code == 401 and r.json()["code"] == "token_expired"
    ghost, _ = create_access_token(uuid.uuid4(), "admin", test_settings)
    assert client.get("/api/auth/me", headers={"Authorization": f"Bearer {ghost}"}).status_code == 401
    # Блокировка действует сразу, не дожидаясь истечения токена
    db_session.get(User, acc.user_id).status = "blocked"
    db_session.flush()
    assert client.get("/api/auth/me", headers=acc.headers).status_code == 401


def test_audit_log_written(client, make_account, db_session):
    acc = make_account()
    client.post("/api/auth/login", json={"email": acc.email, "password": "Wrong-2026"})
    client.post("/api/auth/login", json={"email": acc.email, "password": PASSWORD})
    actions = db_session.scalars(select(AuditLog.action).where(AuditLog.actor_user_id == acc.user_id)).all()
    assert {"auth.register", "auth.email_verified", "auth.login_failed", "auth.login"} <= set(actions)
    # В журнале нет паролей и кодов
    payloads = db_session.scalars(select(AuditLog.payload)).all()
    assert all(PASSWORD not in str(p) for p in payloads)
