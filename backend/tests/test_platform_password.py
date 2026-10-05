"""Платформа: подтверждение email требует пароль из регистрации (защита от подмены пароля чужой
перерегистрацией) и восстановление пароля по коду из письма (экран О4)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import AuditLog, User

PASSWORD = "Parol-2026"


def _register(client, email, password=PASSWORD, role="candidate"):
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": password, "role": role, "consent_personal_data": True},
    )


def _verify(client, email, code, password=PASSWORD):
    return client.post("/api/auth/verify-email", json={"email": email, "code": code, "password": password})


def _user(db, email) -> User:
    return db.scalar(select(User).where(User.email == email.lower()))


def _wrong(code: str) -> str:
    return "000000" if code != "000000" else "111111"


# --- Подтверждение: код + пароль ---


def test_verify_requires_registration_password(client, read_code, db_session):
    email = "pw-check@example.com"
    _register(client, email)
    code = read_code(email)
    r = _verify(client, email, code, password="Chuzhoi-2026")
    assert r.status_code == 400 and r.json()["code"] == "password_mismatch"
    assert r.json()["details"] == {"attempts_left": 4}
    assert r.json()["message"].startswith("Пароль не совпадает с указанным при регистрации")
    assert not _user(db_session, email).email_verified
    # Без пароля — 422 с русским сообщением
    r = client.post("/api/auth/verify-email", json={"email": email, "code": code})
    assert r.status_code == 422
    assert {"field": "password", "message": "Обязательное поле", "type": "missing"} in r.json()["details"]
    # Неверный код при верном пароле — тратится попытка того же кода
    r = _verify(client, email, _wrong(code))
    assert r.status_code == 400 and r.json()["code"] == "code_invalid" and r.json()["details"] == {"attempts_left": 3}
    r = _verify(client, email, code)
    assert r.status_code == 200, r.text


def test_wrong_password_attempts_burn_code(client, read_code):
    email = "pw-burn@example.com"
    _register(client, email)
    code = read_code(email)
    for _ in range(4):
        assert _verify(client, email, code, password="Chuzhoi-2026").json()["code"] == "password_mismatch"
    r = _verify(client, email, code, password="Chuzhoi-2026")
    assert r.status_code == 400 and r.json()["code"] == "code_attempts_exceeded"
    assert _verify(client, email, code).json()["code"] == "code_not_found"


def test_reregistration_cannot_hijack_pending_account(client, read_code, frozen_clock):
    """Владелец ящика зарегистрировался; кто-то перерегистрировал его email со своим паролем.
    Код из письма без чужого пароля аккаунт не активирует, а у злоумышленника нет кода."""
    email = "victim@example.com"
    assert _register(client, email).status_code == 201
    frozen_clock.advance(61)
    assert _register(client, email, password="Attacker-2026", role="employer").status_code == 201
    # Владелец вводит последний код из своего ящика и СВОЙ пароль → отказ, аккаунт не активирован
    r = _verify(client, email, read_code(email))
    assert r.status_code == 400 and r.json()["code"] == "password_mismatch"
    # Злоумышленник с паролем, но без кода, войти не может
    r = client.post("/api/auth/login", json={"email": email, "password": "Attacker-2026"})
    assert r.status_code == 403 and r.json()["code"] == "email_not_verified"
    # Владелец регистрируется заново со своим паролем и подтверждает
    frozen_clock.advance(61)
    assert _register(client, email).status_code == 201
    r = _verify(client, email, read_code(email))
    assert r.status_code == 200 and r.json()["user"]["role"] == "candidate"
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": email, "password": "Attacker-2026"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200


def test_verify_sets_password_for_account_without_one(client, mailer, read_code, db_session, test_settings):
    """Аккаунт без пароля (создан входом через ФСП ID, DECISIONS п. 12) получает пароль при подтверждении."""
    from app.core import auth_service

    user = User(email="fsp-first@example.com", email_domain="example.com", role="candidate", status="pending_email")
    db_session.add(user)
    db_session.flush()
    auth_service.issue_verification_code(db_session, user, test_settings, mailer, enforce_limits=False)
    code = read_code(user.email)
    r = _verify(client, user.email, code, password="short")
    assert r.status_code == 422 and any(d["field"] == "password" for d in r.json()["details"])
    r = _verify(client, user.email, code, password="Novyi-2026")
    assert r.status_code == 200, r.text
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": user.email, "password": "Novyi-2026"}).status_code == 200


def test_blocked_pending_account_cannot_verify(client, read_code, db_session):
    email = "blocked-pending@example.com"
    _register(client, email)
    _user(db_session, email).status = "blocked"
    db_session.flush()
    r = _verify(client, email, read_code(email))
    assert r.status_code == 403 and r.json()["code"] == "account_blocked"
    assert "set-cookie" not in r.headers


def test_code_format_error_in_russian(client):
    r = _verify(client, "a@example.com", "12ab")
    assert r.status_code == 422
    assert {"field": "code", "message": "Код — 6 цифр из письма", "type": "value_error"} in r.json()["details"]
    # Пробелы по краям (копирование из письма) допустимы
    r = _verify(client, "ghost2@example.com", " 123456 ")
    assert r.status_code == 400 and r.json()["code"] == "code_invalid"


# --- Восстановление пароля (экран О4) ---


def _reset_request(client, email):
    return client.post("/api/auth/password-reset/request", json={"email": email})


def _reset_confirm(client, email, code, new_password="Novyi-Parol-1"):
    return client.post(
        "/api/auth/password-reset/confirm", json={"email": email, "code": code, "new_password": new_password}
    )


def test_password_reset_flow(client, mailer, read_code, make_account, db_session, test_settings):
    cookie = test_settings.refresh_cookie_name
    acc = make_account("forgot@example.com")
    r = client.post("/api/auth/login", json={"email": acc.email, "password": PASSWORD})
    old_refresh = r.cookies.get(cookie)
    client.cookies.clear()

    r = _reset_request(client, "Forgot@Example.com")
    assert r.status_code == 200 and r.json() == {"code_ttl_s": 900, "resend_available_in_s": 60}
    mail = mailer.last_to("forgot@example.com")
    assert "восстановления пароля" in mail.subject
    assert "/reset-password?email=forgot%40example.com&code=" in mail.text
    code = read_code(acc.email)

    r = _reset_confirm(client, acc.email, _wrong(code))
    assert r.status_code == 400 and r.json()["code"] == "code_invalid" and r.json()["details"] == {"attempts_left": 4}
    # Слабый пароль — 422, попытка не сгорает
    r = _reset_confirm(client, acc.email, code, new_password="123")
    assert r.status_code == 422 and any(d["field"] == "password" for d in r.json()["details"])
    r = _reset_confirm(client, acc.email, code)
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email"] == acc.email and r.cookies.get(cookie)
    client.cookies.clear()

    # Все прежние сессии завершены, старый пароль не работает, новый — работает
    for raw in (old_refresh, acc.refresh_cookie):
        assert client.post("/api/auth/refresh", headers={"Cookie": f"{cookie}={raw}"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": acc.email, "password": PASSWORD}).status_code == 401
    assert client.post("/api/auth/login", json={"email": acc.email, "password": "Novyi-Parol-1"}).status_code == 200
    # Код одноразовый
    assert _reset_confirm(client, acc.email, code).json()["code"] == "code_not_found"
    actions = set(db_session.scalars(select(AuditLog.action).where(AuditLog.actor_user_id == acc.user_id)))
    assert {"auth.password_reset_requested", "auth.password_reset_failed", "auth.password_reset"} <= actions


def test_password_reset_code_is_not_a_verification_code(client, read_code):
    """Цели кодов разделены: код восстановления не подтверждает регистрацию через verify-email и наоборот."""
    email = "purposes@example.com"
    _register(client, email)
    verify_code = read_code(email)
    _reset_request(client, email)
    reset_code = read_code(email)
    if verify_code == reset_code:  # вероятность 1e-6
        pytest.skip("коды совпали случайно")
    assert _reset_confirm(client, email, verify_code).json()["code"] == "code_invalid"
    r = _verify(client, email, reset_code)
    assert r.status_code == 400 and r.json()["code"] == "code_invalid"
    # Код подтверждения после этого по-прежнему действует (у целей отдельные коды)
    assert _verify(client, email, verify_code).status_code == 200


def test_password_reset_does_not_reveal_unknown_email(client, mailer):
    r = _reset_request(client, "nobody-here@example.com")
    assert r.status_code == 200 and r.json() == {"code_ttl_s": 900, "resend_available_in_s": 60}
    assert mailer.last_to("nobody-here@example.com") is None
    r = _reset_confirm(client, "nobody-here@example.com", "123456")
    assert r.status_code == 400 and r.json()["code"] == "code_invalid"


def test_password_reset_limits_and_expiry(client, make_account, read_code, frozen_clock):
    acc = make_account()
    assert _reset_request(client, acc.email).status_code == 200
    r = _reset_request(client, acc.email)
    assert r.status_code == 429 and r.json()["code"] == "resend_too_soon"
    frozen_clock.advance(61)
    assert _reset_request(client, acc.email).status_code == 200
    code = read_code(acc.email)
    frozen_clock.advance(15 * 60)
    r = _reset_confirm(client, acc.email, code)
    assert r.status_code == 400 and r.json()["code"] == "code_expired"


def test_password_reset_mail_failure_is_503(client, make_account, mailer):
    acc = make_account()
    mailer.fail_next = True
    r = _reset_request(client, acc.email)
    assert r.status_code == 503 and r.json()["code"] == "mail_unavailable"
    # Неотправленное письмо не съедает лимит
    assert _reset_request(client, acc.email).status_code == 200


def test_password_reset_verifies_pending_account(client, read_code, db_session):
    """Сброс по коду из письма = доказательство владения ящиком: «заготовка» аккаунта с чужим паролем
    возвращается владельцу и становится активной."""
    email = "squatted@severny-kod.ru"
    assert _register(client, email, password="Squatter-2026", role="employer").status_code == 201
    assert _reset_request(client, email).status_code == 200  # у восстановления свой лимит отправок
    r = _reset_confirm(client, email, read_code(email), new_password="Vladelec-2026")
    assert r.status_code == 200, r.text
    assert r.json()["user"]["email_verified"] is True and r.json()["user"]["status"] == "active"
    db_session.expire_all()
    assert _user(db_session, email).company_id is not None  # роль развёрнута, как при обычном подтверждении
    r = _verify(client, email, "123456", password="Vladelec-2026")
    assert r.status_code == 409 and r.json()["code"] == "already_verified"
    client.cookies.clear()
    assert client.post("/api/auth/login", json={"email": email, "password": "Squatter-2026"}).status_code == 401
    assert client.post("/api/auth/login", json={"email": email, "password": "Vladelec-2026"}).status_code == 200


def test_password_reset_blocked_account(client, make_account, mailer, db_session):
    acc = make_account()
    db_session.get(User, acc.user_id).status = "blocked"
    db_session.flush()
    mailer.clear()
    assert _reset_request(client, acc.email).status_code == 200
    assert mailer.last_to(acc.email) is None
    r = _reset_confirm(client, acc.email, "123456")
    assert r.status_code == 403 and r.json()["code"] == "account_blocked"
