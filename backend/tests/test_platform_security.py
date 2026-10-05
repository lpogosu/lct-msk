"""Платформа: модульные тесты без БД — argon2id, JWT, коды, токен-корзина, Clock, настройки, ошибки, письма."""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime, timedelta
from email import message_from_bytes
from email.header import decode_header, make_header

import jwt
import pytest
from pydantic import ValidationError

from app.core.clock import Clock, relative_ru
from app.core.errors import ApiError, too_many_requests, validation_details
from app.core.rate_limit import RateLimiter
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    generate_email_code,
    hash_email_code,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    password_problems,
    verify_password,
)
from app.core.settings import RateRule, Settings
from app.mail.sender import MemoryMailer, OutgoingMail, SmtpMailer, build_message
from app.mail.templates import (
    password_reset_link,
    render_password_reset_code,
    render_verification_code,
    verification_link,
)


@pytest.fixture
def s() -> Settings:
    return Settings(_env_file=None, env="test", argon2_time_cost=1, argon2_memory_kib=8192, argon2_parallelism=1)


# --- Пароли ---


def test_password_hash_is_argon2id_and_verifies(s):
    h = hash_password("Parol-2026", s)
    assert h.startswith("$argon2id$")
    assert "Parol-2026" not in h
    assert verify_password(h, "Parol-2026", s)
    assert not verify_password(h, "parol-2026", s)
    assert not verify_password(None, "Parol-2026", s)  # нет хеша — всё равно False, без исключения
    assert not verify_password("не-хеш", "Parol-2026", s)


def test_password_rehash_when_params_change(s):
    h = hash_password("Parol-2026", s)
    assert not password_needs_rehash(h, s)
    stronger = s.model_copy(update={"argon2_time_cost": 2})
    assert password_needs_rehash(h, stronger)


