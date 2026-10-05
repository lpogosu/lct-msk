"""Русские подписи для DTO тестирования: специализации, грейды, статусы, доверие. Без «θ» (DECISIONS п. 10)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.clock import clock, relative_ru
from app.core.configs import load_config
from app.testing.detectors import load_detector_config
from app.testing.irt import BELOW_INTERN, load_scale

QUAL_STATUS_RU = {
    "NONE": "опрос не пройден",
    "SURVEY_DONE": "опрос пройден, тест не начат",
    "TESTING": "идёт тест",
    "CONFIRMED": "грейд подтверждён",
    "NOT_CONFIRMED": "грейд не подтверждён",
    "UPGRADE_TESTING": "идёт тест на повышение",
}

DECISION_RU = {
    "PASSED": "подтверждён",
    "FAILED": "не подтверждён",
    "BORDERLINE_PASS": "подтверждён на границе",
    "BORDERLINE_FAIL": "не подтверждён (на границе)",
}

KIND_RU = {
    "initial": "первичный тест",
    "retake": "повторная попытка",
    "upgrade": "тест на повышение",
    "ext_up": "добор вверх",
    "ext_down": "добор вниз",
}

CHANGE_REASON_RU = {
    "initial": "грейд подтверждён тестом",
    "onboarding_extension": "грейд уточнён добором",
    "upgrade": "повышение по тесту",
    "voluntary_downgrade": "добровольное понижение",
    "invalidated": "результат аннулирован методистом",
}


def taxonomy() -> dict[str, Any]:
    return load_config("taxonomy")


def spec_info(code: str) -> dict[str, Any] | None:
    return next((s for s in taxonomy()["specializations"] if s["code"] == code), None)


def spec_name(code: str) -> str:
    s = spec_info(code)
    return str(s["name"]) if s else code


def spec_testable(code: str) -> bool:
    s = spec_info(code)
    return bool(s and s.get("status", "active") == "active" and s.get("blueprint"))


def grade_name(code: str | None) -> str:
    return load_scale().grade_name(code)


def grade_anchor(code: str) -> str | None:
    g = next((g for g in taxonomy()["grades"] if g["code"] == code), None)
    return g.get("anchor") if g else None


def grade_options() -> list[str]:
    return list(load_scale().grades)


def trust_label(trust: float | None, under_review: bool = False) -> str | None:
    if under_review:
        return "на проверке"
    if trust is None:
        return None
    return "высокое" if trust >= load_detector_config().trust_clean - 1e-9 else "сниженное"


def rel(dt: datetime | None) -> str | None:
    return relative_ru(dt, clock.now()) if dt is not None else None


def score_of(theta: float | None) -> int | None:
    if theta is None:
        return None
    return round(load_scale().score(theta))


def band_name(code: str) -> str:
    return grade_name(None if code == BELOW_INTERN else code)
