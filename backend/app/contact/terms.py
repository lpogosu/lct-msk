"""Условия приглашения: неизменяемые снимки OfferTerms с sha256 и встречное условие «Да, если…».

Концепция §4.3 и §8.1: условия — маленький контракт. Каждая версия (v1 от работодателя, v2 после
принятого встречного) — отдельный снимок; через 10 дней кандидат сверяет «условия совпали?».
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, fields
from datetime import date, datetime
from fractions import Fraction
from typing import Any

from .rules import Check
from .settings import ContactConfig, get_contact_config
from .texts import decimal_ru, relative, rub, salary_range

_UNHASHED = {"sha256", "created_at"}


@dataclass(frozen=True)
class OfferTerms:
    invitation_id: str
    ver: int
    author: str  # employer | candidate
    title: str
    description: str
    salary_from: int
    salary_to: int
    salary_type: str
    contact_method: str
    contact_value: str
    created_at: datetime
    format: str | None = None
    start_date: date | None = None
    vacancy_id: str | None = None
    conditions: tuple[tuple[str, str], ...] = ()
    sha256: str = ""

    def __post_init__(self) -> None:
        if not self.sha256:
            object.__setattr__(self, "sha256", self.compute_hash())

    def content(self) -> dict[str, Any]:
        """Содержимое снимка без служебных полей — то, что защищает хеш."""
        out: dict[str, Any] = {}
        for f in fields(self):
            if f.name in _UNHASHED:
                continue
            value = getattr(self, f.name)
            if isinstance(value, date):
                value = value.isoformat()
            elif isinstance(value, tuple):
                value = [list(v) if isinstance(v, tuple) else v for v in value]
            out[f.name] = value
        return out

    def compute_hash(self) -> str:
        blob = json.dumps(self.content(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def verify(self) -> bool:
        """Снимок не менялся после создания."""
        return self.sha256 == self.compute_hash()


@dataclass(frozen=True)
class CounterOffer:
    """«Да, если…»: до 2 условий из списка и комментарий (концепция §4.3)."""

    salary_from: int | None = None
    format: str | None = None
    start_date: date | None = None
    comment: str = ""
    auto_disclose: bool | None = None  # None → значение по умолчанию из config (галочка стоит)
    disclosure_fields: tuple[str, ...] | None = None

    def conditions(self) -> dict[str, Any]:
        raw = {"salary_from": self.salary_from, "format": self.format, "start_date": self.start_date}
        return {k: v for k, v in raw.items() if v is not None}


def counter_salary_cap(salary_to: int, cfg: ContactConfig | None = None) -> int:
    """Максимум X во встречном: 1.3·to, округлённый вниз до шага 5000."""
    cfg = cfg or get_contact_config()
    c = cfg.counter_offer
    cap = Fraction(str(c.salary_max_ratio_to)) * salary_to
    return int(cap // c.salary_step) * c.salary_step


def validate_counter(
    counter: CounterOffer, terms: OfferTerms, today: date, cfg: ContactConfig | None = None
) -> Check:
    cfg = cfg or get_contact_config()
    c = cfg.counter_offer
    check = Check()
    conditions = counter.conditions()
    unknown = set(conditions) - set(c.conditions)
    if unknown:
        check.error("counter_unknown_condition", "Такое условие выбрать нельзя")
    if not conditions:
        labels = ", ".join(v.lower() for v in c.conditions.values())
        check.error("counter_empty", f"Выберите хотя бы одно условие: {labels}")
    elif len(conditions) > c.max_conditions:
        check.error("counter_too_many", f"Не больше {c.max_conditions} условий во встречном предложении")
    if len(counter.comment or "") > c.comment_max_len:
        check.error("counter_comment_too_long", f"Комментарий — не длиннее {c.comment_max_len} символов")

    x = counter.salary_from
    if x is not None:
        cap = counter_salary_cap(terms.salary_to, cfg)
        if x < terms.salary_from:
            check.error(
                "counter_salary_below_range",
                f"Зарплата во встречном условии — не ниже {rub(terms.salary_from)}: "
                "меньшая сумма уже входит в вилку, просто ответьте «Да»",
            )
        elif Fraction(x) > Fraction(str(c.salary_max_ratio_to)) * terms.salary_to:
            share = Fraction(str(c.salary_max_ratio_to)) * 100
            check.error(
                "counter_salary_above_cap",
                f"Зарплата во встречном условии — не выше {rub(cap)} "
                f"({decimal_ru(float(share), 0)}% верхней границы вилки)",
            )
        if x % c.salary_step:
            check.error("counter_salary_step", f"Сумма указывается с шагом {rub(c.salary_step)}")
        if check.ok and x <= terms.salary_to:
            check.note("within_range", "в пределах вашей вилки")
    if counter.format is not None:
        if counter.format not in cfg.invitation.formats:
            check.error("counter_format", "Выберите формат работы из списка")
        elif counter.format == terms.format:
            check.error("counter_format_same", "Этот формат уже указан в приглашении")
    if counter.start_date is not None and counter.start_date < today:
        check.error("counter_start_date_past", "Дата выхода не может быть в прошлом")
    return check


def counter_summary(
    counter: CounterOffer, terms: OfferTerms, now: datetime, cfg: ContactConfig | None = None
) -> str:
    """Карточка разницы для работодателя: «от 270 000 ₽ (ваша вилка 220 000–280 000 ₽) · удалённо · …»."""
    cfg = cfg or get_contact_config()
    parts: list[str] = []
    if counter.salary_from is not None:
        parts.append(
            f"от {rub(counter.salary_from)} (ваша вилка {salary_range(terms.salary_from, terms.salary_to)})"
        )
    if counter.format is not None:
        parts.append(cfg.invitation.formats.get(counter.format, counter.format).lower())
    if counter.start_date is not None:
        start = datetime.combine(counter.start_date, now.timetz())
        parts.append(f"выход {relative(start, now)}" if start > now else "выход сразу")
    if counter.salary_from is not None and counter.salary_from <= terms.salary_to:
        parts.append("в пределах вашей вилки")
    return " · ".join(parts)


def terms_after_counter(terms: OfferTerms, counter: CounterOffer, at: datetime) -> OfferTerms:
    """OfferTerms v2 после согласия работодателя: from' = max(from, X); to' = max(to, from')."""
    new_from = max(terms.salary_from, counter.salary_from or 0)
    new_to = max(terms.salary_to, new_from)
    conditions = tuple(
        (k, v.isoformat() if isinstance(v, date) else str(v)) for k, v in sorted(counter.conditions().items())
    )
    return OfferTerms(
        invitation_id=terms.invitation_id,
        ver=terms.ver + 1,
        author="candidate",
        title=terms.title,
        description=terms.description,
        salary_from=new_from,
        salary_to=new_to,
        salary_type=terms.salary_type,
        contact_method=terms.contact_method,
        contact_value=terms.contact_value,
        created_at=at,
        format=counter.format if counter.format is not None else terms.format,
        start_date=counter.start_date if counter.start_date is not None else terms.start_date,
        vacancy_id=terms.vacancy_id,
        conditions=conditions,
    )
