"""ФИО из резюме без тяжёлых NLP-зависимостей: словари имён + правила окончаний отчеств и фамилий.

Ищем в шапке (до первого раздела) и в строке «ФИО: …» подряд идущие слова с заглавной кириллицей
(2–3 слова в одной строке). Для каждой перестановки «Фамилия Имя Отчество» / «Имя Отчество Фамилия» /
«Имя Фамилия» / «Фамилия Имя» считаем баллы: имя из словаря, окончание отчества, окончание фамилии,
слово не из стоп-списка (заголовки, города, должности). Побеждает лучший вариант выше порога.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.resume.names_data import FIRST_NAMES, PATRONYMIC_SUFFIXES, STOP_WORDS, SURNAME_SUFFIXES

_WORD = r"[А-ЯЁ][а-яё]+(?:-[А-ЯЁ][а-яё]+)?|[А-ЯЁ]{2,}(?:-[А-ЯЁ]{2,})?"
_RUN_RE = re.compile(rf"(?:{_WORD})(?:[ \t]+(?:{_WORD})){{1,5}}")
_WORD_RE = re.compile(_WORD)
_FIO_LABEL_RE = re.compile(r"^\s*(?:фио|ф\.и\.о\.|имя)\s*[:\-—]\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_HEADER_LINES = 12


@dataclass(frozen=True)
class NameMatch:
    last: str | None
    first: str
    middle: str | None
    start: int
    end: int
    score: float

    @property
    def value(self) -> str:
        return " ".join(p for p in (self.last, self.first, self.middle) if p)


def _cap(word: str) -> str:
    """КОРТЕЖЕВ → Кортежев; двойные фамилии — по частям."""
    return "-".join(p[:1].upper() + p[1:].lower() for p in word.split("-"))


def _low(word: str) -> str:
    return word.lower().replace("ё", "е")


def is_first_name(word: str) -> bool:
    w = word.lower()
    return w in FIRST_NAMES or w.replace("ё", "е") in FIRST_NAMES


def is_patronymic(word: str) -> bool:
    w = _low(word)
    return len(w) >= 6 and w.endswith(tuple(s.replace("ё", "е") for s in PATRONYMIC_SUFFIXES))


def surname_score(word: str) -> float:
    w = _low(word)
    if w in STOP_WORDS or is_first_name(word) or is_patronymic(word):
        return 0.0
    if any(w.endswith(s.replace("ё", "е")) for s in SURNAME_SUFFIXES) and len(w) >= 4:
        return 1.5
    return 0.5 if len(w) >= 3 else 0.0  # нетипичная фамилия — слабый сигнал


def _score(last: str | None, first: str, middle: str | None) -> float:
    if not is_first_name(first):
        return 0.0
    score = 3.0
    if middle is not None:
        if not is_patronymic(middle):
            return 0.0
        score += 3.0
    if last is not None:
        s = surname_score(last)
        if s == 0.0:
            return 0.0
        score += s
    return score


def _candidates(words: list[str]) -> list[tuple[str | None, str, str | None, float]]:
    """Возможные трактовки 2–3 слов: (фамилия, имя, отчество, балл)."""
    out: list[tuple[str | None, str, str | None, float]] = []
    if len(words) == 3:
        a, b, c = words
        out.append((a, b, c, _score(a, b, c)))  # Фамилия Имя Отчество
        out.append((c, a, b, _score(c, a, b)))  # Имя Отчество Фамилия
        out.append((a, b, None, _score(a, b, None) - 0.5))  # «Фамилия Имя» + постороннее слово
        out.append((b, a, None, _score(b, a, None) - 0.5))
    elif len(words) == 2:
        a, b = words
        out.append((a, b, None, _score(a, b, None)))  # Фамилия Имя
        out.append((b, a, None, _score(b, a, None)))  # Имя Фамилия
        out.append((None, a, b, _score(None, a, b) - 1.0))  # Имя Отчество (без фамилии)
    return out


_MIN_SCORE = 4.0  # имя (3) + хотя бы правдоподобная фамилия или отчество


def find_full_name(text: str, header_end: int) -> NameMatch | None:
    """Лучшее ФИО в шапке резюме (или в строке «ФИО: …» где угодно)."""
    regions: list[tuple[int, int, float]] = []  # (начало, конец, бонус)
    for m in _FIO_LABEL_RE.finditer(text):
        regions.append((m.start(1), m.end(1), 1.0))
    # Шапка: до первого раздела, но не дальше 12 строк
    limit, pos = min(header_end, len(text)), 0
    for _ in range(_HEADER_LINES):
        nl = text.find("\n", pos)
        if nl < 0 or nl >= limit:
            regions.append((pos, limit, 0.0))
            break
        regions.append((pos, nl, 0.0))
        pos = nl + 1
    best: NameMatch | None = None
    for r_start, r_end, bonus in regions:
        line = text[r_start:r_end]
        for run in _RUN_RE.finditer(line):
            tokens = [(m.group(0), run.start() + m.start(), run.start() + m.end()) for m in _WORD_RE.finditer(run.group(0))]
            # Окна по 3 и по 2 подряд идущих слова: «Резюме Кортежев Денис Павлович» → «Кортежев Денис Павлович»
            for size in (3, 2):
                for i in range(len(tokens) - size + 1):
                    window = tokens[i : i + size]
                    for last, first, middle, score in _candidates([w for w, _, _ in window]):
                        score += bonus
                        if score < _MIN_SCORE or (best is not None and score <= best.score):
                            continue
                        best = NameMatch(
                            _cap(last) if last else None,
                            _cap(first),
                            _cap(middle) if middle else None,
                            r_start + window[0][1],
                            r_start + window[-1][2],
                            score,
                        )
    return best
