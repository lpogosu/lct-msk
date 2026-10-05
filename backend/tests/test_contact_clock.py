"""Clock (тесты и DEMO_MODE), рабочие дни, относительные даты и русские склонения, проверка config."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.contact.clock import Clock, FakeClock, SystemClock, add_business_days, is_business_day, week_start
from app.contact.settings import get_contact_config
from app.contact.texts import decimal_ru, percent, plural, relative, rub, salary_range

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)  # понедельник, 12:00 МСК
DAY = ("день", "дня", "дней")


def test_fake_clock_frozen_and_advance():
    clock = FakeClock(T0)
    assert isinstance(clock, Clock)
    assert clock.now() == T0 and clock.now() == T0
    assert clock.advance(days=1, hours=2) == T0 + timedelta(days=1, hours=2)
    assert clock.offset_seconds == 26 * 3600
    with pytest.raises(ValueError):
        clock.advance(hours=-1)
    clock.set(T0)
    assert clock.now() == T0 and clock.offset_seconds == 0


def test_fake_clock_demo_presets_over_base_clock():
    base = FakeClock(T0)
    demo = FakeClock(base=base)  # DEMO_MODE: «реальное» время + смещение
    assert demo.advance_preset("+1 день") == T0 + timedelta(days=1)
    assert demo.advance_preset("+3 дня") == T0 + timedelta(days=4)
    base.advance(hours=1)
    assert demo.now() == T0 + timedelta(days=4, hours=1)
    with pytest.raises(KeyError):
        demo.advance_preset("+1 год")


def test_naive_datetime_rejected_and_system_clock_aware():
    with pytest.raises(ValueError):
        FakeClock(datetime(2026, 10, 5, 12, 0))
    assert SystemClock().now().tzinfo is not None


def test_business_days():
    friday = datetime(2026, 10, 9, 12, 0, tzinfo=UTC)
    assert add_business_days(friday, 3) == datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
    assert add_business_days(T0, 3) == T0 + timedelta(days=3)
    saturday = datetime(2026, 10, 10, 7, 0, tzinfo=UTC)
    assert add_business_days(saturday, 1) == datetime(2026, 10, 12, 7, 0, tzinfo=UTC)
    # 04.11 — праздник: понедельник 02.11 + 3 → пятница 06.11
    monday = datetime(2026, 11, 2, 9, 0, tzinfo=UTC)
    assert add_business_days(monday, 3) == datetime(2026, 11, 6, 9, 0, tzinfo=UTC)
    assert not is_business_day(date(2026, 11, 4)) and is_business_day(date(2026, 11, 5))


def test_business_days_use_moscow_date():
    # 22:30 UTC пятницы — это уже суббота 01:30 по Москве: счёт начинается с понедельника
    late_friday = datetime(2026, 10, 9, 22, 30, tzinfo=UTC)
    assert add_business_days(late_friday, 1) == datetime(2026, 10, 11, 22, 30, tzinfo=UTC)  # пн 01:30 МСК


def test_week_start_is_moscow_monday():
    sunday_late = datetime(2026, 10, 11, 20, 59, tzinfo=UTC)  # вс 23:59 МСК — ещё старая неделя
    assert week_start(sunday_late).date() == date(2026, 10, 5)
    ws = week_start(datetime(2026, 10, 11, 21, 30, tzinfo=UTC))  # пн 12.10 00:30 МСК — уже новая неделя
    assert ws.date() == date(2026, 10, 12)
    assert week_start(T0).date() == date(2026, 10, 5)


@pytest.mark.parametrize(
    "n,word",
    [
        (1, "день"),
        (2, "дня"),
        (4, "дня"),
        (5, "дней"),
        (11, "дней"),
        (12, "дней"),
        (21, "день"),
        (22, "дня"),
        (111, "дней"),
    ],
)
def test_plural(n, word):
    assert plural(n, DAY) == word


@pytest.mark.parametrize(
    "delta,text",
    [
        (timedelta(days=3), "через 3 дня"),
        (timedelta(days=1), "через 1 день"),
        (timedelta(days=21), "через 21 день"),
        (timedelta(days=6, hours=12), "через 7 дней"),
        (timedelta(hours=5), "через 5 часов"),
        (timedelta(hours=1), "через 1 час"),
        (timedelta(minutes=30), "через 30 минут"),
        (timedelta(seconds=20), "сейчас"),
        (-timedelta(days=2), "2 дня назад"),
        (-timedelta(hours=3), "3 часа назад"),
    ],
)
def test_relative_dates(delta, text):
    assert relative(T0 + delta, T0) == text


def test_money_and_numbers():
    assert rub(270_000) == "270 000 ₽"
    assert salary_range(220_000, 280_000) == "220 000–280 000 ₽"
    assert decimal_ru(0.9216) == "0,92"
    assert percent(15 / 16) == "94%"


def test_config_values_match_concept():
    cfg = get_contact_config()
    assert cfg.salary.min_from == 30_000 and cfg.salary.step == 1000
    assert (cfg.salary.soft_max_ratio, cfg.salary.hard_max_ratio) == (1.6, 2.0)
    assert cfg.response.windows_days == [3, 5, 7] and cfg.response.default_days == 5
    assert (cfg.response.reminders.after_sent_hours, cfg.response.reminders.before_deadline_hours) == (48, 24)
    assert (cfg.counter_offer.max_conditions, cfg.counter_offer.salary_step) == (2, 5000)
    assert cfg.counter_offer.salary_max_ratio_to == 1.3 and cfg.counter_offer.comment_max_len == 300
    assert (cfg.employer.counter_response_days, cfg.employer.first_contact_business_days) == (3, 3)
    q = cfg.quotas
    assert (q.active_per_need, q.new_per_owner, q.window_days, q.batch_max) == (20, 60, 7, 10)
    assert (q.low_reliability.r_below, q.low_reliability.min_events, q.reduced_factor) == (0.6, 10, 0.5)
    assert (
        cfg.candidate_limits.weekly_options == [3, 5, 10, None] and cfg.candidate_limits.default_weekly == 5
    )
    assert cfg.pair.pause_days == {"declined": 30, "counter_declined": 30, "expired": 14}
    assert cfg.pair.salary_exception_factor == 1.15
    assert cfg.matching.weak_score_below == 40
    assert (cfg.minors.hidden_below_age, cfg.minors.internship_only_below_age) == (16, 18)
    assert set(cfg.reasons.decline) == {
        "salary",
        "stack",
        "format",
        "company",
        "not_looking",
        "other_process",
        "other",
    }
    assert cfg.reasons.decline["other_process"] == "Уже в другом процессе"
