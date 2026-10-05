"""Разбор брифа работодателя — детерминированное ядро на правилах (≤ 300 мс, без LLM).

Концепция §4.2 «Разбор брифа», spec-contact §3.1:
  нормализация → вилка (regex, «к/тыс/000» → рубли, на руки / до НДФЛ) → навыки по словарю с цитатами →
  must / nice по маркерам в окне ±6 токенов (без маркера → must; больше 5 must → первые 4 must) →
  специализация softmax((2·cue + Σ spec_weight + 1,5·cos(emb, proto)) / 1,5) → грейд по маркерам,
  «опыт от N лет» и слабому априору по середине вилки → формат и город по словарю → один уточняющий вопрос.

Каждое поле несёт источник («правило» / «вручную») и цитату (start, end) в исходном тексте.
Эмбеддинг-член подключается через embed_similarity и по умолчанию равен 0 (LLM и модели — вне рантайма ядра).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any, Generic, TypeVar

from app.core.configs import load_config
from app.matching.scoring import FORMATS, GRADES, BriefSpec
from app.matching.skills import (
    TOKEN_RE,
    Match,
    NormText,
    SkillCatalog,
    get_catalog,
    normalize_text,
    token_index,
)

SOURCE_RULE = "правило"
SOURCE_MANUAL = "вручную"

T = TypeVar("T")


class BriefEditError(ValueError):
    """Недопустимая правка брифа (сообщение — для пользователя, по-русски)."""


@dataclass(frozen=True)
class Quote:
    start: int
    end: int
    text: str

    def to_dict(self) -> dict[str, Any]:
        return {"start": self.start, "end": self.end, "text": self.text}


@dataclass(frozen=True)
class Sourced(Generic[T]):
    """Значение поля + источник + цитаты (подсветка фразы в брифе)."""

    value: T
    source: str = SOURCE_RULE
    quotes: tuple[Quote, ...] = ()

    @property
    def span(self) -> tuple[int, int] | None:
        return (self.quotes[0].start, self.quotes[0].end) if self.quotes else None

    @property
    def quote(self) -> str | None:
        return self.quotes[0].text if self.quotes else None


@dataclass(frozen=True)
class SalaryRange:
    from_: int | None
    to: int | None
    net: bool | None  # True — на руки, False — до НДФЛ, None — не указано
    yearly: bool = False


@dataclass(frozen=True)
class SalaryCheck:
    """ok | warn (1,6–2,0×) | blocked (> 2,0×) | invalid | incomplete | missing (DECISIONS п.6)."""

    status: str
    message: str | None = None
    ratio: float | None = None


@dataclass(frozen=True)
class SkillChip:
    skill_id: str
    name: str
    kind: str  # must | nice
    source: str
    quote: Quote | None
    marker: Quote | None  # фраза-маркер («будут плюсом»), если kind выведен из неё
    reason: str  # marker | list | section | default | overflow | manual
    method: str  # dict | fuzzy | manual
    untestable: bool
    modules: tuple[str, ...]


@dataclass(frozen=True)
class ClarifyingQuestion:
    field: str  # spec | grade | salary | skills
    text: str
    options: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class ParsedBrief:
    text: str
    salary: Sourced[SalaryRange] | None
    salary_check: SalaryCheck
    skills: tuple[SkillChip, ...]
    spec_probs: Sourced[Mapping[str, float]]
    grade_probs: Sourced[Mapping[str, float]]
    formats: tuple[Sourced[str], ...]
    city: Sourced[str] | None
    relocation: Sourced[bool] | None
    roles: tuple[Sourced[str], ...]
    question: ClarifyingQuestion | None
    parser_version: str
    experience_years: Sourced[float] | None = None

    @property
    def must(self) -> tuple[str, ...]:
        return tuple(c.skill_id for c in self.skills if c.kind == "must")

    @property
    def nice(self) -> tuple[str, ...]:
        return tuple(c.skill_id for c in self.skills if c.kind == "nice")

    @property
    def top_spec(self) -> str:
        return max(self.spec_probs.value.items(), key=lambda kv: (kv[1], -_spec_order(kv[0])))[0]

    @property
    def top_grade(self) -> str:
        return max(self.grade_probs.value.items(), key=lambda kv: (kv[1], -GRADES.index(kv[0])))[0]

    def chip(self, skill_id: str) -> SkillChip | None:
        return next((c for c in self.skills if c.skill_id == skill_id), None)

    def to_dict(self) -> dict[str, Any]:
        def src(s: Sourced | None, value: Any = None) -> dict[str, Any] | None:
            if s is None:
                return None
            return {
                "value": s.value if value is None else value,
                "source": s.source,
                "quotes": [q.to_dict() for q in s.quotes],
            }

        sal = None
        if self.salary is not None:
            v = self.salary.value
            sal = src(self.salary, {"from": v.from_, "to": v.to, "net": v.net, "yearly": v.yearly})
        return {
            "parser_version": self.parser_version,
            "salary": sal,
            "salary_check": {
                "status": self.salary_check.status,
                "message": self.salary_check.message,
                "ratio": self.salary_check.ratio,
            },
            "skills": [
                {
                    "skill_id": c.skill_id,
                    "name": c.name,
                    "kind": c.kind,
                    "source": c.source,
                    "quote": c.quote.to_dict() if c.quote else None,
                    "marker": c.marker.to_dict() if c.marker else None,
                    "reason": c.reason,
                    "method": c.method,
                    "untestable": c.untestable,
                    "modules": list(c.modules),
                }
                for c in self.skills
            ],
            "spec_probs": src(self.spec_probs, {k: round(v, 4) for k, v in self.spec_probs.value.items()}),
            "grade_probs": src(self.grade_probs, {k: round(v, 4) for k, v in self.grade_probs.value.items()}),
            "formats": [src(f) for f in self.formats],
            "city": src(self.city),
            "relocation": src(self.relocation),
            "roles": [src(r) for r in self.roles],
            "experience_years": src(self.experience_years),
            "question": None
            if self.question is None
            else {
                "field": self.question.field,
                "text": self.question.text,
                "options": [list(o) for o in self.question.options],
            },
        }


def _spec_order(code: str) -> int:
    codes = [s["code"] for s in load_config("taxonomy")["specializations"]]
    return codes.index(code) if code in codes else len(codes)


def _q(nt: NormText, nstart: int, nend: int) -> Quote:
    s, e = nt.to_original(nstart, nend)
    return Quote(s, e, nt.original[s:e])


def _mq(m: Match) -> Quote:
    return Quote(m.start, m.end, m.quote)


def _cfg() -> Mapping[str, Any]:
    return load_config("scoring")["brief_parser"]


# --------------------------------------------------------------------------- вилка

_NUM = r"\d{1,3}(?: \d{3})+|\d{1,3}(?:[.,]\d{3})+(?![.,]?\d)|\d+(?:[.,]\d+)?"
_THOUSAND = r"тыс(?:яч[а-я]*|\.)?|тыщ[а-я]*|т\. ?р\.?|тр\b|к\b|k\b"
_MILLION = r"млн\.?|миллион[а-я]*|кк\b|kk\b"
_CURRENCY = r"₽|руб(?:лей|ля|ль|\.)?|р\.|rub\b|rur\b"


def _amount(i: int) -> str:
    return rf"(?P<n{i}>{_NUM})\s?(?:(?P<k{i}>{_THOUSAND})|(?P<m{i}>{_MILLION}))?\s?(?P<c{i}>{_CURRENCY})?"


_SALARY_RE = re.compile(
    rf"(?<![0-9a-zа-я.,])(?:(?P<pre>от|from)\s)?{_amount(1)}(?:\s?(?:-|до|to|…|\.\.\.?)\s?(?:до\s)?{_amount(2)})?",
)
_UPTO_RE = re.compile(r"(?:до|up to|не более|не выше|max|максимум)\s?$")


def _parse_number(s: str) -> float:
    s = s.replace(" ", "")
    if re.fullmatch(r"\d{1,3}(?:[.,]\d{3})+", s):
        return float(re.sub(r"[.,]", "", s))
    return float(s.replace(",", "."))


def _near(norm: str, start: int, end: int, phrases: Sequence[str]) -> tuple[int, int, str] | None:
    """Ближайшая фраза из списка в norm[start:end] с границами слова → (абс. начало, конец, фраза)."""
    best: tuple[int, int, str] | None = None
    seg = norm[max(0, start) : end]
    for p in phrases:
        pat = re.compile(rf"(?<![0-9a-zа-я]){re.escape(p)}(?![0-9a-zа-я])")
        for m in pat.finditer(seg):
            pos = max(0, start) + m.start()
            if best is None or pos < best[0]:
                best = (pos, pos + len(p), p)
            break
    return best


def parse_salary(text: str | NormText) -> tuple[SalaryRange, tuple[Quote, ...]] | None:
    """Найти вилку в тексте: «220–280 тыс. на руки», «от 200к», «150 000 – 200 000 ₽ gross»…

    Возвращает (вилка, цитаты: сумма и маркер «на руки / до НДФЛ») или None.
    """
    nt = text if isinstance(text, NormText) else normalize_text(text)
    norm = nt.norm
    cfg = _cfg()["salary"]
    words = load_config("skills")["salary_words"]
    not_money = tuple(words["not_money"])
    win = int(cfg["window_chars"])
    best: tuple[float, int, SalaryRange, tuple[Quote, ...]] | None = None
    for m in _SALARY_RE.finditer(norm):
        g = m.groupdict()
        end = m.end()
        while end > m.start() and norm[end - 1] == " ":
            end -= 1
        # Слово после суммы: «лет», «пользователей», «%» — это не деньги.
        tail = norm[end : end + 25].lstrip(" .,")
        is_period = any(tail.startswith(w) for w in words["yearly"])
        if tail.startswith("%") or (not is_period and any(tail.startswith(w) for w in not_money)):
            continue
        nums = []
        for i in (1, 2):
            if g[f"n{i}"] is None:
                nums.append(None)
                continue
            mult = 1000.0 if g[f"k{i}"] else 1_000_000.0 if g[f"m{i}"] else None
            nums.append([_parse_number(g[f"n{i}"]), mult, bool(g[f"c{i}"])])
        a, b = nums
        has_currency = bool(a[2] or (b and b[2]))
        has_mult = bool(a[1] or (b and b[1]))
        # Единица у одного конца диапазона относится и к другому: «220–280 тыс.».
        if b is not None:
            if a[1] is None and b[1] is not None and a[0] < 1000:
                a[1] = b[1]
            if b[1] is None and a[1] is not None and b[0] < 1000:
                b[1] = a[1]
            # «200–250 000 ₽»: у левого конца опущены тысячи (правый — полное число того же порядка).
            if a[1] is None and b[1] is None and a[0] < 1000 and a[0] * 1000 <= b[0] <= a[0] * 3000:
                a[1] = 1000.0
        ctx = _near(norm, m.start() - 30, m.start(), words["context"]) is not None
        values = []
        for x in (a, b):
            if x is None:
                values.append(None)
                continue
            v, mult = x[0], x[1]
            if mult:
                v *= mult
            elif v < 1000 and (has_currency or ctx):
                v *= 1000.0  # «зп 120-150», «150 т.р.» без явной единицы у числа
            elif v < float(cfg["min_plain_rubles"]):
                v = -1.0  # «2», «3.11», «1500» без единиц — не вилка
            values.append(v)
        if any(v is not None and (v <= 0 or v > float(cfg["max_value"])) for v in values):
            continue
        net_m = _near(norm, end, end + win, words["net"]) or _near(
            norm, m.start() - 25, m.start(), words["net"]
        )
        gross_m = _near(norm, end, end + win, words["gross"]) or _near(
            norm, m.start() - 25, m.start(), words["gross"]
        )
        if net_m and gross_m:  # оба рядом — берём ближайший к сумме
            if abs(net_m[0] - end) <= abs(gross_m[0] - end):
                gross_m = None
            else:
                net_m = None
        yearly_m = _near(norm, end, end + win, words["yearly"])
        # «3 000 000 в год» — период сам по себе говорит о деньгах.
        evidence = (
            2 * has_currency
            + 1 * has_mult
            + 2 * ctx
            + 2 * bool(net_m or gross_m)
            + (b is not None)
            + bool(g["pre"])
            + bool(yearly_m)
        )
        if evidence < 1:
            continue
        lo, hi = values
        if yearly_m:
            lo = lo / 12.0 if lo else lo
            hi = hi / 12.0 if hi else hi
        step = int(cfg["step"])

        def rnd(v: float | None, step: int = step) -> int | None:
            return None if v is None else int(math.floor(v / step + 0.5)) * step

        upto = b is None and not g["pre"] and _UPTO_RE.search(norm[max(0, m.start() - 12) : m.start()])
        plus = b is None and end < len(norm) and norm[end] == "+"
        if b is not None:
            rng = SalaryRange(rnd(lo), rnd(hi), True if net_m else False if gross_m else None, bool(yearly_m))
        elif upto:
            rng = SalaryRange(None, rnd(lo), True if net_m else False if gross_m else None, bool(yearly_m))
        elif g["pre"] or plus:
            rng = SalaryRange(rnd(lo), None, True if net_m else False if gross_m else None, bool(yearly_m))
        else:  # одно число: «оклад 250 000 ₽» — фиксированная сумма
            rng = SalaryRange(rnd(lo), rnd(lo), True if net_m else False if gross_m else None, bool(yearly_m))
        quotes = [_q(nt, m.start() if not upto else max(0, m.start() - len(upto.group(0))), end)]
        for mk in (net_m, gross_m):
            if mk:
                quotes.append(_q(nt, mk[0], mk[1]))
        if best is None or evidence > best[0]:
            best = (evidence, m.start(), rng, tuple(quotes))
    if best is None:
        return None
    return best[2], best[3]


def check_salary(rng: SalaryRange | None) -> SalaryCheck:
    """from ≥ 30 000, to ≥ from; to ≤ 1,6·from — норма; 1,6–2,0 — предупреждение; > 2,0 — блок (DECISIONS п.6)."""
    cfg = _cfg()["salary"]
    if rng is None or (rng.from_ is None and rng.to is None):
        return SalaryCheck("missing", "Укажите вилку: от и до, в рублях в месяц")
    if rng.from_ is None or rng.to is None:
        return SalaryCheck("incomplete", "Укажите вилку полностью: и нижнюю, и верхнюю границу")
    if rng.from_ < int(cfg["min_from"]):
        return SalaryCheck(
            "invalid", f"Нижняя граница вилки — не меньше {int(cfg['min_from']):,} ₽".replace(",", " ")
        )
    if rng.to < rng.from_:
        return SalaryCheck("invalid", "Верхняя граница вилки меньше нижней")
    step = int(cfg["step"])
    if rng.from_ % step or rng.to % step:
        # spec-contact §2.4: вилка в рублях с шагом 1000.
        return SalaryCheck("invalid", f"Вилка указывается с шагом {step:,} ₽".replace(",", " "))
    ratio = rng.to / rng.from_
    if ratio > float(cfg["ratio_block"]) + 1e-9:
        return SalaryCheck(
            "blocked",
            f"Вилка слишком широкая: верх больше низа в {_fmt_ratio(ratio)} раза. Сузьте до {_fmt_ratio(float(cfg['ratio_block']))}× — "
            "иначе кандидат не понимает, сколько ему предлагают",
            ratio,
        )
    if ratio > float(cfg["ratio_ok"]) + 1e-9:
        return SalaryCheck(
            "warn",
            f"Широкая вилка ({_fmt_ratio(ratio)}×). Рекомендуем не шире {_fmt_ratio(float(cfg['ratio_ok']))}×: "
            "кандидаты чаще отвечают на узкую вилку",
            ratio,
        )
    return SalaryCheck("ok", None, ratio)


def _fmt_ratio(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


# --------------------------------------------------------------------------- must / nice

_CONNECTORS = frozenset(
    {"и", "или", "а", "также", "and", "or", "etc", "т.д", "т.п", "др", "плюс", "либо", "с"}
)
_SENT_RE = re.compile(r"(?<=[.!?;])\s+|\n")
_CLAUSE_RE = re.compile(r"(?<!\d)[,;](?!\d)|:(?=\s|$)")


@dataclass
class _Clause:
    start: int
    end: int
    skills: list[int] = field(default_factory=list)  # индексы в списке совпадений навыков
    markers: list[Match] = field(default_factory=list)
    pure_list: bool = False
    kind: str | None = None  # собственный маркер клаузы
    marker: Match | None = None
    position: str | None = None  # pre | post


def _segments(norm: str, pattern: re.Pattern[str], start: int, end: int) -> list[tuple[int, int]]:
    out, pos = [], start
    for m in pattern.finditer(norm, start, end):
        if m.start() > pos:
            out.append((pos, m.start()))
        pos = m.end()
    if pos < end:
        out.append((pos, end))
    return out


def classify_skills(
    nt: NormText, skill_matches: Sequence[Match], marker_matches: Sequence[Match], window: int
) -> list[tuple[str, Match | None, str]]:
    """Для каждого совпадения навыка → (kind, маркер, причина).

    Приоритет: маркер в окне ±window токенов той же клаузы (nice-маркер сильнее must-маркера:
    «желательно знание Kafka») > продолжение перечисления (клауза из одних навыков наследует маркер
    соседней: «Желательно Kafka, Docker»; «Kafka, Docker — будут плюсом») > заголовок секции
    («Будет плюсом:» на отдельной строке) > по умолчанию must (spec-contact §3.1 п.2).
    """
    norm = nt.norm
    starts = [m.start() for m in TOKEN_RE.finditer(norm)]
    tokens = [(m.start(), m.end(), m.group(0)) for m in TOKEN_RE.finditer(norm)]
    result: list[tuple[str, Match | None, str] | None] = [None] * len(skill_matches)

    # Заголовки секций: строка заканчивается «:», содержит маркер и не содержит навыков.
    section_kind: list[tuple[str, Match] | None] = [None] * len(skill_matches)
    current: tuple[str, Match] | None = None
    pos = 0
    for line in norm.split("\n"):
        ls, le = pos, pos + len(line)
        pos = le + 1
        if not line.strip():
            current = None  # пустая строка закрывает секцию
            continue
        in_line = [i for i, s in enumerate(skill_matches) if ls <= s.nstart < le]
        mk = [m for m in marker_matches if ls <= m.nstart < le]
        if line.rstrip().endswith(":") and not in_line:
            # Любой заголовок открывает новую секцию; без маркера («Условия:», «Стек:») — секция по умолчанию.
            nice = [m for m in mk if m.id == "nice"]
            current = ("nice", nice[0]) if nice else ("must", mk[0]) if mk else None
            continue
        for i in in_line:
            section_kind[i] = current

    for ss, se in _segments(norm, _SENT_RE, 0, len(norm)):
        clauses = [_Clause(cs, ce) for cs, ce in _segments(norm, _CLAUSE_RE, ss, se)]
        for cl in clauses:
            cl.skills = [i for i, s in enumerate(skill_matches) if cl.start <= s.nstart < cl.end]
            # Маркер относится к клаузе, где он заканчивается: «Хорошо, если знаете Kafka» — запятая внутри маркера.
            cl.markers = [m for m in marker_matches if cl.start <= m.nend - 1 < cl.end]
            nice = [m for m in cl.markers if m.id == "nice"]
            if cl.markers:
                cl.kind = "nice" if nice else "must"
                cl.marker = (nice or cl.markers)[0]
                first_skill = min(
                    (token_index(starts, skill_matches[i].nstart) for i in cl.skills), default=None
                )
                mt = token_index(starts, cl.marker.nstart)
                cl.position = "pre" if first_skill is None or mt <= first_skill else "post"
            covered = [(skill_matches[i].nstart, skill_matches[i].nend) for i in cl.skills]
            rest = [
                t
                for ts, te, t in tokens
                if cl.start <= ts < cl.end and not any(a <= ts < b for a, b in covered)
            ]
            cl.pure_list = bool(cl.skills) and not cl.markers and all(t in _CONNECTORS for t in rest)

        # Собственные маркеры в окне.
        for cl in clauses:
            for i in cl.skills:
                s = skill_matches[i]
                st, en = token_index(starts, s.nstart), token_index(starts, max(s.nstart, s.nend - 1))
                near = []
                for m in cl.markers:
                    mt0, mt1 = token_index(starts, m.nstart), token_index(starts, max(m.nstart, m.nend - 1))
                    dist = 0 if (mt0 <= en and st <= mt1) else min(abs(mt0 - en), abs(st - mt1))
                    if dist <= window:
                        near.append((dist, m))
                nice = sorted((d, m.nstart, m) for d, m in near if m.id == "nice")
                must = sorted((d, m.nstart, m) for d, m in near if m.id == "must")
                if nice:
                    result[i] = ("nice", nice[0][2], "marker")
                elif must:
                    result[i] = ("must", must[0][2], "marker")

        # Перечисление наследует маркер соседней клаузы: слева «pre»-маркер, справа «post»-маркер.
        inherited: dict[int, tuple[str, Match | None]] = {}
        prev: tuple[str, Match | None] | None = None
        for ci, cl in enumerate(clauses):
            if cl.kind is not None and cl.position == "pre":
                prev = (cl.kind, cl.marker)
                continue
            if cl.pure_list and prev is not None:
                inherited[ci] = prev
                continue
            prev = None
        nxt: tuple[str, Match | None] | None = None
        for ci in range(len(clauses) - 1, -1, -1):
            cl = clauses[ci]
            if cl.kind is not None and cl.position == "post":
                nxt = (cl.kind, cl.marker)
                continue
            if cl.pure_list and nxt is not None:
                inherited.setdefault(ci, nxt)
                continue
            nxt = None
        for ci, cl in enumerate(clauses):
            if ci in inherited:
                for i in cl.skills:
                    if result[i] is None:
                        result[i] = (inherited[ci][0], inherited[ci][1], "list")

    out: list[tuple[str, Match | None, str]] = []
    for i, r in enumerate(result):
        if r is None:
            sec = section_kind[i]
            r = (sec[0], sec[1], "section") if sec else ("must", None, "default")
        out.append(r)
    return out


# --------------------------------------------------------------------------- грейд

_EXP_NUM = (
    r"(\d+(?:[.,]\d+)?|одного|один|полутора|двух|два|трех|три|четырех|четыре|пяти|пять|шести|семи|десяти)"
)
_EXP_UNIT = r"(?:-?х\s)?(?:лет|года|год|years?)"
_EXP_PRE = r"(?:от|не менее|более|свыше|больше|минимум|min|at least)\s?"
_EXP_RES = [
    re.compile(
        rf"(?:опыт|стаж|experience)[а-я]*(?:\s[а-яa-z\-]+){{0,4}}?\s(?:{_EXP_PRE})?{_EXP_NUM}(?:\s?-\s?\d+)?\s?\+?\s?{_EXP_UNIT}"
    ),
    re.compile(
        rf"(?:{_EXP_PRE})?{_EXP_NUM}(?:\s?-\s?\d+)?\s?\+?\s?{_EXP_UNIT}(?:\s[а-яa-z]+){{0,2}}?\s(?:опыт|стаж|of experience|experience)"
    ),
    re.compile(r"(?:опыт|стаж)[а-я]*(?:\s[а-я\-]+){0,3}?\sот\s(?:одного\s)?года"),
]


def find_experience(nt: NormText) -> tuple[float, Quote] | None:
    """«опыт от 2 лет», «3+ года опыта», «опыт работы от 2-х лет», «опыт от года» → (N лет, цитата)."""
    numbers = {k: float(v) for k, v in load_config("skills")["experience"]["number_words"].items()}
    for i, rx in enumerate(_EXP_RES):
        m = rx.search(nt.norm)
        if not m:
            continue
        if i == 2:
            return 1.0, _q(nt, m.start(), m.end())
        raw = m.group(1)
        try:
            n = float(raw.replace(",", "."))
        except ValueError:
            n = numbers.get(raw)
        if n is None or n > 30:
            continue
        return n, _q(nt, m.start(), m.end())
    return None


def _peak(grade: str, p: float) -> dict[str, float]:
    """P(grade) = p, остаток поровну соседним грейдам."""
    i = GRADES.index(grade)
    nb = [GRADES[j] for j in (i - 1, i + 1) if 0 <= j < len(GRADES)]
    out = {g: 0.0 for g in GRADES}
    out[grade] = p
    for g in nb:
        out[g] += (1.0 - p) / len(nb)
    return out


def _salary_prior(mid_net: float, cfg: Mapping[str, Any]) -> dict[str, float]:
    """Слабый априор по середине вилки: гауссиана в log-шкале вокруг типичной зарплаты грейда."""
    sig = float(cfg["salary_sigma_log"])
    w = {
        g: math.exp(-((math.log(mid_net / float(c))) ** 2) / (2 * sig * sig))
        for g, c in cfg["salary_centers_net"].items()
    }
    tot = sum(w.values()) or 1.0
    return {g: w.get(g, 0.0) / tot for g in GRADES}


def _subject_markers(nt: NormText, catalog: SkillCatalog, markers: Sequence[Match]) -> list[Match]:
    """Из противоречащих маркеров грейда оставить те, что описывают самого кандидата.

    «Ищем Senior Python-разработчика, будет менторить джунов»: «Senior» стоит рядом с ролью, «джунов» — нет.
    Маркер «про кандидата», если в ±N токенах есть роль, сигнал специализации, слово-роль («разработчик»)
    или глагол найма («ищем», «нужен»). Если таких нет — берём все маркеры (как раньше)."""
    if len({m.id for m in markers}) < 2:
        return list(markers)
    gc = load_config("skills").get("grade_context") or {}
    window = int(gc.get("window_tokens", 2))
    stems = tuple(normalize_text(s).norm for s in gc.get("anchor_stems", ()))
    toks = list(TOKEN_RE.finditer(nt.norm))
    starts = [t.start() for t in toks]
    anchors = {
        i
        for i, t in enumerate(toks)
        if any(part.startswith(stems) for part in re.split(r"[-/.]", t.group(0)) if part)
    }
    for m in catalog.find_roles(nt) + catalog.find_spec_cues(nt):
        anchors.add(token_index(starts, m.nstart))
    picked = [m for m in markers if any(abs(token_index(starts, m.nstart) - a) <= window for a in anchors)]
    return picked or list(markers)


def grade_distribution(
    nt: NormText, catalog: SkillCatalog, salary: SalaryRange | None
) -> tuple[dict[str, float], tuple[Quote, ...], tuple[float, Quote] | None]:
    """Маркеры (P = 0,85) → иначе «опыт от N лет» → смесь с априором по вилке (вес 0,3)."""
    cfg = _cfg()["grade"]
    quotes: list[Quote] = []
    evidence: dict[str, float] | None = None
    markers = _subject_markers(nt, catalog, catalog.find_grade_markers(nt))
    if markers:
        dists = []
        for m in markers:
            plus = m.nend < len(nt.norm) and nt.norm[m.nend] == "+"
            if plus and GRADES.index(m.id) + 1 < len(GRADES):
                d = {g: 0.0 for g in GRADES}
                d[m.id] = float(cfg["plus_split"]["this"])
                d[GRADES[GRADES.index(m.id) + 1]] = float(cfg["plus_split"]["next"])
                quotes.append(_q(nt, m.nstart, m.nend + 1))
            elif m.alias in ("без опыта",):
                d = {g: float(cfg["no_experience"].get(g, 0.0)) for g in GRADES}
                quotes.append(_mq(m))
            else:
                d = _peak(m.id, float(cfg["marker_p"]))
                quotes.append(_mq(m))
            dists.append(d)
        evidence = {g: sum(d[g] for d in dists) / len(dists) for g in GRADES}
    exp = find_experience(nt)
    if evidence is None and exp is not None:
        n, q = exp
        quotes.append(q)
        if n <= float(cfg["exp_junior_max_years"]):
            evidence = _peak("junior", float(cfg["marker_p"]))
        elif n <= float(cfg["exp_middle_max_years"]):
            evidence = {g: float(cfg["exp_split"].get(g, 0.0)) for g in GRADES}
        else:
            evidence = _peak("senior", float(cfg["marker_p"]))
    w = float(cfg["salary_prior_weight"])
    prior = None
    if salary is not None and (salary.from_ or salary.to):
        lo, hi = salary.from_ or salary.to, salary.to or salary.from_
        mid = (lo + hi) / 2.0
        if salary.net is False:
            mid *= 1.0 - float(load_config("scoring")["U"]["ndfl_rate"])
        prior = _salary_prior(mid, cfg)
    base = evidence if evidence is not None else {g: float(cfg["default_prior"][g]) for g in GRADES}
    if prior is not None:
        probs = {g: (1 - w) * base[g] + w * prior[g] for g in GRADES}
    else:
        probs = dict(base)
    tot = sum(probs.values()) or 1.0
    return {g: probs[g] / tot for g in GRADES}, tuple(quotes), exp


# --------------------------------------------------------------------------- специализация

EmbedSimilarity = Callable[[str], Mapping[str, float]]


def spec_distribution(
    nt: NormText,
    catalog: SkillCatalog,
    chips: Sequence[tuple[str, str]],
    embed_similarity: EmbedSimilarity | None = None,
) -> tuple[dict[str, float], dict[str, float], list[Match]]:
    """P(s) = softmax((2·cue_s + Σ spec_weight(k,s) + 1,5·cos(emb, proto_s)) / 1,5) (концепция §4.2).

    chips — (skill_id, kind); nice-навык весит как в K (0,4). embed_similarity по умолчанию отсутствует (0)."""
    cfg = _cfg()
    sc = cfg["spec"]
    specs = [s["code"] for s in catalog.taxonomy["specializations"]]
    cues = {s: 0.0 for s in specs}
    cue_matches = catalog.find_spec_cues(nt)
    for m in cue_matches:
        for s, w in catalog.cue_weights(m.id).items():
            cues[s] = cues.get(s, 0.0) + w
    score = {s: float(sc["cue_weight"]) * min(cues[s], float(sc["cue_cap"])) for s in specs}
    nice_w = float(cfg["nice_weight_for_spec"])
    for sid, kind in chips:
        sk = catalog.skills.get(sid)
        if sk is None:
            continue
        for s, w in sk.spec_weights.items():
            if s in score:
                score[s] += w * (1.0 if kind == "must" else nice_w)
    if embed_similarity is not None:
        for s, cos in embed_similarity(nt.original).items():
            if s in score:
                score[s] += float(sc["emb_weight"]) * float(cos)
    t = float(sc["temperature"])
    mx = max(score.values())
    ex = {s: math.exp((v - mx) / t) for s, v in score.items()}
    tot = sum(ex.values())
    return {s: ex[s] / tot for s in specs}, score, cue_matches


# --------------------------------------------------------------------------- разбор


def parse_brief(
    text: str,
    *,
    salary_from: int | None = None,
    salary_to: int | None = None,
    salary_net: bool | None = None,
    embed_similarity: EmbedSimilarity | None = None,
    catalog: SkillCatalog | None = None,
) -> ParsedBrief:
    """Разобрать бриф. Поля формы вилки (если переданы) имеют приоритет и получают источник «вручную»."""
    cat = catalog or get_catalog()
    cfg = _cfg()
    text = text[: int(cfg["max_chars"])]
    nt = normalize_text(text)

    # Вилка
    found = parse_salary(nt)
    salary: Sourced[SalaryRange] | None = None
    if found is not None:
        salary = Sourced(found[0], SOURCE_RULE, found[1])
    if salary_from is not None or salary_to is not None or salary_net is not None:
        base = salary.value if salary else SalaryRange(None, None, None)
        salary = Sourced(
            SalaryRange(
                salary_from if salary_from is not None else base.from_,
                salary_to if salary_to is not None else base.to,
                salary_net if salary_net is not None else base.net,
                base.yearly,
            ),
            SOURCE_MANUAL,
            salary.quotes if salary else (),
        )
    salary_check = check_salary(salary.value if salary else None)

    # Навыки и must / nice
    smatches = cat.find_skills(nt)
    markers = cat.find_markers(nt)
    kinds = classify_skills(nt, smatches, markers, int(cfg["window_tokens"]))
    chips: dict[str, SkillChip] = {}
    for m, (kind, marker, reason) in zip(smatches, kinds, strict=True):
        sk = cat.skills[m.id]
        if m.id in chips:
            prev = chips[m.id]
            # Повторное упоминание с явным маркером уточняет вид, если первое было «по умолчанию».
            if prev.reason == "default" and reason in ("marker", "list", "section"):
                chips[m.id] = replace(prev, kind=kind, marker=_mq(marker) if marker else None, reason=reason)
            continue
        chips[m.id] = SkillChip(
            m.id,
            sk.name,
            kind,
            SOURCE_RULE,
            _mq(m),
            _mq(marker) if marker else None,
            reason,
            m.method,
            sk.untestable,
            sk.modules,
        )
    ordered = list(chips.values())
    must = [c for c in ordered if c.kind == "must"]
    if len(must) > int(cfg["must_max"]):
        keep = {c.skill_id for c in must[: int(cfg["must_keep_on_overflow"])]}
        ordered = [
            c
            if c.kind != "must" or c.skill_id in keep
            else replace(c, kind="nice", reason="overflow", marker=None)
            for c in ordered
        ]
    skills = tuple(ordered)

    # Специализация
    spec_p, _, cue_matches = spec_distribution(
        nt, cat, [(c.skill_id, c.kind) for c in skills], embed_similarity
    )
    top = max(spec_p, key=lambda s: spec_p[s])
    ev_quotes = [_mq(m) for m in cue_matches if cat.cue_weights(m.id).get(top, 0) > 0]
    ev_quotes += [c.quote for c in skills if c.quote and cat.skills[c.skill_id].spec_weights.get(top, 0) > 0]
    spec_probs: Sourced[Mapping[str, float]] = Sourced(
        spec_p, SOURCE_RULE, tuple(sorted(ev_quotes, key=lambda q: q.start))
    )

    # Грейд
    grade_p, g_quotes, exp = grade_distribution(nt, cat, salary.value if salary else None)
    if salary is not None and salary.quotes:
        g_quotes = g_quotes + (salary.quotes[0],)
    grade_probs: Sourced[Mapping[str, float]] = Sourced(grade_p, SOURCE_RULE, g_quotes)

    # Формат, город, релокация, роли
    fmts: dict[str, Sourced[str]] = {}
    for m in cat.find_formats(nt):
        fmts.setdefault(m.id, Sourced(m.id, SOURCE_RULE, (_mq(m),)))
    cities = cat.find_cities(nt)
    city = Sourced(cities[0].id, SOURCE_RULE, (_mq(cities[0]),)) if cities else None
    reloc = cat.find_relocation(nt)
    relocation = Sourced(True, SOURCE_RULE, (_mq(reloc[0]),)) if reloc else None
    roles: dict[str, Sourced[str]] = {}
    for m in cat.find_roles(nt):
        roles.setdefault(m.id, Sourced(m.id, SOURCE_RULE, (_mq(m),)))

    parsed = ParsedBrief(
        text=text,
        salary=salary,
        salary_check=salary_check,
        skills=skills,
        spec_probs=spec_probs,
        grade_probs=grade_probs,
        formats=tuple(fmts.values()),
        city=city,
        relocation=relocation,
        roles=tuple(roles.values()),
        question=None,
        parser_version=str(cfg["version"]),
        experience_years=Sourced(exp[0], SOURCE_RULE, (exp[1],)) if exp else None,
    )
    return replace(parsed, question=clarifying_question(parsed, cat))


def clarifying_question(p: ParsedBrief, catalog: SkillCatalog | None = None) -> ClarifyingQuestion | None:
    """Не больше одного вопроса: специализация (P < 0,5) → грейд → вилка → навыки."""
    cat = catalog or get_catalog()
    cfg = _cfg()
    names = cat.spec_names
    if p.spec_probs.source != SOURCE_MANUAL:
        ranked = sorted(p.spec_probs.value.items(), key=lambda kv: (-kv[1], _spec_order(kv[0])))
        if ranked[0][1] < float(cfg["spec"]["uncertain_below"]):
            a, b = ranked[0][0], ranked[1][0]
            opts = [(a, names[a]), (b, names[b])]
            if {a, b} & {"backend_python", "backend_go", "backend_java"} and "frontend_js" in {a, b}:
                opts.append(("fullstack", names["fullstack"]))
            label = " или ".join(o[1] for o in opts)
            return ClarifyingQuestion("spec", f"Кого вы ищете: {label}?", tuple(opts))
    if p.grade_probs.source != SOURCE_MANUAL:
        gnames = {g["code"]: g["name"] for g in cat.taxonomy["grades"]}
        ranked_g = sorted(p.grade_probs.value.items(), key=lambda kv: (-kv[1], GRADES.index(kv[0])))
        if ranked_g[0][1] < float(cfg["grade"]["uncertain_below"]):
            a, b = sorted((ranked_g[0][0], ranked_g[1][0]), key=GRADES.index)
            return ClarifyingQuestion(
                "grade",
                f"Какой уровень нужен: {gnames[a]} или {gnames[b]}?",
                ((a, gnames[a]), (b, gnames[b])),
            )
    if p.salary_check.status in ("missing", "incomplete"):
        return ClarifyingQuestion("salary", p.salary_check.message or "Укажите вилку")
    if not p.skills:
        return ClarifyingQuestion("skills", "Какие технологии обязательны для этой роли?")
    return None


# --------------------------------------------------------------------------- ручные правки (источник «вручную»)


def _requote(p: ParsedBrief) -> ParsedBrief:
    return replace(p, question=clarifying_question(p))


def set_skill(p: ParsedBrief, skill_id: str, kind: str, catalog: SkillCatalog | None = None) -> ParsedBrief:
    """Добавить навык или перенести его между must и nice. Must ≤ 5."""
    cat = catalog or get_catalog()
    if kind not in ("must", "nice"):
        raise BriefEditError("Вид навыка: обязательный или желательный")
    if skill_id not in cat.skills:
        raise BriefEditError("Такого навыка нет в справочнике")
    must_max = int(_cfg()["must_max"])
    chips = list(p.skills)
    idx = next((i for i, c in enumerate(chips) if c.skill_id == skill_id), None)
    if kind == "must" and sum(1 for c in chips if c.kind == "must" and c.skill_id != skill_id) >= must_max:
        raise BriefEditError(f"Обязательных навыков — не больше {must_max}")
    if idx is not None:
        chips[idx] = replace(chips[idx], kind=kind, source=SOURCE_MANUAL, reason="manual", marker=None)
    else:
        sk = cat.skills[skill_id]
        found = [m for m in cat.find_skills(p.text, fuzzy=False) if m.id == skill_id]
        quote = _mq(found[0]) if found else None
        chips.append(
            SkillChip(
                skill_id,
                sk.name,
                kind,
                SOURCE_MANUAL,
                quote,
                None,
                "manual",
                "manual",
                sk.untestable,
                sk.modules,
            )
        )
    return _requote(replace(p, skills=tuple(chips)))


def remove_skill(p: ParsedBrief, skill_id: str) -> ParsedBrief:
    return _requote(replace(p, skills=tuple(c for c in p.skills if c.skill_id != skill_id)))


def set_salary(
    p: ParsedBrief, salary_from: int | None, salary_to: int | None, net: bool | None
) -> ParsedBrief:
    quotes = p.salary.quotes if p.salary else ()
    rng = SalaryRange(salary_from, salary_to, net)
    return _requote(replace(p, salary=Sourced(rng, SOURCE_MANUAL, quotes), salary_check=check_salary(rng)))


def set_spec(p: ParsedBrief, spec: str) -> ParsedBrief:
    if spec not in p.spec_probs.value:
        raise BriefEditError("Неизвестная специализация")
    probs = {s: (1.0 if s == spec else 0.0) for s in p.spec_probs.value}
    return _requote(replace(p, spec_probs=Sourced(probs, SOURCE_MANUAL, ())))


def set_grade(p: ParsedBrief, grade: str) -> ParsedBrief:
    if grade not in GRADES:
        raise BriefEditError("Неизвестный грейд")
    probs = {g: (1.0 if g == grade else 0.0) for g in GRADES}
    return _requote(replace(p, grade_probs=Sourced(probs, SOURCE_MANUAL, ())))


def set_formats(p: ParsedBrief, formats: Sequence[str]) -> ParsedBrief:
    bad = [f for f in formats if f not in FORMATS]
    if bad:
        raise BriefEditError("Формат: офис, гибрид или удалённо")
    return _requote(replace(p, formats=tuple(Sourced(f, SOURCE_MANUAL, ()) for f in dict.fromkeys(formats))))


def set_city(p: ParsedBrief, city_id: str | None, catalog: SkillCatalog | None = None) -> ParsedBrief:
    cat = catalog or get_catalog()
    if city_id is not None and city_id not in cat.cities:
        raise BriefEditError("Неизвестный город")
    return _requote(replace(p, city=None if city_id is None else Sourced(city_id, SOURCE_MANUAL, ())))


def answer_question(p: ParsedBrief, option: str) -> ParsedBrief:
    """Ответ на уточняющий вопрос одним нажатием (код варианта)."""
    q = p.question
    if q is None:
        return p
    if q.field == "spec":
        return set_spec(p, option)
    if q.field == "grade":
        return set_grade(p, option)
    raise BriefEditError("На этот вопрос отвечают в форме")


def to_brief_spec(
    p: ParsedBrief, need_id: str, *, spec: str | None = None, grade: str | None = None
) -> BriefSpec:
    """Бриф → вход скоринга. Вилка обязательна; без флага «на руки / до НДФЛ» считаем «на руки»."""
    if p.salary is None or p.salary.value.to is None or p.salary.value.from_ is None:
        raise BriefEditError("Укажите вилку: от и до, в рублях в месяц")
    if p.salary_check.status in ("blocked", "invalid"):
        raise BriefEditError(p.salary_check.message or "Вилка не прошла проверку")
    v = p.salary.value
    return BriefSpec(
        need_id=need_id,
        spec=spec or p.top_spec,
        grade=grade or p.top_grade,
        must=p.must,
        nice=p.nice,
        salary_from=v.from_,
        salary_to=v.to,
        salary_net=True if v.net is None else v.net,
        formats=frozenset(f.value for f in p.formats),
        city=p.city.value if p.city else None,
    )
