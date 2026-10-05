"""Криптография аутентификации: argon2id для паролей, JWT access, непрозрачные refresh-токены, коды email.

Схема сессии (концепция §6.5, аудит R-73):
- access — короткий JWT (15 мин) в теле ответа; фронтенд держит его в памяти и шлёт в Authorization: Bearer;
- refresh — случайные 256 бит в httpOnly-cookie (путь /api/auth). В БД хранится только SHA-256.
  Каждый /refresh выдаёт новый токен той же «семьи», старый помечается rotated. Повторное
  предъявление уже ротированного токена = кража → отзываем всю семью (reuse detection).

Случайность здесь криптографическая (`secrets`), а не воспроизводимая по seed: правило детерминизма
CLAUDE.md относится к алгоритмам (CAT, генераторы), не к секретам.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from app.core.settings import Settings

# --- Пароли: argon2id ---


@lru_cache(maxsize=8)
def _hasher(time_cost: int, memory_kib: int, parallelism: int) -> PasswordHasher:
    return PasswordHasher(time_cost=time_cost, memory_cost=memory_kib, parallelism=parallelism, type=Type.ID)


@lru_cache(maxsize=4)
def _hash_slots(limit: int) -> threading.BoundedSemaphore:
    return threading.BoundedSemaphore(max(1, limit))


@contextmanager
def _argon2_slot(settings: Settings) -> Iterator[None]:
    """Не больше N хеширований одновременно. Каждое занимает argon2_memory_kib (64 МБ): синхронные
    маршруты идут в пуле из 40 потоков, и разрешённая лимитом пачка входов с одного IP (30 запросов)
    без этого ограничения съела бы ~2 ГБ — больше лимита памяти контейнера (768 МБ). Лишние ждут."""
    slots = _hash_slots(settings.argon2_max_concurrent)
    with slots:
        yield


def password_hasher(settings: Settings) -> PasswordHasher:
    return _hasher(settings.argon2_time_cost, settings.argon2_memory_kib, settings.argon2_parallelism)


def hash_password(password: str, settings: Settings) -> str:
    with _argon2_slot(settings):
        return password_hasher(settings).hash(password)


def verify_password(password_hash: str | None, password: str, settings: Settings) -> bool:
    """Проверка пароля. Для отсутствующего хеша всё равно тратим время на хеширование:
    иначе по времени ответа можно узнать, зарегистрирован ли email."""
    ph = password_hasher(settings)
    with _argon2_slot(settings):
        if not password_hash:
            ph.hash(password)
            return False
        try:
            return ph.verify(password_hash, password)
        except (VerifyMismatchError, VerificationError, InvalidHashError, UnicodeError):
            return False


def password_needs_rehash(password_hash: str, settings: Settings) -> bool:
    try:
        return password_hasher(settings).check_needs_rehash(password_hash)
    except (InvalidHashError, UnicodeError, ValueError):
        return True


def password_problems(password: str, settings: Settings) -> list[str]:
    """Требования к паролю (показываются на экране О2 сразу). Пустой список — пароль подходит."""
    problems = []
    if len(password) < settings.password_min_length:
        problems.append(f"Минимум {settings.password_min_length} символов")
    if len(password) > settings.password_max_length:
        problems.append(f"Максимум {settings.password_max_length} символов")
    if not any(ch.isalpha() for ch in password):
        problems.append("Хотя бы одна буква")
    if not any(ch.isdigit() for ch in password):
        problems.append("Хотя бы одна цифра")
    return problems


# --- Access JWT ---

ACCESS_TOKEN_TYPE = "access"


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    role: str
    jti: str
    issued_at: datetime
    expires_at: datetime


class TokenError(Exception):
    """Невалидный/просроченный токен; code — машинная причина."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def create_access_token(user_id: uuid.UUID, role: str, settings: Settings, now: datetime | None = None) -> tuple[str, datetime]:
    """Подписать access-JWT. Время — настенное (UTC), не Clock: см. docstring app/core/clock.py."""
    issued = now or datetime.now(UTC)
    expires = issued + timedelta(seconds=settings.access_token_ttl_s)
    payload = {
        "sub": str(user_id),
        "role": role,
        "type": ACCESS_TOKEN_TYPE,
        "iss": settings.jwt_issuer,
        "iat": int(issued.timestamp()),
        "exp": int(expires.timestamp()),
        "jti": uuid.uuid4().hex,
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=settings.jwt_algorithm)
    return token, expires


def decode_access_token(token: str, settings: Settings) -> AccessClaims:
    try:
        data: dict[str, Any] = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[settings.jwt_algorithm],  # явный список: защита от alg=none / подмены алгоритма
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "type", "iss"]},
        )
    except jwt.ExpiredSignatureError as e:
        raise TokenError("token_expired") from e
    except jwt.PyJWTError as e:
        raise TokenError("token_invalid") from e
    if data.get("type") != ACCESS_TOKEN_TYPE:
        raise TokenError("token_invalid")
    try:
        user_id = uuid.UUID(str(data["sub"]))
    except ValueError as e:
        raise TokenError("token_invalid") from e
    return AccessClaims(
        user_id=user_id,
        role=str(data.get("role", "")),
        jti=str(data.get("jti", "")),
        issued_at=datetime.fromtimestamp(int(data["iat"]), UTC),
        expires_at=datetime.fromtimestamp(int(data["exp"]), UTC),
    )


# --- Refresh: непрозрачный токен, в БД — только хеш ---


def new_refresh_token() -> tuple[str, str]:
    """(сырой токен для cookie, sha256-хеш для БД)."""
    raw = secrets.token_urlsafe(32)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


# --- Коды подтверждения email ---


def generate_email_code(digits: int = 6) -> str:
    """6-значный код (концепция §3.1 шаг 2). Ведущие нули допустимы."""
    return f"{secrets.randbelow(10**digits):0{digits}d}"


def hash_email_code(code: str, user_id: uuid.UUID, settings: Settings) -> str:
    """HMAC(app_secret, user_id|code): утечка таблицы кодов не раскрывает сами коды
    (перебор 10^6 вариантов без секрета не сходится)."""
    msg = f"{user_id}|{code}".encode()
    return hmac.new(settings.app_secret.get_secret_value().encode(), msg, hashlib.sha256).hexdigest()


def codes_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a, b)
