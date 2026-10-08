"""Общие помощники пакета skills: семейства «навык внутри модуля» для проверочного набора по вакансии.

Зачем. Стандартный тест категории меряет модули (Python, SQL, HTTP …). Redis, очереди сообщений, FastAPI, особенности
PostgreSQL он внутри модуля не наблюдает, а резюме в Score не входит. Эти семейства делают навыки НАБЛЮДАЕМЫМИ короткой
задачей в проверочном наборе (DECISIONS п.3, docs/MATCHING.md «Навыки, наблюдаемые через проверочный набор»).
В стандартный тест они не попадают: их модули (redis_core, mq_core, fastapi_core, pg_core) не входят ни в один blueprint
(config/taxonomy.yaml), поэтому CAT их не выбирает и грейд они не меняют.

Ключ — всегда результат исполнения или точной модели по ПОКАЗАННОМУ тексту (families/_scenario.py): реальный FastAPI
через TestClient, sqlite в памяти для SQL-семантики, совпадающей с PostgreSQL, эмуляторы Redis и брокеров с документированными
правилами. Дистрактор — результат того же решателя с включённым типичным заблуждением (мутант), с тегом.
"""

from __future__ import annotations

import re
from abc import abstractmethod
from collections.abc import Iterable
from typing import Any, ClassVar

from app.testing.bank.families._scenario import (
    NORMALIZERS_EXT,
    Scenario,
    ScenarioError,
    ScenarioFamily,
)
from app.testing.bank.families.backend_python._common import GenerationRetry, norm_output, plural, tpl
from app.testing.bank.families.devops._common import numeric_alternatives

SPECS: tuple[str, ...] = ("backend_python",)

_WS = re.compile(r"\s+")
_LOOKALIKE = str.maketrans("АВЕКМНОРСТУХ", "ABEKMHOPCTYX")


def norm_tokens(answer: Any) -> Any:
    """Слова-токены ответа (OK, ERR, имена клиентов, числа): регистр не важен, запятые и «;» — пробелы, кириллические двойники → латиница."""
    if isinstance(answer, list | tuple):
        answer = " ".join(str(x) for x in answer)
    if not isinstance(answer, str):
        return answer
    s = answer.translate(_LOOKALIKE).replace(",", " ").replace(";", " ")
    return _WS.sub(" ", s.strip()).upper()


class SkillFamily(ScenarioFamily):
    """Семейство навыка: решатель `solve_impl` может падать на любом мусоре — наружу выходит только ScenarioError.

    twin_patterns — регулярные выражения, у которых группа 1 — числовой литерал, допустимый для «двойника в буфере»
    (длительности, лимиты, количества; но не имена ключей и не номера строк). По умолчанию двойник чисел не делает."""

    twin_patterns: ClassVar[tuple[str, ...]] = ()
    twin_range: ClassVar[tuple[int, int]] = (2, 120)

    @abstractmethod
    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None: ...

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        try:
            return self.solve_impl(sc, code, mutant)
        except ScenarioError:
            raise
        except Exception as e:  # noqa: BLE001 — любой мусор в показанном (изменённом двойником) тексте — отбраковка, не сбой
            raise ScenarioError(f"{type(e).__name__}: {e}") from e

    def normalize(self, answer: Any) -> Any:
        if self.answer_kind == "tokens":
            return norm_tokens(answer)
        return NORMALIZERS_EXT[self.answer_kind](answer)

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for pat in self.twin_patterns:
            spans += [m.span(1) for m in re.finditer(pat, code, re.M)]
        return sorted(set(spans))

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: Any, p: Any) -> list[str]:
        old = code[span[0] : span[1]]
        if not old.isdigit() or old.startswith("0"):
            return []
        lo, hi = self.twin_range
        return numeric_alternatives(old, rng, p, lo, max(hi, int(old) * 2))


__all__ = [
    "SPECS",
    "GenerationRetry",
    "Scenario",
    "ScenarioError",
    "SkillFamily",
    "norm_output",
    "norm_tokens",
    "plural",
    "tpl",
]


def join_ru(items: Iterable[str]) -> str:
    return ", ".join(items)
