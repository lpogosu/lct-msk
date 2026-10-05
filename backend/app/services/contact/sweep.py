"""Фоновый проход контакта (концепция §4.3: «задача раз в 5 минут только рассылает напоминания»).

Источник истины — ленивое применение сроков при чтении; sweep() лишь делает то же самое для всех сразу,
чтобы напоминания и уведомления «истекло» приходили без захода пользователя, а неудачные вебхуки
повторялись по расписанию. Вызывается DEMO-кнопкой «+1 день» и периодической задачей (если она есть).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.statuses import PENDING_STATUSES
from app.contact.statuses import InvitationStatus as S
from app.models.contact import Application, Invitation

from . import webhooks
from .store import Ctx, refresh_rows


def sweep(db: Session, *, secret: str = "", limit: int = 1000) -> dict[str, Any]:
    ctx = Ctx(db=db, secret=secret)
    statuses = (*PENDING_STATUSES, S.ACCEPTED, S.CLOSED)
    inv_rows = list(db.scalars(select(Invitation).where(Invitation.status.in_(statuses)).limit(limit)))
    refresh_rows(ctx, inv_rows)
    from .applications import PENDING
    from .applications import refresh_rows as refresh_applications

    app_rows = list(db.scalars(select(Application).where(Application.status.in_(PENDING)).limit(limit)))
    refresh_applications(ctx, app_rows)
    db.commit()
    attempts = webhooks.deliver_due(db, None, limit=limit)
    db.commit()
    return {"invitations": len(inv_rows), "applications": len(app_rows), "webhook_attempts": attempts}
