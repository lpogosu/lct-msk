"""Сигнал интереса кандидата (DECISIONS 24): остаток, отправка, отзыв.

Работодатель сигнал не получает уведомлением: он появляется бейджем в подборке/банке/карточке только у тех
кандидатов, чью карточку компания уже открыла или закрепила (DECISIONS 7). На Score не влияет.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from app.api.deps import DbSession, MetaDep, require_roles
from app.core.audit import audit
from app.core.errors import error_responses
from app.models.company import Company
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact import interest as svc
from app.services.contact.common import now, rel
from app.services.contact.schemas import InterestIn, InterestSentOut, InterestStatusOut

router = APIRouter(prefix="/interest", tags=["Сигнал интереса"], responses=error_responses(400, 401, 403, 404, 409, 422))

CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]


@router.get(
    "/me",
    response_model=InterestStatusOut,
    summary="Мои сигналы интереса",
    description="Остаток («осталось 2 из 2»), когда освободится слот, действующие сигналы и текст отдельного согласия.",
)
def my_status(user: CandidateUser, db: DbSession) -> dict:
    return svc.status(db, user)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=InterestSentOut,
    summary="Отправить сигнал интереса",
    description=(
        "Укажите вакансию или компанию. Лимит — config/contact.yaml → interest (по умолчанию 2 за 30 дней, живёт 60 дней; "
        "отзыв лимит не возвращает). Нужно отдельное согласие (`consent: true` при первом сигнале). Работодатель увидит сигнал "
        "только после того, как сам откроет вашу карточку или закрепит вас в шортлисте; на Score он не влияет. "
        "409 `interest_limit` — сигналы закончились, `interest_exists` — компании уже отправлен."
    ),
)
def send(body: InterestIn, user: CandidateUser, db: DbSession, meta: MetaDep) -> dict:
    row = svc.send(
        db, user, company_id=body.company_id, vacancy_id=body.vacancy_id, consent=body.consent, ip=meta.ip, user_agent=meta.user_agent
    )
    audit(db, "interest.sent", actor_id=user.id, actor_role=user.role, entity_type="company", entity_id=row.company_id, ip=meta.ip)
    db.commit()
    name = db.get(Company, row.company_id)
    at = now()
    signal = {
        "id": row.id,
        "company_id": row.company_id,
        "company_name": name.name if name else None,
        "vacancy_id": row.vacancy_id,
        "sent": rel(row.created_at, at),
        "expires": rel(row.expires_at, at),
    }
    return {"signal": signal, "status": svc.status(db, user)}


@router.post(
    "/{signal_id}/revoke",
    response_model=InterestStatusOut,
    summary="Отозвать сигнал",
    description="Сигнал сразу пропадает у работодателя. Использованный слот не возвращается.",
)
def revoke(signal_id: uuid.UUID, user: CandidateUser, db: DbSession, meta: MetaDep) -> dict:
    svc.revoke(db, user, signal_id)
    audit(db, "interest.revoked", actor_id=user.id, actor_role=user.role, entity_type="interest", entity_id=signal_id, ip=meta.ip)
    db.commit()
    return svc.status(db, user)
