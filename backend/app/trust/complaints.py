"""Жалобы кандидатов на приглашения и вакансии, очередь модерации, влияние на квоту компании.

Что делает жалоба: попадает в очередь методиста/администратора (`list_queue`), считается в профиле компании и, если
от N разных кандидатов за окно есть открытые или подтверждённые жалобы, снижает квоту приглашений (config/trust.yaml ->
complaints). Отклонённые модератором жалобы не считаются. Компания о жалобе не уведомляется.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict, not_found
from app.models.company import Company
from app.models.contact import Invitation, Vacancy
from app.models.trust import TrustComplaint
from app.models.users import User
from app.trust import verification
from app.trust.verification import cfg as trust_cfg

COUNTED = ("open", "upheld")


def reasons() -> dict[str, str]:
    from app.contact.settings import get_contact_config

    return dict(get_contact_config().reasons.complaint)


def file_complaint(
    db: Session,
    reporter: User,
    *,
    target_type: str,
    target_id: uuid.UUID,
    company_id: uuid.UUID,
    reason: str,
    comment: str | None,
    at: datetime,
) -> TrustComplaint:
    tc = trust_cfg()["complaints"]
    allowed = reasons()
    if reason not in allowed:
        raise bad_request("complaint_reason", "Выберите причину жалобы: " + ", ".join(v.lower() for v in allowed.values()))
    if comment and len(comment) > int(tc["comment_max_len"]):
        raise bad_request("comment_too_long", f"Комментарий — не длиннее {tc['comment_max_len']} символов")
    if find(db, reporter.id, target_type, target_id) is not None:
        raise conflict("complaint_exists", "Вы уже пожаловались — спасибо, жалоба учтена")
    row = TrustComplaint(
        company_id=company_id,
        reporter_id=reporter.id,
        target_type=target_type,
        target_id=target_id,
        reason=reason,
        comment=(comment or "").strip() or None,
        status="open",
        created_at=at,
    )
    db.add(row)
    db.flush()
    return row


def find(db: Session, reporter_id: uuid.UUID, target_type: str, target_id: uuid.UUID) -> TrustComplaint | None:
    return db.scalar(
        select(TrustComplaint).where(
            TrustComplaint.reporter_id == reporter_id,
            TrustComplaint.target_type == target_type,
            TrustComplaint.target_id == target_id,
        )
    )


def counted_reporters(db: Session, company_id: uuid.UUID, at: datetime, days: int | None = None) -> int:
    """Сколько разных кандидатов пожаловались за окно (открытые и подтверждённые жалобы)."""
    days = int(days or trust_cfg()["complaints"]["window_days"])
    return int(
        db.scalar(
            select(func.count(func.distinct(TrustComplaint.reporter_id))).where(
                TrustComplaint.company_id == company_id,
                TrustComplaint.status.in_(COUNTED),
                TrustComplaint.created_at >= at - timedelta(days=days),
            )
        )
        or 0
    )


def quota_factor(db: Session, company_id: uuid.UUID, at: datetime) -> tuple[float, str | None]:
    """(множитель квоты, причина). 1.0 — жалоб меньше порога. Метка и вакансии при этом не блокируются."""
    tc = trust_cfg()["complaints"]
    if counted_reporters(db, company_id, at) >= int(tc["quota_threshold"]):
        return float(tc["quota_factor"]), str(tc["quota_label"])
    return 1.0, None


def _target_title(db: Session, row: TrustComplaint) -> str | None:
    if row.target_type == "invitation":
        inv = db.get(Invitation, row.target_id)
        return inv.pseudonym if inv else None
    vac = db.get(Vacancy, row.target_id)
    return vac.title if vac else None


def out(db: Session, row: TrustComplaint) -> dict[str, Any]:
    tc = trust_cfg()["complaints"]
    company = db.get(Company, row.company_id)
    return {
        "id": row.id,
        "company_id": row.company_id,
        "company_name": (company.name if company and company.name else None) or "Компания без названия",
        "target_type": row.target_type,
        "target_id": row.target_id,
        "target_title": _target_title(db, row),
        "reason": row.reason,
        "reason_label": reasons().get(row.reason, row.reason),
        "comment": row.comment,
        "status": row.status,
        "status_label": tc["statuses"].get(row.status, row.status),
        "created_at": row.created_at,
        "resolved_at": row.resolved_at,
        "resolution_note": row.resolution_note,
        "company_open_reporters": counted_reporters(db, row.company_id, row.created_at),
        "verification_level": verification.trust_of(db, row.company_id)["level"],
    }


def list_queue(db: Session, status: str | None, limit: int = 100) -> list[dict[str, Any]]:
    q = select(TrustComplaint).order_by(TrustComplaint.created_at.desc()).limit(limit)
    if status:
        q = q.where(TrustComplaint.status == status)
    return [out(db, r) for r in db.scalars(q)]


def resolve(db: Session, moderator: User, complaint_id: uuid.UUID, decision: str, note: str | None, at: datetime) -> dict[str, Any]:
    tc = trust_cfg()["complaints"]
    if decision not in ("upheld", "dismissed"):
        raise bad_request("decision", "Решение — «подтверждена» или «отклонена»")
    row = db.get(TrustComplaint, complaint_id)
    if row is None:
        raise not_found("complaint_not_found", "Жалоба не найдена")
    if row.status != "open":
        raise conflict("complaint_resolved", "Жалоба уже разобрана")
    row.status = decision
    row.resolved_by, row.resolved_at = moderator.id, at
    row.resolution_note = (note or "").strip()[:300] or None
    db.flush()
    if decision == "upheld" and row.reason in tc["revoke_badge_on_upheld"]:
        current = verification.trust_of(db, row.company_id)
        if current["verified"]:
            verification.revoke(db, row.company_id, moderator.id, "complaints", f"Жалоба {row.id}", at)
    return out(db, row)


def report_vacancy(
    db: Session, reporter: User, vacancy_id: uuid.UUID, reason: str, comment: str | None, at: datetime
) -> TrustComplaint:
    """Жалоба кандидата на вакансию: видимая вакансия — опубликованная или та, на которую кандидат откликался."""
    from app.models.contact import Application

    vac = db.get(Vacancy, vacancy_id)
    visible = vac is not None and (
        vac.status == "published"
        or db.scalar(select(Application.id).where(Application.vacancy_id == vac.id, Application.candidate_id == reporter.id))
        is not None
    )
    if vac is None or not visible:
        raise not_found("vacancy_not_found", "Вакансия не найдена")
    return file_complaint(
        db, reporter, target_type="vacancy", target_id=vac.id, company_id=vac.company_id, reason=reason, comment=comment, at=at
    )