def test_argon2_concurrency_is_bounded(s, monkeypatch):
    """Одновременно хешируется не больше argon2_max_concurrent паролей (память контейнера)."""
    import threading
    import time

    from app.core import security

    active, peak, lock = [0], [0], threading.Lock()

    class SlowHasher:
        def hash(self, password):
            with lock:
                active[0] += 1
                peak[0] = max(peak[0], active[0])
            time.sleep(0.05)
            with lock:
                active[0] -= 1
            return "$argon2id$fake"

        def verify(self, h, password):
            self.hash(password)
            return True

    monkeypatch.setattr(security, "password_hasher", lambda settings: SlowHasher())
    limited = s.model_copy(update={"argon2_max_concurrent": 2})
    threads = [threading.Thread(target=security.hash_password, args=("Parol-2026", limited)) for _ in range(4)]
    threads += [threading.Thread(target=security.verify_password, args=("h", "Parol-2026", limited)) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 2


def test_password_policy_messages_in_russian(s):
    assert password_problems("Parol-2026", s) == []
    problems = password_problems("abc", s)
    assert "Минимум 8 символов" in problems and "Хотя бы одна цифра" in problems
    assert "Хотя бы одна буква" in password_problems("12345678", s)


# --- JWT ---


def test_access_token_roundtrip(s):
    uid = uuid.uuid4()
    token, exp = create_access_token(uid, "employer", s)
    claims = decode_access_token(token, s)
    assert claims.user_id == uid and claims.role == "employer"
    assert abs((claims.expires_at - exp).total_seconds()) < 1
    assert (exp - datetime.now(UTC)).total_seconds() == pytest.approx(s.access_token_ttl_s, abs=5)


def test_access_token_rejects_tampering_and_wrong_alg(s):
    token, _ = create_access_token(uuid.uuid4(), "candidate", s)
    other = s.model_copy(update={"jwt_secret": s.jwt_secret.__class__("совсем-другой-секрет-для-подписи-jwt")})
    with pytest.raises(TokenError) as e:
        decode_access_token(token, other)
    assert e.value.code == "token_invalid"
    # alg=none и чужой тип токена не принимаются
    unsigned = jwt.encode({"sub": str(uuid.uuid4()), "type": "access", "iss": s.jwt_issuer, "iat": 1, "exp": 4102444800}, None, algorithm="none")
    with pytest.raises(TokenError):
        decode_access_token(unsigned, s)
    refresh_like = jwt.encode(
        {"sub": str(uuid.uuid4()), "type": "refresh", "iss": s.jwt_issuer, "iat": 1, "exp": 4102444800},
        s.jwt_secret.get_secret_value(),
        algorithm="HS256",
    )
    with pytest.raises(TokenError):
        decode_access_token(refresh_like, s)


def test_access_token_expired(s):
    token, _ = create_access_token(uuid.uuid4(), "candidate", s, now=datetime.now(UTC) - timedelta(hours=1))
    with pytest.raises(TokenError) as e:
        decode_access_token(token, s)
    assert e.value.code == "token_expired"


# --- Refresh и коды ---


def test_refresh_token_stored_as_hash_only():
    raw, h = new_refresh_token()
    assert len(raw) >= 40 and h == hash_token(raw) and raw not in h
    assert new_refresh_token()[0] != raw


def test_email_code_format_and_hmac(s):
    codes = {generate_email_code() for _ in range(200)}
    assert all(len(c) == 6 and c.isdigit() for c in codes)
    assert len(codes) > 190  # криптографический генератор, без повторов на малой выборке
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    assert hash_email_code("123456", u1, s) == hash_email_code("123456", u1, s)
    assert hash_email_code("123456", u1, s) != hash_email_code("123456", u2, s)
    other = s.model_copy(update={"app_secret": s.app_secret.__class__("иной")})
    assert hash_email_code("123456", u1, s) != hash_email_code("123456", u1, other)


# --- Токен-корзина ---


class FakeTime:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


def test_token_bucket_capacity_and_refill():
    t = FakeTime()
    rl = RateLimiter(time_fn=t)
    rule = RateRule(capacity=5, period_s=300)  # 1 токен в 60 с
    assert all(rl.hit("login_email", "a@b.ru", rule).allowed for _ in range(5))
    denied = rl.hit("login_email", "a@b.ru", rule)
    assert not denied.allowed and denied.retry_after_s == pytest.approx(60)
    assert rl.hit("login_email", "other@b.ru", rule).allowed  # ключи независимы
    assert rl.hit("login_ip", "a@b.ru", rule).allowed  # области независимы
    t.t += 59
    assert not rl.hit("login_email", "a@b.ru", rule).allowed
    t.t += 2
    assert rl.hit("login_email", "a@b.ru", rule).allowed
    rl.reset()
    assert rl.hit("login_email", "a@b.ru", rule).remaining == 4


def test_rate_limiter_check_raises_429_with_retry_after():
    rl = RateLimiter(time_fn=FakeTime())
    rule = RateRule(1, 30)
    rl.check("x", "k", rule)
    with pytest.raises(ApiError) as e:
        rl.check("x", "k", rule)
    assert e.value.status_code == 429 and e.value.code == "rate_limited"
    assert e.value.headers == {"Retry-After": "30"}
    assert e.value.details == {"retry_after_s": 30}
    assert "Повторите через 30 с" in e.value.message


def test_too_many_requests_rounds_up():
    assert too_many_requests(0.2).details == {"retry_after_s": 1}
    assert too_many_requests(59.01).details == {"retry_after_s": 60}


# --- Clock и относительные даты (DECISIONS п. 9) ---


def test_clock_freeze_advance_offset():
    c = Clock()
    at = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    c.freeze(at)
    assert c.now() == at
    c.advance(3600)
    assert c.now() == at + timedelta(hours=1)
    c.reset()
    c.set_offset(86400)
    assert (c.now() - datetime.now(UTC)).total_seconds() == pytest.approx(86400, abs=5)
    with pytest.raises(ValueError):
        c.freeze(datetime(2026, 1, 1))  # без часового пояса


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (timedelta(seconds=10), "меньше минуты"),
        (timedelta(seconds=-10), "только что"),
        (timedelta(minutes=5), "через 5 минут"),
        (timedelta(minutes=-1, seconds=-5), "1 минуту назад"),
        (timedelta(hours=2), "через 2 часа"),
        (timedelta(hours=-11), "11 часов назад"),
        (timedelta(days=1, hours=3), "завтра"),
        (timedelta(days=-1), "вчера"),
        (timedelta(days=3), "через 3 дня"),
        (timedelta(days=-21), "21 день назад"),
        (timedelta(days=12), "через 12 дней"),
    ],
)
def test_relative_ru(delta, expected):
    now = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)
    assert relative_ru(now + delta, now) == expected


