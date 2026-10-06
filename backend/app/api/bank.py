"""Раздел «Банк кандидатов» — поиск без брифа (Р9, концепция §3.2 шаг 7, spec-contact §2.1).

Фильтры: специализация, грейд, категория, стек (переключатель «только подтверждённые»), «есть ФСП», отрасль,
режим поиска. Сортировка 0,55·T + 0,25·F + 0,20·A (config/scoring.yaml: bank.weights). Кандидат — псевдоним
и непрозрачная ссылка `ref` в рамках компании; «📌 в шортлист брифа X» — PUT /api/needs/{need_id}/shortlist/{ref}.
Переход в банк и обратно ничего не теряет: версии подборки и шортлист живут на бэкенде.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import CurrentUser, DbSession, LimiterDep, SettingsDep, require_roles
from app.contact.settings import get_contact_config
from app.core.errors import error_responses
from app.core.settings import RateRule
from app.models.enums import UserRole
from app.models.users import User
from app.services.matching import bank as svc
from app.services.matching import needs as needs_svc
from app.services.matching import privacy
from app.services.matching.schemas import BankOut, BankSummaryOut, CandidateCardOut, Grade, SkillOut

router = APIRouter(prefix="/bank", tags=["Потребности и подборка"], responses=error_responses())
skills_router = APIRouter(prefix="/skills", tags=["Потребности и подборка"], responses=error_responses())

Employer = Annotated[User, Depends(require_roles(UserRole.employer))]


def _secret(settings: SettingsDep) -> str:
    return settings.pseudonym_secret.get_secret_value()


Secret = Annotated[str, Depends(_secret)]


def _limit(limiter: LimiterDep, user: User, scope: str = "matching_bank") -> None:
    per_minute = int(get_contact_config().quotas.rate_limit_per_minute)
    limiter.check(scope, str(user.id), RateRule(per_minute, 60))


@router.get(
    "",
    response_model=BankOut,
    summary="Банк кандидатов",
    description=(
        "Поиск без брифа среди кандидатов с подтверждённой категорией и согласием на показ слепой карточки. "
        "Стек: навыки (id справочника) должны быть подтверждены тестом, задачей или ФСП; с confirmed_only=false "
        "подходят и заявленные (серые чипы). Кандидат показывается один раз — лучшей категорией. Сортировка "
        "0,55·T + 0,25·F + 0,20·A, затем T, затем детерминированный ключ. Фильтр «есть ФСП» никого не удаляет: "
        "не прошедшие уходят в группу `hidden` («Скрыто фильтрами (N)», концепция §6.4). need_id — отметить, кто "
        "уже в шортлисте этого брифа. 422 — неизвестная категория, навык, отрасль или режим поиска."
    ),
)
def bank_search(
    user: Employer,
    db: DbSession,
    limiter: LimiterDep,
    secret: Secret,
    spec: Annotated[str | None, Query(max_length=32, description="Специализация, например backend_python")] = None,
    grade: Annotated[Grade | None, Query(description="Грейд")] = None,
    category: Annotated[str | None, Query(max_length=64, description="Категория spec:grade")] = None,
    skill: Annotated[list[str] | None, Query(max_length=10, description="Навыки стека (id справочника), можно несколько")] = None,
    confirmed_only: Annotated[bool, Query(description="Стек: только подтверждённые (по умолчанию) или и заявленные")] = True,
    has_fsp: Annotated[bool | None, Query(description="Есть достижения ФСП")] = None,
    industry: Annotated[str | None, Query(max_length=32, description="Отрасль интереса (код)")] = None,
    search_mode: Annotated[str | None, Query(max_length=16, description="Режим поиска: active или open")] = None,
    page: Annotated[int, Query(ge=1, le=200, description="Страница (по 10)")] = 1,
    hidden_page: Annotated[int, Query(ge=1, le=200, description="Страница группы «Скрыто фильтрами»")] = 1,
    need_id: Annotated[uuid.UUID | None, Query(description="Бриф для отметки «уже в шортлисте»")] = None,
) -> BankOut:
    _limit(limiter, user)
    if need_id is not None:
        needs_svc.get_need(db, user, need_id)  # чужой бриф — 404
    q = svc.BankQuery(
        spec=spec,
        grade=grade,
        category=category,
        skills=tuple(dict.fromkeys(skill or ())),
        confirmed_only=confirmed_only,
        has_fsp=has_fsp,
        industry=industry,
        search_mode=search_mode,
        page=page,
        hidden_page=hidden_page,
        need_id=need_id,
    )
    out = BankOut.model_validate(svc.bank_search(db, user, q, secret=secret))
    db.commit()  # псевдонимы сохраняются при первом показе
    return out


@router.get(
    "/candidates/{ref}",
    response_model=CandidateCardOut,
    summary="Карточка кандидата из банка",
    description=(
        "Слепая карточка (после взаимного «да» — раскрытая) без привязки к брифу. Открытие записывается: "
        "пригласить можно только того, чью карточку открывали (DECISIONS п. 7). 404 — ссылка чужая или кандидат "
        "сейчас недоступен."
    ),
)
def bank_card(
    ref: Annotated[str, Path(min_length=4, max_length=32, description="Непрозрачная ссылка на кандидата")],
    user: Employer,
    db: DbSession,
    limiter: LimiterDep,
    secret: Secret,
) -> CandidateCardOut:
    _limit(limiter, user)
    view = svc.bank_card(db, user, ref, secret=secret)
    out = CandidateCardOut.model_validate(privacy.card_dto(view))
    db.commit()
    return out


@router.get(
    "/summary",
    response_model=BankSummaryOut,
    summary="Сводка банка для карты талантов",
    description=(
        "Одним запросом: по каждой специализации и грейду — число подтверждённых тестом кандидатов, доля с ФСП и ожидания "
        "по зарплате «на руки» (P25/P50/P75); плюс сколько прошли опрос по направлению и ждут теста. Те же правила видимости, "
        "что у списка банка, каждый кандидат считается один раз — лучшей категорией. Приватность: P25/P50/P75 появляются, "
        "когда в ячейке не меньше `privacy_min_n` кандидатов с указанным минимумом, и округлены до ступени. Лимит запросов "
        "отдельный от списка банка: карта не съедает его."
    ),
)
def bank_summary(user: Employer, db: DbSession, limiter: LimiterDep) -> BankSummaryOut:
    _limit(limiter, user, "matching_bank_summary")
    out = BankSummaryOut.model_validate(svc.bank_summary(db, user))
    db.commit()  # ensure_fresh мог пересобрать кэш признаков
    return out


@skills_router.get(
    "",
    response_model=list[SkillOut],
    summary="Справочник навыков",
    description=(
        "Навыки справочника (config/skills.yaml) для фильтров банка и брифа: id, название, категория, модули теста, "
        "специализации и флаг `testable` — навык можно подтвердить тестом. Для фильтра «стек» в банке берутся `testable` навыки "
        "(остальные в банке будут только «заявлено»). Нужен вход."
    ),
)
def skills_list(
    _: CurrentUser,
    testable: Annotated[bool | None, Query(description="true — только проверяемые тестом, false — только непроверяемые")] = None,
    spec: Annotated[str | None, Query(max_length=32, description="Только навыки, значимые для специализации")] = None,
) -> list[SkillOut]:
    items = svc.skills_dictionary()
    if testable is not None:
        items = [s for s in items if s["testable"] is testable]
    if spec is not None:
        items = [s for s in items if spec in s["specs"]]
    return [SkillOut.model_validate(s) for s in items]
