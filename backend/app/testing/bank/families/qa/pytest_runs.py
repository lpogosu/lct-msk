"""qa_testing, прогоны pytest: какие тесты упадут, параметризация, порядок фикстур.

Ключ — исполнение ПОКАЗАННОГО модуля тестов мини-раннером (qa/_minipytest.py; сверяется с настоящим pytest в
tests/test_bank_qa.py). Дистракторы — тот же раннер с включённой моделью заблуждения (остановка на первом падении,
один assert на тест, перемножение/сложение параметров, порядок teardown…).
"""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import register
from app.testing.bank.families.qa._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    ScenarioFamily,
    plural,
)
from app.testing.bank.families.qa._minipytest import MiniPytestError, run_tests
from app.testing.bank.families.qa._suts import SUTS, Sut, lit


def _run(code: str, **kw: Any):
    try:
        return run_tests(code, **kw)
    except MiniPytestError as e:
        raise ScenarioError(str(e)) from e


def _actual(src: str, fn: str, args: tuple[Any, ...]) -> Any:
    ns: dict[str, Any] = {}
    exec(compile(src, "<sut>", "exec", dont_inherit=True), ns)  # noqa: S102 — только наш код
    return ns[fn](*args)


def _corrupt(rng: random.Random, value: Any) -> Any:
    """Неверное «ожидаемое» значение того же вида (тест написан с ошибкой)."""
    if isinstance(value, bool):
        return not value
    if isinstance(value, int):
        return value + rng.choice([-30, -10, -5, -2, -1, 1, 2, 5, 10, 50, 100])
    if isinstance(value, str):
        pool = [v for v in ("weak", "medium", "strong", "pass", "fail") if v != value]
        same = [v for v in pool if (v in ("pass", "fail")) == (value in ("pass", "fail"))]
        return rng.choice(same)
    raise GenerationRetry


# ============================================================== Стажёр: какие тесты упадут


@register
class PytestFailedNames(ScenarioFamily):
    id = "qa.pytest.failed"
    title = "pytest: какие тесты упадут"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "intern"
    position = 0.6
    time_limit_s = 120
    answer_kind = "latin"
    # «text», а не «python»: листинг — тестовый модуль без вывода; общий мутатор двойников не может его исполнить как
    # программу (ключ даёт раннер), двойника строит само семейство (families/_scenario.py)
    language = "text"
    measures = (
        "Исполнение тестов «в уме»: каждый тест независим, pytest не останавливается на первом падении, "
        "в тесте с несколькими assert первый ложный останавливает тест."
    )
    misconceptions = {
        "stop_at_first_failure": "pytest не останавливается на первом упавшем тесте (без ключа -x): "
        "каждый тест запускается и получает свой статус",
        "first_assert_only": "тест падает, если ложен ЛЮБОЙ его assert, а не только первый",
        "last_assert_only": "первый ложный assert уже роняет тест: остальные assert не спасают, "
        "даже если последний верен",
        "passed_instead": "спрашивают про упавшие тесты (FAILED), а не про прошедшие",
    }
    answer_hint = "Введите имена упавших тестов через пробел в порядке запуска (как в файле)."

    def make(self, rng: random.Random) -> Scenario:
        sut = rng.choice(SUTS)
        src, fn, consts = sut.source(rng)
        n_tests = rng.randint(4, min(6, len(sut.cases)))
        cases = rng.sample(sut.cases, n_tests)
        n_fail = rng.randint(1, 3)
        failing = set(rng.sample(range(n_tests), n_fail))
        multi = rng.randrange(n_tests)  # тест с двумя assert
        multi_bad = rng.randrange(2)  # какой из двух assert испорчен
        blocks = []
        for i, case in enumerate(cases):
            n_asserts = 2 if i == multi else 1
            lines = []
            for k in range(n_asserts):
                args = sut.args(rng, consts)
                actual = _actual(src, fn, args)
                # в multi-тесте портим либо первый, либо второй assert; в остальных — единственный
                bad = i in failing and (n_asserts == 1 or k == multi_bad)
                expected = _corrupt(rng, actual) if bad else actual
                lines.append(f"    assert {fn}({', '.join(lit(a) for a in args)}) == {lit(expected)}")
            blocks.append(f"def test_{fn}_{case}():\n" + "\n".join(lines))
        code = src + "\n\n\n" + "\n\n\n".join(blocks)
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Тесты запускают командой pytest без дополнительных ключей. Какие из них завершатся со статусом FAILED? "
            "Помните: каждый тест запускается независимо."
        )

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        kw: dict[str, Any] = {}
        if mutant == "stop_at_first_failure":
            kw["stop_on_first_failure"] = True
        elif mutant == "first_assert_only":
            kw["keep_asserts"] = "first"
        elif mutant == "last_assert_only":
            kw["keep_asserts"] = "last"
        res = _run(code, **kw)
        names = res.names("passed" if mutant == "passed_instead" else "failed")
        return " ".join(names) if names else None

    def why(self, sc: Scenario, raw_key: str) -> str:
        res = _run(sc.code)
        n_fail, n_ok = len(res.names("failed")), len(res.names("passed"))
        return (
            "pytest запускает каждый тест отдельно: падение одного не отменяет остальные. В тесте с несколькими assert "
            f"выполнение прекращается на первом ложном. Итог: упало {plural(n_fail, 'тест', 'теста', 'тестов')}, "
            f"прошло {plural(n_ok, 'тест', 'теста', 'тестов')}."
        )


