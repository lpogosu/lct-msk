"""Модерация доверия (методист или администратор): очередь жалоб кандидатов, сигналы по компаниям, отзыв метки.

Концепция и границы MVP — docs/TRUST_AND_SAFETY.md. Эвристики только подсказывают, решение принимает человек.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, MetaDep, require_roles
from app.core.audit import audit
from app.core.clock import clock
from app.core.errors import error_responses
from app.models.enums import UserRole
from app.models.users import User
from app.trust import complaints, flags, verification
from app.trust.schemas import (
    CompanyFlagsOut,
    ComplaintOut,
    ComplaintResolveIn,
    RevokeIn,
    RevokeOut,
)

router = APIRouter(prefix="/admin/trust", tags=["Администрирование"], responses=error_responses())

Moderator = Annotated[User, Depends(require_roles(UserRole.admin, UserRole.methodist))]


@router.get(
    "/complaints",
    response_model=list[ComplaintOut],
    summary="Очередь жалоб",
    description="Жалобы кандидатов на приглашения и вакансии, новые сверху. `status`: open | upheld | dismissed; без фильтра — все.",
)
def list_complaints(
    user: Moderator,  # noqa: ARG001
    db: DbSession,
    status: Annotated[str | None, Query(pattern="^(open|upheld|dismissed)$")] = "open",
) -> list[dict]:
    return complaints.list_queue(db, status)


@router.post(
    "/complaints/{complaint_id}/resolve",
    response_model=ComplaintOut,
    summary="Разобрать жалобу",
    description=(
        "`upheld` — подтверждена (считается в квоте компании; для причин fake_vacancy, payment_request, collects_resume "
        "снимает метку «Проверенная компания»), `dismissed` — отклонена (в квоте не считается). 409 — уже разобрана."
    ),
)
def resolve_complaint(complaint_id: uuid.UUID, body: ComplaintResolveIn, user: Moderator, db: DbSession, meta: MetaDep) -> dict:
    out = complaints.resolve(db, user, complaint_id, body.decision, body.note, clock.now())
    audit(db, "trust.complaint_resolved", actor_id=user.id, actor_role=user.role, entity_type="complaint", entity_id=complaint_id, ip=meta.ip, decision=body.decision)
    db.commit()
    return out


@router.get(
    "/flags",
    response_model=list[CompanyFlagsOut],
    summary="Автосигналы по компаниям",
    description=(
        "Эвристики по данным платформы: много приглашений и ни одного принятого, вакансия висит без откликов, зарплата в приглашении "
        "ниже брифа, сообщения о расхождении условий. Ничего не блокируют — подсказка модератору."
    ),
)
def list_flags(user: Moderator, db: DbSession) -> list[dict]:  # noqa: ARG001
    return flags.flagged_companies(db, clock.now())


@router.post(
    "/companies/{company_id}/revoke",
    response_model=RevokeOut,
    summary="Отозвать метку «Проверенная компания»",
    description="Метка пропадает у кандидатов сразу; компания работает как прежде, но подать реквизиты заново не может, пока отзыв не снят.",
)
def revoke(company_id: uuid.UUID, body: RevokeIn, user: Moderator, db: DbSession, meta: MetaDep) -> RevokeOut:
    verification.revoke(db, company_id, user.id, body.reason, body.note, clock.now())
    audit(db, "trust.verification_revoked", actor_id=user.id, actor_role=user.role, entity_type="company", entity_id=company_id, ip=meta.ip, reason=body.reason)
    db.commit()
    return RevokeOut(company_id=company_id, level="none", revoked=True)


@router.post(
    "/companies/{company_id}/restore",
    response_model=RevokeOut,
    summary="Снять отзыв метки",
    description="Стирает отзыв и прежний результат: компания сможет подать реквизиты на проверку заново.",
)
def restore(company_id: uuid.UUID, user: Moderator, db: DbSession, meta: MetaDep) -> RevokeOut:
    verification.restore(db, company_id)
    audit(db, "trust.verification_restored", actor_id=user.id, actor_role=user.role, entity_type="company", entity_id=company_id, ip=meta.ip)
    db.commit()
    return RevokeOut(company_id=company_id, level="none", revoked=False)
