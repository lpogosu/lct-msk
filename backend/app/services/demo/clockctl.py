"""Часы демо: сдвиг «+1 день / +3 дня», сброс, временной сдвиг на время сборки мира.

Бизнес-часы платформы — app.core.clock.clock (DECISIONS п. 19): сроки приглашений, свежесть теста, паузы.
Смещение переживает перезапуск бэкенда — оно хранится в demo_clock (концепция §9.4).
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import clock
from app.models.system import DemoClock

# Сборка мира и тик автоответчика не должны одновременно двигать общие часы процесса.
CLOCK_LOCK = threading.RLock()


def _state() -> tuple[datetime | None, float]:
    # У Clock нет публичного чтения «заморожен ли», а восстановить состояние после временного сдвига нужно
    # точно (тесты замораживают часы). Поэтому читаем приватные поля — только здесь и только для этого.
    return clock._frozen, clock.offset_seconds  # noqa: SLF001


def _restore(frozen: datetime | None, offset: float) -> None:
    clock.reset()
    if frozen is not None:
        clock.freeze(frozen)
    clock.set_offset(offset)


@contextmanager
def clock_at(moment: datetime) -> Iterator[None]:
    """Часы замораживаются на moment (aware); при выходе возвращается прежнее состояние (заморозка и смещение)."""
    with CLOCK_LOCK:
        frozen, offset = _state()
        clock.freeze(moment)
        try:
            yield
        finally:
            _restore(frozen, offset)


@contextmanager
def clock_ticking_from(moment: datetime) -> Iterator[None]:
    """Часы стартуют с moment и дальше идут вперёд только по явному clock.advance (история в прошлом)."""
    with clock_at(moment):
        yield


def real_now() -> datetime:
    """Текущее время по часам платформы (с учётом смещения демо) — «сегодня» для сборки мира."""
    return clock.now()


def days_ago(n: float, *, base: datetime | None = None) -> datetime:
    return (base or clock.now()) - timedelta(days=n)


def persist_offset(db: Session) -> int:
    """Записать смещение Clock в demo_clock (строка id = 1)."""
    row = db.get(DemoClock, 1)
    seconds = int(round(clock.offset_seconds))
    if row is None:
        row = DemoClock(id=1, offset_seconds=seconds)
        db.add(row)
    else:
        row.offset_seconds = seconds
    db.flush()
    return seconds


def describe(now: datetime | None = None) -> dict[str, Any]:
    now = now or clock.now()
    offset = int(round(clock.offset_seconds))
    return {
        "now": now.astimezone(UTC),
        "offset_seconds": offset,
        "offset_days": round(offset / 86400, 3),
        "shifted": offset != 0,
    }
