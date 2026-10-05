"""Уведомления в кабинете (и письмо, где это нужно): приглашения, отклики, задачи.

Контракт для других модулей:
- notify_user(db, user_id, kind, message, entity_type=..., entity_id=..., payload=...) → Notification;
- notify_company(db, company_id, ...) — всем сотрудникам компании-работодателя;
- send_mail_safe(mailer, to, subject, text) — письмо «по возможности»: сбой SMTP не ломает действие.

Тексты — готовые русские фразы без дат в абсолютном виде (DECISIONS 9). Работодателю в текстах — только
псевдоним кандидата, никаких внутренних id (DECISIONS 20).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.mail.sender import Mailer, MailError, OutgoingMail
from app.models.contact import Notification
from app.models.enums import UserRole, UserStatus
from app.models.users import User

log = logging.getLogger("vzaimno.notify")

KINDS = ("invitation", "application", "task", "company", "system")


def notify_user(
    db: Session,
    user_id: uuid.UUID,
    kind: str,
    message: str,
    *,
    entity_type: str | None = None,
    entity_id: Any = None,
    payload: Mapping[str, Any] | None = None,
) -> Notification:
    if kind not in KINDS:
        raise ValueError(f"Неизвестный вид уведомления: {kind}")
    row = Notification(
        user_id=user_id,
        kind=kind,
        message=message[:500],
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        payload=dict(payload or {}),
        created_at=clock.now(),
    )
    db.add(row)
    return row


def company_members(db: Session, company_id: uuid.UUID) -> list[User]:
    return list(
        db.scalars(
            select(User).where(
                User.company_id == company_id,
                User.role == UserRole.employer.value,
                User.status == UserStatus.active.value,
            )
        )
    )


def notify_company(
    db: Session,
    company_id: uuid.UUID,
    kind: str,
    message: str,
    *,
    entity_type: str | None = None,
    entity_id: Any = None,
    payload: Mapping[str, Any] | None = None,
) -> list[Notification]:
    return [
        notify_user(db, u.id, kind, message, entity_type=entity_type, entity_id=entity_id, payload=payload)
        for u in company_members(db, company_id)
    ]


def send_mail_safe(mailer: Mailer | None, to: str, subject: str, text: str) -> bool:
    """Письмо — дополнительный канал: при сбое почты уведомление в кабинете уже есть."""
    if mailer is None:
        return False
    try:
        mailer.send(OutgoingMail(to=to, subject=subject, text=text, tags={"purpose": "notification"}))
        return True
    except MailError as e:  # pragma: no cover — путь сбоя SMTP проверяется в почтовом модуле
        log.warning("Письмо-уведомление на %s не отправлено: %s", to, e)
        return False


# --- чтение (раздел «Уведомления») ---------------------------------------------------------------


def list_for(
    db: Session, user_id: uuid.UUID, *, unread_only: bool = False, kind: str | None = None, limit: int = 100
) -> list[Notification]:
    q = select(Notification).where(Notification.user_id == user_id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    if kind is not None:
        q = q.where(Notification.kind == kind)
    return list(db.scalars(q.order_by(Notification.created_at.desc(), Notification.id).limit(limit)))


def unread_counts(db: Session, user_id: uuid.UUID) -> dict[str, int]:
    rows = db.execute(
        select(Notification.kind, func.count())
        .where(Notification.user_id == user_id, Notification.read_at.is_(None))
        .group_by(Notification.kind)
    ).all()
    return {kind: int(n) for kind, n in rows}


def mark_read(db: Session, row: Notification, at: datetime | None = None) -> Notification:
    if row.read_at is None:
        row.read_at = at or clock.now()
    return row


def mark_all_read(db: Session, user_id: uuid.UUID, kind: str | None = None) -> int:
    q = update(Notification).where(Notification.user_id == user_id, Notification.read_at.is_(None))
    if kind is not None:
        q = q.where(Notification.kind == kind)
    result = db.execute(q.values(read_at=clock.now()).execution_options(synchronize_session="fetch"))
    return int(result.rowcount or 0)
