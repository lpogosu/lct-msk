"""Общие помощники тестов антиподсказчика (тестов здесь нет): крошечное семейство и сборка экземпляров."""

from __future__ import annotations

from app.testing.bank.base import Family, Instance, Rendered
from app.testing.bank.execution import run_js, run_python

SPEC_CODE = (
    "def f(nums):\n"
    "    total = 0\n"
    "    for x in nums:\n"
    "        if x % 3 == 0:\n"
    "            total += x * 2\n"
    "    return total\n"
    "print(f([3, 9, 4]))"
)
PRICES_CODE = 'prices = {"a": 120, "b": 340, "c": 560, "d": 780}\nprint(sum(prices.values()))'

ORDERS_COLUMNS = ["id", "customer", "status", "amount"]
ORDERS_ROWS = [
    [1, "Анна", "paid", 1200], [2, "Борис", "cancelled", 3000], [3, "Анна", "paid", 800],
    [4, "Вера", "paid", 3200], [5, "Борис", "paid", 1500], [6, "Вера", "cancelled", 700],
    [7, "Борис", "paid", 900],
]
ORDERS_QUERY = (
    "SELECT customer, SUM(amount) AS total\nFROM orders\nWHERE status = 'paid'\nGROUP BY customer\n"
    "HAVING COUNT(*) >= 2\nORDER BY total DESC\nLIMIT 1;"
)


class TinyFamily(Family):
    """Семейство для тестов: не регистрируется в реестре, экземпляры собираются руками."""

    id = "test.tiny"
    title = "Тестовое семейство"
    specs = ("backend_python",)
    module = "test"
    format = "predict_output"
    target_grade = "junior"

    def generate(self, seed: int) -> Instance:  # pragma: no cover
        raise NotImplementedError


def code_instance(
    code: str, language: str = "python", distractors: dict[str, str] | None = None
) -> tuple[TinyFamily, Instance]:
    """Экземпляр с кодом; ключ — результат исполнения (run_python / run_js), не введён руками."""
    fam = TinyFamily()
    raw = run_python(code) if language == "python" else run_js(code)
    inst = Instance(
        fam.id,
        0,
        Rendered(prompt="Что напечатает программа?", code=code, language=language, answer_mode="text"),
        fam.normalize(raw),
        distractors or {},
    )
    return fam, inst


def orders_rendered() -> Rendered:
    return Rendered(
        prompt="Дана таблица orders. Что вернёт запрос?",
        code=ORDERS_QUERY,
        language="sql",
        table={"name": "orders", "columns": list(ORDERS_COLUMNS), "rows": [list(r) for r in ORDERS_ROWS]},
        answer_mode="text",
    )
