"""Ответы кандидата на приглашение «руками сервиса»: тем же слоем, что HTTP-роуты /api/invitations.

Используется сборкой мира (истории приглашений) и автоответчиком синтетических кандидатов. Решения принимает
движок app/contact; здесь — только вызов `act` с теми же аргументами, что у роутов (просмотр → ответ).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.settings import get_contact_config
from app.core.errors import ApiError
from app.models.contact import Invitation, OfferTerms
from app.models.users import User
from app.services.contact import invitations as svc
from app.services.contact.store import Ctx


@dataclass(frozen=True)
class Terms:
    salary_from: int
    salary_to: int
    salary_net: bool
    format: str | None


def current_terms(db: Session, row: Invitation) -> Terms:
    t = db.scalar(
        select(OfferTerms).where(OfferTerms.invitation_id == row.id, OfferTerms.ver == row.current_terms_ver)
    )
    if t is None:  # pragma: no cover — у приглашения всегда есть v1
        raise LookupError(f"У приглашения {row.id} нет условий")
    return Terms(int(t.salary_from), int(t.salary_to), bool(t.salary_net), t.format)


def _ctx(db: Session, secret: str) -> Ctx:
    return Ctx(db=db, mailer=None, secret=secret)  # письма кандидатам-демо не рассылаем


def _own(db: Session, user: User, invitation_id: uuid.UUID) -> Invitation:
    return svc.get_for_candidate(db, user, invitation_id)


def view(db: Session, user: User, invitation_id: uuid.UUID, *, secret: str = "") -> None:
    ctx = _ctx(db, secret)
    row = _own(db, user, invitation_id)
    svc.mark_viewed_if_new(ctx, row, user)
    ctx.finish()


def accept(db: Session, user: User, invitation_id: uuid.UUID, *, secret: str = "") -> None:
    """«Да» с согласием на передачу контактов (поля — из настроек приватности кандидата)."""
    ctx = _ctx(db, secret)
    row = _own(db, user, invitation_id)
    svc.mark_viewed_if_new(ctx, row, user)
    fields = svc.candidate_fields(db, user.id, None)
    svc.act(ctx, row, "accept", "candidate", user.id, consent=True, fields=fields)
    ctx.finish()


def decline(db: Session, user: User, invitation_id: uuid.UUID, *, reason: str, comment: str = "", secret: str = "") -> None:
    ctx = _ctx(db, secret)
    row = _own(db, user, invitation_id)
    svc.mark_viewed_if_new(ctx, row, user)
    svc.act(ctx, row, "decline", "candidate", user.id, reason=reason, comment=comment)
    ctx.finish()


def counter(
    db: Session,
    user: User,
    invitation_id: uuid.UUID,
    *,
    salary_from: int | None = None,
    fmt: str | None = None,
    start_date: date | None = None,
    comment: str = "",
    secret: str = "",
) -> None:
    """«Да, если…» (одно встречное условие, один раз)."""
    ctx = _ctx(db, secret)
    row = _own(db, user, invitation_id)
    svc.mark_viewed_if_new(ctx, row, user)
    fields = svc.candidate_fields(db, user.id, None)
    offer = svc.make_counter(
        salary_from=salary_from, fmt=fmt, start_date=start_date, comment=comment, auto_disclose=None, fields=fields
    )
    svc.act(ctx, row, "counter", "candidate", user.id, counter=offer, comment=comment)
    ctx.finish()


def counter_step() -> int:
    return int(get_contact_config().counter_offer.salary_step)


def counter_max_ratio() -> float:
    return float(get_contact_config().counter_offer.salary_max_ratio_to)


def safe(fn: Any, *args: Any, **kwargs: Any) -> tuple[bool, str | None]:
    """Вызов ответа с переводом отказа движка в (False, сообщение): автоответчик не падает на правилах."""
    try:
        fn(*args, **kwargs)
    except ApiError as e:
        return False, e.message
    return True, None