# --- Настройки ---


def test_settings_parse_rates_and_csv(monkeypatch):
    monkeypatch.setenv("VZ_CORS_ORIGINS", "http://localhost:5173, http://127.0.0.1:5173")
    monkeypatch.setenv("VZ_RL_LOGIN_EMAIL", "3/60")
    st = Settings(_env_file=None)
    assert st.cors_origins == ["http://localhost:5173", "http://127.0.0.1:5173"]
    assert st.rate("login_email") == RateRule(3, 60)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, rl_login_ip="много")


def test_personal_domains_single_source_with_contact_quotas():
    """DECISIONS п. 8: «личный email» при регистрации и квота на аккаунт в модуле контакта — один список."""
    from app.contact.quotas import is_personal_email
    from app.core.configs import load_config
    from app.core.domains import is_personal_domain, personal_email_domains

    listed = set(load_config("contact")["quotas"]["personal_email_domains"])
    assert personal_email_domains() == {d.lower() for d in listed}
    for domain in ("gmail.com", "mail.ru", "yandex.ru", "bk.ru", "list.ru", "inbox.ru", "icloud.com", "outlook.com", "hotmail.com"):
        assert is_personal_domain(domain) and is_personal_domain(domain.upper())
        assert is_personal_email(f"hr@{domain}")
    for domain in ("severny-kod.ru", "example.com"):
        assert not is_personal_domain(domain) and not is_personal_email(f"hr@{domain}")


def test_settings_prod_requires_real_secrets():
    with pytest.raises(ValidationError, match="VZ_JWT_SECRET"):
        Settings(_env_file=None, env="prod", cookie_secure=True)
    ok = Settings(
        _env_file=None,
        env="prod",
        cookie_secure=True,
        jwt_secret="s1-" + "x" * 30,
        app_secret="s2-" + "x" * 30,
        pseudonym_secret="s3-" + "x" * 30,
    )
    assert ok.env == "prod"
    with pytest.raises(ValidationError, match="не короче 32"):
        Settings(_env_file=None, env="prod", cookie_secure=True, jwt_secret="short", app_secret="s2-" + "x" * 30, pseudonym_secret="s3-" + "x" * 30)
    with pytest.raises(ValidationError, match="Secure"):
        Settings(_env_file=None, cookie_samesite="none", cookie_secure=False)


# --- Ошибки валидации по-русски ---


def test_validation_details_translated():
    errors = [
        {"type": "missing", "loc": ("body", "email"), "msg": "Field required"},
        {"type": "string_too_short", "loc": ("body", "password"), "msg": "...", "ctx": {"min_length": 1}},
        {"type": "value_error", "loc": ("body", "email"), "msg": "value is not a valid email address: x", "ctx": {"error": "value is not a valid email address"}},
        {"type": "value_error", "loc": ("body", "consent"), "msg": "Value error, Нужно согласие", "ctx": {"error": ValueError("Нужно согласие")}},
        {"type": "something_new", "loc": ("query", "x"), "msg": "?"},
        {"type": "string_too_long", "loc": ("body", "comment"), "msg": "...", "ctx": {"max_length": 300}},
        {"type": "too_short", "loc": ("body", "items"), "msg": "...", "ctx": {"min_length": 2}},
        {"type": "literal_error", "loc": ("body", "role"), "msg": "...", "ctx": {"expected": "'candidate' or 'employer'"}},
        {"type": "enum", "loc": ("body", "mode"), "msg": "...", "ctx": {"expected": "'a', 'b' or 'c'"}},
        # Английский текст сторонней библиотеки пользователю не показываем
        {"type": "value_error", "loc": ("body", "url"), "msg": "Value error, invalid host", "ctx": {"error": ValueError("invalid host")}},
    ]
    out = validation_details(errors)
    assert [d["field"] for d in out] == ["email", "password", "email", "consent", "x", "comment", "items", "role", "mode", "url"]
    assert [d["message"] for d in out] == [
        "Обязательное поле",
        "Минимум 1 символ",
        "Некорректный адрес электронной почты",
        "Нужно согласие",
        "Некорректное значение",
        "Максимум 300 символов",
        "Минимум 2 элемента",
        "Допустимые значения: 'candidate', 'employer'",
        "Допустимые значения: 'a', 'b', 'c'",
        "Некорректное значение",
    ]
    assert not any(" or " in d["message"] for d in out)


