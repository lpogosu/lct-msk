"""Кто что видит: проекции профиля кандидата для работодателя (концепция §4.5).

CandidateBlind — до «да» (или отклика): псевдоним, категория, навыки с источниками, задачи, полоса теста,
обобщённое ФСП, регион, сигнал по зарплате. CandidateDisclosed — только при активной ContactDisclosure:
плюс точный балл, полная выписка ФСП, город и те ПДн, что разрешил кандидат.

Матрица — белый список из config/contact.yaml: поле, которого в ней нет, не попадает никуда.
Возраст и пол не показываются никогда. В слепой проекции ПДн-ключи вычищаются на любой глубине.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from datetime import datetime
from typing import Any, Literal

from .disclosure import ContactDisclosure
from .settings import ContactConfig, get_contact_config

View = Literal["blind", "disclosed"]


class DisclosureRequired(PermissionError):
    """Попытка отдать CandidateDisclosed без действующего согласия."""

    def __init__(self) -> None:
        super().__init__("Контакты кандидата скрыты: нет действующего согласия на раскрытие")


def pii_keys_in(obj: Any, cfg: ContactConfig | None = None) -> set[str]:
    """Все ПДн-ключи на любой глубине — для контрактного теста «0 полей ПДн до согласия»."""
    cfg = cfg or get_contact_config()
    pii = set(cfg.visibility.pii_fields)
    found: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for k, v in node.items():
                if k in pii:
                    found.add(k)
                walk(v)
        elif isinstance(node, list | tuple):
            for v in node:
                walk(v)

    walk(obj)
    return found


def _scrub(node: Any, pii: set[str]) -> Any:
    if isinstance(node, Mapping):
        return {k: _scrub(v, pii) for k, v in node.items() if k not in pii}
    if isinstance(node, list | tuple):
        return [_scrub(v, pii) for v in node]
    return copy.deepcopy(node)


def score_band(test: Mapping[str, Any], cfg: ContactConfig | None = None) -> dict[str, Any]:
    """Полоса «топ-10/25/50%» и интервал на линейке вместо точного балла."""
    cfg = cfg or get_contact_config()
    vis = cfg.visibility
    label = vis.test_band_default
    pct = test.get("percentile")
    if pct is not None:
        for band in sorted(vis.test_bands, key=lambda b: -b.min_percentile):
            if pct >= band.min_percentile:
                label = band.label
                break
    out: dict[str, Any] = {"band": label}
    for key in vis.test_public_keys:
        if key in test:
            out[key] = copy.deepcopy(test[key])
    return out


def fsp_public(items: Any, cfg: ContactConfig | None = None) -> list[dict[str, Any]]:
    """Обобщённое ФСП: дисциплина, уровень, год, диапазон места, роль — без ЕКП, команды и протокола
    (пара «ЕКП + место» однозначно выдаёт человека по публичным протоколам)."""
    cfg = cfg or get_contact_config()
    vis = cfg.visibility
    out = []
    for item in items or []:
        pub = {k: copy.deepcopy(item[k]) for k in vis.fsp_public_keys if k in item}
        place = item.get("place")
        band = item.get("place_band") or vis.fsp_place_default
        if isinstance(place, int):
            band = vis.fsp_place_default
            for pb in sorted(vis.fsp_place_bands, key=lambda b: b.max_place):
                if place <= pb.max_place:
                    band = pb.label
                    break
        pub["place_band"] = band
        out.append(pub)
    return out


def salary_signal(
    profile: Mapping[str, Any], offer_salary_to: int | None, cfg: ContactConfig | None = None
) -> dict[str, Any] | None:
    """Минимум кандидата: по умолчанию только значок «в вилке ✓ / выше вилки», число — если он выбрал «exact»."""
    cfg = cfg or get_contact_config()
    minimum = profile.get("salary_min")
    if minimum is None:
        return None
    out: dict[str, Any] = {}
    if offer_salary_to is not None:
        sig = cfg.visibility.salary_signal
        out["signal"] = sig.within if minimum <= offer_salary_to else sig.above
    if profile.get("salary_visibility") == "exact":
        out["value"] = minimum
    return out or None


def _project(
    profile: Mapping[str, Any],
    view: View,
    *,
    disclosure: ContactDisclosure | None,
    offer_salary_to: int | None,
    cfg: ContactConfig,
) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name, rule in cfg.visibility.fields.items():
        if name not in profile:
            continue
        r = rule.blind if view == "blind" else rule.disclosed
        value = profile[name]
        if value is None and r in ("test_band", "fsp_public"):
            # Нет теста (импорт без теста, DECISIONS 5) или ФСП — обобщать нечего, но и падать незачем.
            out[name] = None
            continue
        if r == "show":
            out[name] = copy.deepcopy(value)
        elif r == "if_allowed":
            if view == "disclosed" and disclosure is not None and disclosure.allows(name):
                out[name] = copy.deepcopy(value)
        elif r == "test_band":
            out[name] = score_band(value, cfg)
        elif r == "fsp_public":
            out[name] = fsp_public(value, cfg)
        elif r == "salary_signal":
            sig = salary_signal(profile, offer_salary_to, cfg)
            if sig is not None:
                out[name] = sig
        # hide / never — поле не попадает в проекцию
    return out


def blind_view(
    profile: Mapping[str, Any],
    *,
    pseudonym: str,
    offer_salary_to: int | None = None,
    cfg: ContactConfig | None = None,
) -> dict[str, Any]:
    """CandidateBlind: ни одного ПДн-ключа ни на какой глубине."""
    cfg = cfg or get_contact_config()
    body = _project(profile, "blind", disclosure=None, offer_salary_to=offer_salary_to, cfg=cfg)
    body = _scrub(body, set(cfg.visibility.pii_fields))
    return {"view": "blind", "pseudonym": pseudonym, **body}


def disclosure_valid_for(
    disclosure: ContactDisclosure | None, candidate_id: Any, company_id: str, now: datetime
) -> bool:
    return (
        disclosure is not None
        and disclosure.candidate_id == candidate_id
        and disclosure.company_id == company_id
        and disclosure.is_active(now)
    )


def disclosed_view(
    profile: Mapping[str, Any],
    *,
    pseudonym: str,
    company_id: str,
    disclosure: ContactDisclosure,
    now: datetime,
    offer_salary_to: int | None = None,
    cfg: ContactConfig | None = None,
) -> dict[str, Any]:
    """CandidateDisclosed: только при активном согласии этого кандидата на эту компанию."""
    cfg = cfg or get_contact_config()
    if not disclosure_valid_for(disclosure, profile.get("candidate_id"), company_id, now):
        raise DisclosureRequired()
    body = _project(profile, "disclosed", disclosure=disclosure, offer_salary_to=offer_salary_to, cfg=cfg)
    return {
        "view": "disclosed",
        "pseudonym": pseudonym,
        "disclosure": {"basis": disclosure.basis, "fields": list(disclosure.fields)},
        **body,
    }


def employer_view(
    profile: Mapping[str, Any],
    *,
    pseudonym: str,
    company_id: str,
    now: datetime,
    disclosure: ContactDisclosure | None = None,
    offer_salary_to: int | None = None,
    cfg: ContactConfig | None = None,
) -> dict[str, Any]:
    """То, что API отдаёт работодателю: раскрытая проекция при действующем согласии, иначе слепая."""
    if disclosure is not None and disclosure_valid_for(
        disclosure, profile.get("candidate_id"), company_id, now
    ):
        return disclosed_view(
            profile,
            pseudonym=pseudonym,
            company_id=company_id,
            disclosure=disclosure,
            now=now,
            offer_salary_to=offer_salary_to,
            cfg=cfg,
        )
    return blind_view(profile, pseudonym=pseudonym, offer_salary_to=offer_salary_to, cfg=cfg)
