"""API-ключи компании для /api/ats/v1 (выбор: статичный ключ, а не OAuth2 client credentials).

Почему ключ: интеграция «сервер—сервер» без пользователя; ключ с областями доступа (scopes) проще выдать
и отозвать из профиля компании, чем держать отдельный OAuth-сервер. Формат: `vzk_<key_id>_<секрет>`; `key_id` —
публичная часть для поиска, в БД — только sha256 секрета (секрет высокоэнтропийный, соль не нужна).
Ключ показывается один раз при создании/перевыпуске. Отозванный ключ перестаёт работать сразу.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict, not_found, unauthorized
from app.models.company import Company
from app.models.integration import CompanyApiKey
from app.models.users import User
from app.services.contact.common import cfg, now


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def validate_scopes(scopes: Sequence[str]) -> list[str]:
    known = cfg().ats.scopes
    clean = list(dict.fromkeys(scopes))
    if not clean:
        raise bad_request("ats_scopes_empty", "Выберите хотя бы одну область доступа ключа")
    bad = [s for s in clean if s not in known]
    if bad:
        raise bad_request("ats_scope_unknown", f"Неизвестная область доступа: {', '.join(bad)}")
    return clean


def _new_plain() -> tuple[str, str, str]:
    c = cfg().ats
    key_id = secrets.token_hex(4)
    secret = secrets.token_urlsafe(c.key_secret_bytes)
    return key_id, secret, f"{c.key_prefix}_{key_id}_{secret}"


def active_keys(db: Session, company_id: uuid.UUID) -> list[CompanyApiKey]:
    return list(
        db.scalars(
            select(CompanyApiKey)
            .where(CompanyApiKey.company_id == company_id, CompanyApiKey.revoked_at.is_(None))
            .order_by(CompanyApiKey.created_at, CompanyApiKey.id)
        )
    )


def create(db: Session, company_id: uuid.UUID, user: User, name: str, scopes: Sequence[str]) -> tuple[CompanyApiKey, str]:
    name = (name or "").strip()
    if not name or len(name) > 80:
        raise bad_request("ats_key_name", "Назовите ключ (до 80 знаков): например, «Хантфлоу, прод»")
    scope_list = validate_scopes(scopes)
    limit = cfg().ats.max_keys_per_company
    n = db.scalar(select(func.count()).select_from(CompanyApiKey).where(CompanyApiKey.company_id == company_id, CompanyApiKey.revoked_at.is_(None)))
    if (n or 0) >= limit:
        raise conflict("ats_keys_limit", f"Можно держать не больше {limit} действующих ключей — отзовите ненужный")
    key_id, secret, plain = _new_plain()
    row = CompanyApiKey(company_id=company_id, created_by=user.id, name=name, key_id=key_id, key_hash=_hash(secret), scopes=scope_list)
    db.add(row)
    db.flush()
    return row, plain


def get_owned(db: Session, company_id: uuid.UUID, key_pk: uuid.UUID) -> CompanyApiKey:
    row = db.get(CompanyApiKey, key_pk)
    if row is None or row.company_id != company_id:
        raise not_found("ats_key_not_found", "Ключ не найден")
    return row


def rotate(db: Session, row: CompanyApiKey, user: User) -> tuple[CompanyApiKey, str]:
    """Перевыпуск: старый ключ отзывается сразу, новый наследует название и области."""
    if row.revoked_at is not None:
        raise conflict("ats_key_revoked", "Ключ уже отозван — создайте новый")
    row.revoked_at = now()
    key_id, secret, plain = _new_plain()
    new = CompanyApiKey(company_id=row.company_id, created_by=user.id, name=row.name, key_id=key_id, key_hash=_hash(secret), scopes=list(row.scopes or []))
    db.add(new)
    db.flush()
    return new, plain


def revoke(row: CompanyApiKey) -> None:
    if row.revoked_at is None:
        row.revoked_at = now()


def authenticate(db: Session, header: str | None) -> tuple[CompanyApiKey, Company]:
    """`Authorization: Bearer vzk_<id>_<секрет>` → ключ и компания. Любая ошибка — одинаковый 401 (ничего не подсказываем)."""
    fail = unauthorized("ats_key_invalid", "Недействительный API-ключ: проверьте заголовок Authorization: Bearer vzk_…")
    if not header or not header.lower().startswith("bearer "):
        raise fail
    token = header[7:].strip()
    prefix = cfg().ats.key_prefix + "_"
    if not token.startswith(prefix):
        raise fail
    rest = token[len(prefix):]
    key_id, sep, secret = rest.partition("_")
    if not sep or not key_id or not secret:
        raise fail
    row = db.scalar(select(CompanyApiKey).where(CompanyApiKey.key_id == key_id))
    if row is None or not hmac.compare_digest(row.key_hash, _hash(secret)) or row.revoked_at is not None:
        raise fail
    company = db.get(Company, row.company_id)
    if company is None:
        raise fail
    row.last_used_at = now()
    return row, company
