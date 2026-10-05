"""Источник времени для механики контакта.

Сроки применяются лениво при чтении (концепция §4.3), поэтому «сейчас» — единственный вход, от которого
зависят статусы. Его подменяют: FakeClock в тестах и в DEMO_MODE («+1 день / +3 дня», POST /demo/advance).
Все даты — aware datetime (UTC внутри); в UI — только относительные формулировки (DECISIONS 9, texts.relative).
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Protocol, runtime_checkable

from .settings import ContactConfig, get_contact_config


@runtime_checkable
class Clock(Protocol):
    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FakeClock:
    """Подменяемые часы.

    FakeClock(start=dt) — замороженное время (тесты): двигается только через advance().
    FakeClock() — реальное время плюс накопленное смещение (DEMO_MODE): offset_seconds хранится в DemoClock.
    """

    def __init__(
        self,
        start: datetime | None = None,
        *,
        base: Clock | None = None,
        offset: timedelta = timedelta(0),
    ) -> None:
        if start is not None:
            require_aware(start)
        self._start = start
        self._base: Clock = base or SystemClock()
        self._offset = offset

    def now(self) -> datetime:
        t = self._start if self._start is not None else self._base.now()
        return t + self._offset

    @property
    def offset_seconds(self) -> int:
        return int(self._offset.total_seconds())

    def advance(
        self, *, days: float = 0, hours: float = 0, minutes: float = 0, seconds: float = 0
    ) -> datetime:
        delta = timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)
        if delta < timedelta(0):
            raise ValueError("Часы не идут назад")
        self._offset += delta
        return self.now()

    def advance_preset(self, label: str, cfg: ContactConfig | None = None) -> datetime:
        """DEMO_MODE: «+1 день» / «+3 дня» из config/contact.yaml → demo.advance_presets."""
        cfg = cfg or get_contact_config()
        for preset in cfg.demo.advance_presets:
            if preset.label == label:
                return self.advance(days=preset.days)
        raise KeyError(f"Нет демо-сдвига «{label}»")

    def set(self, moment: datetime) -> None:
        require_aware(moment)
        self._start = moment
        self._offset = timedelta(0)


def require_aware(moment: datetime) -> datetime:
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError("Ожидается datetime с часовым поясом")
    return moment


def now_of(clock: Clock | datetime) -> datetime:
    """Принимает часы или уже взятое «сейчас» — удобно для чистых функций."""
    moment = clock if isinstance(clock, datetime) else clock.now()
    return require_aware(moment)


def local_tz(cfg: ContactConfig | None = None) -> timezone:
    cfg = cfg or get_contact_config()
    return timezone(timedelta(hours=cfg.timezone_offset_hours))


def to_local(moment: datetime, cfg: ContactConfig | None = None) -> datetime:
    return require_aware(moment).astimezone(local_tz(cfg))


def local_date(moment: datetime, cfg: ContactConfig | None = None) -> date:
    return to_local(moment, cfg).date()


def week_start(moment: datetime, cfg: ContactConfig | None = None) -> datetime:
    """Понедельник 00:00 по Москве недели, в которую попадает moment (лимит кандидата — ISO-неделя)."""
    local = to_local(moment, cfg)
    monday = local.date() - timedelta(days=local.weekday())
    return datetime(monday.year, monday.month, monday.day, tzinfo=local.tzinfo)


def is_business_day(day: date, cfg: ContactConfig | None = None) -> bool:
    cfg = cfg or get_contact_config()
    return day.weekday() not in cfg.business_days.weekend and day not in cfg.business_days.holidays


def add_business_days(start: datetime, n: int, cfg: ContactConfig | None = None) -> datetime:
    """start + n рабочих дней по Москве с сохранением времени суток («свяжется до …», концепция §4.3).

    Выходные и праздники из config пропускаются; принятие в субботу считается с понедельника.
    """
    cfg = cfg or get_contact_config()
    local = to_local(start, cfg)
    day = local.date()
    added = 0
    while added < n:
        day += timedelta(days=1)
        if is_business_day(day, cfg):
            added += 1
    result = datetime.combine(day, local.timetz())
    return result.astimezone(UTC)
