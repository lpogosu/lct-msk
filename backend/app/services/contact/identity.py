"""Как работодатель называет кандидата: псевдоним «К-4821» и непрозрачная ссылка ref (DECISIONS 20).

Источник истины — сервис подбора (app.services.matching: get_alias, resolve_ref, card_opened, score_snapshot,
candidate_profile_view), чтобы подпись в приглашении совпадала с подборкой. Пока сервис подбора собирается
параллельно, здесь же — запасной путь на тех же таблицах (candidate_aliases, profile_views, shortlist_pins,
need_versions) и на той же формуле HMAC (app/contact/pseudonym.py).

Работодатель передаёт в API ref или псевдоним; внутренний id кандидата в его запросы и ответы не попадает.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.contact.pseudonym import pseudonym as hmac_pseudonym
from app.core.errors import conflict
from app.models.matching import CandidateAlias, Need, NeedVersion, ProfileView, Shortlist

log = logging.getLogger("vzaimno.contact")


def _matching() -> Any | None:
    try:
        import app.services.matching as m
    except Exception:  # noqa: BLE001 — модуль подбора собирается параллельно; работаем на запасном пути
        return None
    return m


@dataclass(frozen=True)
class Ident:
    pseudonym: str
    ref: str | None


def normalize_label(value: str) -> str:
    """«к-4821», «K-4821» (латиница), « К-4821 » → «К-4821»."""
    return (value or "").strip().upper().replace("K", "К")


def default_secret() -> str:
    """Секрет псевдонимов из окружения (VZ_PSEUDONYM_SECRET) — тот же, что у сервиса подбора."""
    from app.core.settings import get_settings

    return get_settings().pseudonym_secret.get_secret_value()


def alias(db: Session, secret: str, company_id: uuid.UUID, candidate_id: uuid.UUID) -> Ident:
    m = _matching()
    if m is not None and hasattr(m, "get_alias"):
        a = m.get_alias(db, company_id, candidate_id, secret=secret or None)
        return Ident(pseudonym=str(a.pseudonym), ref=getattr(a, "ref", None))
    row = db.get(CandidateAlias, (company_id, candidate_id))
    if row is not None:
        return Ident(row.pseudonym, row.ref)
    return Ident(hmac_pseudonym(secret or default_secret(), str(company_id), str(candidate_id)), None)


def card_opened(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID) -> bool:
    """DECISIONS 7: открыта панель Р6 ИЛИ кандидат в шортлисте любого брифа компании."""
    m = _matching()
    if m is not None and hasattr(m, "card_opened"):
        return bool(m.card_opened(db, company_id, candidate_id))
    viewed = db.scalar(
        select(ProfileView.id)
        .where(ProfileView.company_id == company_id, ProfileView.candidate_id == candidate_id)
        .limit(1)
    )
    return viewed is not None or shortlisted(db, company_id, candidate_id)


def in_bank(db: Session, candidate_id: uuid.UUID) -> bool:
    """Кандидат в банке: есть действующее согласие на показ слепой карточки (концепция §3.1 шаг 6, §3.3).

    Отозвал — его нет ни в подборке, ни в банке; компания, открывавшая карточку раньше, не может писать ему
    приглашения и присылать проверочный набор (для движка это то же, что режим «не ищу»)."""
    from app.core.consents import has_active_consent
    from app.models.enums import ConsentPurpose

    return has_active_consent(db, candidate_id, ConsentPurpose.blind_card)


def accepts_company(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID) -> bool:
    """Можно ли компании обращаться к кандидату по своей инициативе (приглашение, проверочный набор):
    кандидат в банке, не в режиме «не ищу», компании нет в его блок-листе. Причина наружу не выдаётся."""
    from app.models.candidate import CandidateProfile

    profile = db.get(CandidateProfile, candidate_id)
    if profile is not None:
        if profile.search_mode == "hidden" or company_id in set(profile.blocked_company_ids or []):
            return False
    return in_bank(db, candidate_id)


def shortlisted(db: Session, company_id: uuid.UUID, candidate_id: uuid.UUID, need_id: uuid.UUID | None = None) -> bool:
    q = (
        select(Shortlist.need_id)
        .join(Need, Need.id == Shortlist.need_id)
        .where(Need.company_id == company_id, Shortlist.candidate_id == candidate_id)
    )
    if need_id is not None:
        q = q.where(Shortlist.need_id == need_id)
    return db.scalar(q.limit(1)) is not None


def pool(db: Session, company_id: uuid.UUID, need_id: uuid.UUID | None = None) -> list[uuid.UUID]:
    """Кандидаты, которых компания вправе назвать: открытые карточки и шортлисты её брифов."""
    viewed = db.scalars(select(ProfileView.candidate_id).where(ProfileView.company_id == company_id).distinct())
    q = select(Shortlist.candidate_id).join(Need, Need.id == Shortlist.need_id).where(Need.company_id == company_id)
    if need_id is not None:
        q = q.where(Shortlist.need_id == need_id)
    return list(dict.fromkeys([*viewed, *db.scalars(q)]))


def resolve(
    db: Session,
    secret: str,
    company_id: uuid.UUID,
    value: str,
    candidates: Iterable[uuid.UUID] | None = None,
) -> uuid.UUID | None:
    """ref или псевдоним из запроса работодателя → id кандидата (или None: такого кандидата компания не видела)."""
    raw = (value or "").strip()
    if not raw:
        return None
    m = _matching()
    if m is not None and hasattr(m, "resolve_ref"):
        try:
            found = m.resolve_ref(db, company_id, raw)
        except Exception:  # noqa: BLE001 — формат ref не подошёл: пробуем как псевдоним
            found = None
        if found is not None:
            return found
    label = normalize_label(raw)
    row = db.scalar(
        select(CandidateAlias).where(
            CandidateAlias.company_id == company_id,
            or_(CandidateAlias.ref == raw, CandidateAlias.pseudonym == label),
        )
    )
    if row is not None:
        return row.candidate_id
    stored = set(db.scalars(select(CandidateAlias.candidate_id).where(CandidateAlias.company_id == company_id)))
    pool_ids = candidates if candidates is not None else pool(db, company_id)
    sec = secret or default_secret()
    matches = [
        cid
        for cid in dict.fromkeys(pool_ids)
        if cid not in stored and hmac_pseudonym(sec, str(company_id), str(cid)) == label
    ]
    if len(matches) > 1:
        raise conflict(
            "pseudonym_ambiguous",
            f"Псевдоним {label} совпал у двух кандидатов — откройте карточку нужного и повторите",
        )
    return matches[0] if matches else None


def score_snapshot(db: Session, need_id: uuid.UUID | None, candidate_id: uuid.UUID) -> dict[str, Any] | None:
    """Строка кандидата из текущей версии подборки брифа: балл, место, «почему вы» (без внутренних id)."""
    if need_id is None:
        return None
    m = _matching()
    raw: dict[str, Any] | None = None
    if m is not None and hasattr(m, "score_snapshot"):
        raw = m.score_snapshot(db, need_id, candidate_id)
    else:
        need = db.get(Need, need_id)
        version = db.get(NeedVersion, need.current_version_id) if need and need.current_version_id else None
        for item in (version.items if version else None) or []:
            if isinstance(item, dict) and str(item.get("candidate_id")) == str(candidate_id):
                raw = item
                break
    if not raw:
        return None
    # DECISIONS 20: в снимке, который видит работодатель, нет id кандидата и доказательств
    why = raw.get("why_you") or []
    return {
        "score": raw.get("score"),
        "rank": raw.get("rank"),
        "category": raw.get("category"),
        "parts": raw.get("parts") or {},
        "why_you": [str(w) for w in why][:3],
    }
