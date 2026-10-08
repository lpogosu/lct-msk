"""Дешёвые автоматические сигналы для модерации: эвристики на данных, которые уже есть в базе.

Ничего не блокируют и не меняют квоту сами — это подсказки для очереди методиста/администратора (docs/TRUST_AND_SAFETY.md).
Пороги и подписи — config/trust.yaml -> flags. Считаются «на лету» по компании, без фоновой задачи.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.matching.market import salary_vs_market
from app.models.company import Company, ReliabilityEventRow
from app.models.contact import Application, Invitation, OfferTerms, Vacancy
from app.models.matching import Need
from app.trust import verification
from app.trust.verification import cfg as trust_cfg

DISCREPANCY_KINDS = ("terms_lower_salary", "terms_no_vacancy")


def _many_invites_no_outcomes(db: Session, company_id: uuid.UUID, at: datetime, c: dict[str, Any]) -> str | None:
    since = at - timedelta(days=int(c["window_days"]))
    sent = int(
        db.scalar(select(func.count()).select_from(Invitation).where(Invitation.company_id == company_id, Invitation.created_at >= since))
        or 0
    )
    if sent < int(c["min_sent"]):
        return None
    accepted = int(
        db.scalar(
            select(func.count())
            .select_from(Invitation)
            .where(Invitation.company_id == company_id, Invitation.created_at >= since, Invitation.accepted_at.is_not(None))
        )
        or 0
    )
    return f"Отправлено {sent} за {c['window_days']} дн., принятых — {accepted}" if accepted == 0 else None


def _vacancy_idle(db: Session, company_id: uuid.UUID, at: datetime, c: dict[str, Any]) -> str | None:
    before = at - timedelta(days=int(c["open_days"]))
    vacancies = db.scalars(
        select(Vacancy).where(Vacancy.company_id == company_id, Vacancy.status == "published", Vacancy.published_at <= before)
    ).all()
    idle = []
    for v in vacancies:
        applied = db.scalar(select(func.count()).select_from(Application).where(Application.vacancy_id == v.id)) or 0
        accepted = (
            db.scalar(
                select(func.count()).select_from(Invitation).where(Invitation.vacancy_id == v.id, Invitation.accepted_at.is_not(None))
            )
            or 0
        )
        if applied == 0 and accepted == 0:
            idle.append(v.title)
    return ("Без откликов и контактов: " + "; ".join(idle[:3])) if idle else None


def _salary_below_brief(db: Session, company_id: uuid.UUID, c: dict[str, Any]) -> str | None:
    """Нижняя граница в приглашении (первая версия условий) ниже вилки брифа: на разных базисах «на руки / до НДФЛ» не сравниваем."""
    rows = db.execute(
        select(OfferTerms.salary_from, OfferTerms.salary_net, Need.salary_from, Need.salary_net)
        .join(Invitation, Invitation.id == OfferTerms.invitation_id)
        .join(Need, Need.id == Invitation.need_id)
        .where(Invitation.company_id == company_id, OfferTerms.ver == 1)
    ).all()
    ratio = float(c["tolerance_ratio"])
    low = [r for r in rows if r[1] == r[3] and r[0] < ratio * r[2]]
    return f"Приглашений с зарплатой ниже брифа: {len(low)}" if len(low) >= int(c["min_invitations"]) else None


def _salary_far_below_market(db: Session, company_id: uuid.UUID, c: dict[str, Any]) -> str | None:
    """Вилка брифа сильно ниже рынка (открытые данные): сигнал, не блокировка. Основание gross/net учитывается."""
    needs = db.scalars(select(Need).where(Need.company_id == company_id, Need.status.in_(c["statuses"]))).all()
    low = []
    for n in needs:
        r = salary_vs_market(n.specialization, n.grade, n.salary_to, bool(n.salary_net), float(c["ratio"]))
        if r and r["far_below"]:
            low.append((n.title or n.specialization or "бриф", n.salary_to, r["benchmark"]["gross"]["p25"]))
    if len(low) < int(c["min_needs"]):
        return None
    t, to, p25 = low[0]

    def money(x: int) -> str:
        return f"{x:,}".replace(",", " ") + " ₽"

    return (f"Брифов с вилкой сильно ниже рынка: {len(low)} (например, «{t}»: до {money(to)}; "
            f"нижний квартиль рынка — {money(p25)} до НДФЛ)")


def _terms_discrepancy(db: Session, company_id: uuid.UUID, at: datetime, c: dict[str, Any], window_days: int) -> str | None:
    n = int(
        db.scalar(
            select(func.count())
            .select_from(ReliabilityEventRow)
            .where(
                ReliabilityEventRow.company_id == company_id,
                ReliabilityEventRow.kind.in_(DISCREPANCY_KINDS),
                ReliabilityEventRow.at >= at - timedelta(days=window_days),
            )
        )
        or 0
    )
    return f"Сообщений о расхождении условий: {n}" if n >= int(c["min_events"]) else None


def company_flags(db: Session, company_id: uuid.UUID, at: datetime) -> list[dict[str, str]]:
    fc = trust_cfg()["flags"]
    window = int(trust_cfg()["complaints"]["window_days"])
    found = {
        "many_invites_no_outcomes": _many_invites_no_outcomes(db, company_id, at, fc["many_invites_no_outcomes"]),
        "vacancy_idle": _vacancy_idle(db, company_id, at, fc["vacancy_idle"]),
        "salary_below_brief": _salary_below_brief(db, company_id, fc["salary_below_brief"]),
        "salary_far_below_market": _salary_far_below_market(db, company_id, fc["salary_far_below_market"]),
        "terms_discrepancy": _terms_discrepancy(db, company_id, at, fc["terms_discrepancy"], window),
    }
    return [{"code": code, "label": fc[code]["label"], "detail": detail} for code, detail in found.items() if detail]


def flagged_companies(db: Session, at: datetime, limit: int = 200) -> list[dict[str, Any]]:
    """Компании хотя бы с одним сигналом. Перебор по компаниям, у которых вообще есть приглашения или опубликованные вакансии."""
    ids = set(db.scalars(select(Invitation.company_id).distinct())) | set(
        db.scalars(select(Vacancy.company_id).where(Vacancy.status == "published").distinct())
    )
    out = []
    for cid in sorted(ids, key=str)[:limit]:
        flags = company_flags(db, cid, at)
        if not flags:
            continue
        company = db.get(Company, cid)
        out.append(
            {
                "company_id": cid,
                "company_name": (company.name if company and company.name else None) or "Компания без названия",
                "verification_level": verification.trust_of(db, cid)["level"],
                "flags": flags,
            }
        )
    return out
