"""Регулярные задачи работодателя (экран Р11, концепция §3.2 шаг 12, §5.4).

Тип A — откалиброванное семейство из каталога: каждый кандидат получает свой одноразовый экземпляр,
проверка автоматическая. Тип B — «предложи подход»: общий текст и рубрика из шаблона (4 критерия по 0–2),
оценивает работодатель. Аудитория — специализация × грейд ±1; ≤ 5 активных задач; ≤ 15 минут;
обязательная отметка «это не рабочая задача компании». Решения обезличены: псевдоним «К-…» и кнопка «пригласить».
"""

from __future__ import annotations

import uuid
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contact.antifraud import check_text, comment_contacts_problem
from app.core.errors import bad_request, conflict, not_found
from app.models.company import Company
from app.models.enums import AssignmentStatus, EmployerTaskStatus, TaskKind
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.users import User
from app.services.contact import identity
from app.services.contact.company import company_of, require_complete
from app.services.notify import notify_user

from .common import (
    DONE,
    GRADES,
    SCORED,
    cfg,
    family,
    family_minutes,
    grade_name,
    invalidate_features,
    module_name,
    now,
    rel,
    spec_names,
    withdrawn_family_ids,
)

# --- создание ----------------------------------------------------------------------------------


@dataclass
class TaskSpec:
    kind: str
    specialization: str
    grade: str
    not_real_work_ack: bool
    title: str | None = None
    family_id: str | None = None
    prompt: str | None = None
    time_limit_min: int | None = None
    tags: list[str] = field(default_factory=list)


def _skill_ids(tags: Sequence[str]) -> list[str]:
    """Теги задачи — навыки из справочника (для бонуса свежести «тег ∈ навыки брифа»). Неизвестные отбрасываем."""
    try:
        from app.matching.skills import get_catalog

        cat = get_catalog()
        return list(dict.fromkeys(t for t in tags if t in cat.skills))
    except Exception:  # noqa: BLE001 — справочник недоступен: теги как есть
        return list(dict.fromkeys(t for t in tags if t))


def _active_count(db: Session, company_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(EmployerTask)
            .where(
                EmployerTask.company_id == company_id,
                EmployerTask.status == EmployerTaskStatus.active.value,
                EmployerTask.is_check_set.is_(False),
            )
        )
        or 0
    )


def _check_audience(spec: str, grade: str) -> None:
    if spec not in spec_names():
        raise bad_request("audience_invalid", "Выберите специализацию аудитории из справочника")
    if grade not in GRADES:
        raise bad_request("audience_invalid", "Выберите грейд аудитории: стажёр, junior, middle или senior")


def create_task(db: Session, user: User, spec: TaskSpec) -> EmployerTask:
    c = cfg()
    company = company_of(db, user)
    require_complete(company)
    if not spec.not_real_work_ack:
        raise bad_request("not_real_work_ack_required", f"Подтвердите отметку: «{c.not_real_work_label}»")
    _check_audience(spec.specialization, spec.grade)
    if _active_count(db, company.id) >= c.employer_active_max:
        raise conflict(
            "tasks_active_limit",
            f"Активных задач может быть не больше {c.employer_active_max} — поставьте лишние на паузу или закройте",
        )
    at = now()
    task = EmployerTask(
        company_id=company.id,
        created_by=user.id,
        kind=spec.kind,
        audience={"specialization": spec.specialization, "grade": spec.grade},
        tags=_skill_ids(spec.tags),
        status=EmployerTaskStatus.active.value,
        not_real_work_ack=True,
        created_at=at,
        updated_at=at,
    )
    if spec.kind == TaskKind.catalog.value:
        fam = family(spec.family_id)
        if fam is None or fam.id in withdrawn_family_ids(db):
            raise bad_request("family_not_found", "Такого семейства нет в каталоге")
        fits = spec.specialization in fam.specs or (
            spec.specialization == "fullstack" and bool({"backend_python", "frontend_js"} & set(fam.specs))
        )
        if not fits:
            raise bad_request("family_audience", "Это семейство не проверяет выбранную специализацию")
        minutes = family_minutes(fam)
        if minutes > c.max_minutes:
            raise bad_request("task_too_long", f"Задача — не дольше {c.max_minutes} минут")
        task.family_id = fam.id
        task.module = fam.module
        task.time_limit_min = minutes
        task.title = (spec.title or fam.title).strip()[: c.title_max_len]
    elif spec.kind == TaskKind.approach.value:
        prompt = (spec.prompt or "").strip()
        if len(prompt) < c.prompt_min_len:
            raise bad_request("prompt_too_short", f"Опишите задачу — не короче {c.prompt_min_len} символов")
        if len(prompt) > c.prompt_max_len:
            raise bad_request("prompt_too_long", f"Текст задачи — не длиннее {c.prompt_max_len} символов")
        fraud = check_text(prompt, spec.title or "")
        if fraud.caution:
            # Антифейк (концепция §8.1): контакты, предоплата и документы в задаче недопустимы
            raise bad_request("task_text_caution", "Уберите из задачи: " + "; ".join(fraud.reasons).lower())
        title = (spec.title or "").strip()
        if not title:
            raise bad_request("title_required", "Укажите название задачи")
        minutes = spec.time_limit_min or c.max_minutes
        if not 1 <= minutes <= c.max_minutes:
            raise bad_request("task_too_long", f"Задача — не дольше {c.max_minutes} минут")
        task.title = title[: c.title_max_len]
        task.prompt = prompt
        task.time_limit_min = minutes
        task.rubric = {"max_points": c.rubric.max_points, "criteria": dict(c.rubric.criteria)}
    else:
        raise bad_request("kind_invalid", "Тип задачи: из каталога или «предложи подход»")
    db.add(task)
    db.flush()
    return task


