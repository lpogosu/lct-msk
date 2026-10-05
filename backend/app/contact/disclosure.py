"""ContactDisclosure — отдельное согласие кандидата на передачу контактов конкретной компании (152-ФЗ).

CandidateDisclosed отдаётся работодателю только при активной записи (концепция §4.5); отзыв согласия
снова скрывает контакты.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Literal

from .clock import require_aware
from .settings import ContactConfig, get_contact_config

Basis = Literal["invitation", "application"]


@dataclass(frozen=True)
class ContactDisclosure:
    id: str
    candidate_id: str
    company_id: str
    basis: Basis
    basis_id: str
    fields: tuple[str, ...]
    consent_text_ver: str
    granted_at: datetime
    revoked_at: datetime | None = None

    def is_active(self, at: datetime | None = None) -> bool:
        if self.revoked_at is None:
            return True
        return at is not None and require_aware(at) < self.revoked_at

    def allows(self, field_name: str) -> bool:
        return field_name in self.fields

    def revoke(self, at: datetime) -> ContactDisclosure:
        if self.revoked_at is not None:
            return self
        return replace(self, revoked_at=require_aware(at))


def disclosable_fields(cfg: ContactConfig | None = None) -> frozenset[str]:
    cfg = cfg or get_contact_config()
    return frozenset(k for k, r in cfg.visibility.fields.items() if r.disclosed == "if_allowed")


def chosen_fields(fields: Iterable[str] | str | None, cfg: ContactConfig | None = None) -> tuple[str, ...]:
    """Что кандидат разрешил раскрыть: только поля, раскрываемые по согласию; None → значения по умолчанию.

    Строка — это одно поле, а не набор букв (иначе «email» превратился бы в пустое согласие).
    """
    cfg = cfg or get_contact_config()
    allowed = disclosable_fields(cfg)
    if isinstance(fields, str):
        fields = [fields]
    chosen = cfg.disclosure.default_fields if fields is None else list(fields)
    return tuple(dict.fromkeys(f for f in chosen if f in allowed))


def disclosure_fields_problem(
    fields: Iterable[str] | str | None, cfg: ContactConfig | None = None
) -> str | None:
    """Причина отказа, если в согласии нет ни одного канала связи; None — всё в порядке."""
    cfg = cfg or get_contact_config()
    if set(chosen_fields(fields, cfg)) & set(cfg.disclosure.contact_fields):
        return None
    options = ", ".join(cfg.disclosure.contact_fields.values())
    return f"Оставьте хотя бы один способ связи ({options}) — иначе компания не сможет с вами связаться"


def grant_disclosure(
    *,
    candidate_id: str,
    company_id: str,
    basis: Basis,
    basis_id: str,
    at: datetime,
    fields: Iterable[str] | str | None = None,
    cfg: ContactConfig | None = None,
) -> ContactDisclosure:
    """Создаёт запись согласия. fields — что кандидат разрешил (К13); по умолчанию — из config.

    Поля, которые матрица видимости не раскрывает по согласию (возраст, пол…), отбрасываются молча:
    кандидат не может «разрешить» то, что не показывается никогда. Согласие без единого канала связи
    не создаётся (disclosure_fields_problem) — вызывающий код показывает причину кандидату.
    """
    cfg = cfg or get_contact_config()
    problem = disclosure_fields_problem(fields, cfg)
    if problem:
        raise ValueError(problem)
    clean = chosen_fields(fields, cfg)
    return ContactDisclosure(
        id=f"{basis}:{basis_id}",
        candidate_id=candidate_id,
        company_id=company_id,
        basis=basis,
        basis_id=basis_id,
        fields=clean,
        consent_text_ver=cfg.disclosure.consent_text_version,
        granted_at=require_aware(at),
    )


def active_disclosure_for(
    disclosures: Iterable[ContactDisclosure], candidate_id: str, company_id: str, at: datetime
) -> ContactDisclosure | None:
    """Активное согласие для пары (кандидат, компания) — основание отдать CandidateDisclosed."""
    for d in disclosures:
        if d.candidate_id == candidate_id and d.company_id == company_id and d.is_active(at):
            return d
    return None
