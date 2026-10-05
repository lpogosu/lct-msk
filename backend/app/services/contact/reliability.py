"""Надёжность компании R из журнала событий (spec-contact §3.7). Формула — в app/contact/reliability.py."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.reliability import Reliability, ReliabilityEvent, compute_reliability, event_polarity
from app.models.company import ReliabilityEventRow

from .common import cfg


def _uuid(v: Any) -> uuid.UUID | None:
    if v is None:
        return None
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def record(db: Session, ev: ReliabilityEvent) -> ReliabilityEventRow:
    event_polarity(ev.kind, cfg())  # неизвестный код — ошибка сразу, а не тихий ноль в R
    row = ReliabilityEventRow(
        company_id=_uuid(ev.company_id),
        kind=ev.kind,
        at=ev.at,
        candidate_id=_uuid(ev.candidate_id),
        invitation_id=_uuid(ev.invitation_id),
        application_id=_uuid(ev.application_id),
    )
    db.add(row)
    return row


def events_of(db: Session, company_id: uuid.UUID) -> list[ReliabilityEvent]:
    rows = db.scalars(select(ReliabilityEventRow).where(ReliabilityEventRow.company_id == company_id))
    return [
        ReliabilityEvent(
            company_id=str(r.company_id),
            kind=r.kind,
            at=r.at,
            candidate_id=str(r.candidate_id) if r.candidate_id else None,
            invitation_id=str(r.invitation_id) if r.invitation_id else None,
            application_id=str(r.application_id) if r.application_id else None,
        )
        for r in rows
    ]


def company_reliability(db: Session, company_id: uuid.UUID, at: datetime) -> Reliability:
    db.flush()  # события этого же запроса тоже учитываются
    return compute_reliability(events_of(db, company_id), at, cfg())


def reliability_dict(rel: Reliability) -> dict[str, Any]:
    """Что видит кандидат до решения и работодатель в профиле (Р1): R с разложением."""
    return {
        "r": round(rel.r, 2),
        "enough_data": rel.enough_data,
        "display": rel.display,
        "breakdown": list(rel.breakdown),
        "good": rel.good,
        "bad": rel.bad,
        "discrepancy_badge": rel.discrepancy_badge,
        "quota_factor": rel.quota_factor,
    }
