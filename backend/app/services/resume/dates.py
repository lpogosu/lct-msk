"""Периоды работы и стаж в месяцах (концепция §3.1.1: «стаж в месяцах по интервалам дат»).

Форматы: «Март 2021 — настоящее время», «03.2021 – 10.2025», «09/2017 - 06/2019», «янв. 2020 — дек. 2022»,
«2016–2019», «с 2019 по 2021», «по н.в.». Стаж = объединение периодов: пересекающиеся месяцы
(совместительство) считаются один раз. Месяцы включительны: «март 2021 — март 2021» = 1 месяц.
Год без месяца: начало — январь, конец — декабрь (но не позже текущего месяца).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

_MONTHS: dict[str, int] = {}
for _num, _forms in enumerate(
    (
        ("январь", "января", "янв", "january", "jan"),
        ("февраль", "февраля", "фев", "февр", "february", "feb"),
        ("март", "марта", "мар", "march", "mar"),
        ("апрель", "апреля", "апр", "april", "apr"),
        ("май", "мая", "may"),
        ("июнь", "июня", "июн", "june", "jun"),
        ("июль", "июля", "июл", "july", "jul"),
        ("август", "августа", "авг", "august", "aug"),
        ("сентябрь", "сентября", "сен", "сент", "september", "sep", "sept"),
        ("октябрь", "октября", "окт", "october", "oct"),
        ("ноябрь", "ноября", "ноя", "нояб", "november", "nov"),
        ("декабрь", "декабря", "дек", "december", "dec"),
    ),
    start=1,
):
    for _f in _forms:
        _MONTHS[_f] = _num

_MONTH_ALT = "|".join(sorted(_MONTHS, key=len, reverse=True))
_YEAR = r"(?:19[5-9]\d|20[0-4]\d)"
_DATE = (
    rf"(?:(?P<{{p}}m1>0?[1-9]|1[0-2])\s*[./]\s*(?P<{{p}}y1>{_YEAR})"  # 03.2021, 3/2021
    rf"|(?P<{{p}}mon>{_MONTH_ALT})\.?\s+(?P<{{p}}y2>{_YEAR})"  # март 2021, янв. 2020
    rf"|(?P<{{p}}y3>{_YEAR}))"  # 2016
)
_PRESENT = (
    r"(?:(?:по\s+)?настоящее\s+время|(?:по\s+)?наст\.?\s*вр\.?|(?:по\s+)?н\.\s?в\.?|сейчас|по\s+сей\s+день"
    r"|present|now|current|today)"
)
_RANGE_RE = re.compile(
    rf"(?<![\w.])(?:с\s+)?{_DATE.format(p='a')}\s*(?:[—–\-]+|по|to|until|до)\s*"
    rf"(?:(?P<present>{_PRESENT})|{_DATE.format(p='b')})(?![\w])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Period:
    start: int  # индекс месяца: год·12 + (месяц − 1)
    end: int  # включительно
    present: bool
    span: tuple[int, int]  # в тексте

    @property
    def months(self) -> int:
        return self.end - self.start + 1


def month_index(year: int, month: int) -> int:
    return year * 12 + (month - 1)


def fmt_month(idx: int) -> str:
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _part(m: re.Match[str], p: str, *, is_end: bool) -> int | None:
    g = m.groupdict()
    if g.get(f"{p}y1"):
        return month_index(int(g[f"{p}y1"]), int(g[f"{p}m1"]))
    if g.get(f"{p}y2"):
        mon = _MONTHS[g[f"{p}mon"].lower().rstrip(".")]
        return month_index(int(g[f"{p}y2"]), mon)
    if g.get(f"{p}y3"):
        return month_index(int(g[f"{p}y3"]), 12 if is_end else 1)
    return None


def find_periods(text: str, now: datetime, spans: list[tuple[int, int]] | None = None) -> list[Period]:
    """Все периоды «дата — дата | настоящее время». spans — где искать (None — весь текст)."""
    now_idx = month_index(now.year, now.month)
    out: list[Period] = []
    for s, e in spans if spans is not None else [(0, len(text))]:
        for m in _RANGE_RE.finditer(text, s, e):
            start = _part(m, "a", is_end=False)
            if start is None:
                continue
            present = bool(m.group("present"))
            end = now_idx if present else _part(m, "b", is_end=True)
            if end is None:
                continue
            end = min(end, now_idx)
            if start > end or start > now_idx:
                continue
            out.append(Period(start, end, present, (m.start(), m.end())))
    out.sort(key=lambda p: (p.start, p.end))
    return out


def merged_months(periods: list[Period]) -> tuple[int, int]:
    """(стаж в месяцах без двойного счёта, сколько месяцев пересекались)."""
    total_raw = sum(p.months for p in periods)
    merged: list[list[int]] = []
    for p in sorted(periods, key=lambda p: p.start):
        if merged and p.start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], p.end)
        else:
            merged.append([p.start, p.end])
    total = sum(e - s + 1 for s, e in merged)
    return total, total_raw - total
