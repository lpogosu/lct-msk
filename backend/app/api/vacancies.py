"""Вакансии [доп.] (концепция §3.1 шаг 13, §3.2 шаг 11, §5.5; spec-contact §2.2 п. 7).

Работодатель публикует бриф как вакансию (тот же текст, обязательная вилка). Кандидат видит список, отсортированный
тем же Score, что и подборка работодателя (кандидат против брифа вакансии), и откликается с отдельным согласием
(POST /api/applications).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, require_roles
from app.core.errors import error_responses
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact import vacancies as svc
from app.services.contact.common import now
from app.services.contact.schemas import (
    VacancyCandidateDetail,
    VacancyCandidateItem,
    VacancyCreateIn,
    VacancyEmployerOut,
    VacancyPatchIn,
    VacancyPublishOut,
)

router = APIRouter(prefix="/vacancies", tags=["Вакансии"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]
CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]


def _employer_out(db: DbSession, vac: Any) -> dict[str, Any]:
    counts = svc.application_counts(db, [vac.id]).get(vac.id, {})
    return {
        **svc.vacancy_base(vac, now()),
        "applications_total": sum(counts.values()),
        "applications_new": counts.get("submitted", 0),
        "how_we_check": vac.test_profile or None,
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=VacancyPublishOut,
    summary="Опубликовать бриф как вакансию",
    description=(
        "Тот же текст и вилка, что в брифе (можно уточнить заголовок, описание, вилку, формат, город). Вилка "
        "обязательна и проверяется как в приглашении: шире 1,6× — предупреждение, шире 2× — 400. Нужен "
        "заполненный профиль компании (409). Один бриф — одна опубликованная вакансия (409 `vacancy_exists`)."
    ),
)
def publish(body: VacancyCreateIn, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    vac, warnings = svc.publish(
        db,
        user,
        svc.VacancySpec(
            need_id=body.need_id,
            title=body.title,
            description=body.description,
            salary_from=body.salary_from,
            salary_to=body.salary_to,
            salary_type=body.salary_type,
            format=body.format,
            city=body.city,
        ),
    )
    db.commit()
    return {"vacancy": _employer_out(db, vac), "warnings": warnings}


@router.get(
    "/mine",
    response_model=list[VacancyEmployerOut],
    summary="Мои вакансии",
    description="Вакансии компании (Р10) со счётчиками откликов и блоком «как мы проверим».",
)
def mine(user: EmployerUser, db: DbSession) -> list[dict[str, Any]]:
    return svc.employer_list(db, user)


@router.patch(
    "/{vacancy_id}",
    response_model=VacancyPublishOut,
    summary="Изменить вакансию",
    description="Меняет переданные поля опубликованной вакансии. Вилка проверяется заново. 409 — вакансия снята.",
)
def patch(vacancy_id: uuid.UUID, body: VacancyPatchIn, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    vac = svc.owned_vacancy(db, user, vacancy_id)
    warnings = svc.update(db, vac, svc.VacancyPatch(**body.model_dump()))
    db.commit()
    return {"vacancy": _employer_out(db, vac), "warnings": warnings}


@router.post(
    "/{vacancy_id}/close",
    response_model=VacancyEmployerOut,
    summary="Снять вакансию с публикации",
    description="Новые отклики не принимаются; поданные отклики остаются, ответить на них нужно в срок.",
)
def close(vacancy_id: uuid.UUID, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    vac = svc.owned_vacancy(db, user, vacancy_id)
    svc.close(db, vac)
    db.commit()
    return _employer_out(db, vac)


@router.get(
    "",
    response_model=list[VacancyCandidateItem],
    summary="Вакансии для меня",
    description=(
        "Опубликованные вакансии (К12), отсортированные тем же Score, что видит работодатель (вы против брифа "
        "вакансии): компания и её надёжность, вилка, статус вашего отклика. `only_matching=true` (по умолчанию) "
        "скрывает вакансии вне ваших категорий (Score 0). Без подтверждённой категории — по свежести публикации."
    ),
)
def candidate_list(
    user: CandidateUser,
    db: DbSession,
    only_matching: Annotated[bool, Query(description="Только вакансии по вашим категориям")] = True,
) -> list[dict[str, Any]]:
    return svc.candidate_list(db, user, only_matching=only_matching)


@router.get(
    "/{vacancy_id}",
    response_model=VacancyCandidateDetail,
    summary="Вакансия (кандидат)",
    description=(
        "Карточка вакансии: вилка, компания и надёжность с разложением, ваш Score, «как мы проверим», текст "
        "согласия на передачу контактов для отклика."
    ),
)
def candidate_detail(vacancy_id: uuid.UUID, user: CandidateUser, db: DbSession) -> dict[str, Any]:
    return svc.candidate_detail(db, user, vacancy_id)
