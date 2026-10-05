"""Надёжность работодателя R = (5·0.8 + good) / (5 + good + bad) — контрольные примеры (spec-contact §3.7)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contact.reliability import ReliabilityEvent, compute_reliability, event_polarity, reliability_value

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def events(kind: str, n: int, *, days_ago: int = 1, start: int = 0) -> list[ReliabilityEvent]:
    return [
        ReliabilityEvent("co-1", kind, NOW - timedelta(days=days_ago), candidate_id=f"c{start + i}")
        for i in range(n)
    ]


@pytest.mark.parametrize(
    "good,bad,expected",
    [
        (0, 0, 0.8),  # новая компания стартует с 0.8
        (19, 1, 0.92),  # 23/25
        (13, 1, 17 / 19),
        (0, 5, 0.4),  # 4/10
        (0, 10, 4 / 15),
        (95, 0, 0.99),  # 99/100
    ],
)
def test_reliability_formula_examples(good, bad, expected):
    assert reliability_value(good, bad) == pytest.approx(expected)


def test_new_company_shows_low_data():
    rel = compute_reliability([], NOW)
    assert rel.r == pytest.approx(0.8)
    assert rel.enough_data is False
    assert rel.display == "Надёжность: мало данных"
    assert rel.quota_factor == 1.0 and rel.discrepancy_badge is None


def test_four_events_is_still_low_data_five_is_enough():
    assert compute_reliability(events("terms_matched", 4), NOW).display == "Надёжность: мало данных"
    rel = compute_reliability(events("terms_matched", 5), NOW)
    assert rel.enough_data and rel.display.startswith("Надёжность 0,90")  # (4+5)/(10)


def test_breakdown_matches_concept_example():
    evs = (
        events("application_answered", 15)
        + events("application_no_response", 1)
        + events("contacted_in_time", 17)
        + events("contact_overdue", 1)
        + events("terms_matched", 11)
        + events("terms_lower_salary", 1)
    )
    rel = compute_reliability(evs, NOW)
    assert (rel.good, rel.bad) == (43, 3)
    assert rel.r == pytest.approx(47 / 51)
    assert rel.display == (
        "Надёжность 0,92 · отвечает на отклики в срок 94% · связывается после принятия 17/18 · условия совпали 11/12"
    )
    assert rel.discrepancy_badge is None  # один кандидат с «зарплата ниже» — ещё не расхождение


def test_breakdown_omits_parts_without_data():
    rel = compute_reliability(events("contacted_in_time", 6), NOW)
    assert rel.breakdown == ("связывается после принятия 6/6",)


def test_discrepancy_badge_needs_two_distinct_candidates_within_90_days():
    same = [
        ReliabilityEvent("co-1", "terms_lower_salary", NOW - timedelta(days=5), candidate_id="a"),
        ReliabilityEvent("co-1", "terms_no_vacancy", NOW - timedelta(days=6), candidate_id="a"),
    ]
    assert compute_reliability(same, NOW).discrepancy_badge is None
    old_and_new = [
        ReliabilityEvent("co-1", "terms_lower_salary", NOW - timedelta(days=100), candidate_id="a"),
        ReliabilityEvent("co-1", "terms_no_vacancy", NOW - timedelta(days=6), candidate_id="b"),
    ]
    assert compute_reliability(old_and_new, NOW).discrepancy_badge is None
    two = [
        ReliabilityEvent("co-1", "terms_lower_salary", NOW - timedelta(days=80), candidate_id="a"),
        ReliabilityEvent("co-1", "terms_no_vacancy", NOW - timedelta(days=6), candidate_id="b"),
    ]
    rel = compute_reliability(two, NOW)
    assert rel.discrepancy_badge == "есть расхождения условий"
    assert rel.quota_factor == 0.5


def test_low_r_with_enough_events_halves_quota():
    assert compute_reliability(events("contact_overdue", 10), NOW).quota_factor == 0.5
    assert compute_reliability(events("contact_overdue", 9), NOW).quota_factor == 1.0
    # R ≥ 0.6 — квота полная даже при многих событиях
    mixed = events("contact_overdue", 5) + events("contacted_in_time", 20, start=100)
    assert compute_reliability(mixed, NOW).quota_factor == 1.0


def test_future_events_ignored_and_unknown_kind_rejected():
    future = [ReliabilityEvent("co-1", "contact_overdue", NOW + timedelta(days=1))]
    assert compute_reliability(future, NOW).bad == 0
    with pytest.raises(ValueError):
        compute_reliability([ReliabilityEvent("co-1", "liked", NOW)], NOW)
    assert event_polarity("counter_unanswered") == "bad"
    assert event_polarity("outcome_reported") == "good"
