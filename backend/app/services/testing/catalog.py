"""Входы CAT-движка из БД (spec-testing §3.4, §3.10): метаданные семейств с параметрами шкалы,
экспозицией за 30 дней и числом свободных экземпляров, история семейств кандидата за 12 месяцев,
медианы времени ответа по семействам.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta

from sqlalchemy import and_, distinct, func, select
from sqlalchemy.orm import Session

from app.models.enums import EXCLUSIVE_SESSION_KINDS
from app.models.testing import ItemFamilyParams, ItemInstance, TestResponse, TestSession
from app.services.testing.limits import EXPOSURE_MIN_SESSIONS, EXPOSURE_WINDOW_DAYS, MEDIAN_RT_MIN_RESPONSES
from app.services.testing.pool import families_for
from app.testing.cat import FamilyMeta, ScaleParam, load_cat_config, meta_from_family


def family_params(db: Session, family_ids: Iterable[str] | None = None) -> dict[tuple[str, str], ItemFamilyParams]:
    q = select(ItemFamilyParams)
    if family_ids is not None:
        q = q.where(ItemFamilyParams.family_id.in_(list(family_ids)))
    return {(p.family_id, p.specialization): p for p in db.scalars(q)}


def free_counts(db: Session, family_ids: Iterable[str]) -> dict[str, int]:
    rows = db.execute(
        select(ItemInstance.family_id, func.count())
        .where(ItemInstance.family_id.in_(list(family_ids)), ItemInstance.issued_at.is_(None))
        .group_by(ItemInstance.family_id)
    )
    return {fid: int(n) for fid, n in rows}


def exposure_30d(db: Session, spec: str, now: datetime) -> dict[str, float]:
    """Доля сессий специализации за 30 дней, в которых показано семейство (§2.5 «экспозиция»).

    Пока сессий за окно меньше EXPOSURE_MIN_SESSIONS, доля не считается (см. limits.py).
    """
    since = now - timedelta(days=EXPOSURE_WINDOW_DAYS)
    base = and_(
        TestSession.specialization == spec,
        TestSession.kind.in_(EXCLUSIVE_SESSION_KINDS),
        TestSession.demo_assisted.is_(False),
        TestResponse.shown_at >= since,
    )
    total = db.scalar(
        select(func.count(distinct(TestResponse.session_id))).join(TestSession, TestSession.id == TestResponse.session_id).where(base)
    ) or 0
    if total < EXPOSURE_MIN_SESSIONS:
        return {}
    rows = db.execute(
        select(TestResponse.family_id, func.count(distinct(TestResponse.session_id)))
        .join(TestSession, TestSession.id == TestResponse.session_id)
        .where(base)
        .group_by(TestResponse.family_id)
    )
    return {fid: n / total for fid, n in rows}


def family_metas(db: Session, spec: str, now: datetime) -> list[FamilyMeta]:
    """FamilyMeta для шкалы spec: статус и (b, σ_b) — из ItemFamilyParams этой шкалы (у общих модулей
    своя b на каждой шкале), без строки параметров семейство в тест не попадает."""
    fams = families_for(spec)
    ids = [f.id for f in fams]
    params = family_params(db, ids)
    free = free_counts(db, ids)
    expo = exposure_30d(db, spec, now)
    out: list[FamilyMeta] = []
    for f in fams:
        p = params.get((f.id, spec))
        if p is None:
            continue
        scale_params = {
            s: ScaleParam(b=params[(f.id, s)].b, sigma_b=params[(f.id, s)].sigma_b)
            for s in f.specs
            if (f.id, s) in params
        }
        out.append(
            meta_from_family(
                f,
                params=scale_params,
                status=p.status,
                exposure_30d=expo.get(f.id, 0.0),
                free_instances=free.get(f.id, 0),
            )
        )
    return out


def seen_history(db: Session, user_id: uuid.UUID, now: datetime, exclude_sessions: Iterable[uuid.UUID] = ()) -> dict[str, datetime]:
    """Семейства, которые кандидат видел за 12 месяцев (неповтор §2.5) → когда видел последний раз."""
    cfg = load_cat_config()
    since = now - timedelta(days=cfg.family_cooldown_days)
    excl = list(exclude_sessions)
    q = (
        select(TestResponse.family_id, func.max(TestResponse.shown_at))
        .join(TestSession, TestSession.id == TestResponse.session_id)
        .where(TestSession.candidate_id == user_id, TestResponse.shown_at >= since)
        .group_by(TestResponse.family_id)
    )
    if excl:
        q = q.where(TestResponse.session_id.not_in(excl))
    return {fid: ts for fid, ts in db.execute(q)}


def median_rt_ms(db: Session, family_ids: Iterable[str], exclude_session_ids: Iterable[uuid.UUID] = ()) -> dict[str, float]:
    """Медиана времени ответа по семейству — по чужим сессиям (свой ответ не должен задавать «норму»).
    Режим жюри (demo_assisted, DECISIONS п. 4: автоответчик 30–90 с) «норму» тоже не задаёт."""
    ids = list(family_ids)
    if not ids:
        return {}
    excl = list(exclude_session_ids)
    q = (
        select(
            TestResponse.family_id,
            func.percentile_cont(0.5).within_group(TestResponse.rt_ms),
            func.count(),
        )
        .join(TestSession, TestSession.id == TestResponse.session_id)
        .where(
            TestResponse.family_id.in_(ids),
            TestResponse.rt_ms.is_not(None),
            TestResponse.answered_at.is_not(None),
            TestSession.demo_assisted.is_(False),
        )
        .group_by(TestResponse.family_id)
    )
    if excl:
        q = q.where(TestResponse.session_id.not_in(excl))
    return {fid: float(med) for fid, med, n in db.execute(q) if med is not None and n >= MEDIAN_RT_MIN_RESPONSES}
