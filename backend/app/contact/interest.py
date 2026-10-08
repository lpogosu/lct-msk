"""«Сигнал интереса» кандидата (DECISIONS 24): чистые правила без БД.

Идея Coles–Kushnir–Niederle (NBER w16185): дефицитный дешёвый сигнал «мне эта компания интересна» помогает
обеим сторонам найти друг друга, не превращаясь в спам-отклик. Здесь он НЕ входит в Score (Score остаётся чистой
оценкой соответствия) и виден работодателю только после открытия карточки (DECISIONS 7) — слепой поток цел.
Числа — config/contact.yaml → interest; обоснование — симуляция рынка (docs/MATCHING.md, eval/market_sim.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from .settings import InterestCfg


@dataclass(frozen=True)
class SignalRecord:
    created_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None


def expires_at(created: datetime, cfg: InterestCfg) -> datetime:
    return created + timedelta(days=cfg.lifetime_days)


def is_active(rec: SignalRecord, now: datetime) -> bool:
    """Виден работодателю: не отозван и срок не истёк."""
    return rec.revoked_at is None and now < rec.expires_at


def used_in_window(created_ats: list[datetime], now: datetime, cfg: InterestCfg) -> int:
    """Сколько сигналов потрачено за скользящее окно. Отозванные считаются: лимит при отзыве не возвращается."""
    since = now - timedelta(days=cfg.window_days)
    return sum(1 for c in created_ats if c > since)


def remaining(created_ats: list[datetime], now: datetime, cfg: InterestCfg) -> int:
    return max(0, cfg.per_window - used_in_window(created_ats, now, cfg))


def next_slot_at(created_ats: list[datetime], now: datetime, cfg: InterestCfg) -> datetime | None:
    """Когда освободится слот (None — слот уже есть)."""
    if remaining(created_ats, now, cfg) > 0:
        return None
    window = sorted(c for c in created_ats if c > now - timedelta(days=cfg.window_days))
    return window[0] + timedelta(days=cfg.window_days)
