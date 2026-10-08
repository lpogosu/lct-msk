"""«Проверочный набор по вакансии» (DECISIONS 3, концепция §5.5).

must-навыки брифа → модули по графу навыков → 3–5 откалиброванных семейств типа A (≤ 15 минут) — план строит
движок подбора (app.matching.coverage.plan_check_set, тот же, что на вкладке «Как мы проверим»). Каждому
кандидату из шортлиста — свои одноразовые экземпляры как регулярная задача. Стандартный тест и грейд не
трогаются; результат идёт в подтверждение модуля (K) и свежесть (A) — подбор читает балл, модуль и время.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict, not_found
from app.models.enums import AssignmentStatus, EmployerTaskStatus, NeedStatus, TaskKind
from app.models.matching import Need, Shortlist
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.users import User
from app.services.contact import identity
from app.services.contact.company import company_of
from app.services.notify import notify_user

from .common import (
    DONE,
    SCORED,
    SOURCE_CHECK_SET,
    cfg,
    family,
    family_minutes,
    module_name,
    now,
    rel,
    withdrawn_family_ids,
)


def _seed(need_id: uuid.UUID) -> int:
    """Тот же seed, что у превью «Как мы проверим» (app.services.matching.needs): набор = превью."""
    try:
        from app.services.matching.needs import _seed as matching_seed

        return int(matching_seed(need_id))
    except Exception:  # noqa: BLE001 — формула совпадает с сервисом подбора
        return int(hashlib.sha256(f"check-set|{need_id}".encode()).hexdigest()[:12], 16)


def _must(need: Need) -> tuple[str, ...]:
    brief = (need.parsed or {}).get("brief") or {}
    must = brief.get("must")
    if must is None:
        must = [s.get("skill") for s in (need.must_skills or []) if isinstance(s, dict) and s.get("skill")]
    return tuple(str(s) for s in must)


def plan_for(need: Need) -> Any:
    from app.matching.coverage import family_catalog_from_registry, family_info_from_registry, plan_check_set

    brief = (need.parsed or {}).get("brief") or {}
    return plan_check_set(
        _must(need),
        family_catalog=family_catalog_from_registry(),
        family_info=family_info_from_registry(),
        seed=_seed(need.id),
        spec=brief.get("spec") or need.specialization,
        brief_grade=brief.get("grade") or need.grade,
    )


def _owned_need(db: Session, user: User, need_id: uuid.UUID) -> Need:
    need = db.get(Need, need_id)
    if need is None or user.company_id is None or need.company_id != user.company_id:
        raise not_found("need_not_found", "Бриф не найден")
    return need


def _slot_task(db: Session, user: User, need: Need, slot: Any, at: Any) -> EmployerTask:
    """Задача набора на слот (переиспользуется при повторной отправке новым кандидатам шортлиста)."""
    task = db.scalar(
        select(EmployerTask).where(
            EmployerTask.need_id == need.id,
            EmployerTask.is_check_set.is_(True),
            EmployerTask.family_id == slot.family_id,
        )
    )
    if task is not None:
        return task
    fam = family(slot.family_id)
    assert fam is not None
    brief = (need.parsed or {}).get("brief") or {}
    task = EmployerTask(
        company_id=need.company_id,
        created_by=user.id,
        kind=TaskKind.catalog.value,
        title=f"{module_name(fam.module)}: {fam.title}",
        family_id=fam.id,
        module=fam.module,
        audience={"specialization": brief.get("spec") or need.specialization, "grade": brief.get("grade") or need.grade},
        tags=list(slot.skills),
        time_limit_min=family_minutes(fam),
        status=EmployerTaskStatus.active.value,
        is_platform=False,
        not_real_work_ack=True,
        need_id=need.id,
        is_check_set=True,
        created_at=at,
        updated_at=at,
    )
    db.add(task)
    db.flush()
    return task


def create(db: Session, user: User, secret: str, need_id: uuid.UUID, refs: Sequence[str]) -> dict[str, Any]:
    company = company_of(db, user)
    need = _owned_need(db, user, need_id)
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — проверочный набор не отправляется")
    if not refs:
        raise bad_request("candidates_required", "Выберите кандидатов из шортлиста")
    plan = plan_for(need)
    withdrawn = withdrawn_family_ids(db)
    slots = [s for s in plan.slots if s.family_id and family(s.family_id) is not None and s.family_id not in withdrawn]
    if not slots:
        raise conflict(
            "check_set_empty",
            plan.note or "Обязательные навыки брифа тестом не проверяются — проверьте их на интервью",
        )
    total = sum(family_minutes(family(s.family_id)) for s in slots)
    if total > cfg().max_minutes:
        # План движка укладывается в бюджет по time_limit_s; округление до минут могло его превысить
        while slots and sum(family_minutes(family(s.family_id)) for s in slots) > cfg().max_minutes:
            slots.pop()
        total = sum(family_minutes(family(s.family_id)) for s in slots)
    shortlisted = set(db.scalars(select(Shortlist.candidate_id).where(Shortlist.need_id == need.id)))
    at = now()
    due = at + timedelta(days=cfg().check_set_due_days)
    tasks = [_slot_task(db, user, need, s, at) for s in slots]
    sent: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    for ref in dict.fromkeys(refs):
        cid = identity.resolve(db, secret, company.id, ref, list(shortlisted))
        if cid is None or cid not in shortlisted:
            rejected.append(
                {
                    "ref": ref,
                    "pseudonym": identity.normalize_label(ref),
                    "message": "Проверочный набор отправляется только кандидатам из шортлиста этого брифа",
                }
            )
            continue
        ident = identity.alias(db, secret, company.id, cid)
        if not identity.accepts_company(db, company.id, cid):
            # Блок-лист, «не ищу» или отозванное согласие на показ: набор — тоже обращение компании к кандидату.
            # Причину не раскрываем (как у приглашения: «сейчас не принимает»).
            rejected.append(
                {"ref": ident.ref, "pseudonym": ident.pseudonym, "message": f"{ident.pseudonym} сейчас не принимает задачи"}
            )
            continue
        created = 0
        for task in tasks:
            exists = db.scalar(
                select(TaskAssignment.id).where(
                    TaskAssignment.employer_task_id == task.id, TaskAssignment.candidate_id == cid
                )
            )
            if exists is not None:
                continue
            db.add(
                TaskAssignment(
                    id=uuid.uuid4(),
                    employer_task_id=task.id,
                    candidate_id=cid,
                    status=AssignmentStatus.offered.value,
                    offered_at=at,
                    due_at=due,
                    kind=task.kind,
                    module=task.module,
                    family_id=task.family_id,
                    source=SOURCE_CHECK_SET,
                    need_id=need.id,
                    created_at=at,
                )
            )
            created += 1
        if created:
            notify_user(
                db,
                cid,
                "task",
                f"{company.name or 'Компания'} прислала проверочный набор по вакансии: {created} коротких задач, "
                f"около {total} минут. Стандартный тест и грейд он не меняет",
                entity_type="need",
                entity_id=need.id,
            )
            sent.append({"ref": ident.ref, "pseudonym": ident.pseudonym, "assignments": created})
        else:
            rejected.append({"ref": ident.ref, "pseudonym": ident.pseudonym, "message": "Набор этому кандидату уже отправлен"})
    db.flush()
    return {
        "need_id": need.id,
        "slots": [
            {
                "module": s.module,
                "module_name": s.module_name,
                "family_id": s.family_id,
                "title": family(s.family_id).title,
                "minutes": family_minutes(family(s.family_id)),
                "skills": list(s.skills),
            }
            for s in slots
        ],
        "untestable": list(plan.untestable),
        "total_minutes": total,
        "due_text": rel(due, at),
        "sent": sent,
        "rejected": rejected,
        "note": plan.note,
    }


def results(db: Session, user: User, secret: str, need_id: uuid.UUID) -> dict[str, Any]:
    """Результаты набора по кандидатам: что решено, по каким модулям, общий балл (обезличенно)."""
    company = company_of(db, user)
    need = _owned_need(db, user, need_id)
    rows = list(
        db.execute(
            select(TaskAssignment, EmployerTask)
            .join(EmployerTask, EmployerTask.id == TaskAssignment.employer_task_id)
            .where(EmployerTask.need_id == need.id, EmployerTask.is_check_set.is_(True))
            .order_by(TaskAssignment.candidate_id, EmployerTask.module)
        )
    )
    labels = cfg().status_labels
    at = now()
    by_cand: dict[uuid.UUID, list[tuple[TaskAssignment, EmployerTask]]] = {}
    for a, t in rows:
        by_cand.setdefault(a.candidate_id, []).append((a, t))
    out = []
    for cid, items in by_cand.items():
        ident = identity.alias(db, secret, company.id, cid)
        scored = [a for a, _ in items if a.status in SCORED]
        out.append(
            {
                "pseudonym": ident.pseudonym,
                "candidate_ref": ident.ref,
                "done": sum(1 for a, _ in items if a.status in DONE),
                "total": len(items),
                "correct": sum(1 for a in scored if a.is_correct),
                "items": [
                    {
                        "module": t.module,
                        "module_name": module_name(t.module),
                        "title": t.title,
                        "status": a.status,
                        "status_label": labels.get(a.status, a.status),
                        "is_correct": a.is_correct,
                        "finished_text": rel(a.finished_at, at),
                    }
                    for a, t in items
                ],
            }
        )
    out.sort(key=lambda d: (-d["correct"], -d["done"], d["pseudonym"]))
    return {"need_id": need.id, "candidates": out}
