"""Обратная синхронизация: статус из ATS → переход в НАШЕЙ машине состояний (app/contact), без обходных путей.

Таблица соответствия (приглашение):
  contacted → mark_contacted      («Связались», в срок — плюс к надёжности)
  interview → mark_interview      (этап «собеседование»; контакт отмечается вместе с ним)
  offer     → этап «оффер»        (только после контакта; в машине состояний этапа нет — хранится как stage=offer)
  hired     → close / hired       (только после контакта)
  rejected  → close / not_matched (причина обязательна: skills_missing | position_closed | chose_other)
Отклик: viewed → view, interested → interest (при необходимости с просмотром), rejected → reject (причина обязательна).

Недопустимый переход машины — 409 с русским сообщением самой машины. Повтор уже достигнутого состояния — 200 без
изменений (`applied: false`). Надёжность компании обновляется теми же побочными эффектами, что при ручной отметке.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import bad_request, conflict
from app.models.company import Company
from app.models.contact import Application, Invitation
from app.models.users import User
from app.services.contact import applications as app_svc
from app.services.contact import invitations as inv_svc
from app.services.contact.common import cfg
from app.services.contact.store import Ctx, refresh_rows
from app.services.notify import company_members


def actor_for(db: Session, company: Company, created_by: uuid.UUID | None) -> uuid.UUID:
    """От чьего имени пишется событие: автор ключа, иначе любой сотрудник компании."""
    if created_by is not None and db.get(User, created_by) is not None:
        return created_by
    members = company_members(db, company.id)
    if not members:
        raise conflict("ats_no_actor", "В компании нет активных сотрудников, от имени которых можно записать статус")
    return members[0].id


def allowed(kind: str) -> list[str]:
    m = cfg().ats.status_map
    return list(m.invitation if kind == "invitation" else m.application)


def _check_status(kind: str, status: str) -> None:
    ok = allowed(kind)
    if status not in ok:
        raise bad_request("ats_status_unknown", f"Неизвестный статус «{status}». Допустимые для {'приглашений' if kind == 'invitation' else 'откликов'}: {', '.join(ok)}")


def apply_invitation(ctx: Ctx, row: Invitation, status: str, actor: uuid.UUID, *, reason: str | None, skill_ids: list[str], comment: str | None) -> bool:
    _check_status("invitation", status)
    refresh_rows(ctx, [row])
    if status == "contacted":
        if row.contacted_at is not None:
            return False
        inv_svc.act(ctx, row, "mark_contacted", "employer", actor)
    elif status == "interview":
        if row.stage in ("interview", "offer"):
            return False
        inv_svc.act(ctx, row, "mark_interview", "employer", actor)
    elif status == "offer":
        if row.stage == "offer":
            return False
        if row.status != "accepted":
            raise conflict("contact_rejected", "Оффер возможен только по принятому приглашению с открытыми контактами", {"reason": "offer_requires_accepted"})
        if row.contacted_at is None:
            raise conflict("contact_rejected", "Сначала передайте статус «contacted» или «interview»: оффер без контакта невозможен", {"reason": "offer_requires_contact"})
        row.stage = "offer"
    elif status == "hired":
        if row.status == "closed" and row.outcome == "hired":
            return False
        inv_svc.act(ctx, row, "close", "employer", actor, comment=comment, outcome="hired", reason=None, skill_ids=[])
    else:  # rejected
        if row.status == "closed" and row.outcome == "not_matched":
            return False
        valid = cfg().reasons.not_matched
        if reason not in valid:
            raise bad_request("ats_reason_required", f"Для статуса «rejected» передайте reason: {', '.join(valid)}")
        inv_svc.act(ctx, row, "close", "employer", actor, comment=comment, outcome="not_matched", reason=reason, skill_ids=list(skill_ids))
    return True


def apply_application(ctx: Ctx, row: Application, status: str, actor: uuid.UUID, *, reason: str | None, skill_ids: list[str], comment: str | None) -> bool:
    _check_status("application", status)
    app_svc.refresh_rows(ctx, [row])
    if status == "viewed":
        if row.status in ("viewed", "interested"):
            return False
        app_svc.act(ctx, row, "view", "employer", actor)
    elif status == "interested":
        if row.status == "interested":
            return False
        if row.status == "submitted":
            app_svc.act(ctx, row, "view", "employer", actor)
        app_svc.act(ctx, row, "interest", "employer", actor)
    else:
        if row.status == "rejected":
            return False
        valid = cfg().reasons.application_reject
        if reason not in valid:
            raise bad_request("ats_reason_required", f"Для статуса «rejected» передайте reason: {', '.join(valid)}")
        if row.status == "submitted":
            app_svc.act(ctx, row, "view", "employer", actor)
        app_svc.act(ctx, row, "reject", "employer", actor, **app_svc.reject_payload(reason, comment, skill_ids))
    return True


def owned_invitation(db: Session, company_id: uuid.UUID, invitation_id: uuid.UUID) -> Invitation | None:
    row = db.get(Invitation, invitation_id)
    return row if row is not None and row.company_id == company_id else None


def owned_application(db: Session, company_id: uuid.UUID, application_id: uuid.UUID) -> Application | None:
    from app.models.contact import Vacancy

    row = db.get(Application, application_id)
    if row is None:
        return None
    vac = db.scalar(select(Vacancy).where(Vacancy.id == row.vacancy_id))
    return row if vac is not None and vac.company_id == company_id else None

