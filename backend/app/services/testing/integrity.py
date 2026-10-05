"""Детекторы целостности на данных из БД (spec-testing §3.10). Сами правила — в app/testing/detectors.py.

- «Чужой ключ» — при каждом ответе: хеш нормализованного ответа ищется по индексу key_hash среди
  экземпляров того же семейства, выданных ДРУГИМ аккаунтам; срабатывает при доле коллизий < 2%.
  ≥ 3 таких флагов по семейству за 7 дней → семейство в карантин (DriftEvent).
- l_z, «быстро и верно», события — при решении сессии, по зачётным ответам цепочки.
Флаги хранятся строками таблицы flags; кандидат видит свои (без источника), работодатель — никогда.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.models.enums import FamilyStatus, FlagStatus
from app.models.testing import DriftEvent, ItemFamilyParams, ItemInstance
from app.models.testing import Flag as FlagRow
from app.models.testing import SessionEvent as SessionEventRow
from app.models.testing import TestSession as SessionRow
from app.services.testing.catalog import median_rt_ms
from app.testing.detectors import (
    KIND_FAST,
    KIND_LZ,
    Flag,
    IssuedInstance,
    SessionEvent,
    TimedResponse,
    answer_hash,
    detect_events,
    detect_fast_correct,
    detect_foreign_key,
    detect_person_fit,
    family_needs_quarantine_by_foreign_keys,
    key_collision_rate,
    load_detector_config,
)
from app.testing.session import TestSession


def flag_from_row(row: FlagRow) -> Flag:
    d = row.details or {}
    return Flag(
        kind=row.kind,
        severity=row.severity,
        message_ru=str(d.get("message_ru") or ""),
        value=row.value,
        family_id=d.get("family_id"),
        source_session_id=str(row.source_session_id) if row.source_session_id else None,
        source_account_id=d.get("source_account_id"),
        status=row.status,
    )


def session_flag_rows(db: Session, session_id: uuid.UUID) -> list[FlagRow]:
    return list(db.scalars(select(FlagRow).where(FlagRow.session_id == session_id).order_by(FlagRow.created_at, FlagRow.id)))


def persist_flag(db: Session, session_id: uuid.UUID, flag: Flag, now: datetime, extra: dict[str, Any] | None = None) -> FlagRow:
    details: dict[str, Any] = {"message_ru": flag.message_ru}
    if flag.family_id:
        details["family_id"] = flag.family_id
    if flag.source_account_id:
        details["source_account_id"] = flag.source_account_id
    if extra:
        details.update(extra)
    src = None
    if flag.source_session_id:
        try:
            src = uuid.UUID(flag.source_session_id)
        except ValueError:
            src = None
    row = FlagRow(
        session_id=session_id,
        kind=flag.kind[:16],
        severity=flag.severity,
        value=flag.value,
        source_session_id=src,
        details=details,
        status=FlagStatus.open.value,
        created_at=now,
    )
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# «Чужой ключ»
# ---------------------------------------------------------------------------


def family_collision_rate(db: Session, family_id: str) -> float:
    """1 − различных ключей / всего в пуле семейства (как key_collision_rate движка, но SQL-агрегатом)."""
    total, distinct_n = db.execute(
        select(func.count(), func.count(func.distinct(ItemInstance.key_hash))).where(ItemInstance.family_id == family_id)
    ).one()
    if not total:
        return key_collision_rate([])
    return 1.0 - distinct_n / total


def check_foreign_key(
    db: Session,
    *,
    family_id: str,
    normalized_answer: Any,
    own_key_hash: str,
    user_id: uuid.UUID,
) -> Flag | None:
    """Ответ совпал с ключом экземпляра, выданного другому аккаунту → жёсткий флаг с источником."""
    h = answer_hash(family_id, normalized_answer)
    if h == own_key_hash:
        return None
    rows = db.execute(
        select(ItemInstance.id, ItemInstance.key_hash, ItemInstance.issued_to_user_id, ItemInstance.issued_to_session_id)
        .where(
            ItemInstance.family_id == family_id,
            ItemInstance.key_hash == h,
            ItemInstance.issued_to_user_id.is_not(None),
            ItemInstance.issued_to_user_id != user_id,
        )
        .order_by(ItemInstance.issued_at)
        .limit(5)
    ).all()
    if not rows:
        return None
    issued = [
        IssuedInstance(str(i), family_id, kh, str(acc), str(sess) if sess else "")
        for i, kh, acc, sess in rows
    ]
    return detect_foreign_key(
        family_id=family_id,
        answer_key_hash=h,
        own_key_hash=own_key_hash,
        account_id=str(user_id),
        issued=issued,
        family_collision_rate=family_collision_rate(db, family_id),
    )


def maybe_quarantine_family(db: Session, family_id: str, now: datetime) -> bool:
    """≥ 3 «чужих ключа» по семейству за 7 дней → QUARANTINE семейства на всех шкалах (§3.10)."""
    cfg = load_detector_config()
    since = now - timedelta(days=cfg.foreign_key_quarantine_days)
    times = db.scalars(
        select(FlagRow.created_at).where(
            FlagRow.kind == "foreign_key",
            FlagRow.details["family_id"].astext == family_id,
            FlagRow.created_at >= since,
            FlagRow.status != FlagStatus.dismissed.value,
        )
    ).all()
    if not family_needs_quarantine_by_foreign_keys(times, now, cfg):
        return False
    changed = db.execute(
        update(ItemFamilyParams)
        .where(ItemFamilyParams.family_id == family_id, ItemFamilyParams.status == FamilyStatus.operational.value)
        .values(status=FamilyStatus.quarantine.value)
    ).rowcount
    if changed:
        db.add(
            DriftEvent(
                family_id=family_id,
                at=now,
                statistic="foreign_key",
                value=float(len(times)),
                action="quarantine",
            )
        )
        db.flush()
    return bool(changed)


# ---------------------------------------------------------------------------
# Поведенческие детекторы при решении
# ---------------------------------------------------------------------------


def chain_session_ids(db: Session, row: SessionRow) -> list[uuid.UUID]:
    """Сессия и её предки по parent_session_id (доборы продолжают тот же апостериор)."""
    out = [row.id]
    parent = row.parent_session_id
    seen = {row.id}
    while parent is not None and parent not in seen:
        out.append(parent)
        seen.add(parent)
        parent = db.scalar(select(SessionRow.parent_session_id).where(SessionRow.id == parent))
    return out


def load_events(db: Session, session_id: uuid.UUID) -> list[SessionEvent]:
    rows = db.scalars(select(SessionEventRow).where(SessionEventRow.session_id == session_id).order_by(SessionEventRow.ts, SessionEventRow.id))
    return [SessionEvent(ts=r.ts, type=r.type, payload=dict(r.payload or {})) for r in rows]


def behavioral_flags(
    db: Session,
    row: SessionRow,
    sess: TestSession,
    *,
    end: datetime,
    chain_ids: Sequence[uuid.UUID],
    already: Iterable[Flag] = (),
) -> list[Flag]:
    """l_z и «быстро и верно» — по всем зачётным ответам цепочки (флаг, уже поставленный в цепочке,
    не дублируется), события — только этой сессии."""
    scored = sess.cat.scored
    items = [r.item for r in scored]
    xs = [r.correct for r in scored]
    have = {f.kind for f in already}
    flags: list[Flag] = []
    if KIND_LZ not in have:
        f = detect_person_fit(items, xs)
        if f:
            flags.append(f)
    if KIND_FAST not in have:
        medians = median_rt_ms(db, {r.family_id for r in scored}, exclude_session_ids=chain_ids)
        if medians:
            timed = [TimedResponse(r.family_id, r.item, r.correct, r.rt_ms) for r in scored]
            f = detect_fast_correct(timed, medians)
            if f:
                flags.append(f)
    flags.extend(detect_events(load_events(db, row.id), end))
    return flags