def owned_task(db: Session, user: User, task_id: uuid.UUID) -> EmployerTask:
    task = db.get(EmployerTask, task_id)
    if task is None or user.company_id is None or task.company_id != user.company_id:
        raise not_found("task_not_found", "Задача не найдена")
    return task


_STATUS_FLOW = {
    "pause": (EmployerTaskStatus.active.value, EmployerTaskStatus.paused.value),
    "activate": (EmployerTaskStatus.paused.value, EmployerTaskStatus.active.value),
}


def change_status(db: Session, task: EmployerTask, action: str) -> EmployerTask:
    if task.is_check_set:
        raise conflict("task_is_check_set", "Проверочный набор управляется из брифа")
    if action == "close":
        task.status = EmployerTaskStatus.closed.value
    else:
        src, dst = _STATUS_FLOW[action]
        if task.status != src:
            raise conflict("task_status", "Это действие недоступно в текущем статусе задачи")
        if dst == EmployerTaskStatus.active.value and task.company_id and _active_count(db, task.company_id) >= cfg().employer_active_max:
            raise conflict("tasks_active_limit", f"Активных задач может быть не больше {cfg().employer_active_max}")
        task.status = dst
    task.updated_at = now()
    return task


# --- чтение ------------------------------------------------------------------------------------


