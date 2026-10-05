"""qa_testing, моки: unittest.mock — side_effect, учёт вызовов, порядок аргументов при стопке patch.

Ключ — исполнение ПОКАЗАННОГО кода настоящей библиотекой unittest.mock (имена Mock, patch, call подставлены в
пространство имён, импорты в листинге опущены); дистракторы — исполнение того же кода с типичным заблуждением.
Двойник в буфере меняет одно число в показанном коде (families/_scenario.py).
"""

from __future__ import annotations

import builtins
import io
import random
import re
from typing import Any
from unittest.mock import Mock, call, patch

from app.testing.bank.base import register
from app.testing.bank.families.qa._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    ScenarioFamily,
)


def run_mock_code(code: str) -> str:
    """Исполнить код с Mock, patch и call; вернуть stdout (print перехватывается подменой в глобалах, как в exec_py)."""
    buf = io.StringIO()

    def _print(*args: Any, sep: str | None = " ", end: str | None = "\n", file: Any = None, flush: bool = False) -> None:
        if file is not None:
            builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
            return
        buf.write((" " if sep is None else sep).join(map(str, args)) + ("\n" if end is None else end))

    ns: dict[str, Any] = {"__name__": "__qa_mock__", "print": _print, "Mock": Mock, "patch": patch, "call": call}
    try:
        exec(compile(code, "<qa-mock>", "exec", dont_inherit=True), ns)  # noqa: S102 — только наш код
    except Exception as e:  # noqa: BLE001 — любое исключение = негодный сценарий (или мутант, который падает)
        raise ScenarioError(f"{type(e).__name__}: {e}") from e
    return buf.getvalue().rstrip("\n")


_SERVICES = ["notify_all", "send_reminders", "alert_users", "ping_clients"]
_TEXTS = ["hi", "ok", "bye", "news", "alert"]
_STORES = [("Store", "stock", "price"), ("Shop", "balance", "fee"), ("Depot", "left", "cost")]


