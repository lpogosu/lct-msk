"""Разбиение резюме на разделы по заголовкам («Опыт работы», «Образование», «Ключевые навыки»…).

Заголовок — короткая строка, которая целиком (без двоеточия и хвоста вида «— 6 лет 2 месяца»)
совпадает с одним из известных названий раздела. Текст до первого заголовка — «шапка» (header):
там обычно ФИО, желаемая должность и контакты.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

HEADINGS: dict[str, tuple[str, ...]] = {
    "contacts": ("контакты", "контактная информация", "контактные данные", "связь со мной", "contacts", "contact"),
    "summary": ("о себе", "обо мне", "кратко о себе", "summary", "about", "about me", "профиль"),
    "position": ("желаемая должность", "желаемая должность и зарплата", "цель", "позиция", "position", "objective"),
    "experience": (
        "опыт работы", "опыт", "профессиональный опыт", "места работы", "трудовая деятельность",
        "experience", "work experience", "employment", "professional experience",
    ),
    "education": ("образование", "высшее образование", "основное образование", "education"),
    "skills": (
        "навыки", "ключевые навыки", "технические навыки", "профессиональные навыки", "стек", "стек технологий",
        "технологии", "skills", "hard skills", "tech stack", "technical skills", "key skills",
    ),
    "soft_skills": ("личные качества", "soft skills", "софт-скиллы", "качества"),
    "projects": ("проекты", "pet-проекты", "пет-проекты", "projects"),
    "courses": (
        "курсы", "повышение квалификации", "сертификаты", "курсы и сертификаты", "повышение квалификации, курсы",
        "courses", "certificates",
    ),
    "languages": ("языки", "знание языков", "иностранные языки", "languages"),
    "achievements": ("достижения", "награды", "achievements"),
    "extra": ("дополнительная информация", "дополнительно", "additional information", "additional"),
}

_LOOKUP = {name: key for key, names in HEADINGS.items() for name in names}
_MAX_HEADING_LEN = 60
# Хвост после названия раздела, который не мешает считать строку заголовком: «— 6 лет 2 месяца»
_TAIL_RE = re.compile(r"^[\s—–\-:|]*(?:\d+\s*(?:лет|год|года|месяц|месяца|месяцев|years?|months?)\s*)*$", re.IGNORECASE)


def _norm(line: str) -> str:
    s = line.strip().lower().replace("ё", "е")
    s = re.sub(r"\s+", " ", s)
    return s.strip(" :—–-|•·")


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    start: int  # начало строки заголовка (для header — 0)
    body_start: int  # начало текста раздела
    end: int


def iter_lines(text: str) -> Iterator[tuple[int, int, str]]:
    """(начало, конец, строка) без символа перевода строки."""
    pos = 0
    for line in text.split("\n"):
        yield pos, pos + len(line), line
        pos += len(line) + 1


def heading_key(line: str) -> str | None:
    s = _norm(line)
    if not s or len(s) > _MAX_HEADING_LEN:
        return None
    if s in _LOOKUP:
        return _LOOKUP[s]
    for name, key in sorted(_LOOKUP.items(), key=lambda kv: -len(kv[0])):
        if s.startswith(name) and _TAIL_RE.match(s[len(name):]):
            return key
    return None


def split_sections(text: str) -> list[Section]:
    marks: list[tuple[int, int, str, str]] = []  # (начало строки, конец строки, key, title)
    for start, end, line in iter_lines(text):
        key = heading_key(line)
        if key is not None:
            marks.append((start, end, key, line.strip()))
    sections: list[Section] = []
    first = marks[0][0] if marks else len(text)
    if first > 0:
        sections.append(Section("header", "", 0, 0, first))
    for i, (start, end, key, title) in enumerate(marks):
        nxt = marks[i + 1][0] if i + 1 < len(marks) else len(text)
        sections.append(Section(key, title, start, min(end + 1, nxt), nxt))
    return sections


def section_spans(sections: list[Section], *keys: str) -> list[tuple[int, int]]:
    return [(s.body_start, s.end) for s in sections if s.key in keys]


def in_spans(pos: int, spans: list[tuple[int, int]]) -> bool:
    return any(s <= pos < e for s, e in spans)
