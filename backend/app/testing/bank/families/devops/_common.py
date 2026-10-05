"""Общие помощники пакета devops (spec-testing §3.8).

Ключи считаются точными решателями по ПОКАЗАННОМУ тексту (families/_scenario.py): модель кеша слоёв Docker, правила
nginx, cron по vixie-cron, Kubernetes по документации, YAML — настоящим парсером (PyYAML), bash — моделью, сверенной
с настоящим bash в tests/test_bank_devops.py. Руками ключ не вводится. Дистракторы — результат того же решателя с
включённым типичным заблуждением (мутант), с тегом.
"""

from __future__ import annotations

import random
from abc import abstractmethod
from typing import Any

import yaml

from app.testing.bank.families._scenario import (
    NORMALIZERS_EXT,
    Scenario,
    ScenarioError,
    ScenarioFamily,
)
from app.testing.bank.families.backend_python._common import (
    BankFamily,
    GenerationRetry,
    plural,
    tpl,
)

SPECS: tuple[str, ...] = ("devops",)
MODULE = "devops_core"

# Любая ошибка разбора ПОКАЗАННОГО (возможно, изменённого двойником) текста — это отбраковка кандидата, а не сбой.
_PARSE_ERRORS = (ValueError, KeyError, IndexError, TypeError, AttributeError, ZeroDivisionError, yaml.YAMLError)


class OpsFamily(ScenarioFamily):
    """Семейство DevOps: решатель `solve_impl` может падать на любом мусоре — наружу выходит только ScenarioError."""

    @abstractmethod
    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None: ...

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        try:
            return self.solve_impl(sc, code, mutant)
        except ScenarioError:
            raise
        except _PARSE_ERRORS as e:
            raise ScenarioError(f"{type(e).__name__}: {e}") from e


def numeric_alternatives(
    old: str,
    rng: random.Random,
    p: Any,
    lo: int,
    hi: int,
    *,
    width: int | None = None,
    n: int = 6,
) -> list[str]:
    """Новые значения числового токена в [lo, hi]: длина ±max_length_delta, без путаемых глифов (гейт 6).

    width — фиксированная ширина с ведущими нулями («09», «041»): такие токены общий мутатор не трогает (ведущий ноль).
    """
    from app.testing.anticheat import literals as lit  # noqa: PLC0415 — отложенный импорт, как в _scenario

    out: list[str] = []
    for _ in range(80):
        s = str(rng.randint(lo, hi))
        if width:
            s = s.zfill(width)
        if s == old or s in out:
            continue
        if abs(len(s) - len(old)) > p.max_length_delta or not lit.glyph_ok(old, s, p.confusable_pairs):
            continue
        out.append(s)
        if len(out) >= n:
            break
    return out


__all__ = [
    "MODULE",
    "NORMALIZERS_EXT",
    "SPECS",
    "BankFamily",
    "GenerationRetry",
    "OpsFamily",
    "Scenario",
    "ScenarioError",
    "ScenarioFamily",
    "numeric_alternatives",
    "plural",
    "tpl",
]
