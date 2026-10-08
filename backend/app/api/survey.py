"""Раздел «Опрос» (экран К1, концепция §2.4 и §3.1 шаг 3, spec-testing §2.2 п.2 и §3.7).

Четыре шага с сохранением после каждого: отрасли → «что делали руками за 12 месяцев» → технологии,
стаж и роли → специализация. Классификатор (app/testing/survey.py) даёт вероятности направлений и
подсказку «по ответам вы ближе к …», но решает кандидат. Самооценка и технологии — только серые чипы
«заявлено», в оценку уровня не входят. Направления survey_only — лист ожидания.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import DbSession, LimiterDep, require_roles
from app.core.errors import error_responses
from app.models.enums import UserRole
from app.models.users import User
from app.services.testing import limits
from app.services.testing import survey as svc
from app.services.testing.schemas import (
    SurveyActionsIn,
    SurveyClassificationOut,
    SurveyFormOut,
    SurveyHistoryItemOut,
    SurveyIndustriesIn,
    SurveySpecIn,
    SurveyStackIn,
    SurveyStateOut,
)

router = APIRouter(prefix="/survey", tags=["Опрос"], responses=error_responses())

Candidate = Annotated[User, Depends(require_roles(UserRole.candidate))]


@router.get(
    "/form",
    response_model=SurveyFormOut,
    summary="Анкета опроса",
    description=(
        "Структура опроса из справочника: отрасли, 16 действий с частотами, технологии с давностью, модули для "
        "самооценки и специализации со статусом (тест работает / лист ожидания / производная категория)."
    ),
)
def form(user: Candidate) -> SurveyFormOut:
    return svc.form_out()


@router.get(
    "/current",
    response_model=SurveyStateOut,
    summary="Текущая анкета",
    description=(
        "Последняя анкета кандидата: сколько шагов сохранено, какой шаг следующий, сохранённые ответы "
        "(чтобы продолжить после обрыва связи) и итог классификатора, если опрос завершён."
    ),
)
def current(user: Candidate, db: DbSession) -> SurveyStateOut:
    return svc.state_out(db, svc.latest(db, user.id))


@router.put(
    "/steps/industries",
    response_model=SurveyStateOut,
    summary="Шаг 1: сфера бизнеса",
    description=(
        "Отрасли интереса — только фасет для подбора, в оценку не входят. После завершённого опроса этот шаг "
        "начинает новую версию анкеты (прошлые ответы подставляются)."
    ),
)
def step_industries(body: SurveyIndustriesIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> SurveyStateOut:
    limiter.check("survey", str(user.id), limits.RL_SURVEY)
    sv = svc.save_industries(db, user, body.industries)
    out = svc.state_out(db, sv)
    db.commit()
    return out


@router.put(
    "/steps/actions",
    response_model=SurveyStateOut,
    summary="Шаг 2: что делали руками",
    description="16 действий за последние 12 месяцев × «не делал / иногда / регулярно». Неуказанные — «не делал».",
)
def step_actions(body: SurveyActionsIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> SurveyStateOut:
    limiter.check("survey", str(user.id), limits.RL_SURVEY)
    sv = svc.save_actions(db, user, dict(body.actions))
    out = svc.state_out(db, sv)
    db.commit()
    return out


@router.put(
    "/steps/stack",
    response_model=SurveyStateOut,
    summary="Шаг 3: технологии и стаж",
    description=(
        "Технологии с давностью использования, стаж в месяцах, роли в команде и самооценка по модулям 0–3. "
        "Всё это — только «заявлено»: подсказка по стажу при выборе грейда и серые чипы, в оценку не входит."
    ),
)
def step_stack(body: SurveyStackIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> SurveyStateOut:
    limiter.check("survey", str(user.id), limits.RL_SURVEY)
    sv = svc.save_stack(
        db,
        user,
        technologies=dict(body.technologies),
        experience_months=body.experience_months,
        roles=list(body.roles),
        self_assessment=dict(body.self_assessment),
    )
    out = svc.state_out(db, sv)
    db.commit()
    return out


@router.post(
    "/hint",
    response_model=SurveyClassificationOut,
    summary="Подсказка по специализации",
    description=(
        "Предпросмотр классификатора для выбранной на шаге 4 специализации (ничего не сохраняет): вероятности "
        "направлений и подсказка «по ответам вы ближе к …», если разница не меньше 0,2. Решает кандидат."
    ),
)
def hint(body: SurveySpecIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> SurveyClassificationOut:
    limiter.check("survey", str(user.id), limits.RL_SURVEY)
    return svc.hint(db, user, body.chosen_spec)


@router.put(
    "/steps/specialization",
    response_model=SurveyStateOut,
    summary="Шаг 4: специализация",
    description=(
        "Завершает опрос: итоговая специализация — выбор кандидата. Для тестируемых направлений открывается выбор "
        "грейда и тест; для Fullstack — обе составляющие; для направлений без теста — лист ожидания."
    ),
)
def step_specialization(body: SurveySpecIn, user: Candidate, db: DbSession, limiter: LimiterDep) -> SurveyStateOut:
    limiter.check("survey", str(user.id), limits.RL_SURVEY)
    sv, _ = svc.complete(db, user, body.chosen_spec)
    out = svc.state_out(db, sv)
    db.commit()
    return out


@router.get(
    "/history",
    response_model=list[SurveyHistoryItemOut],
    summary="История опросов",
    description="Завершённые анкеты кандидата, от новых к старым: версия, выбранная и наиболее вероятная специализация, стаж.",
)
def history(user: Candidate, db: DbSession) -> list[SurveyHistoryItemOut]:
    return svc.history_out(db, user)
