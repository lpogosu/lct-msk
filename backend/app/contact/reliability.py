"""Надёжность работодателя R (spec-contact §3.7, концепция §4.3).

    R = (prior_strength·prior_mean + good) / (prior_strength + good + bad) = (5·0.8 + good) / (5 + good + bad)

Новая компания стартует с 0.8. Кандидат видит R с разложением до решения; при < 5 событиях — «мало данных».
Если за 90 дней ≥ 2 разных кандидата ответили «зарплата ниже» / «вакансии нет» — бейдж
«есть расхождения условий» и квота ×0.5 (как и при R < 0.6 при ≥ 10 событиях).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .clock import require_aware
from .settings import ContactConfig, get_contact_config
from .texts import decimal_ru, percent


@dataclass(frozen=True)
class ReliabilityEvent:
    company_id: str
    kind: str  # код из reliability.good / reliability.bad
    at: datetime
    candidate_id: str | None = None
    invitation_id: str | None = None
    application_id: str | None = None


@dataclass(frozen=True)
class Reliability:
    r: float
    good: int
    bad: int
    enough_data: bool
    display: str  # «Надёжность 0,92 · отвечает на отклики в срок 94% · …» или «Надёжность: мало данных»
    breakdown: tuple[str, ...]
    discrepancy_badge: str | None
    quota_factor: float
    counts: dict[str, int]

    @property
    def events(self) -> int:
        return self.good + self.bad


def event_polarity(kind: str, cfg: ContactConfig | None = None) -> str:
    cfg = cfg or get_contact_config()
    if kind in cfg.reliability.good:
        return "good"
    if kind in cfg.reliability.bad:
        return "bad"
    raise ValueError(f"Неизвестное событие надёжности: {kind}")


def reliability_value(good: int, bad: int, cfg: ContactConfig | None = None) -> float:
    cfg = cfg or get_contact_config()
    rc = cfg.reliability
    return (rc.prior_strength * rc.prior_mean + good) / (rc.prior_strength + good + bad)


def has_discrepancy(
    events: Iterable[ReliabilityEvent], now: datetime, cfg: ContactConfig | None = None
) -> bool:
    """≥ 2 РАЗНЫХ кандидата за 90 дней: один недовольный кандидат бейдж не включает."""
    cfg = cfg or get_contact_config()
    d = cfg.reliability.discrepancy
    since = require_aware(now) - timedelta(days=d.window_days)
    who = {
        e.candidate_id or f"anon:{e.invitation_id or e.application_id}"
        for e in events
        if e.kind in d.kinds and since <= e.at <= now
    }
    return len(who) >= d.min_distinct_candidates


def _ratio_part(label: str, ok: int, total: int, as_percent: bool) -> str | None:
    if total <= 0:
        return None
    return f"{label} {percent(ok / total)}" if as_percent else f"{label} {ok}/{total}"


def compute_reliability(
    events: Iterable[ReliabilityEvent], now: datetime, cfg: ContactConfig | None = None
) -> Reliability:
    cfg = cfg or get_contact_config()
    rc = cfg.reliability
    qc = cfg.quotas
    events = [e for e in events if e.at <= now]
    counts: dict[str, int] = {k: 0 for k in (*rc.good, *rc.bad)}
    for e in events:
        event_polarity(e.kind, cfg)  # неизвестный код — ошибка, а не тихий ноль
        counts[e.kind] += 1
    good = sum(counts[k] for k in rc.good)
    bad = sum(counts[k] for k in rc.bad)
    r = reliability_value(good, bad, cfg)
    enough = good + bad >= rc.min_events_for_value

    def c(*kinds: str) -> int:
        return sum(counts.get(k, 0) for k in kinds)

    parts = [
        _ratio_part(
            "отвечает на отклики в срок",
            c("application_answered"),
            c("application_answered", "application_no_response"),
            as_percent=True,
        ),
        _ratio_part(
            "связывается после принятия",
            c("contacted_in_time"),
            # закрыть принятое, так и не связавшись, — тоже «не связались» (invitation._e_record_outcome)
            c("contacted_in_time", "contact_overdue", "contact_disputed", "withdrawn_after_accept"),
            as_percent=False,
        ),
        _ratio_part(
            "условия совпали",
            c("terms_matched"),
            c("terms_matched", "terms_lower_salary", "terms_no_vacancy", "terms_promise_broken"),
            as_percent=False,
        ),
    ]
    breakdown = tuple(p for p in parts if p)
    head = f"Надёжность {decimal_ru(r)}" if enough else f"Надёжность: {rc.low_data_label}"
    display = " · ".join((head, *breakdown)) if enough else head

    badge = rc.discrepancy.badge if has_discrepancy(events, now, cfg) else None
    low = r < qc.low_reliability.r_below and good + bad >= qc.low_reliability.min_events
    factor = qc.reduced_factor if (low or badge) else 1.0
    return Reliability(
        r=r,
        good=good,
        bad=bad,
        enough_data=enough,
        display=display,
        breakdown=breakdown,
        discrepancy_badge=badge,
        quota_factor=factor,
        counts=counts,
    )
