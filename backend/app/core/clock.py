"""Clock — единый источник «текущего времени» для бизнес-логики.

Сроки (код подтверждения, TTL приглашений, паузы, свежесть) считаются от clock.now(), а не от
datetime.now(): в тестах часы замораживаются/сдвигаются, в DEMO_MODE кнопка «+1 день» двигает
смещение (концепция §4.3 «Clock подменяется в тестах и в DEMO_MODE», §9.4 «Д»).
Подпись access-JWT — инфраструктура и живёт по настенным часам (см. app/core/security.py).

DECISIONS п. 9: даты в UI — только относительные, через Clock → relative_ru().
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta


class Clock:
    """Часы с поддержкой заморозки (тесты) и смещения (DEMO_MODE). Потокобезопасны."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._offset = timedelta(0)
        self._frozen: datetime | None = None

    def now(self) -> datetime:
        with self._lock:
            base = self._frozen if self._frozen is not None else datetime.now(UTC)
            return base + self._offset

    @property
    def offset_seconds(self) -> float:
        return self._offset.total_seconds()

    def advance(self, seconds: float) -> None:
        """Сдвинуть часы вперёд (DEMO «+1 день», тесты «прошло 16 минут»)."""
        with self._lock:
            self._offset += timedelta(seconds=seconds)

    def set_offset(self, seconds: float) -> None:
        with self._lock:
            self._offset = timedelta(seconds=seconds)

    def freeze(self, at: datetime | None = None) -> None:
        """Остановить часы на `at` (aware UTC) или на текущем моменте."""
        with self._lock:
            base = at if at is not None else datetime.now(UTC)
            if base.tzinfo is None:
                raise ValueError("Clock.freeze ожидает datetime с часовым поясом")
            self._frozen = base.astimezone(UTC)
            self._offset = timedelta(0)

    def reset(self) -> None:
        with self._lock:
            self._frozen = None
            self._offset = timedelta(0)


# Процессный экземпляр. Импортируйте модуль, а не значение: `from app.core import clock as clock_mod`
# или `from app.core.clock import clock` (объект один и тот же, меняется его состояние).
clock = Clock()


def get_clock() -> Clock:
    """FastAPI-зависимость."""
    return clock


def utcnow() -> datetime:
    """Текущее время по Clock — используется как default в ORM-моделях."""
    return clock.now()


def _plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


_UNITS = (
    (86400, ("день", "дня", "дней")),
    (3600, ("час", "часа", "часов")),
    (60, ("минуту", "минуты", "минут")),
)


def relative_ru(target: datetime, now: datetime | None = None) -> str:
    """Относительная дата по-русски: «через 3 дня», «2 часа назад», «только что».

    Берём самую крупную целую единицу (дни → часы → минуты), остаток отбрасываем: 2 дня 23 часа →
    «через 2 дня». Для сроков это безопасная сторона — срок не выглядит дальше, чем он есть.
    """
    now = now if now is not None else clock.now()
    delta = (target - now).total_seconds()
    seconds = abs(delta)
    if seconds < 60:
        return "только что" if delta <= 0 else "меньше минуты"
    for unit_s, forms in _UNITS:
        if seconds >= unit_s:
            n = int(seconds // unit_s)
            if unit_s == 86400 and n == 1:
                return "завтра" if delta > 0 else "вчера"
            words = f"{n} {_plural(n, *forms)}"
            return f"через {words}" if delta > 0 else f"{words} назад"
    return "только что"  # pragma: no cover — недостижимо
