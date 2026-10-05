"""Контакты из резюме: email, телефон, Telegram. Регулярные выражения — общие с маскированием ПДн
(app/llm/masking.py), чтобы «что считаем контактом» не расходилось между разбором и защитой."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.llm.masking import EMAIL_RE, PHONE_RE, TELEGRAM_HANDLE_RE, TELEGRAM_LINK_RE


@dataclass(frozen=True)
class Found:
    value: str  # нормализованное значение
    start: int
    end: int


def find_email(text: str) -> Found | None:
    m = EMAIL_RE.search(text)
    return Found(m.group(0).lower(), m.start(), m.end()) if m else None


def normalize_phone(raw: str) -> str:
    """+7XXXXXXXXXX: 8 и 7 в начале — один и тот же российский код."""
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 11 and digits[0] in "78":
        digits = "7" + digits[1:]
    return "+" + digits


def find_phone(text: str) -> Found | None:
    m = PHONE_RE.search(text)
    return Found(normalize_phone(m.group(0)), m.start(), m.end()) if m else None


_TG_CONTEXT = re.compile(r"(?:telegram|телеграм|tg)\b", re.IGNORECASE)


def find_telegram(text: str) -> Found | None:
    """t.me/handle — однозначно; @handle — если это не часть email (обеспечено регулярным выражением)."""
    m = TELEGRAM_LINK_RE.search(text)
    if m:
        return Found("@" + m.group(1), m.start(), m.end())
    best: Found | None = None
    for m in TELEGRAM_HANDLE_RE.finditer(text):
        line_start = text.rfind("\n", 0, m.start()) + 1
        context = text[line_start : m.start()]
        found = Found("@" + m.group(1), m.start(), m.end())
        if _TG_CONTEXT.search(context):
            return found  # «Telegram: @handle» — самый надёжный случай
        best = best or found
    return best
