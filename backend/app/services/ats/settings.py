"""Настройки ATS компании и шифрование токена (Fernet, ключ из VZ_APP_SECRET — как ключи экземпляров теста)."""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.core.errors import bad_request
from app.models.integration import AtsSettings
from app.services.contact import webhooks
from app.services.contact.common import cfg
from app.services.testing.keystore import decrypt_key, encrypt_key


def get(db: Session, company_id: uuid.UUID) -> AtsSettings | None:
    return db.get(AtsSettings, company_id)


def ats_type(db: Session, company_id: uuid.UUID) -> str:
    row = get(db, company_id)
    return row.ats_type if row else "none"


def token_of(row: AtsSettings | None) -> str | None:
    if row is None or not row.token_enc:
        return None
    return str(decrypt_key(row.token_enc))


def token_hint(row: AtsSettings | None) -> str | None:
    t = token_of(row)
    return ("•" * 8 + t[-4:]) if t else None


def mode_of(row: AtsSettings | None) -> str:
    """none — ATS не подключена; emulator — запросы только записываются; real — отправляются (есть токен)."""
    if row is None or row.ats_type != "huntflow":
        return "none"
    return "real" if row.token_enc else "emulator"


def save(
    db: Session,
    company_id: uuid.UUID,
    *,
    type_: str,
    base_url: str | None,
    account_id: str | None,
    token: str | None,
    fields: set[str],
) -> AtsSettings:
    """token: None/не передан — не менять, пустая строка — стереть."""
    if type_ not in cfg().ats.types:
        raise bad_request("ats_type_invalid", "Неизвестный тип ATS: выберите «Нет», «Вебхук» или «Хантфлоу»")
    url = (base_url or "").strip() or None
    if "base_url" in fields and url:
        problem = webhooks.validate_url(url)
        if problem:
            raise bad_request("ats_base_url_invalid", problem.replace("вебхука", "ATS"))
    acc = (account_id or "").strip() or None
    if "account_id" in fields and acc and (not acc.isdigit() or len(acc) > 12):
        raise bad_request("ats_account_invalid", "Номер организации в Хантфлоу — число")
    row = get(db, company_id)
    if row is None:  # создаём только после всех проверок: при отказе в сессии ничего не остаётся
        row = AtsSettings(company_id=company_id)
        db.add(row)
    row.ats_type = type_
    if "base_url" in fields:
        row.base_url = url
    if "account_id" in fields:
        row.account_id = acc
    if "token" in fields and token is not None:
        t = token.strip()
        row.token_enc = encrypt_key(t) if t else None
    db.flush()
    return row
