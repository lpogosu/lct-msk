"""Словари подбора (config/skills.yaml) и поиск в тексте с цитатами-спанами.

Поиск: нормализация (регистр, ё→е, тире, пробелы) с картой позиций в исходный текст →
Aho-Corasick по алиасам → проверка границ слова и русских окончаний → снятие пересечений
(побеждает самое длинное совпадение) → rapidfuzz ≥ 90 для опечаток (концепция §3.1.1, spec-contact §3.1).
Каждое совпадение несёт (start, end) в ИСХОДНОМ тексте: text[start:end] == quote.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import ahocorasick
from rapidfuzz import fuzz, process

from app.core.configs import load_config

_DASHES = "‐‑‒–—―−"
_SPACES = "            　\t\r\f\v"
_QUOTES = {"«": '"', "»": '"', "“": '"', "”": '"', "„": '"', "’": "'", "‘": "'"}
_CYR = frozenset("абвгдежзийклмнопрстуфхцчшщъыьэюя")
_CYR_VOWEL_END = frozenset("аеиоуыэюяйь")


def _norm_char(ch: str) -> str:
    """Посимвольная нормализация, сохраняющая длину 1 → 1 (кроме пробелов, их схлопываем отдельно)."""
    if ch in _DASHES:
        return "-"
    if ch in _QUOTES:
        return _QUOTES[ch]
    if ch in _SPACES:
        return " "
    low = ch.lower()
    if len(low) != 1:
        return ch
    if low == "ё":
        return "е"
    return low


def is_word_char(ch: str) -> bool:
    return ch.isalnum() or ch == "_"


@dataclass(frozen=True)
class NormText:
    """Нормализованный текст и карта «позиция в norm → позиция в исходном тексте»."""

    original: str
    norm: str
    index: tuple[int, ...]

    def to_original(self, start: int, end: int) -> tuple[int, int]:
        if end <= start:
            pos = self.index[start] if start < len(self.index) else len(self.original)
            return pos, pos
        return self.index[start], self.index[end - 1] + 1


def normalize_text(text: str) -> NormText:
    """Нижний регистр, ё→е, тире → «-», пробелы схлопнуты; переводы строк сохранены
    (один «\\n» или «\\n\\n» для пустой строки) — по ним парсер брифа режет строки и секции."""
    out: list[str] = []
    idx: list[int] = []
    i, n = 0, len(text)
    while i < n:
        ch = _norm_char(text[i])
        if ch == " " or ch == "\n":
            j, newlines = i, 0
            while j < n and (_norm_char(text[j]) in (" ", "\n")):
                if text[j] == "\n":
                    newlines += 1
                j += 1
            if out:  # ведущие пробелы отбрасываем
                if newlines >= 2:
                    out.extend("\n\n")
                    idx.extend((i, i))
                elif newlines == 1:
                    out.append("\n")
                    idx.append(i)
                else:
                    out.append(" ")
                    idx.append(i)
            i = j
            continue
        out.append(ch)
        idx.append(i)
        i += 1
    return NormText(original=text, norm="".join(out), index=tuple(idx))


def normalize_alias(alias: str) -> str:
    return normalize_text(str(alias)).norm.strip()


def _is_cyrillic_word_end(s: str) -> bool:
    return bool(s) and s[-1] in _CYR


@dataclass(frozen=True)
class Match:
    """Найденная сущность. start/end — в исходном тексте, nstart/nend — в нормализованном."""

    id: str
    start: int
    end: int
    quote: str
    alias: str
    nstart: int
    nend: int
    method: str = "dict"  # dict | fuzzy
    score: float = 100.0


@dataclass(frozen=True)
class _Variant:
    id: str
    alias: str
    allow_suffix: bool
    require_suffix: bool


_TOKEN_RE = re.compile(r"[0-9a-zа-я][0-9a-zа-я+#]*(?:[.\-/][0-9a-zа-я+#]+)*")


class PhraseMatcher:
    """Aho-Corasick по алиасам словаря + границы слова + русские окончания + rapidfuzz для опечаток."""

    def __init__(
        self,
        entries: Iterable[tuple[str, str]],
        *,
        min_stem_len: int = 5,
        max_suffix: int = 3,
        fuzzy: bool = False,
        fuzzy_threshold: float = 90.0,
        fuzzy_min_len: int = 6,
    ) -> None:
        self.max_suffix = max_suffix
        self.fuzzy = fuzzy
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_min_len = fuzzy_min_len
        variants: dict[str, list[_Variant]] = {}
        exact_keys: set[str] = set()
        stems: list[tuple[str, _Variant]] = []
        for alias, ident in entries:
            a = normalize_alias(alias)
            if not a:
                continue
            cyr = _is_cyrillic_word_end(a)
            allow = cyr and len(a) >= min_stem_len
            variants.setdefault(a, []).append(_Variant(ident, a, allow, False))
            exact_keys.add(a)
            # Слово на гласную/ь («кафка», «пермь»): основа без последней буквы + обязательное окончание
            # («кафкой», «перми»). Только для достаточно длинных основ, чтобы не ловить посторонние слова.
            if cyr and a[-1] in _CYR_VOWEL_END and len(a) - 1 >= max(4, min_stem_len - 1):
                stems.append((a[:-1], _Variant(ident, a, True, True)))
        for stem, var in stems:
            if stem not in exact_keys:
                variants.setdefault(stem, []).append(var)
        self._automaton = ahocorasick.Automaton()
        for key, vs in variants.items():
            self._automaton.add_word(key, (key, tuple(vs)))
        self._empty = not variants
        if not self._empty:
            self._automaton.make_automaton()
        fz: dict[str, str] = {}
        for key, vs in variants.items():
            if " " not in key and len(key) >= fuzzy_min_len and not vs[0].require_suffix:
                fz.setdefault(key, vs[0].id)
        self._fuzzy_keys = list(fz)
        self._fuzzy_ids = fz

    def _accept(self, norm: str, start: int, end: int, var: _Variant) -> int | None:
        """Вернуть конец совпадения (с окончанием) или None, если границы слова не сходятся."""
        key = norm[start:end]
        if start > 0 and is_word_char(key[0]) and is_word_char(norm[start - 1]):
            return None
        n = len(norm)
        right_ok = end >= n or not is_word_char(key[-1]) or not is_word_char(norm[end])
        if right_ok and not var.require_suffix:
            return end
        if not var.allow_suffix:
            return None
        j = end
        while j < n and norm[j] in _CYR and j - end <= self.max_suffix:
            j += 1
        suffix_len = j - end
        if suffix_len < 1 or suffix_len > self.max_suffix:
            return None
        if j < n and is_word_char(norm[j]):
            return None
        return j

    def find(self, text: str | NormText, *, fuzzy: bool | None = None) -> list[Match]:
        nt = text if isinstance(text, NormText) else normalize_text(text)
        norm = nt.norm
        cands: list[tuple[int, int, str, str, str, float]] = []
        if not self._empty and norm:
            for end_idx, (key, vs) in self._automaton.iter(norm):
                start = end_idx - len(key) + 1
                for var in vs:
                    end = self._accept(norm, start, end_idx + 1, var)
                    if end is not None:
                        cands.append((start, end, var.id, var.alias, "dict", 100.0))
                        break
        chosen = _resolve_overlaps(cands)
        use_fuzzy = self.fuzzy if fuzzy is None else fuzzy
        if use_fuzzy and self._fuzzy_keys:
            taken = [(s, e) for s, e, *_ in chosen]
            for m in _TOKEN_RE.finditer(norm):
                tok = m.group(0).rstrip(".-/")
                ts, te = m.start(), m.start() + len(tok)
                if len(tok) < self.fuzzy_min_len or tok.isdigit():
                    continue
                if any(s < te and ts < e for s, e in taken):
                    continue
                best = process.extractOne(
                    tok, self._fuzzy_keys, scorer=fuzz.ratio, score_cutoff=self.fuzzy_threshold
                )
                if best is None:
                    continue
                key, score, _ = best
                chosen.append((ts, te, self._fuzzy_ids[key], key, "fuzzy", float(score)))
                taken.append((ts, te))
            chosen.sort(key=lambda c: c[0])
        out: list[Match] = []
        for s, e, ident, alias, method, score in chosen:
            os_, oe = nt.to_original(s, e)
            out.append(Match(ident, os_, oe, nt.original[os_:oe], alias, s, e, method, score))
        return out


def _resolve_overlaps(cands: list[tuple[int, int, str, str, str, float]]):
    """Самое длинное совпадение побеждает («react native» > «react», «gitlab ci» > «gitlab»)."""
    picked: list[tuple[int, int, str, str, str, float]] = []
    for c in sorted(cands, key=lambda c: (-(c[1] - c[0]), c[0])):
        if all(c[1] <= p[0] or p[1] <= c[0] for p in picked):
            picked.append(c)
    picked.sort(key=lambda c: c[0])
    return picked


# --------------------------------------------------------------------------- справочники


@dataclass(frozen=True)
class Skill:
    id: str
    name: str
    category: str
    modules: tuple[str, ...]
    spec_weights: Mapping[str, float]
    aliases: tuple[str, ...]
    untestable: bool
    untestable_reason: str | None


@dataclass(frozen=True)
class Role:
    id: str
    name: str
    aliases: tuple[str, ...]
    spec_weights: Mapping[str, float]


@dataclass(frozen=True)
class City:
    id: str
    name: str
    region: str


@dataclass
class SkillCatalog:
    """Справочник навыков, ролей, софт-скиллов, форматов и городов с готовыми матчерами."""

    data: Mapping[str, Any]
    taxonomy: Mapping[str, Any]
    skills: dict[str, Skill] = field(init=False)
    roles: dict[str, Role] = field(init=False)
    soft_skills: dict[str, str] = field(init=False)
    formats: dict[str, str] = field(init=False)
    cities: dict[str, City] = field(init=False)
    module_names: dict[str, str] = field(init=False)
    spec_names: dict[str, str] = field(init=False)
    spec_status: dict[str, str] = field(init=False)
    errors: list[str] = field(init=False)

    def __post_init__(self) -> None:
        d = self.data
        self.errors = []
        self.module_names = {k: v["name"] for k, v in self.taxonomy["modules"].items()}
        self.spec_names = {s["code"]: s["name"] for s in self.taxonomy["specializations"]}
        self.spec_status = {s["code"]: s.get("status", "active") for s in self.taxonomy["specializations"]}
        cats = d["categories"]
        reasons = d["untestable_reasons"]
        self.skills = {}
        for sid, s in d["skills"].items():
            cat = cats.get(s["cat"])
            if cat is None:
                self.errors.append(f"{sid}: неизвестная категория {s['cat']}")
                cat = {}
            modules = tuple(s.get("modules") or ())
            untestable = bool(s.get("untestable", False))
            reason_code = cat.get("untestable_reason", "tech")
            spec = s.get("spec", cat.get("spec", {})) or {}
            self.skills[sid] = Skill(
                id=sid,
                name=str(s["name"]),
                category=s["cat"],
                modules=modules,
                spec_weights={k: float(v) for k, v in spec.items()},
                aliases=tuple(str(a) for a in s.get("aliases", ())),
                untestable=untestable,
                untestable_reason=reasons.get(reason_code) if untestable else None,
            )
        self.roles = {
            rid: Role(
                rid,
                r["name"],
                tuple(str(a) for a in r["aliases"]),
                {k: float(v) for k, v in (r.get("spec") or {}).items()},
            )
            for rid, r in d["roles"].items()
        }
        self.soft_skills = {k: v["name"] for k, v in d["soft_skills"].items()}
        self.formats = {k: v["name"] for k, v in d["formats"].items()}
        self.cities = {k: City(k, v["name"], v["region"]) for k, v in d["cities"].items()}
        self._validate()
        self._build_matchers()

    # ---------------------------------------------------------------- проверки
    def _validate(self) -> None:
        mods = set(self.module_names)
        specs = set(self.spec_names)
        seen: dict[str, str] = {}
        for s in self.skills.values():
            for m in s.modules:
                if m not in mods:
                    self.errors.append(f"{s.id}: неизвестный модуль {m}")
            if s.untestable == bool(s.modules):
                self.errors.append(f"{s.id}: untestable должен быть true ровно тогда, когда нет модулей")
            for sp in s.spec_weights:
                if sp not in specs:
                    self.errors.append(f"{s.id}: неизвестная специализация {sp}")
            if not s.aliases:
                self.errors.append(f"{s.id}: нет алиасов")
            for a in s.aliases:
                na = normalize_alias(a)
                if na in seen and seen[na] != s.id:
                    self.errors.append(f"алиас «{na}» у {seen[na]} и {s.id}")
                seen[na] = s.id
        for r in self.roles.values():
            for sp in r.spec_weights:
                if sp not in specs:
                    self.errors.append(f"роль {r.id}: неизвестная специализация {sp}")
        for i, cue in enumerate(self.data.get("spec_cues", ())):
            for sp in cue.get("spec", {}):
                if sp not in specs:
                    self.errors.append(f"spec_cues[{i}]: неизвестная специализация {sp}")
        for g in self.data["grade_markers"]:
            if g not in {x["code"] for x in self.taxonomy["grades"]}:
                self.errors.append(f"grade_markers: неизвестный грейд {g}")

    def _build_matchers(self) -> None:
        bp = load_config("scoring").get("brief_parser", {})
        thr = float(bp.get("fuzzy_threshold", 90))
        fmin = int(bp.get("fuzzy_min_len", 6))
        self._skills_m = PhraseMatcher(
            ((a, s.id) for s in self.skills.values() for a in s.aliases),
            min_stem_len=5,
            max_suffix=3,
            fuzzy=True,
            fuzzy_threshold=thr,
            fuzzy_min_len=fmin,
        )
        cue_entries: list[tuple[str, str]] = []
        for r in self.roles.values():
            cue_entries += [(a, f"role:{r.id}") for a in r.aliases]
        self._cue_weights: dict[str, Mapping[str, float]] = {
            f"role:{r.id}": r.spec_weights for r in self.roles.values()
        }
        for i, cue in enumerate(self.data.get("spec_cues", ())):
            cue_entries += [(a, f"cue:{i}") for a in cue["aliases"]]
            self._cue_weights[f"cue:{i}"] = {k: float(v) for k, v in cue["spec"].items()}
        self._cues_m = PhraseMatcher(cue_entries, min_stem_len=5, max_suffix=3)
        self._roles_m = PhraseMatcher(
            ((a, r.id) for r in self.roles.values() for a in r.aliases), min_stem_len=5
        )
        self._soft_m = PhraseMatcher(
            ((a, k) for k, v in self.data["soft_skills"].items() for a in v["aliases"]),
            min_stem_len=5,
            max_suffix=5,
        )
        self._formats_m = PhraseMatcher(
            ((a, k) for k, v in self.data["formats"].items() for a in v["aliases"]),
            min_stem_len=4,
            max_suffix=4,
        )
        self._reloc_m = PhraseMatcher(
            ((a, "relocation") for a in self.data.get("relocation_aliases", ())), min_stem_len=5
        )
        self._cities_m = PhraseMatcher(
            ((a, k) for k, v in self.data["cities"].items() for a in v["aliases"]),
            min_stem_len=4,
            max_suffix=3,
        )
        markers = self.data["markers"]
        self._markers_m = PhraseMatcher(
            [(a, "nice") for a in markers["nice"]] + [(a, "must") for a in markers["must"]],
            min_stem_len=4,
            max_suffix=4,
        )
        self._grade_m = PhraseMatcher(
            ((a, g) for g, aliases in self.data["grade_markers"].items() for a in aliases),
            min_stem_len=4,
            max_suffix=3,
        )

    # ---------------------------------------------------------------- доступ
    def skill(self, skill_id: str) -> Skill:
        return self.skills[skill_id]

    def name(self, skill_id: str) -> str:
        s = self.skills.get(skill_id)
        return s.name if s else skill_id

    def modules_of(self, skill_id: str) -> tuple[str, ...]:
        s = self.skills.get(skill_id)
        return s.modules if s else ()

    def cue_weights(self, cue_id: str) -> Mapping[str, float]:
        return self._cue_weights.get(cue_id, {})

    # ---------------------------------------------------------------- поиск
    def find_skills(self, text: str | NormText, *, fuzzy: bool = True) -> list[Match]:
        return self._skills_m.find(text, fuzzy=fuzzy)

    def find_spec_cues(self, text: str | NormText) -> list[Match]:
        return self._cues_m.find(text)

    def find_roles(self, text: str | NormText) -> list[Match]:
        return self._roles_m.find(text)

    def find_soft_skills(self, text: str | NormText) -> list[Match]:
        return self._soft_m.find(text)

    def find_formats(self, text: str | NormText) -> list[Match]:
        return self._formats_m.find(text)

    def find_relocation(self, text: str | NormText) -> list[Match]:
        return self._reloc_m.find(text)

    def find_cities(self, text: str | NormText) -> list[Match]:
        return self._cities_m.find(text)

    def find_markers(self, text: str | NormText) -> list[Match]:
        return self._markers_m.find(text)

    def find_grade_markers(self, text: str | NormText) -> list[Match]:
        return self._grade_m.find(text)


@lru_cache(maxsize=1)
def get_catalog() -> SkillCatalog:
    cat = SkillCatalog(load_config("skills"), load_config("taxonomy"))
    if cat.errors:
        raise ValueError("config/skills.yaml: " + "; ".join(cat.errors[:10]))
    return cat


def token_starts(norm: str) -> list[int]:
    """Начала токенов нормализованного текста (для окна ±N токенов)."""
    return [m.start() for m in _TOKEN_RE.finditer(norm)]


def token_index(starts: list[int], pos: int) -> int:
    """Номер токена, в котором (или перед которым) стоит позиция pos."""
    return max(0, bisect_right(starts, pos) - 1)


TOKEN_RE = _TOKEN_RE
