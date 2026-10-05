"""Общие помощники валидации: сиды, реальный банк, грейды.

Сиды: у каждого потока свой генератор `random.Random("<seed>:<поток>")` — добавление эксперимента
не сдвигает остальные, а результат не зависит от PYTHONHASHSEED (строковый сид хешируется sha512).
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from datetime import date
from functools import lru_cache

from app.testing.bank.registry import all_families
from app.testing.cat import FamilyMeta, load_blueprint, meta_from_family
from app.testing.irt import BELOW_INTERN, Scale, load_scale

MASTER_SEED = 20261005
# Дата «сейчас» для синтетики (как у генератора демо-реестра ФСП): данные не зависят от сегодняшней даты.
REF_DATE = date(2026, 10, 5)
TESTED_SPECS: tuple[str, ...] = ("backend_python", "frontend_js")


def stream(name: str, seed: int = MASTER_SEED) -> random.Random:
    """Независимый детерминированный генератор для потока `name`."""
    return random.Random(f"{seed}:{name}")


@lru_cache(maxsize=1)
def real_bank() -> tuple[FamilyMeta, ...]:
    """Реальный банк: все зарегистрированные семейства с экспертными приорами (контракт bank/base.py)."""
    fams = all_families()
    return tuple(meta_from_family(fams[fid]) for fid in sorted(fams))


def bank_for(spec: str, bank: Sequence[FamilyMeta] | None = None) -> list[FamilyMeta]:
    """Семейства шкалы spec (общие модули входят в обе специализации)."""
    bp = load_blueprint(spec)
    return [f for f in (bank if bank is not None else real_bank()) if spec in f.specs and f.module in bp]


def grade_index(grade: str | None, scale: Scale | None = None) -> int:
    """Порядковый номер грейда: «Ниже Стажёра» и «не подтверждён» = −1, Стажёр = 0, …, Senior = 3."""
    scale = scale or load_scale()
    if grade is None or grade == BELOW_INTERN:
        return -1
    return scale.grades.index(grade)


def grade_from_index(i: int, scale: Scale | None = None) -> str:
    scale = scale or load_scale()
    return BELOW_INTERN if i < 0 else scale.grades[min(i, len(scale.grades) - 1)]


def grade_band(grade: str, scale: Scale | None = None) -> tuple[float, float]:
    """Полоса уровня грейда [нижний порог; верхний порог). Для крайних — разумные границы шкалы."""
    scale = scale or load_scale()
    if grade == BELOW_INTERN:
        lo = scale.cut(scale.grades[0])
        return lo - 1.0, lo
    lo = scale.cut(grade)
    nxt = scale.next_grade(grade)
    hi = scale.cut(nxt) if nxt is not None else lo + 1.2
    return lo, hi


def quotas_for_length(blueprint: dict[str, float], length: int) -> dict[str, int]:
    """Квоты модулей для формы фиксированной длины (метод наибольших остатков)."""
    raw = {m: q * length for m, q in blueprint.items()}
    out = {m: int(v) for m, v in raw.items()}
    rest = length - sum(out.values())
    for m in sorted(raw, key=lambda m: (-(raw[m] - out[m]), m))[:rest]:
        out[m] += 1
    return out
