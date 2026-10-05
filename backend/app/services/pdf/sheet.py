"""Содержимое стандартизированного PDF-профиля (концепция §3.1.2) и код сверки.

ProfileSheet — всё, что попадает на лист. Код сверки = HMAC-SHA256(секрет платформы, содержимое листа +
дата выдачи), первые 32 hex-символа (128 бит): изменённый документ не найдёт свою запись на /v/{код},
а повторная выдача того же содержимого в тот же день даёт тот же код.

Слепая версия (кандидат может отправить её сам): псевдоним вместо имени, без контактов, города, компаний и
вуза, ФСП — обобщённо, тест — интервалом на линейке без точного балла. Полная (кандидату и работодателю
после раскрытия): ФИО, контакты, точный балл, опыт, выписка ФСП с номером ЕКП.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any, Literal

Variant = Literal["blind", "full"]


@dataclass(frozen=True)
class RulerCut:
    code: str
    name: str
    score: int


@dataclass(frozen=True)
class RulerData:
    lo: int  # левый край линейки (Стажёр, 300)
    hi: int  # правый край (Senior, 600)
    cuts: tuple[RulerCut, ...]
    interval: tuple[int, int] | None  # 80%-интервал балла
    score: int | None  # точный балл — только в полной версии


@dataclass(frozen=True)
class ModuleTag:
    name: str
    symbol: str  # ✓✓ | ✓ | ~
    state: str  # strong | confirmed | insufficient


@dataclass(frozen=True)
class TaskLine:
    title: str
    result: str | None
    when: str  # дата выполнения


@dataclass(frozen=True)
class FspBlock:
    linked: bool
    lines: tuple[str, ...] = ()  # обобщённо (слепая) или кратко (полная)
    badges: tuple[str, ...] = ()
    extract: str | None = None  # выписка из протокола с ЕКП — только полная версия
    source_label: str | None = None  # «Демо-реестр ФСП»
    registry_on: str | None = None  # дата данных реестра, ДД.ММ.ГГГГ
    neutral: str | None = None  # нейтральная строка без ФСП


@dataclass(frozen=True)
class ProfileSheet:
    variant: Variant
    issued_on: date
    title: str  # ФИО (полная) или «Кандидат П-4821» (слепая)
    subtitle: str | None  # заявленная роль/грейд
    contacts: tuple[str, ...]  # только полная
    location: str | None  # полная — город, слепая — субъект РФ
    category_name: str | None
    category_note: str | None  # «подтверждено тестом 14.09.2026 · уверенность 94%»
    borderline: bool
    ruler: RulerData | None
    modules: tuple[ModuleTag, ...]
    declared_skills: tuple[str, ...]
    roles: tuple[str, ...]
    soft_skills: tuple[str, ...]
    experience_label: str | None
    experience: tuple[str, ...]  # только полная
    education: str | None  # только полная
    fsp: FspBlock
    tasks: tuple[TaskLine, ...]
    freshness: str | None
    session_ref: str | None
    claims: dict[str, Any] = field(default_factory=dict)  # что покажет страница сверки (без ПДн)


def canonical(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def sheet_hash(sheet: ProfileSheet, secret: str, subject: str = "") -> str:
    """Код сверки: HMAC содержимого листа, даты выдачи и владельца (subject — id кандидата: одинаковые листы
    двух кандидатов не делят одну запись сверки). Секрет не даёт подобрать код по известному содержимому."""
    body = asdict(sheet)
    msg = canonical({"v": 1, "sheet": body, "subject": subject})
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()[:32]


def verify_url(public_base_url: str, code: str) -> str:
    return f"{public_base_url.rstrip('/')}/v/{code}"
