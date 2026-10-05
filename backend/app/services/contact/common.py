"""Общее для слоя контакта: конфиг, часы, перевод доменных отказов в ошибки API, лимиты запросов."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any

from app.contact.common import ContactError
from app.contact.rules import Check
from app.contact.settings import ContactConfig, get_contact_config
from app.contact.texts import relative
from app.core.clock import clock
from app.core.errors import ApiError, bad_request, conflict
from app.core.rate_limit import RateLimiter
from app.core.settings import RateRule


def cfg() -> ContactConfig:
    return get_contact_config()


def now() -> datetime:
    """DECISIONS 19: сроки контакта — по общим часам платформы (DEMO_MODE «+1 день» их двигает)."""
    return clock.now()


def rel(target: datetime | None, at: datetime | None = None) -> str | None:
    """Относительная дата для UI (DECISIONS 9) или None."""
    if target is None:
        return None
    return relative(target, at or now())


def issues(check: Check | None) -> dict[str, Any]:
    if check is None:
        return {}
    return {
        "errors": [{"code": i.code, "message": i.message} for i in check.errors],
        "warnings": [{"code": i.code, "message": i.message} for i in check.warnings],
        "notes": [{"code": i.code, "message": i.message} for i in check.notes],
    }


# Отказы по содержимому формы — 400; остальное (квоты, паузы, сроки, статус) — 409.
VALIDATION_CODES = frozenset(
    {
        "title_required",
        "title_too_long",
        "description_too_short",
        "description_too_long",
        "contact_method",
        "contact_value",
        "response_days",
        "format",
        "salary_type",
        "salary_from_min",
        "salary_order",
        "salary_step",
        "salary_too_wide",
        "decline_reason_valid",
        "withdraw_reason_valid",
        "counter_decline_reason_valid",
        "counter_valid",
        "outcome_valid",
        "consent_given",
        "reject_reason_valid",
        "consent_required",
        "consent_fields",
        "unknown_action",
    }
)


def contact_error(err: ContactError) -> ApiError:
    """ContactError доменного модуля → ApiError с тем же кодом и русским текстом."""
    details: dict[str, Any] = {"reason": err.code}
    if err.check is not None:
        details.update(issues(err.check))
        codes = [i.code for i in err.check.errors]
    else:
        codes = [err.code]
    if codes and all(c in VALIDATION_CODES for c in codes):
        return bad_request("contact_invalid", err.message, details)
    return conflict("contact_rejected", err.message, details)


def check_rate(limiter: RateLimiter, scope: str, key: Any, per_minute: int) -> None:
    """Токен-корзина «N в минуту» (429 с Retry-After при превышении)."""
    limiter.check(scope, str(key), RateRule(per_minute, 60))


def uniq(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(values))
