"""Журнал согласий (152-ФЗ, экран К13): список своих согласий, выдача и отзыв.

Доступ только к своим записям: чужая запись неотличима от несуществующей (404) — защита от IDOR.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, MetaDep
from app.api.schemas import ConsentDocumentOut, ConsentGrantRequest, ConsentOut
from app.core.access import get_owned_or_404
from app.core.audit import audit
from app.core.consents import (
    CONSENT_DOCUMENTS,
    CONTEXTUAL_PURPOSES,
    current_version,
    find_active_consent,
    grant_consent,
    revocable_in_journal,
    revoke_consent,
)
from app.core.errors import bad_request, conflict, error_responses
from app.models.users import Consent

router = APIRouter(prefix="/consents", tags=["Согласия"], responses=error_responses())


def _out(c: Consent) -> ConsentOut:
    return ConsentOut(
        id=c.id,
        purpose=c.purpose,
        text_version=c.text_version,
        granted_at=c.granted_at,
        revoked_at=c.revoked_at,
        scope=c.scope or {},
        active=c.revoked_at is None,
    )


@router.get(
    "/documents",
    response_model=list[ConsentDocumentOut],
    summary="Тексты согласий",
    description="Актуальные версии документов-согласий: цель, версия, название и краткое содержание. Доступно без входа.",
)
def documents() -> list[ConsentDocumentOut]:
    return [
        ConsentDocumentOut(
            purpose=d.purpose,
            version=d.version,
            title=d.title,
            summary=d.summary,
            required_for_registration=d.required_for_registration,
            grantable_in_journal=d.purpose not in CONTEXTUAL_PURPOSES,
            revocable_in_journal=d.revocable_in_journal,
        )
        for d in CONSENT_DOCUMENTS.values()
    ]


@router.get(
    "",
    response_model=list[ConsentOut],
    summary="Мой журнал согласий",
    description="Все согласия текущего пользователя, включая отозванные, от новых к старым.",
)
def list_consents(user: CurrentUser, db: DbSession) -> list[ConsentOut]:
    rows = db.scalars(select(Consent).where(Consent.user_id == user.id).order_by(Consent.granted_at.desc()))
    return [_out(c) for c in rows]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ConsentOut,
    responses={200: {"model": ConsentOut, "description": "Такое согласие уже действует — возвращена его запись"}},
    summary="Дать согласие",
    description=(
        "Записывает новое согласие отдельной записью журнала с версией текста (201). Если такое согласие с той "
        "же редакцией текста уже действует, возвращает его без новой записи (200). Согласие на передачу "
        "контактов (`contact_disclosure`) здесь не выдаётся — только в приглашении или отклике "
        "(400 `consent_contextual`). 409 `consent_outdated` — пользователь видел устаревшую версию текста."
    ),
)
def grant(
    body: ConsentGrantRequest,
    response: Response,
    user: CurrentUser,
    db: DbSession,
    meta: MetaDep,
) -> ConsentOut:
    if body.purpose in CONTEXTUAL_PURPOSES:
        raise bad_request(
            "consent_contextual",
            "Согласие на передачу контактов даётся в приглашении или при отклике на вакансию",
        )
    actual = current_version(body.purpose)
    existing = find_active_consent(db, user.id, body.purpose, actual)
    if existing is not None and body.text_version in (None, actual):
        # Повторная галочка (К5 и К13) не плодит дублей в журнале
        response.status_code = status.HTTP_200_OK
        return _out(existing)
    consent = grant_consent(
        db, user.id, body.purpose, text_version=body.text_version, ip=meta.ip, user_agent=meta.user_agent
    )
    audit(db, "consent.granted", actor_id=user.id, actor_role=user.role, entity_type="consent", entity_id=consent.id, ip=meta.ip, purpose=consent.purpose)
    db.commit()
    return _out(consent)


@router.post(
    "/{consent_id}/revoke",
    response_model=ConsentOut,
    summary="Отозвать согласие",
    description=(
        "Отзывает согласие: запись остаётся в журнале с датой отзыва. Отзыв согласия на передачу контактов "
        "снова скрывает контакты от компании. 404 — записи нет или она чужая; 409 `consent_already_revoked`; "
        "409 `consent_not_revocable_here` — согласие на обработку ПДн отзывается только вместе с удалением аккаунта."
    ),
)
def revoke(
    consent_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    meta: MetaDep,
) -> ConsentOut:
    consent = get_owned_or_404(db, Consent, consent_id, user, message="Согласие не найдено")
    if not revocable_in_journal(consent.purpose):
        # Иначе запись была бы «отозвана», а обработка шла бы дальше — хуже, чем честный отказ
        raise conflict(
            "consent_not_revocable_here",
            "Согласие на обработку персональных данных нужно для работы аккаунта: отозвать его можно только "
            "вместе с удалением аккаунта. Напишите в поддержку",
        )
    revoke_consent(db, consent)
    audit(db, "consent.revoked", actor_id=user.id, actor_role=user.role, entity_type="consent", entity_id=consent.id, ip=meta.ip, purpose=consent.purpose)
    db.commit()
    return _out(consent)
