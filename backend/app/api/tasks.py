"""Регулярные короткие задачи [доп.] (концепция §3.1 шаг 14, §3.2 шаг 12, §5.4) и «проверочный набор по
вакансии» (DECISIONS 3).

Работодатель: каталог откалиброванных семейств (тип A — каждому кандидату свой одноразовый экземпляр,
автопроверка) или «предложи подход» (тип B, рубрика 4 × 0–2); аудитория — специализация × грейд ±1; не больше
5 активных задач и 15 минут; решения обезличены. Кандидат: не чаще одного предложения в 7 дней, не больше 3
активных, после 3 пропусков подряд — пауза 30 дней; без задач компаний — тренировочная задача платформы.
Итоги идут в свежесть профиля и подтверждение модуля (подбор их читает), тип A — ещё и в уровень.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import DbSession, LimiterDep, SettingsDep, require_roles
from app.core.errors import error_responses
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact.common import cfg as contact_cfg
from app.services.contact.common import check_rate
from app.services.tasks import candidate as cand_svc
from app.services.tasks import catalog as catalog_svc
from app.services.tasks import check_sets as check_svc
from app.services.tasks import employer as emp_svc
from app.services.tasks.schemas import (
    AssignmentActionOut,
    AssignmentStartOut,
    CheckSetIn,
    CheckSetOut,
    CheckSetResultsOut,
    FamilyOut,
    ReviewIn,
    SubmissionOut,
    SubmitIn,
    TaskCreateIn,
    TaskOut,
    TasksOverviewOut,
)

router = APIRouter(prefix="/tasks", tags=["Регулярные задачи"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]
CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]


def _secret(settings: SettingsDep) -> str:
    return settings.pseudonym_secret.get_secret_value()


# --- работодатель ------------------------------------------------------------------------------


@router.get(
    "/catalog",
    response_model=list[FamilyOut],
    summary="Каталог семейств (тип A)",
    description=(
        "Откалиброванные семейства банка заданий: модуль, время, грейд, бейдж «откалиброван». Каждый кандидат "
        "получит свой одноразовый экземпляр выбранного семейства. Фильтры: `specialization`, `module`."
    ),
)
def catalog(
    user: EmployerUser,
    db: DbSession,
    specialization: Annotated[str | None, Query(description="Код специализации")] = None,
    module: Annotated[str | None, Query(description="Код модуля теста")] = None,
) -> list[dict[str, Any]]:
    return catalog_svc.catalog(db, specialization=specialization, module=module)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    response_model=TaskOut,
    summary="Создать задачу",
    description=(
        "Тип A (`catalog`): `family_id` из каталога. Тип B (`approach`): `title`, `prompt` ≥ 50 символов, рубрика "
        "из шаблона (4 критерия по 0–2). Аудитория — специализация × грейд ±1. Обязательна отметка «это не рабочая "
        "задача компании». 409 — уже 5 активных задач или профиль компании не заполнен; 400 — контакты или просьбы "
        "об оплате в тексте задачи."
    ),
)
def create(body: TaskCreateIn, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    task = emp_svc.create_task(
        db,
        user,
        emp_svc.TaskSpec(
            kind=body.kind,
            specialization=body.specialization,
            grade=body.grade,
            not_real_work_ack=body.not_real_work_ack,
            title=body.title,
            family_id=body.family_id,
            prompt=body.prompt,
            time_limit_min=body.time_limit_min,
            tags=list(body.tags),
        ),
    )
    db.commit()
    return emp_svc.task_out(task)


@router.get(
    "",
    response_model=list[TaskOut],
    summary="Мои задачи",
    description="Задачи компании (без проверочных наборов) со статистикой: предложено, решено, ждут оценки, средний балл.",
)
def list_tasks(user: EmployerUser, db: DbSession) -> list[dict[str, Any]]:
    return emp_svc.list_tasks(db, user)


def _status(task_id: uuid.UUID, user: User, db: DbSession, action: str) -> dict[str, Any]:
    task = emp_svc.owned_task(db, user, task_id)
    emp_svc.change_status(db, task, action)
    db.commit()
    return emp_svc.task_out(task)


@router.post("/{task_id}/pause", response_model=TaskOut, summary="Поставить задачу на паузу", description="Новые предложения по задаче не создаются.")
def pause(task_id: uuid.UUID, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    return _status(task_id, user, db, "pause")


@router.post(
    "/{task_id}/activate",
    response_model=TaskOut,
    summary="Возобновить задачу",
    description="409 — уже 5 активных задач.",
)
def activate(task_id: uuid.UUID, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    return _status(task_id, user, db, "activate")


@router.post("/{task_id}/close", response_model=TaskOut, summary="Закрыть задачу", description="Задача больше не предлагается; решения остаются.")
def close(task_id: uuid.UUID, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    return _status(task_id, user, db, "close")


@router.get(
    "/{task_id}/submissions",
    response_model=list[SubmissionOut],
    summary="Решения по задаче",
    description=(
        "Обезличенные решения: псевдоним «К-…» в рамках компании и ссылка для «пригласить». Тип A — верно/неверно, "
        "тип B — текст подхода и оценка по рубрике."
    ),
)
def submissions(task_id: uuid.UUID, user: EmployerUser, db: DbSession, settings: SettingsDep) -> list[dict[str, Any]]:
    task = emp_svc.owned_task(db, user, task_id)
    out = emp_svc.submissions(db, _secret(settings), task)
    db.commit()  # псевдонимы фиксируются при первом показе
    return out


@router.post(
    "/assignments/{assignment_id}/review",
    response_model=SubmissionOut,
    summary="Оценить решение (тип B)",
    description=(
        "Оценка по рубрике: все 4 критерия по 0–2 и комментарий ≤ 300 символов. Кандидат получает уведомление, "
        "итог идёт в свежесть его профиля. После оценки кандидата можно пригласить."
    ),
)
def review(assignment_id: uuid.UUID, body: ReviewIn, user: EmployerUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    out = emp_svc.review(db, user, _secret(settings), assignment_id, body.scores, body.comment)
    db.commit()
    return out


@router.post(
    "/check-sets",
    status_code=status.HTTP_201_CREATED,
    response_model=CheckSetOut,
    summary="Проверочный набор по вакансии",
    description=(
        "DECISIONS 3: must-навыки брифа → модули теста → 3–5 откалиброванных семейств типа A (≤ 15 минут). Каждому "
        "кандидату из шортлиста брифа — свои одноразовые экземпляры как регулярная задача (срок 3 дня). Стандартный "
        "тест и грейд не меняются; результат подтверждает модули в подборке. Кандидатам вне шортлиста и тем, кто "
        "сейчас не принимает обращений компании, набор не уходит (`rejected`, причина не раскрывается). "
        "409 — навыки брифа тестом не проверяются."
    ),
)
def create_check_set(body: CheckSetIn, user: EmployerUser, db: DbSession, settings: SettingsDep, limiter: LimiterDep) -> dict[str, Any]:
    check_rate(limiter, "check_set", user.company_id or user.id, contact_cfg().quotas.rate_limit_per_minute)
    out = check_svc.create(db, user, _secret(settings), body.need_id, list(body.candidates))
    db.commit()
    return out


@router.get(
    "/check-sets/{need_id}",
    response_model=CheckSetResultsOut,
    summary="Результаты проверочного набора",
    description="По каждому кандидату шортлиста: сколько задач решено, сколько верно, по каким модулям.",
)
def check_set_results(need_id: uuid.UUID, user: EmployerUser, db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    out = check_svc.results(db, user, _secret(settings), need_id)
    db.commit()
    return out


# --- кандидат --------------------------------------------------------------------------------


@router.get(
    "/me",
    response_model=TasksOverviewOut,
    summary="Мои задачи (кандидат)",
    description=(
        "Предложения и история (К11): компания или «тренировочная задача от платформы», время, на что влияет. "
        "Новое предложение появляется не чаще раза в 7 дней, активных — не больше 3; после 3 пропусков подряд — "
        "пауза 30 дней (отказ не штрафуется)."
    ),
)
def my_tasks(user: CandidateUser, db: DbSession) -> dict[str, Any]:
    out = cand_svc.overview(db, user)
    db.commit()
    return out


@router.post(
    "/assignments/{assignment_id}/start",
    response_model=AssignmentStartOut,
    summary="Открыть задачу",
    description=(
        "Тип A: генерируется ваш одноразовый экземпляр (ключ остаётся на сервере), включается серверный таймер. "
        "Тип B: текст задачи и критерии оценки. Повторное открытие возвращает тот же экземпляр. "
        "503 — генератор семейства временно недоступен."
    ),
    responses=error_responses(503),
)
def start(assignment_id: uuid.UUID, user: CandidateUser, db: DbSession) -> dict[str, Any]:
    out = cand_svc.start(db, user, assignment_id)
    db.commit()
    return out


@router.post(
    "/assignments/{assignment_id}/submit",
    response_model=AssignmentActionOut,
    summary="Отправить решение",
    description=(
        "Тип A проверяется сразу: верно/неверно, разбор и правильный ответ вашего экземпляра; после таймера ответ не "
        "засчитывается. Тип B уходит компании на оценку — без контактов и ссылок (DECISIONS 20)."
    ),
)
def submit(assignment_id: uuid.UUID, body: SubmitIn, user: CandidateUser, db: DbSession, limiter: LimiterDep) -> dict[str, Any]:
    check_rate(limiter, "task_submit", user.id, contact_cfg().http_limits.task_submit_per_minute)
    out = cand_svc.submit(db, user, assignment_id, body.answer)
    db.commit()
    return out


@router.post(
    "/assignments/{assignment_id}/skip",
    response_model=AssignmentActionOut,
    summary="Пропустить задачу",
    description="Пропуск не штрафуется; после 3 пропусков подряд предложения встают на паузу 30 дней.",
)
def skip(assignment_id: uuid.UUID, user: CandidateUser, db: DbSession) -> dict[str, Any]:
    out = cand_svc.skip(db, user, assignment_id)
    db.commit()
    return out
