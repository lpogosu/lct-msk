"""Отклики [доп.] (концепция §3.1 шаг 13, §3.2 шаг 11; spec-contact §2.3).

Кандидат откликается на опубликованную вакансию с отдельной галочкой согласия на передачу контактов — компания
видит их сразу. Работодатель отвечает за 7 дней: «есть интерес» или отказ с причиной (причина уходит кандидату со
ссылкой на задачу). Без ответа — «нет ответа» и минус к надёжности компании. Отклики ранжируются тем же Score.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, LimiterDep, MailerDep, SettingsDep, require_roles
from app.core.errors import error_responses
from app.core.settings import Settings
from app.mail.sender import Mailer
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact import applications as svc
from app.services.contact.common import cfg, check_rate, now
from app.services.contact.company import company_of
from app.services.contact.schemas import (
    ApplicationCandidateCard,
    ApplicationCandidateDetail,
    ApplicationEmployerCard,
    ApplicationEmployerDetail,
    ApplyIn,
    RejectIn,
)
from app.services.contact.store import Ctx

router = APIRouter(prefix="/applications", tags=["Отклики"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]
CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]


def _ctx(db: DbSession, mailer: Mailer | None, settings: Settings) -> Ctx:
    return Ctx(db=db, mailer=mailer, secret=settings.pseudonym_secret.get_secret_value())


# --- кандидат ---------------------------------------------------------------------------------


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=ApplicationCandidateDetail,
    summary="Откликнуться на вакансию",
    description=(
        "Отклик с отдельной галочкой «передать мои контакты этой компании» (`consent: true`, 152-ФЗ): без неё "
        "отклик не отправляется (400). Компания видит контакты сразу и должна ответить за 7 дней. `fields` — какие "
        "поля раскрыть (по умолчанию — из настроек приватности). 409 — вы уже откликались."
    ),
)
def apply(
    body: ApplyIn, user: CandidateUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    check_rate(limiter, "apply", user.id, cfg().http_limits.apply_per_minute)
    ctx = _ctx(db, mailer, settings)
    row = svc.submit(ctx, user, body.vacancy_id, consent=body.consent, fields=body.fields)
    ctx.finish()
    return svc.candidate_detail(ctx, row)


@router.get(
    "/mine",
    response_model=list[ApplicationCandidateCard],
    summary="Мои отклики",
    description="Отклики кандидата со статусами и сроком ответа компании. «Нет ответа» применяется при чтении.",
)
def mine(user: CandidateUser, db: DbSession, settings: SettingsDep) -> list[dict[str, Any]]:
    ctx = _ctx(db, None, settings)
    rows = svc.candidate_rows(db, user.id)
    svc.refresh_rows(ctx, rows)
    ctx.finish()
    at = now()
    return svc.candidate_cards(ctx, rows, at)


@router.get(
    "/mine/{application_id}",
    response_model=ApplicationCandidateDetail,
    summary="Мой отклик",
    description="Статус, причина отказа со ссылками на задачу по модулю теста, какие поля переданы компании.",
)
def mine_detail(application_id: uuid.UUID, user: CandidateUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    ctx = _ctx(db, None, settings)
    row = svc.get_for_candidate(db, user, application_id)
    svc.refresh_rows(ctx, [row])
    ctx.finish()
    return svc.candidate_detail(ctx, row)


def _candidate_act(
    application_id: uuid.UUID, user: User, db: DbSession, mailer: Mailer, settings: Settings, limiter: LimiterDep, action: str
) -> dict[str, Any]:
    check_rate(limiter, "application_respond", user.id, cfg().http_limits.respond_per_minute)
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_candidate(db, user, application_id)
    svc.act(ctx, row, action, "candidate", user.id)
    ctx.finish()
    return svc.candidate_detail(ctx, row)


@router.post(
    "/{application_id}/withdraw",
    response_model=ApplicationCandidateDetail,
    summary="Отозвать отклик",
    description="Отклик отзывается, согласие на передачу контактов — тоже: контакты у компании снова скрыты.",
)
def withdraw(
    application_id: uuid.UUID, user: CandidateUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _candidate_act(application_id, user, db, mailer, settings, limiter, "withdraw")


@router.post(
    "/{application_id}/revoke-consent",
    response_model=ApplicationCandidateDetail,
    summary="Отозвать согласие после отказа",
    description="После отказа или молчания компании контакты остаются у неё, пока вы не отзовёте согласие (152-ФЗ).",
)
def revoke_consent(
    application_id: uuid.UUID, user: CandidateUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _candidate_act(application_id, user, db, mailer, settings, limiter, "revoke_consent")


# --- работодатель ------------------------------------------------------------------------------


@router.get(
    "",
    response_model=list[ApplicationEmployerCard],
    summary="Отклики на мои вакансии",
    description=(
        "Отклики (Р10), отсортированные тем же Score, что и подборка; таймер 7 дней на ответ. Фильтры: "
        "`vacancy_id`, `status`."
    ),
)
def employer_list(
    user: EmployerUser,
    db: DbSession,
    settings: SettingsDep,
    vacancy_id: Annotated[uuid.UUID | None, Query(description="Только отклики на эту вакансию")] = None,
    status_: Annotated[str | None, Query(alias="status", description="Только отклики в этом статусе")] = None,
) -> list[dict[str, Any]]:
    ctx = _ctx(db, None, settings)
    company = company_of(db, user)
    rows = svc.employer_rows(db, company.id, vacancy_id, None)
    svc.refresh_rows(ctx, rows)
    ctx.finish()
    if status_ is not None:
        rows = [r for r in rows if r.status == status_]
    at = now()
    return svc.sort_by_score(svc.employer_cards(ctx, rows, at))


@router.get(
    "/{application_id}",
    response_model=ApplicationEmployerDetail,
    summary="Отклик (работодатель)",
    description=(
        "Карточка отклика: Score против брифа вакансии и карточка кандидата с контактами (он откликнулся сам и дал "
        "согласие). Открытие отмечает отклик просмотренным — ответить можно только после просмотра."
    ),
)
def employer_detail(
    application_id: uuid.UUID, user: EmployerUser, db: DbSession, mailer: MailerDep, settings: SettingsDep
) -> dict[str, Any]:
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_employer(db, user, application_id)
    svc.mark_viewed_by_employer(ctx, row, user)
    ctx.finish()
    return svc.employer_detail(ctx, row)


def _employer_act(
    application_id: uuid.UUID,
    user: User,
    db: DbSession,
    mailer: Mailer,
    settings: Settings,
    limiter: LimiterDep,
    action: str,
    **payload: Any,
) -> dict[str, Any]:
    check_rate(limiter, "application_respond", user.id, cfg().http_limits.respond_per_minute)
    ctx = _ctx(db, mailer, settings)
    row = svc.get_for_employer(db, user, application_id)
    svc.act(ctx, row, action, "employer", user.id, **payload)
    ctx.finish()
    return svc.employer_detail(ctx, row)


@router.post(
    "/{application_id}/interest",
    response_model=ApplicationEmployerDetail,
    summary="«Есть интерес»",
    description="Ответ в срок (7 дней) — плюс к надёжности. 409 — отклик ещё не открыт или срок истёк.",
)
def interest(
    application_id: uuid.UUID, user: EmployerUser, db: DbSession, mailer: MailerDep, settings: SettingsDep, limiter: LimiterDep
) -> dict[str, Any]:
    return _employer_act(application_id, user, db, mailer, settings, limiter, "interest")


@router.post(
    "/{application_id}/reject",
    response_model=ApplicationEmployerDetail,
    summary="Отказать с причиной",
    description=(
        "Причина обязательна: skills_missing (с `skill_ids` — каких навыков не хватило), salary, position_closed, "
        "chose_other, other. Кандидат получает причину и ссылку на короткую задачу по модулю теста."
    ),
)
def reject(
    application_id: uuid.UUID,
    body: RejectIn,
    user: EmployerUser,
    db: DbSession,
    mailer: MailerDep,
    settings: SettingsDep,
    limiter: LimiterDep,
) -> dict[str, Any]:
    return _employer_act(
        application_id, user, db, mailer, settings, limiter, "reject", **svc.reject_payload(body.reason, body.comment, body.skill_ids)
    )
