"""Pull-API /api/ats/v1: вакансии, приглашения и отклики компании с курсорной пагинацией по `updated_at`.

Курсор — непрозрачная строка (updated_at + id): порядок (updated_at, id) стабилен, повторный запрос с тем же курсором
возвращает те же записи (идемпотентность чтения). `updated_since` включительно: ATS не пропустит запись, изменённую
в ту же секунду. Контакты кандидата — только при области `contacts:read` И действующем согласии на раскрытие,
иначе блока `contacts` нет вовсе; внутренних id кандидата в ответах нет никогда.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.core.errors import bad_request
from app.models.contact import Application, ContactDisclosure, Invitation, OfferTerms, Vacancy
from app.services.contact import identity
from app.services.contact.common import cfg
from app.services.contact.profiles import candidate_facts

CONTACT_KEYS = ("full_name", "email", "phone", "telegram")


def encode_cursor(at: datetime, row_id: uuid.UUID) -> str:
    raw = json.dumps({"t": at.isoformat(), "i": str(row_id)}, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[datetime, uuid.UUID]:
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        d = json.loads(raw)
        return datetime.fromisoformat(d["t"]), uuid.UUID(d["i"])
    except (ValueError, KeyError, TypeError):
        raise bad_request("ats_cursor_invalid", "Неверный курсор: начните с первой страницы или используйте next_cursor из ответа") from None


def page_limit(limit: int | None) -> int:
    c = cfg().ats
    return max(1, min(limit or c.page_size_default, c.page_size_max))


def paginate(
    db: Session, model: Any, base_where: list[Any], *, updated_since: datetime | None, cursor: str | None, limit: int | None
) -> tuple[list[Any], str | None]:
    """Страница записей и next_cursor (None — конец). Запрашиваем limit+1, чтобы честно знать, есть ли продолжение."""
    n = page_limit(limit)
    q = select(model).where(*base_where)
    if updated_since is not None:
        q = q.where(model.updated_at >= updated_since)
    if cursor:
        t, i = decode_cursor(cursor)
        q = q.where(or_(model.updated_at > t, and_(model.updated_at == t, model.id > i)))
    rows = list(db.scalars(q.order_by(model.updated_at, model.id).limit(n + 1)))
    more = len(rows) > n
    rows = rows[:n]
    return rows, (encode_cursor(rows[-1].updated_at, rows[-1].id) if more and rows else None)


def _active_disclosure(db: Session, basis: str, basis_id: uuid.UUID) -> ContactDisclosure | None:
    d = db.scalar(
        select(ContactDisclosure)
        .where(ContactDisclosure.basis == basis, ContactDisclosure.basis_id == basis_id)
        .order_by(ContactDisclosure.granted_at.desc())
        .limit(1)
    )
    return d if d is not None and d.revoked_at is None else None


def _candidate_block(db: Session, pseudonym: str, candidate_id: uuid.UUID, basis: str, basis_id: uuid.UUID, with_contacts: bool) -> dict[str, Any]:
    out: dict[str, Any] = {"pseudonym": pseudonym, "contacts_open": False}
    d = _active_disclosure(db, basis, basis_id)
    if d is None:
        return out
    out["contacts_open"] = True
    if with_contacts:
        facts = candidate_facts(db, candidate_id)
        out["contacts"] = {k: facts.get(k) for k in CONTACT_KEYS if k in (d.fields or []) and facts.get(k)}
    return out


def vacancy_item(v: Vacancy) -> dict[str, Any]:
    return {
        "id": v.id,
        "need_id": v.need_id,
        "title": v.title,
        "description": v.description,
        "specialization": v.specialization,
        "grade": v.grade,
        "salary": {"from": v.salary_from, "to": v.salary_to, "net": v.salary_net, "currency": "RUB"},
        "format": v.format,
        "city": v.city,
        "status": v.status,
        "published_at": v.published_at,
        "updated_at": v.updated_at,
    }


def invitation_item(db: Session, row: Invitation, *, with_contacts: bool) -> dict[str, Any]:
    t = db.scalar(select(OfferTerms).where(OfferTerms.invitation_id == row.id, OfferTerms.ver == row.current_terms_ver))
    return {
        "id": row.id,
        "vacancy_id": row.vacancy_id,
        "need_id": row.need_id,
        "status": row.status,
        "status_label": cfg().status_labels.invitation.get(row.status, row.status),
        "stage": row.stage,
        "outcome": row.outcome,
        "outcome_reason": (row.outcome_reason or {}).get("reason"),
        "terms": (
            {
                "ver": t.ver,
                "title": t.title,
                "salary": {"from": t.salary_from, "to": t.salary_to, "net": t.salary_net, "currency": "RUB"},
                "format": t.format,
                "sha256": t.sha256,
            }
            if t
            else None
        ),
        "sent_at": row.created_at,
        "responded_at": row.responded_at,
        "accepted_at": row.accepted_at,
        "contacted_at": row.contacted_at,
        "closed_at": row.closed_at,
        "candidate": _candidate_block(db, row.pseudonym, row.candidate_id, "invitation", row.id, with_contacts),
        "updated_at": row.updated_at,
    }


def application_item(db: Session, row: Application, company_id: uuid.UUID, secret: str, *, with_contacts: bool) -> dict[str, Any]:
    ident = identity.alias(db, secret, company_id, row.candidate_id)
    snap = row.score_snapshot or {}
    return {
        "id": row.id,
        "vacancy_id": row.vacancy_id,
        "status": row.status,
        "status_label": cfg().status_labels.application.get(row.status, row.status),
        "reject_reason": row.reject_reason,
        "score": snap.get("score"),
        "category": snap.get("category"),
        "submitted_at": row.created_at,
        "responded_at": row.responded_at,
        "candidate": _candidate_block(db, ident.pseudonym, row.candidate_id, "application", row.id, with_contacts),
        "updated_at": row.updated_at,
    }


def company_vacancy_filter(company_id: uuid.UUID) -> list[Any]:
    return [Vacancy.company_id == company_id, Vacancy.status != "draft"]


def company_invitation_filter(company_id: uuid.UUID, status: str | None) -> list[Any]:
    w: list[Any] = [Invitation.company_id == company_id]
    if status:
        w.append(Invitation.status == status)
    return w


def company_application_filter(db: Session, company_id: uuid.UUID, status: str | None) -> list[Any]:
    ids = select(Vacancy.id).where(Vacancy.company_id == company_id)
    w: list[Any] = [Application.vacancy_id.in_(ids)]
    if status:
        w.append(Application.status == status)
    return w