def _stats(db: Session, task_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    out: dict[uuid.UUID, dict[str, Any]] = {i: {"counts": Counter(), "scores": []} for i in task_ids}
    if not task_ids:
        return out
    for tid, status, score in db.execute(
        select(TaskAssignment.employer_task_id, TaskAssignment.status, TaskAssignment.score).where(
            TaskAssignment.employer_task_id.in_(list(task_ids))
        )
    ):
        out[tid]["counts"][status] += 1
        if status in SCORED and score is not None:
            out[tid]["scores"].append(float(score))
    return out


def task_out(task: EmployerTask, stats: Mapping[str, Any] | None = None) -> dict[str, Any]:
    c = cfg()
    aud = task.audience or {}
    counts: Counter = (stats or {}).get("counts") or Counter()
    scores: list[float] = (stats or {}).get("scores") or []
    return {
        "id": task.id,
        "kind": task.kind,
        "kind_label": c.kind_labels.get(task.kind, task.kind),
        "title": task.title,
        "family_id": task.family_id,
        "module": task.module,
        "module_name": module_name(task.module),
        "prompt": task.prompt,
        "rubric": task.rubric,
        "audience": {
            "specialization": aud.get("specialization"),
            "specialization_name": spec_names().get(aud.get("specialization") or "", aud.get("specialization")),
            "grade": aud.get("grade"),
            "grade_name": grade_name(aud.get("grade")),
            "window": c.audience_grade_window,
        },
        "tags": list(task.tags or []),
        "minutes": task.time_limit_min,
        "status": task.status,
        "is_check_set": bool(task.is_check_set),
        "need_id": task.need_id,
        "not_real_work_label": c.not_real_work_label,
        "stats": {
            "offered": sum(counts.values()),
            "submitted": sum(counts[s] for s in DONE),
            "awaiting_review": counts[AssignmentStatus.awaiting_review.value],
            "skipped": counts[AssignmentStatus.skipped.value] + counts[AssignmentStatus.expired.value],
            "avg_score": round(sum(scores) / len(scores), 2) if scores else None,
        },
    }


def list_tasks(db: Session, user: User) -> list[dict[str, Any]]:
    company = company_of(db, user)
    rows = list(
        db.scalars(
            select(EmployerTask)
            .where(EmployerTask.company_id == company.id, EmployerTask.is_check_set.is_(False))
            .order_by(EmployerTask.created_at.desc())
        )
    )
    stats = _stats(db, [t.id for t in rows])
    return [task_out(t, stats.get(t.id)) for t in rows]


def score_text(task: EmployerTask, a: TaskAssignment) -> str | None:
    if a.score is None:
        return None
    if task.kind == TaskKind.catalog.value:
        return "Верно" if a.is_correct else "Неверно"
    rub = task.rubric or {}
    total = int(rub.get("max_points", cfg().rubric.max_points)) * len(rub.get("criteria") or cfg().rubric.criteria)
    return f"{round(float(a.score) * total)} из {total}"


def submission_out(db: Session, secret: str, task: EmployerTask, a: TaskAssignment, at: datetime) -> dict[str, Any]:
    """Решение глазами работодателя: псевдоним и ссылка для «пригласить», без id кандидата (DECISIONS 20)."""
    assert task.company_id is not None
    ident = identity.alias(db, secret, task.company_id, a.candidate_id)
    labels = cfg().status_labels
    return {
        "assignment_id": a.id,
        "pseudonym": ident.pseudonym,
        "candidate_ref": ident.ref,
        "status": a.status,
        "status_label": labels.get(a.status, a.status),
        "submitted_text": rel(a.submitted_at, at),
        "answer": a.answer if task.kind == TaskKind.approach.value else None,
        "is_correct": a.is_correct,
        "score": a.score,
        "score_text": score_text(task, a),
        "rubric_scores": a.rubric_scores,
        "review_comment": a.review_comment,
        "can_review": task.kind == TaskKind.approach.value and a.status == AssignmentStatus.awaiting_review.value,
    }


def submissions(db: Session, secret: str, task: EmployerTask) -> list[dict[str, Any]]:
    at = now()
    rows = db.scalars(
        select(TaskAssignment)
        .where(TaskAssignment.employer_task_id == task.id, TaskAssignment.status.in_(DONE))
        .order_by(TaskAssignment.submitted_at.desc().nulls_last(), TaskAssignment.id)
    )
    return [submission_out(db, secret, task, a, at) for a in rows]


# --- оценка решения типа B -------------------------------------------------------------------


def owned_assignment(db: Session, user: User, assignment_id: uuid.UUID) -> tuple[TaskAssignment, EmployerTask]:
    a = db.get(TaskAssignment, assignment_id, with_for_update=True, populate_existing=True)
    task = db.get(EmployerTask, a.employer_task_id) if a else None
    if a is None or task is None or user.company_id is None or task.company_id != user.company_id:
        raise not_found("assignment_not_found", "Решение не найдено")
    return a, task


def review(
    db: Session,
    user: User,
    secret: str,
    assignment_id: uuid.UUID,
    scores: Mapping[str, int],
    comment: str | None,
) -> dict[str, Any]:
    a, task = owned_assignment(db, user, assignment_id)
    if task.kind != TaskKind.approach.value:
        raise conflict("review_not_needed", "Задачи из каталога проверяются автоматически")
    if a.status != AssignmentStatus.awaiting_review.value:
        raise conflict("review_status", "Это решение уже оценено или ещё не отправлено")
    rub = task.rubric or {"max_points": cfg().rubric.max_points, "criteria": dict(cfg().rubric.criteria)}
    criteria: dict[str, str] = dict(rub.get("criteria") or {})
    max_points = int(rub.get("max_points", cfg().rubric.max_points))
    if set(scores) != set(criteria):
        raise bad_request(
            "rubric_incomplete", "Оцените все критерии: " + ", ".join(v.lower() for v in criteria.values())
        )
    for k, v in scores.items():
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= max_points:
            raise bad_request("rubric_value", f"Оценка по критерию «{criteria[k]}» — от 0 до {max_points}")
    if comment and len(comment) > cfg().comment_max_len:
        raise bad_request("comment_too_long", f"Комментарий — не длиннее {cfg().comment_max_len} символов")
    at = now()
    total = max_points * len(criteria)
    a.rubric_scores = {k: int(v) for k, v in scores.items()}
    # Балл хранится долей 0..1 — так его читает подбор (качество q задачи в свежести A)
    a.score = round(sum(scores.values()) / total, 4) if total else 0.0
    a.status = AssignmentStatus.reviewed.value
    a.reviewed_at = at
    a.finished_at = at
    a.reviewer_id = user.id
    a.review_comment = (comment or "").strip() or None
    company = db.get(Company, task.company_id) if task.company_id else None
    notify_user(
        db,
        a.candidate_id,
        "task",
        f"{(company.name if company else None) or 'Компания'} оценила ваше решение «{task.title}»: "
        f"{sum(scores.values())} из {total}",
        entity_type="task_assignment",
        entity_id=a.id,
    )
    # Работодатель видел обезличенное решение — «пригласить» из списка решений не упрётся в DECISIONS 7
    if task.company_id is not None:
        try:
            from app.services.matching.privacy import record_view

            record_view(db, task.company_id, a.candidate_id, viewer_id=user.id, need_id=task.need_id)
        except Exception:  # noqa: BLE001 — без записи просмотра пригласить можно после открытия карточки
            pass
    invalidate_features(db, a.candidate_id)
    db.flush()
    return submission_out(db, secret, task, a, at)


def screen_answer(text: str) -> str | None:
    """DECISIONS 20: решение типа B уходит работодателю до раскрытия — без контактов и ссылок."""
    return comment_contacts_problem(text)
