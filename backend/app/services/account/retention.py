"""Сроки хранения персональных данных (152-ФЗ: хранить не дольше, чем нужно для цели; config/privacy.yaml → retention).

Ночная задача планировщика (config/scheduler.yaml → jobs.retention), вручную — `python -m scripts.retention` не нужен: вызов
`retention_sweep(db)` идемпотентен. Что делает:

- неподтверждённые аккаунты (email не подтверждён дольше `unverified_account_days`) удаляются целиком — у них нет профиля
  и согласий сверх регистрационного: письмо не дошло или адрес чужой/опечатка; повторная регистрация возможна;
- сожжённые и просроченные коды подтверждения, просроченные и отозванные refresh-токены, брошенные входы через ФСП ID;
- IP в журнале аудита и IP/user-agent в записях согласий обезличиваются (поле → NULL) по истечении срока; сами записи
  остаются: событие и версия текста согласия не содержат ПДн;
- сырые ответы реестра ФСП, сохранённые до включения минимизации, приводятся к минимальному виду
  (app/services/fsp_link/minimize.py).

Все параметры — из конфига; «сейчас» — часы платформы (в DEMO_MODE сдвиг часов двигает и сроки — это ожидаемо для демо).
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from sqlalchemy import and_, delete, or_, select, update
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.models.enums import UserStatus
from app.models.fsp import FspAuthState, FspLink
from app.models.users import AuditLog, Consent, EmailCode, RefreshToken, User
from app.services.fsp_link.minimize import minimize_link

log = logging.getLogger("vzaimno.retention")

def retention_sweep(db: Session) -> dict[str, Any]:
    cfg = load_config("privacy")["retention"]
    now = clock.now()
    day = timedelta(days=1)
    out: dict[str, Any] = {}
    batch = int(cfg["batch"])

    stale = select(User.id).where(
        User.email_verified.is_(False),
        User.status == UserStatus.pending_email.value,
        User.is_demo.is_(False),
        User.created_at < now - int(cfg["unverified_account_days"]) * day,
    ).limit(batch)
    ids = list(db.scalars(stale))
    if ids:
        # каскад: коды, согласие на обработку из регистрации, привязка ФСП ID, созданная входом без подтверждения
        db.execute(delete(User).where(User.id.in_(ids)).execution_options(synchronize_session=False))
    out["unverified_accounts"] = len(ids)

    out["email_codes"] = _rows(
        db.execute(
            delete(EmailCode).where(
                or_(
                    EmailCode.expires_at < now - int(cfg["email_code_days"]) * day,
                    and_(EmailCode.consumed_at.is_not(None), EmailCode.consumed_at < now - int(cfg["email_code_days"]) * day),
                )
            )
        )
    )
    token_cut = now - int(cfg["refresh_token_days"]) * day
    out["refresh_tokens"] = _rows(
        db.execute(
            delete(RefreshToken).where(
                or_(
                    RefreshToken.expires_at < token_cut,
                    and_(RefreshToken.revoked_at.is_not(None), RefreshToken.revoked_at < token_cut),
                )
            )
        )
    )
    out["fsp_auth_states"] = _rows(
        db.execute(delete(FspAuthState).where(FspAuthState.expires_at < now - int(cfg["fsp_auth_state_days"]) * day))
    )
    out["audit_ip_anonymized"] = _rows(
        db.execute(
            update(AuditLog)
            .where(AuditLog.ip.is_not(None), AuditLog.at < now - int(cfg["audit_ip_days"]) * day)
            .values(ip=None)
        )
    )
    consent_cut = now - int(cfg["consent_ip_days"]) * day
    out["consent_ip_anonymized"] = _rows(
        db.execute(
            update(Consent)
            .where(or_(Consent.ip.is_not(None), Consent.user_agent.is_not(None)), Consent.granted_at < consent_cut)
            .values(ip=None, user_agent=None)
        )
    )
    out["fsp_links_minimized"] = _minimize_fsp_links(db, batch)
    db.commit()
    log.info("Хранение данных: %s", out)
    return out


def _rows(result: Any) -> int:
    return int(result.rowcount or 0)


def _minimize_fsp_links(db: Session, batch: int) -> int:
    """Старые строки привязки ФСП с полным ответом реестра → минимальный вид (идемпотентно, порциями)."""
    if not bool(load_config("privacy")["fsp_registry"]["minimize_stored_response"]):
        return 0
    n = 0
    for link in db.scalars(select(FspLink).where(FspLink.athlete.is_not(None) | FspLink.achievements.is_not(None)).limit(batch)):
        if minimize_link(link):
            n += 1
    db.flush()
    return n
