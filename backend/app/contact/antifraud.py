"""Антифейк в MVP: стоп-слова и эвристики → плашка «Требует осторожности» у кандидата (концепция §8.1).

Не блокирует отправку: честный работодатель может случайно написать «паспорт» в описании офиса.
Решение остаётся за кандидатом, но с понятной причиной. ML-классификатор по жалобам — вне MVP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .settings import ContactConfig, get_contact_config


@dataclass(frozen=True)
class FraudFinding:
    code: str
    reason: str
    match: str


@dataclass(frozen=True)
class FraudCheck:
    findings: tuple[FraudFinding, ...]
    label: str

    @property
    def caution(self) -> bool:
        return bool(self.findings)

    @property
    def codes(self) -> list[str]:
        return [f.code for f in self.findings]

    @property
    def reasons(self) -> list[str]:
        return [f.reason for f in self.findings]

    @property
    def badge(self) -> str | None:
        """«Требует осторожности» или None, если сигналов нет."""
        return self.label if self.findings else None


def normalize(text: str) -> str:
    return (text or "").lower().replace("ё", "е")


_Compiled = tuple[tuple[str, str, tuple[re.Pattern[str], ...]], ...]
# Ключ — id конфига (pydantic-модель с dict-полями не хешируется); сам конфиг держим, чтобы id не переиспользовался.
_CACHE: dict[int, tuple[ContactConfig, _Compiled]] = {}


def _compiled(cfg: ContactConfig) -> _Compiled:
    hit = _CACHE.get(id(cfg))
    if hit is not None and hit[0] is cfg:
        return hit[1]
    compiled = _compile(cfg)
    _CACHE[id(cfg)] = (cfg, compiled)
    return compiled


def _compile(cfg: ContactConfig) -> _Compiled:
    af = cfg.antifraud
    out = []
    for rule in af.rules:
        patterns = list(rule.patterns)
        if rule.code == "short_link" and af.short_link_domains:
            domains = "|".join(re.escape(d) for d in af.short_link_domains)
            # домен-сокращатель + путь: «bit.ly/abc», «https://clck.ru/3Xyz»
            patterns.append(rf"(?<![\w.-])(?:https?://)?(?:www\.)?(?:{domains})/\S+")
        out.append((rule.code, rule.reason, tuple(re.compile(p) for p in patterns)))
    return tuple(out)


def check_text(*parts: str, cfg: ContactConfig | None = None) -> FraudCheck:
    """Проверяет заголовок и описание приглашения. Поле «способ связи» сюда не передаём: контакт там легален."""
    cfg = cfg or get_contact_config()
    text = normalize("\n".join(p for p in parts if p))
    findings: list[FraudFinding] = []
    for code, reason, patterns in _compiled(cfg):
        for pattern in patterns:
            m = pattern.search(text)
            if m:
                findings.append(FraudFinding(code=code, reason=reason, match=m.group(0)))
                break
    return FraudCheck(findings=tuple(findings), label=cfg.antifraud.label)
