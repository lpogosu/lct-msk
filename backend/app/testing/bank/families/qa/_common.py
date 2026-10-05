"""Общие помощники пакета qa (spec-testing §3.8).

Ключи считаются только исполнением или точным решателем по ПОКАЗАННОМУ тексту (см. families/_scenario.py):
- тесты pytest исполняет мини-раннер `_minipytest` (сверяется с настоящим pytest в tests/test_bank_qa.py);
- покрытие ветвей считает трассировщик `_branch` по AST (сверяется с независимым подсчётом в тесте);
- SQL — sqlite в памяти (execution.run_sql); проверки HTTP-контракта — исполнение Python.
Дистракторы — результат того же решателя с включённым типичным заблуждением (мутант), с тегом.
"""

from __future__ import annotations

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

SPECS: tuple[str, ...] = ("qa",)
MODULE = "qa_testing"

__all__ = [
    "MODULE",
    "NORMALIZERS_EXT",
    "SPECS",
    "BankFamily",
    "GenerationRetry",
    "Scenario",
    "ScenarioError",
    "ScenarioFamily",
    "plural",
    "tpl",
]