# ============================================================== Junior: параметризация


def _par_ids(items: list[str]) -> str:
    return " ".join(items)


@register
class PytestParametrize(ScenarioFamily):
    id = "qa.pytest.parametrize"
    title = "pytest.mark.parametrize: сколько запусков и какие упадут"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "junior"
    position = 0.3
    time_limit_s = 150
    answer_kind = "latin"
    # «text», а не «python»: листинг — тестовый модуль без вывода; общий мутатор двойников не может его исполнить как
    # программу (ключ даёт раннер), двойника строит само семейство (families/_scenario.py)
    language = "text"
    measures = (
        "Раскрытие параметризации: стопка декораторов перемножается, id собирается от нижнего декоратора к верхнему, "
        "skip остаётся в числе запусков, xfail-падение не считается упавшим."
    )
    misconceptions = {
        "sum_not_product": "параметризующие декораторы перемножаются: например, 3 значения × 2 значения = 6 запусков, "
        "а не 5",
        "id_top_first": "в id значения идут от нижнего (ближайшего к функции) декоратора к верхнему",
        "skip_not_collected": "тест с меткой skip pytest собирает и считает в итоге (он будет skipped), "
        "просто не выполняет",
        "xfail_as_failed": "упавший xfail-тест получает статус xfailed и не считается упавшим",
    }
    answer_hint = (
        "Введите число собранных запусков (включая пропущенные) и через пробел id упавших в порядке запуска, "
        "например: 6 test_cost[100-2]."
    )

    def make(self, rng: random.Random) -> Scenario:
        sut: Sut = rng.choice([s for s in SUTS if s.key in ("discount", "shipping", "fee", "tax", "pages")])
        src, fn, consts = sut.source(rng)
        if rng.random() < 0.5:
            return self._stacked(rng, sut, src, fn, consts)
        return self._tuples(rng, sut, src, fn, consts)

    def _stacked(self, rng: random.Random, sut: Sut, src: str, fn: str, consts: dict[str, Any]) -> Scenario:
        if sut.key not in ("discount", "shipping"):
            raise GenerationRetry
        # значения параметров: у порогов, без повторов
        if sut.key == "discount":
            qty_vals = sorted(rng.sample(range(1, consts["t1"] + 6), rng.randint(2, 4)))
            price_vals = sorted(rng.sample(range(100, 1200, 50), rng.randint(2, 3)))
            top_name, top_vals = "qty", qty_vals
            bottom_name, bottom_vals = "price", price_vals
            call = f"{fn}(price, qty)"
        else:
            w_vals = sorted(rng.sample([1, 3, consts["w1"], consts["w1"] + 2, consts["w1"] + 9, consts["w1"] + 15],
                                       rng.randint(2, 4)))
            top_name, top_vals = "weight", w_vals
            bottom_name, bottom_vals = "express", [False, True]
            call = f"{fn}(weight, express)"
        # порог подбираем по реально вычисленным значениям: упасть должно 1–4 сочетаний
        outs = []
        for b in bottom_vals:
            for t in top_vals:
                args = (b, t) if sut.key == "discount" else (t, b)
                outs.append(_actual(src, fn, args))
        uniq = sorted(set(outs))
        if len(uniq) < 4:
            raise GenerationRetry
        threshold = rng.choice(uniq[len(uniq) // 2 : -1])
        op = "<="
        n_fail = sum(1 for o in outs if o > threshold)
        if not 1 <= n_fail <= 4:
            raise GenerationRetry
        code = (
            f"{src}\n\n\n"
            f"@pytest.mark.parametrize(\"{top_name}\", {lit(top_vals)})\n"
            f"@pytest.mark.parametrize(\"{bottom_name}\", {lit(bottom_vals)})\n"
            f"def test_{fn}_limit({bottom_name}, {top_name}):\n"
            f"    assert {call} {op} {threshold}"
        )
        return Scenario(code, {"mode": "stacked", "n_top": len(top_vals), "n_bottom": len(bottom_vals)})

    def _tuples(self, rng: random.Random, sut: Sut, src: str, fn: str, consts: dict[str, Any]) -> Scenario:
        rows = []
        for _ in range(rng.randint(4, 6)):
            args = sut.args(rng, consts)
            rows.append((args, _actual(src, fn, args)))
        # без повторов аргументов (id различаются)
        if len({r[0] for r in rows}) != len(rows):
            raise GenerationRetry
        n = len(rows)
        skip_i, xfail_i = rng.sample(range(n), 2)
        bad = set(rng.sample([i for i in range(n) if i not in (skip_i, xfail_i)], rng.randint(1, 2))) | {xfail_i}
        argnames = {"discount": "price, qty", "shipping": "weight, express", "fee": "amount", "tax": "amount",
                    "pages": "total"}[sut.key]
        lines = []
        for i, (args, actual) in enumerate(rows):
            expected = _corrupt(rng, actual) if i in bad else actual
            tup = ", ".join([*(lit(a) for a in args), lit(expected)])
            if i == skip_i:
                lines.append(f"    pytest.param({tup}, marks=pytest.mark.skip(reason=\"ждём правку\")),")
            elif i == xfail_i:
                lines.append(f"    pytest.param({tup}, marks=pytest.mark.xfail(reason=\"известный дефект\")),")
            else:
                lines.append(f"    ({tup}),")
        call_args = argnames
        code = (
            f"{src}\n\n\n"
            f"@pytest.mark.parametrize(\"{argnames}, expected\", [\n" + "\n".join(lines) + "\n])\n"
            f"def test_{fn}_cases({call_args}, expected):\n"
            f"    assert {fn}({call_args}) == expected"
        )
        return Scenario(code, {"mode": "tuples"})

    def prompt(self, sc: Scenario) -> str:
        return (
            "Тестовый модуль запускают командой pytest (импорт pytest в файле есть, в листинге его нет). Сколько запусков "
            "теста соберёт pytest (в том числе пропущенных) и какие из них упадут?"
        )

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        res = _run(code)
        stacked = sc.data["mode"] == "stacked"
        total = len(res.items)
        failed = res.names("failed")
        if mutant == "sum_not_product":
            if not stacked:
                return None
            total = sc.data["n_top"] + sc.data["n_bottom"]
        elif mutant == "id_top_first":
            if not stacked:
                return None
            failed = [re.sub(r"\[(.*)\]", lambda m: "[" + "-".join(reversed(m.group(1).split("-"))) + "]", f)
                      for f in failed]
        elif mutant == "skip_not_collected":
            if "skipped" not in res.counts():
                return None
            total -= res.counts()["skipped"]
        elif mutant == "xfail_as_failed":
            if "xfailed" not in res.counts():
                return None
            failed = [it.nodeid for it in res.items if it.outcome in ("failed", "xfailed")]
        return " ".join([str(total), *failed])

    def why(self, sc: Scenario, raw_key: str) -> str:
        res = _run(sc.code)
        c = res.counts()
        extra = ""
        if "skipped" in c or "xfailed" in c:
            extra = (" Метка skip не убирает тест из сборки (он станет skipped); упавший xfail-тест получает статус "
                     "xfailed и в упавшие не попадает.")
        if sc.data["mode"] == "stacked":
            how = (f"Стопка декораторов перемножается: {sc.data['n_top']} × {sc.data['n_bottom']} = "
                   f"{sc.data['n_top'] * sc.data['n_bottom']} запусков; в id значения идут от нижнего декоратора "
                   "к верхнему, а верхний меняется быстрее.")
        else:
            how = "Каждая строка списка параметров — отдельный запуск; id собирается из значений строки через «-»."
        return f"{how}{extra} Итого собрано {len(res.items)}, упало: {c.get('failed', 0)}."


# ============================================================== Senior: порядок установки и разбора фикстур

_FX_NAMES = ["db", "cache", "conn", "queue", "client", "session", "storage", "broker", "tmpdir", "token"]
_TEST_CASES = ["create", "read", "update", "delete", "list", "search", "export", "sync"]


@register
class FixtureOrder(ScenarioFamily):
    id = "qa.fixture.order"
    title = "pytest: порядок установки и разбора фикстур"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "senior"
    position = -0.3
    time_limit_s = 180
    enemies = ("qa.flaky.diagnose",)  # шаблон флаки с module-фикстурой подсказывает правило областей видимости
    answer_kind = "latin"
    # «text», а не «python»: листинг — тестовый модуль без вывода; общий мутатор двойников не может его исполнить как
    # программу (ключ даёт раннер), двойника строит само семейство (families/_scenario.py)
    language = "text"
    measures = (
        "Жизненный цикл фикстур: области видимости (module устанавливается раньше function), autouse, "
        "зависимости, teardown в обратном порядке, module-teardown после последнего теста."
    )
    misconceptions = {
        "no_scope_sort": "фикстуры более широкой области (module) pytest устанавливает раньше фикстур function — "
        "даже если в сигнатуре теста они стоят позже",
        "fifo_teardown": "разбор идёт в обратном порядке установки: последняя установленная фикстура "
        "закрывается первой",
        "no_module_cache": "фикстура с scope=\"module\" создаётся один раз на файл и живёт до конца модуля",
        "module_teardown_after_last_use": "module-фикстура закрывается в конце модуля, а не сразу после последнего "
        "теста, который её использует",
    }
    answer_hint = (
        "Введите всё, что напечатает `pytest -s`, по порядку: слова-события через пробел или с новой строки "
        "(print из фикстур и тестов; значки прогресса не пишите)."
    )

    def make(self, rng: random.Random) -> Scenario:
        names = rng.sample(_FX_NAMES, 4)
        mod1, mod2, fun1, aut = names[0], names[1], names[2], "audit" if rng.random() < 0.5 else "trace"
        vals = {n: rng.randint(10, 99) * 10 + rng.randint(1, 9) for n in names}

        def fx(name: str, *, scope: str | None = None, autouse: bool = False, deps: tuple[str, ...] = ()) -> str:
            dec_args = []
            if scope:
                dec_args.append(f'scope="{scope}"')
            if autouse:
                dec_args.append("autouse=True")
            dec = f"@pytest.fixture({', '.join(dec_args)})" if dec_args else "@pytest.fixture"
            body = f'    print("{name}:up")\n    yield {vals.get(name, 0)}\n    print("{name}:down")'
            return f"{dec}\ndef {name}({', '.join(deps)}):\n{body}"

        mod2_dep = rng.random() < 0.5
        parts = [
            fx(mod1, scope="module"),
            fx(mod2, scope="module", deps=(mod1,) if mod2_dep else ()),
            fx(fun1, deps=(mod1,) if rng.random() < 0.5 else ()),
            fx(aut, autouse=True),
        ]
        head = parts[:3]
        rng.shuffle(head)  # порядок определения в файле на установку не влияет, но разнообразит текст
        parts = [*head, parts[3]]
        cases = rng.sample(_TEST_CASES, 3)
        # сигнатуры: функции стоят РАНЬШЕ module-фикстур — повод для ловушки порядка
        tests = [
            (cases[0], [fun1, mod2]),
            (cases[1], [mod1]),
            (cases[2], []),
        ]
        tparts = []
        for case, args in tests:
            body = f'    print("test_{case}"' + "".join(f", {a}" for a in args) + ")"
            tparts.append(f"def test_{case}({', '.join(args)}):\n{body}")
        code = "\n\n\n".join(parts) + "\n\n\n" + "\n\n\n".join(tparts)
        # Порядок обхода зависимостей зависит от версии pytest (до 8.3 — в ширину, с 8.4 — в глубину): берём только
        # те модули, где вывод одинаков в обоих вариантах, иначе задание неоднозначно.
        if _run(code).out != _run(code, quirks=frozenset({"bfs_closure"})).out:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Модуль запускают командой `pytest -s` (вывод print не перехватывается; импорт pytest в файле есть, в листинге "
            "его нет). Какие строки и в каком порядке напечатают фикстуры и тесты? Значения, которые фикстуры отдают "
            "тестам, печатаются вместе с именем теста."
        )

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        quirks = frozenset({mutant}) if mutant else frozenset()
        res = _run(code, quirks=quirks)
        if any(it.outcome != "passed" for it in res.items):
            raise ScenarioError("тесты модуля должны проходить")
        return " ".join(res.out)

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Перед каждым тестом pytest строит список нужных фикстур: autouse, затем аргументы теста и их зависимости, "
            "и сортирует его по области видимости (module раньше function). Зависимость устанавливается до зависящей "
            "от неё фикстуры. Фикстура с yield после теста закрывается в обратном порядке; фикстуры module живут до "
            "конца файла и закрываются после последнего теста. Итоговый вывод: " + raw_key + "."
        )