@register
class MockCalls(ScenarioFamily):
    id = "qa.mock.calls"
    title = "Моки: side_effect, учёт вызовов и порядок аргументов patch"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "senior"
    position = -0.5
    time_limit_s = 150
    answer_kind = "output"
    language = "text"
    measures = (
        "unittest.mock: side_effect-список выдаётся по очереди, call_count и call_args_list считают вызовы, call_args — "
        "последний вызов, при стопке @patch первым аргументом идёт НИЖНИЙ декоратор, код после раннего return не "
        "выполняется."
    )
    misconceptions = {
        "return_value_instead": "список в side_effect выдаётся по одному элементу на вызов; если присвоить его в "
        "return_value, каждый вызов вернёт весь список (он «истинный»)",
        "zero_based_slip": "call_args_list нумеруется с нуля, а call_args — это последний вызов, а не первый",
        "top_down_order": "при стопке @patch в тест передаются моки снизу вверх: ближайший к функции декоратор — "
        "первый аргумент",
        "no_short_circuit": "после раннего return остальной код не выполняется: мок цены остаётся без вызовов, "
        "если остатка не хватило",
    }
    answer_hint = (
        "Введите вывод программы построчно (или через пробел); вызов печатается так, как его показывает mock: "
        "call(102, 'hi')."
    )

    def make(self, rng: random.Random) -> Scenario:
        return self._side_effect(rng) if rng.random() < 0.5 else self._patch_stack(rng)

    # --- шаблон A: side_effect и учёт вызовов
    def _side_effect(self, rng: random.Random) -> Scenario:
        fn = rng.choice(_SERVICES)
        n = rng.randint(3, 5)
        ids = sorted(rng.sample(range(100, 999), n))
        effects = [rng.random() < 0.5 for _ in range(n)]
        if all(effects) or not any(effects):
            raise GenerationRetry
        text = rng.choice(_TEXTS)
        pick = rng.choice(["list", "last"])
        k = rng.randint(1, n - 2)
        show = f"mailer.send.call_args_list[{k}]" if pick == "list" else "mailer.send.call_args"
        code = (
            f"def {fn}(mailer, users, text):\n"
            "    sent = 0\n"
            "    for user in users:\n"
            "        if mailer.send(user, text):\n"
            "            sent += 1\n"
            "    return sent\n\n\n"
            "mailer = Mock()\n"
            f"mailer.send.side_effect = {effects}\n"
            f'print({fn}(mailer, {ids}, "{text}"))\n'
            "print(mailer.send.call_count)\n"
            f"print({show})"
        )
        return Scenario(code, {"kind": "side_effect", "pick": pick})

    # --- шаблон Б: стопка patch.object, порядок аргументов и ранний возврат
    def _patch_stack(self, rng: random.Random) -> Scenario:
        cls, a, b = rng.choice(_STORES)
        stock = rng.randint(1, 8)
        qty = rng.randint(2, 7)
        price = rng.randint(2, 19)
        sku = f"SKU-{rng.randint(1000, 9999)}"
        code = (
            f"class {cls}:\n"
            f"    def {a}(self, sku): ...\n"
            f"    def {b}(self, sku): ...\n\n\n"
            "def order_total(store, sku, qty):\n"
            f"    if store.{a}(sku) < qty:\n"
            "        return 0\n"
            f"    return store.{b}(sku) * qty\n\n\n"
            f'@patch.object({cls}, "{a}", return_value={stock})\n'
            f'@patch.object({cls}, "{b}", return_value={price})\n'
            "def test_order(first_mock, second_mock):\n"
            f'    print(order_total({cls}(), "{sku}", {qty}))\n'
            "    print(first_mock.call_count, second_mock.call_count)\n"
            "    print(first_mock.call_args, second_mock.call_args)\n\n\n"
            "test_order()"
        )
        return Scenario(code, {"kind": "patch_stack", "a": a, "b": b})

    def prompt(self, sc: Scenario) -> str:
        return (
            "Код выполняют целиком (импорт Mock, patch и call из unittest.mock в листинге опущен). Что он напечатает? "
            "Исполняется настоящая библиотека mock."
        )

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        data = sc.data
        if mutant == "return_value_instead":
            if data["kind"] != "side_effect":
                return None
            code = code.replace(".side_effect =", ".return_value =")
        elif mutant == "zero_based_slip":
            if data["kind"] != "side_effect":
                return None
            if data["pick"] == "list":
                code = re.sub(r"call_args_list\[(\d+)\]", lambda m: f"call_args_list[{int(m.group(1)) + 1}]", code)
            else:
                code = code.replace("print(mailer.send.call_args)", "print(mailer.send.call_args_list[0])")
        elif mutant == "top_down_order":
            if data["kind"] != "patch_stack":
                return None
            code = code.replace("(first_mock, second_mock)", "(second_mock, first_mock)")
        elif mutant == "no_short_circuit":
            if data["kind"] != "patch_stack":
                return None
            a, b = data["a"], data["b"]
            code = code.replace(
                f"    if store.{a}(sku) < qty:\n        return 0\n    return store.{b}(sku) * qty",
                f"    have, price = store.{a}(sku), store.{b}(sku)\n    if have < qty:\n        return 0\n"
                "    return price * qty",
            )
        try:
            out = run_mock_code(code)
        except ScenarioError:
            if mutant is None:
                raise
            return None  # мутант не исполняется — как ответ не годится
        return " ".join(out.split())

    def why(self, sc: Scenario, raw_key: str) -> str:
        if sc.data["kind"] == "side_effect":
            return (
                "side_effect-список отдаёт по одному элементу на вызов, поэтому «отправлено» считает только True. "
                "call_count — число вызовов, call_args_list нумеруется с нуля, call_args — последний вызов. "
                f"Вывод: {raw_key}."
            )
        return (
            "@patch.object-декораторы применяются снизу вверх: первый аргумент теста — мок ближайшего к функции "
            "декоратора. Код после раннего return не выполняется, поэтому у мока цены может быть 0 вызовов. "
            f"Вывод: {raw_key}."
        )