# --- Письма ---


def test_verification_mail_template():
    link = verification_link("http://localhost:5173/", "maria+test@example.com", "042917")
    assert link == "http://localhost:5173/verify-email?email=maria%2Btest%40example.com&code=042917"
    m = render_verification_code("042917", link, 900)
    assert "042917" in m.subject and "042917" in m.text
    assert "15 минут" in m.text and "15 минут" in m.html
    assert link in m.text
    assert "&amp;code=042917" in m.html  # ссылка экранирована в HTML
    assert 'lang="ru"' in m.html and "#402FFF" in m.html
    assert render_verification_code("000001", link, 60).text.count("1 минуту") == 1


def test_password_reset_mail_template():
    link = password_reset_link("http://localhost:5173", "maria@example.com", "000123")
    assert link == "http://localhost:5173/reset-password?email=maria%40example.com&code=000123"
    m = render_password_reset_code("000123", link, 900)
    assert "000123" in m.subject and "восстановления пароля" in m.subject
    assert "000123" in m.text and link in m.text and "15 минут" in m.text
    assert "Задать новый пароль" in m.html and "&amp;code=000123" in m.html
    assert "пароль останется прежним" in m.text
    # Письмо подтверждения не изменилось по смыслу
    v = render_verification_code("000123", "http://x/verify-email", 900)
    assert "Подтвердить email" in v.html and "аккаунт не будет активирован" in v.text


def test_build_message_multipart_utf8():
    rendered = render_verification_code("123456", "http://x/verify-email", 900)
    msg = build_message(OutgoingMail.from_rendered("a@example.com", rendered), "Взаимно <no-reply@vzaimno.local>")
    parsed = message_from_bytes(msg.as_bytes())
    assert parsed.is_multipart()
    assert [p.get_content_type() for p in parsed.walk() if not p.is_multipart()] == ["text/plain", "text/html"]
    assert str(make_header(decode_header(parsed["Subject"]))) == rendered.subject
    assert "no-reply@vzaimno.local" in parsed["From"]


def test_memory_mailer_failure_mode():
    mm = MemoryMailer()
    mm.fail_next = True
    with pytest.raises(Exception, match="SMTP"):
        mm.send(OutgoingMail("a@example.com", "t", "x"))
    mm.send(OutgoingMail("a@example.com", "t2", "x"))
    assert mm.last_to("a@example.com").subject == "t2"


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


@pytest.mark.skipif(not _port_open("localhost", 1025), reason="Mailpit не запущен (docker compose up -d mailpit)")
def test_smtp_mailer_delivers_to_mailpit():
    """Интеграция с Mailpit: письмо уходит по SMTP и видно через его API."""
    import httpx

    s = Settings(_env_file=None, smtp_host="localhost", smtp_port=1025, smtp_security="none")
    marker = uuid.uuid4().hex[:8]
    to = f"smtp-{marker}@example.com"
    SmtpMailer(s).send(OutgoingMail.from_rendered(to, render_verification_code("314159", "http://x/verify-email", 900)))
    try:
        found = httpx.get("http://localhost:8025/api/v1/search", params={"query": f"to:{to}"}, timeout=3).json()
    except httpx.HTTPError:
        pytest.skip("API Mailpit недоступно")
    assert found["messages_count"] >= 1
    assert "314159" in found["messages"][0]["Subject"]
