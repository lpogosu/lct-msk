"""Создание демо-аккаунтов и вымышленных имён; пароли выводятся из VZ_APP_SECRET.

Аккаунты создаются прямо в БД (ORM), минуя код из письма: seed не должен слать сотни писем. Всё остальное — пароль
(argon2id), согласие на обработку ПДн, профиль кандидата, компания — как после настоящего подтверждения email
(app.core.auth_service._activate_email). Реальных ФИО нет: фамилии — из IT-терминов (config/demo.yaml → names).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import random
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.consents import grant_consent, has_active_consent
from app.core.domains import email_domain, normalize_email
from app.core.security import create_access_token, hash_password
from app.core.settings import Settings
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.enums import ConsentPurpose, UserRole, UserStatus
from app.models.users import User
from app.services.demo.cfg import section
from app.services.demo.models import DemoPersona

_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
}


_NAMESPACE = uuid.UUID("0c0a5a7e-5eed-4d3e-9a55-6d656d6f2026")


def stable_id(kind: str, key: str) -> uuid.UUID:
    """Постоянный id демо-сущности (компания, HR): пересборка мира даёт те же адреса и ссылки."""
    return uuid.uuid5(_NAMESPACE, f"{kind}:{key}")


def translit(text: str) -> str:
    return "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())


def password_for(settings: Settings, key: str) -> str:
    """Пароль демо-аккаунта: префикс + base32 от HMAC(app_secret, ключ). Одна и та же пара (секрет, ключ) всегда
    даёт один пароль — файл с учётными данными можно восстановить, ничего не храня."""
    cfg = section("accounts")
    digest = hmac.new(settings.app_secret.get_secret_value().encode(), f"demo-account:{key}".encode(), hashlib.sha256).digest()
    body = base64.b32encode(digest).decode().rstrip("=")[: int(cfg["password_length"])]
    if not any(c.isdigit() for c in body):  # требование политики: хотя бы одна цифра
        body = body[:-1] + str(digest[0] % 8 + 2)
    return f"{cfg['password_prefix']}{body}"


def fictional_name(rng: random.Random, female: bool | None = None) -> tuple[str, bool]:
    """(«Имя Фамилия», женское ли). Фамилии вымышленные: терминология разработки."""
    names = section("names")
    female = rng.random() < 0.5 if female is None else female
    first = rng.choice(names["first_f" if female else "first_m"])
    last = rng.choice(names["surnames_f" if female else "surnames_m"])
    return f"{first} {last}", female


# Сброс демо удаляет аккаунты персон и пересоздаёт их: id и хеш пароля сохраняются, чтобы вход оператора не слетал
# (токен живёт 15 минут) и не тратилось время на argon2. Заполняет только app.services.demo.world.
REUSE: dict[str, tuple[uuid.UUID, str | None]] = {}


@dataclass
class Credential:
    key: str
    role: str
    email: str
    title: str
    password: str | None
    where: str  # где использовать (подсказка в GET /api/demo/accounts)


def ensure_user(
    db: Session,
    settings: Settings,
    *,
    email: str,
    role: str,
    password: str | None,
    company: Company | None = None,
    password_hash: str | None = None,
    user_id: uuid.UUID | None = None,
    created_at: Any = None,
) -> User:
    """Активный подтверждённый аккаунт. Существующий (по email) возвращается как есть: seed идемпотентен."""
    email = normalize_email(email)
    user = db.scalar(select(User).where(User.email == email))
    if user is not None:
        return user
    now = created_at or clock.now()
    kwargs: dict[str, Any] = {}
    if email in REUSE:
        user_id, password_hash = REUSE[email]
    if user_id is not None:
        kwargs["id"] = user_id
    if password_hash is None and password is not None:
        password_hash = hash_password(password, settings)
    user = User(
        email=email,
        email_domain=email_domain(email),
        role=role,
        status=UserStatus.active.value,
        email_verified=True,
        email_verified_at=now,
        password_hash=password_hash,
        company_id=company.id if company is not None else None,
        is_demo=True,
        created_at=now,
        updated_at=now,
        **kwargs,
    )
    db.add(user)
    db.flush()
    if not has_active_consent(db, user.id, ConsentPurpose.processing):
        grant_consent(db, user.id, ConsentPurpose.processing, scope={"context": "demo_seed"})
    if role == UserRole.candidate.value and db.get(CandidateProfile, user.id) is None:
        db.add(CandidateProfile(user_id=user.id, created_at=now, updated_at=now))
        db.flush()
    return user


def mark(db: Session, user: User, kind: str, key: str | None = None, **profile: Any) -> DemoPersona:
    """Роль аккаунта в демо-мире (demo_personas). Повторный вызов обновляет запись."""
    row = db.get(DemoPersona, user.id)
    if row is None:
        row = DemoPersona(user_id=user.id, kind=kind, key=key, profile=dict(profile))
        db.add(row)
    else:
        row.kind, row.key, row.profile = kind, key, {**(row.profile or {}), **profile}
    db.flush()
    return row


def token_for(settings: Settings, user: User) -> str:
    """Access-JWT напрямую, без пароля (внутренние вызовы сборки мира)."""
    token, _ = create_access_token(user.id, user.role, settings)
    return token
