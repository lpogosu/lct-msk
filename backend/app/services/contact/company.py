"""Профиль компании (экран Р1): заполненность, «так видит кандидат», квота приглашений.

Без заполненного профиля приглашения не отправляются (концепция §3.2 шаг 2): кандидат должен видеть
название, направление, описание и способ связи до решения (§4.5).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.contact.quotas import SenderState, SentRecord, quota_usage
from app.core.errors import conflict
from app.models.company import Company
from app.models.contact import Invitation
from app.models.users import User

from .common import cfg, now, rel
from .reliability import company_reliability


def missing_fields(company: Company) -> list[str]:
    """Чего не хватает для «профиль заполнен» — подписи для работодателя."""
    req = cfg().company_profile
    missing: list[str] = []
    if not (company.name or "").strip():
        missing.append(req.required["name"])
    if not (company.field or "").strip():
        missing.append(req.required["field"])
    if len((company.description or "").strip()) < req.description_min_len:
        missing.append(req.required["description"])
    if not any(str(v).strip() for v in (company.contact_methods or {}).values()):
        missing.append(req.required["contact_methods"])
    return missing


def sync_completion(company: Company, at: datetime | None = None) -> None:
    """profile_completed_at ставится при первом полном заполнении и снимается, если профиль снова неполный."""
    if missing_fields(company):
        company.profile_completed_at = None
    elif company.profile_completed_at is None:
        company.profile_completed_at = at or now()


def require_complete(company: Company) -> None:
    missing = missing_fields(company)
    if missing:
        raise conflict(
            "company_profile_incomplete",
            "Заполните профиль компании, чтобы отправлять приглашения: " + ", ".join(missing),
            {"missing": missing},
        )


def sent_records(db: Session, company: Company, user: User | None, at: datetime) -> list[SentRecord]:
    """Отправки владельца квоты (DECISIONS 8): корпоративный домен — вся компания; личная почта — компания
    аккаунта (она и так своя у каждого аккаунта с личным доменом)."""
    since = at - timedelta(days=cfg().quotas.window_days)
    rows = db.scalars(
        select(Invitation).where(
            Invitation.company_id == company.id,
            or_(Invitation.created_at > since, Invitation.status.in_(("sent", "viewed", "countered"))),
        )
    )
    return [
        SentRecord(
            invitation_id=str(r.id),
            company_id=str(r.company_id),
            candidate_id=str(r.candidate_id),
            sent_at=r.created_at,
            status=r.status,
            viewed=r.viewed_at is not None,
            need_id=str(r.need_id) if r.need_id else None,
        )
        for r in rows
    ]


def sender_state(db: Session, company: Company, user: User, at: datetime) -> SenderState:
    return SenderState(
        company_id=str(company.id),
        email=user.email,
        account_id=str(user.id),
        sends=tuple(sent_records(db, company, user, at)),
        reliability=company_reliability(db, company.id, at),
    )


def quota_dict(db: Session, company: Company, user: User, at: datetime) -> dict[str, Any]:
    usage = quota_usage(sender_state(db, company, user, at), at, cfg())
    return {
        "used": usage.used,
        "limit": usage.limit,
        "remaining": usage.remaining,
        "label": usage.label,
        "reduced_reason": usage.reduced_reason,
        "next_slot_in": rel(usage.next_slot_at, at) if usage.used >= usage.limit else None,
    }


def company_of(db: Session, user: User) -> Company:
    if user.company_id is None:
        raise conflict("company_missing", "У аккаунта нет компании. Подтвердите email работодателя")
    company = db.get(Company, user.company_id)
    if company is None:
        raise conflict("company_missing", "У аккаунта нет компании. Подтвердите email работодателя")
    return company


def company_id_of(user: User) -> uuid.UUID:
    if user.company_id is None:
        raise conflict("company_missing", "У аккаунта нет компании. Подтвердите email работодателя")
    return user.company_id
