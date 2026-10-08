"""Регулярные задачи глазами кандидата (экран К11, концепция §3.1 шаг 14, §5.4).

- Предложение — не чаще одного в 7 дней, не больше 3 активных; после 3 пропусков подряд — пауза 30 дней
  (отказ не штрафуется). Предложения создаются лениво при открытии раздела.
- Аудитория — специализация × грейд ±1 подтверждённой категории. Если подходящих задач компаний нет —
  «тренировочная задача от платформы» из того же каталога.
- Тип A: при открытии генерируется свой одноразовый экземпляр (ключ — только на сервере), проверка
  автоматическая, серверный таймер. Тип B: текст и рубрика; решение без контактов уходит работодателю.
- Итог (балл, модуль, момент завершения) читает подбор: свежесть A и подтверждение модуля в K.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict, not_found
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.contact import Notification, Vacancy
from app.models.enums import AssignmentStatus, EmployerTaskStatus, TaskKind
from app.models.matching import Need
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.testing import ItemInstance
from app.models.users import User
from app.services.contact.reliability import company_reliability
from app.services.notify import notify_company

from .common import (
    ACTIVE,
    DONE,
    GRADES,
    SKIPS,
    SOURCE_CHECK_SET,
    SOURCE_OFFER,
    apply_to_theta,
    audience_fits,
    candidate_categories,
    cfg,
    families,
    family,
    family_minutes,
    grade_index,
    invalidate_features,
    issue_task_instance,
    module_name,
    now,
    rel,
    solve_deadline,
    withdrawn_family_ids,
)
from .employer import score_text, screen_answer

# --- ленивые сроки и пауза ------------------------------------------------------------------------


def expire_due(db: Session, candidate_id: uuid.UUID, at: datetime) -> None:
    """Не решённые к сроку задачи → «Срок истёк» (для серии пропусков считается как пропуск)."""
    rows = db.scalars(
        select(TaskAssignment).where(
            TaskAssignment.candidate_id == candidate_id,
            TaskAssignment.status.in_(ACTIVE),
            TaskAssignment.due_at.is_not(None),
            TaskAssignment.due_at <= at,
        )
    )
    for a in rows:
        a.status = AssignmentStatus.expired.value
    db.flush()


def pause_until(db: Session, candidate_id: uuid.UUID) -> datetime | None:
    """3 пропуска подряд среди предложений (не проверочных наборов) → пауза 30 дней от последнего."""
    c = cfg()
    rows = list(
        db.scalars(
            select(TaskAssignment)
            .where(
                TaskAssignment.candidate_id == candidate_id,
                TaskAssignment.source == SOURCE_OFFER,
                TaskAssignment.status.notin_(ACTIVE),
            )
            .order_by(TaskAssignment.offered_at.desc().nulls_last(), TaskAssignment.created_at.desc())
            .limit(c.skip_streak)
        )
    )
    if len(rows) < c.skip_streak or any(a.status not in SKIPS for a in rows):
        return None
    last = max((a.skipped_at or a.due_at or a.offered_at or a.created_at) for a in rows)
    return last + timedelta(days=c.skip_pause_days)


def _last_offer_at(db: Session, candidate_id: uuid.UUID) -> datetime | None:
    return db.scalar(
        select(func.max(TaskAssignment.offered_at)).where(
            TaskAssignment.candidate_id == candidate_id, TaskAssignment.source == SOURCE_OFFER
        )
    )


def _active_count(db: Session, candidate_id: uuid.UUID) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(TaskAssignment)
            .where(TaskAssignment.candidate_id == candidate_id, TaskAssignment.status.in_(ACTIVE))
        )
        or 0
    )


# --- выбор предложения --------------------------------------------------------------------------


def _pick(user_id: uuid.UUID, key: str) -> int:
    """Детерминированный порядок «кому что предложить» (без глобального random)."""
    return int(hashlib.sha256(f"task-offer|{user_id}|{key}".encode()).hexdigest()[:12], 16)


def _platform_task(db: Session, fam: Any, spec: str, at: datetime) -> EmployerTask:
    task = db.scalar(
        select(EmployerTask).where(EmployerTask.is_platform.is_(True), EmployerTask.family_id == fam.id).limit(1)
    )
    if task is not None:
        return task
    task = EmployerTask(
        company_id=None,
        kind=TaskKind.catalog.value,
        title=fam.title,
        family_id=fam.id,
        module=fam.module,
        audience={"specialization": spec, "grade": fam.target_grade},
        tags=[],
        time_limit_min=family_minutes(fam),
        status=EmployerTaskStatus.active.value,
        is_platform=True,
        not_real_work_ack=True,
        created_at=at,
        updated_at=at,
    )
    db.add(task)
    db.flush()
    return task


def _training_family(db: Session, user_id: uuid.UUID, cats: list[tuple[str, str]], at: datetime) -> tuple[Any, str] | None:
    """Тренировочная задача: семейство специализации кандидата, грейд ±1, ещё не выдававшееся ему."""
    window = cfg().audience_grade_window
    seen = set(
        db.scalars(
            select(TaskAssignment.family_id).where(
                TaskAssignment.candidate_id == user_id, TaskAssignment.family_id.is_not(None)
            )
        )
    )
    withdrawn = withdrawn_family_ids(db)
    options: list[tuple[Any, str]] = []
    for fid, fam in sorted(families().items()):
        if fid in seen or fid in withdrawn or family_minutes(fam) > cfg().max_minutes:
            continue
        fi = grade_index(fam.target_grade)
        for spec, grade in cats:
            ci = grade_index(grade)
            if spec in fam.specs and fi is not None and ci is not None and abs(fi - ci) <= window:
                options.append((fam, spec))
                break
    if not options:
        return None
    week = at.isocalendar()
    return min(options, key=lambda o: _pick(user_id, f"{week.year}-{week.week}|{o[0].id}"))


def ensure_offer(db: Session, user: User, at: datetime) -> TaskAssignment | None:
    """Создать новое предложение, если правила позволяют (лимиты §5.4). Иначе — None."""
    c = cfg()
    cats = candidate_categories(db, user.id)
    if not cats:
        return None
    # Два одновременных GET /tasks/me не должны создать два предложения в одну неделю
    db.get(User, user.id, with_for_update=True, populate_existing=True)
    paused = pause_until(db, user.id)
    if paused is not None and paused > at:
        return None
    if _active_count(db, user.id) >= c.candidate_active_max:
        return None
    last = _last_offer_at(db, user.id)
    if last is not None and at - last < timedelta(days=c.offer_interval_days):
        return None
    assigned = set(
        db.scalars(select(TaskAssignment.employer_task_id).where(TaskAssignment.candidate_id == user.id))
    )
    profile = db.get(CandidateProfile, user.id)
    blocked = set(profile.blocked_company_ids or []) if profile else set()
    tasks = [
        t
        for t in db.scalars(
            select(EmployerTask).where(
                EmployerTask.status == EmployerTaskStatus.active.value,
                EmployerTask.is_check_set.is_(False),
                EmployerTask.is_platform.is_(False),
                EmployerTask.company_id.is_not(None),
            )
        )
        if t.id not in assigned and t.company_id not in blocked and audience_fits(t.audience, cats, c.audience_grade_window)
    ]
    if tasks:
        task = min(tasks, key=lambda t: _pick(user.id, str(t.id)))
    else:
        picked = _training_family(db, user.id, cats, at)
        if picked is None:
            return None
        task = _platform_task(db, picked[0], picked[1], at)
        if task.id in assigned:
            return None
    a = TaskAssignment(
        id=uuid.uuid4(),
        employer_task_id=task.id,
        candidate_id=user.id,
        status=AssignmentStatus.offered.value,
        offered_at=at,
        due_at=at + timedelta(days=c.assignment_due_days),
        kind=task.kind,
        module=task.module,
        family_id=task.family_id,
        source=SOURCE_OFFER,
        created_at=at,
    )
    db.add(a)
    db.flush()
    return a


# --- DTO ---------------------------------------------------------------------------------------


def _origin(db: Session, task: EmployerTask, a: TaskAssignment) -> dict[str, Any]:
    c = cfg()
    if task.company_id is None:
        return {"kind": "platform", "label": c.training_label, "company": None}
    company = db.get(Company, task.company_id)
    rel_ = company_reliability(db, company.id, now()) if company else None
    comp = {"name": company.name or "Компания", "reliability": rel_.display if rel_ else None} if company else None
    if a.source == SOURCE_CHECK_SET:
        need = db.get(Need, a.need_id) if a.need_id else None
        vac = db.scalar(select(Vacancy).where(Vacancy.need_id == a.need_id).limit(1)) if a.need_id else None
        title = (vac.title if vac else None) or (need.title if need else None)
        label = f"{c.check_set_label} «{title}»" if title else c.check_set_label
        return {"kind": "check_set", "label": label, "company": comp}
    return {"kind": "company", "label": comp["name"] if comp else "Компания", "company": comp}


def assignment_out(db: Session, a: TaskAssignment, task: EmployerTask, at: datetime) -> dict[str, Any]:
    c = cfg()
    effect = c.effects.get(task.kind, "")
    if "{module}" in effect:
        effect = effect.format(module=module_name(task.module) or "")
    deadline = solve_deadline(a, task) if a.status == AssignmentStatus.opened.value else None
    return {
        "id": a.id,
        "task_id": task.id,
        "kind": task.kind,
        "kind_label": c.kind_labels.get(task.kind, task.kind),
        "title": task.title,
        "module": task.module,
        "module_name": module_name(task.module),
        "minutes": task.time_limit_min,
        "effect": effect,
        "origin": _origin(db, task, a),
        "source": a.source,
        "status": a.status,
        "status_label": c.status_labels.get(a.status, a.status),
        "due_text": rel(a.due_at, at) if a.status in ACTIVE else None,
        "solve_until_text": rel(deadline, at) if deadline else None,
        "not_real_work_label": c.not_real_work_label,
        "result": {
            "score": a.score,
            "score_text": score_text(task, a),
            "is_correct": a.is_correct,
            "rubric_scores": a.rubric_scores,
            "review_comment": a.review_comment,
        }
        if a.status in DONE
        else None,
    }


def overview(db: Session, user: User) -> dict[str, Any]:
    """К11: активные предложения, история, пауза и когда ждать следующую задачу."""
    at = now()
    expire_due(db, user.id, at)
    ensure_offer(db, user, at)
    rows = list(
        db.execute(
            select(TaskAssignment, EmployerTask)
            .join(EmployerTask, EmployerTask.id == TaskAssignment.employer_task_id)
            .where(TaskAssignment.candidate_id == user.id)
            .order_by(TaskAssignment.created_at.desc())
        )
    )
    active = [assignment_out(db, a, t, at) for a, t in rows if a.status in ACTIVE]
    history = [assignment_out(db, a, t, at) for a, t in rows if a.status not in ACTIVE]
    c = cfg()
    paused = pause_until(db, user.id)
    paused = paused if paused and paused > at else None
    last = _last_offer_at(db, user.id)
    next_at = last + timedelta(days=c.offer_interval_days) if last else None
    cats = candidate_categories(db, user.id)
    empty = None
    if not active:
        if not cats:
            empty = "Задачи появятся после подтверждения категории тестом"
        elif paused:
            empty = f"После трёх пропусков подряд предложения на паузе — снова {rel(paused, at)}"
        else:
            empty = "Задач пока нет — следующая придёт " + (rel(next_at, at) if next_at and next_at > at else "скоро")
    return {
        "active": active,
        "history": history,
        "limits": {
            "offer_interval_days": c.offer_interval_days,
            "active_max": c.candidate_active_max,
            "skip_streak": c.skip_streak,
            "skip_pause_days": c.skip_pause_days,
        },
        "paused_until_text": rel(paused, at) if paused else None,
        "next_offer_text": rel(next_at, at) if next_at and next_at > at else None,
        "empty_text": empty,
    }


# --- действия кандидата ------------------------------------------------------------------------


def owned(db: Session, user: User, assignment_id: uuid.UUID) -> tuple[TaskAssignment, EmployerTask]:
    # FOR UPDATE: два одновременных «открыть» не породят два экземпляра, два «отправить» — двух итогов
    a = db.get(TaskAssignment, assignment_id, with_for_update=True, populate_existing=True)
    if a is None or a.candidate_id != user.id:
        raise not_found("assignment_not_found", "Задача не найдена")
    task = db.get(EmployerTask, a.employer_task_id)
    assert task is not None
    return a, task


def _public_instance(inst: ItemInstance) -> dict[str, Any]:
    try:
        from app.services.testing.views import public_rendered

        return dict(public_rendered(dict(inst.rendered or {})))
    except Exception:  # noqa: BLE001 — представление модуля тестирования недоступно: публичная часть как есть
        return dict(inst.rendered or {})


def start(db: Session, user: User, assignment_id: uuid.UUID) -> dict[str, Any]:
    at = now()
    expire_due(db, user.id, at)
    a, task = owned(db, user, assignment_id)
    if a.status not in ACTIVE:
        raise conflict("assignment_status", "Задача уже решена, пропущена или её срок истёк")
    out = assignment_out(db, a, task, at)
    if task.kind == TaskKind.catalog.value:
        fam = family(task.family_id)
        if fam is None:
            raise conflict("task_unavailable", "Семейство задачи больше недоступно")
        if a.instance_id is None:
            inst = issue_task_instance(db, a, fam, at)
            a.instance_id = inst.id
        else:
            inst = db.get(ItemInstance, a.instance_id)
            assert inst is not None
        if a.opened_at is None:
            a.opened_at = at
        a.status = AssignmentStatus.opened.value
        db.flush()
        out = assignment_out(db, a, task, at)
        out["item"] = {**_public_instance(inst), "variant_code": inst.variant_code}
    else:
        if a.opened_at is None:
            a.opened_at = at
        a.status = AssignmentStatus.opened.value
        db.flush()
        out = assignment_out(db, a, task, at)
        rub = task.rubric or {}
        out["prompt"] = task.prompt
        out["rubric"] = {"max_points": rub.get("max_points"), "criteria": rub.get("criteria")}
        out["answer_max_len"] = cfg().answer_max_len
    return out


def _instance(row: ItemInstance) -> Any:
    from app.services.testing.sessions import instance_of

    return instance_of(row)


def _check(fam: Any, inst: Any, answer: Any) -> tuple[bool, str | None]:
    """Автопроверка семейства; ответ необычной формы — неверный, а не 500 (как в тесте, sessions.py)."""
    try:
        correct, tag = fam.check(inst, answer)
    except Exception:  # noqa: BLE001 — переопределённый check семейства не обязан принимать любую форму
        return False, None
    return bool(correct), tag


def submit(db: Session, user: User, assignment_id: uuid.UUID, answer: Any) -> dict[str, Any]:
    at = now()
    expire_due(db, user.id, at)
    a, task = owned(db, user, assignment_id)
    if a.status == AssignmentStatus.offered.value:
        raise conflict("assignment_not_started", "Сначала откройте задачу")
    if a.status != AssignmentStatus.opened.value:
        raise conflict("assignment_status", "Задача уже решена, пропущена или её срок истёк")
    c = cfg()
    result: dict[str, Any] = {}
    if task.kind == TaskKind.catalog.value:
        fam = family(task.family_id)
        inst_row = db.get(ItemInstance, a.instance_id) if a.instance_id else None
        if fam is None or inst_row is None:
            raise conflict("task_unavailable", "Экземпляр задачи недоступен — откройте задачу заново")
        if len(json.dumps(answer, ensure_ascii=False, default=str)) > c.answer_max_len:
            raise bad_request("answer_too_long", f"Ответ — не длиннее {c.answer_max_len} символов")
        inst = _instance(inst_row)
        deadline = solve_deadline(a, task)
        timed_out = deadline is not None and at > deadline
        correct, tag = (False, None) if timed_out else _check(fam, inst, answer)
        a.answer = answer
        a.is_correct = bool(correct)
        a.score = 1.0 if correct else 0.0
        a.status = AssignmentStatus.auto_scored.value
        a.submitted_at = at
        a.finished_at = at
        a.module = fam.module
        a.family_id = fam.id
        level = apply_to_theta(db, user.id, fam, bool(correct))
        result = {
            "correct": bool(correct),
            "timed_out": timed_out,
            "message": "Время на задачу истекло — ответ не засчитан" if timed_out else ("Верно" if correct else "Неверно"),
            "explanation": inst.explanation or None,
            "correct_answer": inst.key,
            "misconception": (fam.misconceptions.get(tag) if tag and hasattr(fam, "misconceptions") else None),
            "upgrade_hint": (level or {}).get("hint_text_ru"),
        }
    else:
        text = answer if isinstance(answer, str) else None
        if not text or not text.strip():
            raise bad_request("answer_required", "Опишите подход — ответ не может быть пустым")
        if len(text) > c.answer_max_len:
            raise bad_request("answer_too_long", f"Ответ — не длиннее {c.answer_max_len} символов")
        problem = screen_answer(text)
        if problem:
            raise bad_request("answer_contacts", problem)
        a.answer = text.strip()
        a.status = AssignmentStatus.awaiting_review.value
        a.submitted_at = at
        if task.company_id is not None:
            from app.services.contact import identity

            pseudonym = identity.alias(db, "", task.company_id, user.id).pseudonym
            notify_company(
                db,
                task.company_id,
                "task",
                f"Новое решение задачи «{task.title}» от {pseudonym} — оцените по рубрике",
                entity_type="employer_task",
                entity_id=task.id,
            )
        result = {"message": "Решение отправлено — компания оценит его по рубрике"}
    invalidate_features(db, user.id)
    db.flush()
    return {**assignment_out(db, a, task, at), "outcome": result}


def skip(db: Session, user: User, assignment_id: uuid.UUID) -> dict[str, Any]:
    at = now()
    expire_due(db, user.id, at)
    a, task = owned(db, user, assignment_id)
    if a.status not in ACTIVE:
        raise conflict("assignment_status", "Задача уже решена, пропущена или её срок истёк")
    a.status = AssignmentStatus.skipped.value
    a.skipped_at = at
    db.flush()
    paused = pause_until(db, user.id)
    out = assignment_out(db, a, task, at)
    out["outcome"] = {
        "message": "Задача пропущена — это не штрафуется",
        "paused_until_text": rel(paused, at) if paused and paused > at else None,
    }
    return out


GRADE_ORDER = GRADES


# --- фоновая рассылка регулярных заданий (#15) ------------------------------------------------------


def offer_sweep(db: Session, *, limit: int, remind_before_h: int) -> dict[str, int]:
    """Планировщик: предлагает регулярные задания кандидатам по каденции `offer_interval_days` (не чаще), шлёт
    уведомление «вам предложено задание» и один раз напоминает о близком сроке. Правила — те же, что у ленивого
    ensure_offer при открытии /tasks (лимиты, пауза после пропусков, блок-лист компаний). Не штрафует (§5.4)."""
    from app.models.enums import QualificationStatus, UserRole, UserStatus
    from app.models.testing import Qualification
    from app.services.notify import notify_user

    at = now()
    c = cfg()
    recent = (
        select(TaskAssignment.candidate_id)
        .where(TaskAssignment.source == SOURCE_OFFER, TaskAssignment.offered_at >= at - timedelta(days=c.offer_interval_days))
        .scalar_subquery()
    )
    ids = list(
        db.scalars(
            select(Qualification.candidate_id)
            .join(User, User.id == Qualification.candidate_id)
            .where(
                Qualification.status.in_((QualificationStatus.confirmed.value, QualificationStatus.upgrade_testing.value)),
                User.role == UserRole.candidate.value,
                User.status == UserStatus.active.value,
                Qualification.candidate_id.notin_(recent),
            )
            .distinct()
            .order_by(Qualification.candidate_id)
            .limit(limit)
        )
    )
    offered = 0
    for uid in ids:
        user = db.get(User, uid)
        if user is None:
            continue
        with db.begin_nested():
            expire_due(db, uid, at)
            a = ensure_offer(db, user, at)
            if a is None:
                continue
            task = db.get(EmployerTask, a.employer_task_id)
            notify_user(
                db,
                uid,
                "task",
                f"Вам предложено регулярное задание «{task.title if task else 'задание'}»: до {c.max_minutes} минут, "
                f"срок на решение — {c.assignment_due_days} дн. Отказ или пропуск ничем не грозит.",
                entity_type="task_assignment",
                entity_id=a.id,
                payload={"kind": "offer"},
            )
            offered += 1
    reminded = 0
    horizon = at + timedelta(hours=remind_before_h)
    due_soon = db.scalars(
        select(TaskAssignment)
        .where(
            TaskAssignment.status.in_(ACTIVE),
            TaskAssignment.source == SOURCE_OFFER,
            TaskAssignment.due_at.is_not(None),
            TaskAssignment.due_at > at,
            TaskAssignment.due_at <= horizon,
        )
        .limit(limit)
    ).all()
    for a in due_soon:
        sent = db.scalar(
            select(func.count())
            .select_from(Notification)
            .where(
                Notification.entity_type == "task_assignment",
                Notification.entity_id == str(a.id),
                Notification.payload["kind"].astext == "reminder",
            )
        )
        if sent:
            continue
        notify_user(
            db,
            a.candidate_id,
            "task",
            f"Напоминание: срок на регулярное задание скоро истечёт ({rel(a.due_at, at)}). Можно решить или пропустить.",
            entity_type="task_assignment",
            entity_id=a.id,
            payload={"kind": "reminder"},
        )
        reminded += 1
    db.flush()
    return {"candidates_checked": len(ids), "offered": offered, "reminded": reminded}
