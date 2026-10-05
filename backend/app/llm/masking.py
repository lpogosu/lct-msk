"""Маскирование ПДн перед вызовом LLM (концепция §3.1.1: «персональные данные маскируются до вызова»).

Маска сохраняет длину текста: каждый символ ПДн (кроме пробелов и переводов строк) заменяется на «*».
Поэтому смещения цитат, которые вернула LLM по замаскированному тексту, совпадают со смещениями
в исходном тексте — подсветка цитаты в резюме работает без пересчёта.

Регулярные выражения контактов здесь — единые для платформы: ими же пользуется разбор резюме.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

MASK_CHAR = "*"

EMAIL_RE = re.compile(r"(?<![\w.%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
# Российские номера: +7 (912) 345-67-89, 8 912 345 67 89, +79123456789, 8-912-345-67-89
PHONE_RE = re.compile(
    r"(?<![\d+])(?:\+7|8|7)[\s\-]?\(?\d{3}\)?[\s\-]?\d{3}[\s\-]?\d{2}[\s\-]?\d{2}(?!\d)"
)
TELEGRAM_LINK_RE = re.compile(r"(?:https?://)?(?:t|telegram)\.me/([A-Za-z0-9_]{5,32})\b", re.IGNORECASE)
TELEGRAM_HANDLE_RE = re.compile(r"(?<![\w.@])@([A-Za-z][A-Za-z0-9_]{4,31})\b")
URL_RE = re.compile(
    r"(?:https?://|www\.)\S+|\b(?:github|gitlab|linkedin|vk|habr|hh)\.(?:com|ru)/\S+", re.IGNORECASE
)
# Строки с датой рождения и возрастом — квазиидентификаторы (в Score не участвуют, концепция §4.5)
BIRTH_LINE_RE = re.compile(r"^.*(?:рожд|родил|возраст|дата рожд).*$", re.IGNORECASE | re.MULTILINE)

_PATTERNS: tuple[re.Pattern[str], ...] = (
    URL_RE,
    EMAIL_RE,
    PHONE_RE,
    TELEGRAM_LINK_RE,
    TELEGRAM_HANDLE_RE,
    BIRTH_LINE_RE,
)


@dataclass(frozen=True)
class MaskedText:
    text: str  # той же длины, что исходный
    spans: tuple[tuple[int, int], ...]  # замаскированные участки исходного текста

    @property
    def count(self) -> int:
        return len(self.spans)


def _merge(spans: Iterable[tuple[int, int]]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for s, e in sorted(spans):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out


def pii_spans(text: str, extra: Iterable[tuple[int, int]] = ()) -> list[tuple[int, int]]:
    """Участки ПДн: контакты, ссылки на профили, дата рождения/возраст + переданные явно (например, ФИО)."""
    spans = [(m.start(), m.end()) for p in _PATTERNS for m in p.finditer(text)]
    spans.extend((max(0, s), min(len(text), e)) for s, e in extra if e > s)
    return _merge(spans)


def mask_pii(text: str, extra_spans: Iterable[tuple[int, int]] = ()) -> MaskedText:
    """Замаскировать ПДн с сохранением длины. extra_spans — участки, найденные правилами (ФИО)."""
    spans = pii_spans(text, extra_spans)
    chars = list(text)
    for s, e in spans:
        for i in range(s, e):
            if not chars[i].isspace():
                chars[i] = MASK_CHAR
    return MaskedText("".join(chars), tuple(spans))
