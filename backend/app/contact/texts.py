"""Русские формулировки: склонения, относительные даты (DECISIONS 9), деньги, десятичная запятая."""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from .clock import require_aware

_DAYS = ("день", "дня", "дней")
_HOURS = ("час", "часа", "часов")
_MINUTES = ("минуту", "минуты", "минут")


def plural(n: int, forms: tuple[str, str, str]) -> str:
    """plural(1, ("день","дня","дней")) → «день»; 3 → «дня»; 11 → «дней»."""
    n = abs(int(n))
    if n % 10 == 1 and n % 100 != 11:
        return forms[0]
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return forms[1]
    return forms[2]


def count_words(n: int, forms: tuple[str, str, str]) -> str:
    return f"{n} {plural(n, forms)}"


def days(n: int) -> str:
    return count_words(n, _DAYS)


def _half_up(x: float) -> int:
    # round() в Python банковский (6.5 → 6); для «через N дней» нужно обычное округление.
    return math.floor(x + 0.5)


def _span(delta: timedelta) -> str:
    seconds = delta.total_seconds()
    if seconds >= 86400:
        return days(max(1, _half_up(seconds / 86400)))
    if seconds >= 3600:
        return count_words(max(1, _half_up(seconds / 3600)), _HOURS)
    return count_words(max(1, math.ceil(seconds / 60)), _MINUTES)


def relative(target: datetime, now: datetime) -> str:
    """Относительная дата для UI: «через 3 дня», «через 5 часов», «2 дня назад», «сейчас»."""
    delta = require_aware(target) - require_aware(now)
    if abs(delta) < timedelta(minutes=1):
        return "сейчас"
    if delta > timedelta(0):
        return f"через {_span(delta)}"
    return f"{_span(-delta)} назад"


def rub(amount: int | float) -> str:
    """270000 → «270 000 ₽»."""
    return f"{int(round(amount)):,}".replace(",", " ") + " ₽"


def salary_range(salary_from: int, salary_to: int) -> str:
    return f"{int(salary_from):,}–{int(salary_to):,}".replace(",", " ") + " ₽"


def decimal_ru(value: float, digits: int = 2) -> str:
    """0.92 → «0,92»."""
    return f"{value:.{digits}f}".replace(".", ",")


def percent(value: float) -> str:
    """0.94 → «94%»."""
    return f"{round(value * 100)}%"
