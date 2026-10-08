"""«Сигнал интереса» (DECISIONS 24): отправка, отзыв, статус кандидата и видимость работодателю.

Правило видимости — единственное место, где сигнал выходит к работодателю: `visible_for_company` отдаёт его
только по тем кандидатам, чью карточку компания уже открывала или закрепила (DECISIONS 7). Работодателю
уведомление не уходит: сигнал не отклик, без его интереса он невидим. Score не затрагивается.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.contact import interest as rules
from app.core.consents import find_active_consent, grant_consent
from app.core.errors import bad_request, conflict, not_found
from app.models.company import Company
from app.models.contact import InterestSignal, Vacancy
from app.models.enums import ConsentPurpose, VacancyStatus
from app.models.matching import Need, ProfileView, Shortlist
from app.models.users import User

from .common import cfg, rel
from .common import now as clock_now


def _rec(row: InterestSignal) -> rules.SignalRecord:
    return rules.SignalRecord(row.created_at, row.expires_at, row.revoked_at)


def _mine(db: Session, candidate_id: uuid.UUID) -> list[InterestSignal]:
    q = select(InterestSignal).where(InterestSignal.candidate_id == candidate_id)
    return list(db.scalars(q.order_by(InterestSignal.created_at)))


def _out(row: InterestSignal, at: datetime, company_name: str | None) -> dict[str, Any]:
    return {
        "id": row.id,
        "company_id": row.company_id,
        "company_name": company_name,
        "vacancy_id": row.vacancy_id,
        "sent": rel(row.created_at, at),
        "expires": rel(row.expires_at, at),
    }


def status(db: Session, user: User) -> dict[str, Any]:
    """Остаток сигналов и действующие сигналы кандидата."""
    c = cfg().interest
    at = clock_now()
    rows = _mine(db, user.id)
    created = [r.created_at for r in rows]
    active = [r for r in rows if rules.is_active(_rec(r), at)]
    names: dict[uuid.UUID, str] = {}
    if active:
        names = dict(db.execute(select(Company.id, Company.name).where(Company.id.in_({r.company_id for r in active}))).all())
    left = rules.remaining(created, at, c)
    nxt = rules.next_slot_at(created, at, c)
    return {
        "limit": c.per_window,
        "remaining": left,
        "window_days": c.window_days,
        "lifetime_days": c.lifetime_days,
        "label": f"осталось {left} из {c.per_window}",
        "next_slot_in": rel(nxt, at) if nxt else None,
        "consent_text": c.consent_text,
        "consent_text_version": c.consent_text_version,
        "consent_given": find_active_consent(db, user.id, ConsentPurpose.interest_signal) is not None,
        "active": [_out(r, at, names.get(r.company_id)) for r in active],
    }


def active_for(db: Session, user: User, company_id: uuid.UUID) -> InterestSignal | None:
    at = clock_now()
    for r in _mine(db, user.id):
        if r.company_id == company_id and rules.is_active(_rec(r), at):
            return r
    return None


def send(
    db: Session,
    user: User,
    *,
    company_id: uuid.UUID | None,
    vacancy_id: uuid.UUID | None,
    consent: bool,
    ip: str | None = None,
    user_agent: str | None = None,
) -> InterestSignal:
    if (company_id is None) == (vacancy_id is None):
        raise bad_request("interest_target", "Укажите либо вакансию, либо компанию")
    if vacancy_id is not None:
        vac = db.get(Vacancy, vacancy_id)
        if vac is None or vac.status != VacancyStatus.published.value:
            raise not_found("vacancy_not_found", "Вакансия не найдена")
        company_id = vac.company_id
    assert company_id is not None
    if db.get(Company, company_id) is None:
        raise not_found("company_not_found", "Компания не найдена")
    c = cfg().interest
    at = clock_now()
    if active_for(db, user, company_id) is not None:
        raise conflict("interest_exists", "Вы уже отметили интерес к этой компании", {"company_id": str(company_id)})
    created = [r.created_at for r in _mine(db, user.id)]
    if rules.remaining(created, at, c) <= 0:
        nxt = rules.next_slot_at(created, at, c)
        raise conflict(
            "interest_limit",
            f"Сигналы закончились: {c.per_window} за {c.window_days} дней. Следующий — {rel(nxt, at)}",
            {"next_slot_in": rel(nxt, at)},
        )
    consent_row = find_active_consent(db, user.id, ConsentPurpose.interest_signal)
    if consent_row is None:
        if not consent:
            raise bad_request("consent_required", "Нужно согласие на сигнал интереса", {"purpose": "interest_signal"})
        consent_row = grant_consent(
            db, user.id, ConsentPurpose.interest_signal, text_version=c.consent_text_version, ip=ip, user_agent=user_agent
        )
    row = InterestSignal(
        candidate_id=user.id,
        company_id=company_id,
        vacancy_id=vacancy_id,
        consent_id=consent_row.id,
        created_at=at,
        expires_at=rules.expires_at(at, c),
    )
    db.add(row)
    db.flush()
    return row


def revoke(db: Session, user: User, signal_id: uuid.UUID) -> InterestSignal:
    row = db.get(InterestSignal, signal_id)
    if row is None or row.candidate_id != user.id:
        raise not_found("interest_not_found", "Сигнал не найден")
    if row.revoked_at is None:
        row.revoked_at = clock_now()  # лимит не возвращается (rules.used_in_window)
        db.flush()
    return row


def revoke_all(db: Session, candidate_id: uuid.UUID) -> None:
    db.execute(
        update(InterestSignal)
        .where(InterestSignal.candidate_id == candidate_id, InterestSignal.revoked_at.is_(None))
        .values(revoked_at=clock_now())
    )


def _opened(db: Session, company_id: uuid.UUID, ids: set[uuid.UUID]) -> set[uuid.UUID]:
    """Кандидаты, чьи карточки компания открывала или закрепила (DECISIONS 7), пакетно."""
    viewed = set(
        db.scalars(
            select(ProfileView.candidate_id).where(ProfileView.company_id == company_id, ProfileView.candidate_id.in_(ids))
        )
    )
    pinned = set(
        db.scalars(
            select(Shortlist.candidate_id).where(
                Shortlist.candidate_id.in_(ids), Shortlist.need_id == Need.id, Need.company_id == company_id
            )
        )
    )
    return viewed | pinned


def visible_for_company(
    db: Session, company_id: uuid.UUID, candidate_ids: Iterable[uuid.UUID] | None = None
) -> dict[uuid.UUID, dict[str, Any]]:
    """Бейдж «сигнал интереса» для работодателя: только по открытым/закреплённым карточкам и только действующий.

    candidate_ids=None — по всем кандидатам компании (для фильтра и сортировки банка)."""
    q = select(InterestSignal).where(InterestSignal.company_id == company_id)
    if candidate_ids is not None:
        ids = set(candidate_ids)
        if not ids:
            return {}
        q = q.where(InterestSignal.candidate_id.in_(ids))
    at = clock_now()
    rows = db.scalars(q.order_by(InterestSignal.created_at.desc()))
    live: dict[uuid.UUID, InterestSignal] = {}
    for r in rows:
        if rules.is_active(_rec(r), at) and r.candidate_id not in live:
            live[r.candidate_id] = r
    if not live:
        return {}
    opened = _opened(db, company_id, set(live))
    c = cfg().interest
    return {
        cid: {
            "label": c.badge_short,
            "text": c.badge,
            "hint": c.badge_hint,
            "sent": rel(r.created_at, at),
            "expires": rel(r.expires_at, at),
        }
        for cid, r in live.items()
        if cid in opened
    }
