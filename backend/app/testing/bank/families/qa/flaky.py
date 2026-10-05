"""qa_testing, нестабильные («флаки») тесты: найти строку-причину и выбрать исправление.

Каждый шаблон проверяется ИСПОЛНЕНИЕМ мини-раннером (qa/_minipytest.py):
- до исправления тесты проходят в порядке файла (или при «дневных» часах), но падают в другом порядке запуска (или
  при «ночных» часах) — это и есть нестабильность;
- верное исправление проходит при ВСЕХ перестановках порядка (или при всех часах);
- каждое неверное исправление проверено так же и обязано оставаться нестабильным или ломать тест.
Если проверка не сошлась — экземпляр перегенерируется, поэтому ключ не зависит от разметки автора шаблона.
"""

from __future__ import annotations

import datetime as dt
import itertools
import random
from typing import Any

from app.testing.bank.base import Instance, register
from app.testing.bank.families.backend_python._common import tpl
from app.testing.bank.families.qa._common import MODULE, SPECS, BankFamily, GenerationRetry
from app.testing.bank.families.qa._minipytest import MiniPytestError, run_tests

_HOURS = (3, 12, 20)  # ночь, день, вечер: «флаки» от часов запуска проявится на ночном или вечернем


class _FakeDatetime(dt.datetime):
    """datetime с управляемым «сейчас»: нужен, чтобы проверить зависимость теста от часов исполнением."""

    hour_now = 12

    @classmethod
    def now(cls, tz: Any = None) -> _FakeDatetime:  # noqa: D102
        return cls(2026, 3, 4, cls.hour_now, 30)

    @classmethod
    def utcnow(cls) -> _FakeDatetime:  # noqa: D102
        return cls(2026, 3, 4, (cls.hour_now - 3) % 24, 30)


def _run_at_hour(code: str, hour: int):
    fake = type("FakeDatetime", (_FakeDatetime,), {"hour_now": hour})
    src = code.replace("from datetime import datetime\n", "")
    return run_tests(src, extra_globals={"datetime": fake})


def _all_pass(code: str, template: str) -> bool:
    """Тесты проходят при всех перестановках порядка (или при всех часах для шаблона с часами)."""
    try:
        if template == "clock":
            return all(not _run_at_hour(code, h).names("failed") and not _run_at_hour(code, h).names("error")
                       for h in _HOURS)
        names = [n for n, _ in _tests(code)]
        return all(
            set(run_tests(code, order=list(p)).counts()) <= {"passed"} for p in itertools.permutations(names)
        )
    except MiniPytestError:
        return False
    except Exception:  # noqa: BLE001 — исправление сломало код (TypeError и т.п.): тесты не проходят
        return False


def _tests(code: str) -> list[tuple[str, Any]]:
    from app.testing.bank.families.qa._minipytest import MiniPytest  # noqa: PLC0415

    return MiniPytest(code).tests


def _passes_default(code: str, template: str) -> bool:
    try:
        if template == "clock":
            return not _run_at_hour(code, 12).names("failed")
        return set(run_tests(code).counts()) <= {"passed"}
    except Exception:  # noqa: BLE001
        return False


def _apply(lines: list[str], line_no: int, new_text: str) -> str:
    out = list(lines)
    original = out[line_no - 1]
    indent = original[: len(original) - len(original.lstrip())]
    out[line_no - 1] = indent + new_text.strip()
    return "\n".join(out)


# --------------------------------------------------------------------------------------------- шаблоны

_NAMES_OPEN = ["is_open", "is_working_time", "in_service_window", "can_deliver_now"]
_NAMES_ADD = ["add_tag", "collect_label", "push_note", "remember_item"]
_NAMES_CFG = ["build_config", "make_options", "load_settings", "prepare_preset"]
_NAMES_CART = ["cart", "basket", "queue_items", "visited"]
_ITEMS = ["tea", "jam", "soap", "milk", "rice", "salt", "nuts"]
_CONFIG_BASES = ["DEFAULTS", "BASE", "PRESET", "SETTINGS"]
_CONFIG_KEYS = [("retries", "timeout"), ("limit", "window"), ("workers", "backoff")]


def _clock(rng: random.Random) -> tuple[list[str], list[tuple[int, str, str | None]], str]:
    fn = rng.choice(_NAMES_OPEN)
    start, end = rng.randint(8, 10), rng.randint(17, 19)
    code = tpl(
        """
        from datetime import datetime


        def $fn(now):
            return $start <= now.hour < $end


        def test_${fn}_during_day():
            assert $fn(datetime.now())


        def test_${fn}_at_noon():
            assert $fn(datetime(2026, 3, 4, 12, 0))
        """,
        fn=fn, start=start, end=end,
    )
    lines = code.splitlines()
    bug = next(i for i, ln in enumerate(lines, 1) if "datetime.now()" in ln)
    prod = next(i for i, ln in enumerate(lines, 1) if ln.strip().startswith("return"))
    fixes = [
        (bug, f"assert {fn}(datetime(2026, 3, 4, 12, 0))", None),
        (bug, f"assert {fn}(datetime.now().replace(minute=0))", "replace_minute"),
        (bug, f"assert {fn}(datetime.utcnow())", "utc_clock"),
        (prod, f"return {start} <= now.hour <= {end}", "change_production_code"),
    ]
    symptom = f"тест `test_{fn}_during_day` стабильно проходит днём на CI-агенте, но падает в ночных и вечерних прогонах"
    return lines, fixes, symptom


def _mutable_default(rng: random.Random) -> tuple[list[str], list[tuple[int, str, str | None]], str]:
    fn = rng.choice(_NAMES_ADD)
    a, b = rng.sample(_ITEMS, 2)
    code = tpl(
        """
        def $fn(item, bucket=[]):
            if bucket is None:
                bucket = []
            bucket.append(item)
            return bucket


        def test_${fn}_first():
            assert $fn("$a") == ["$a"]


        def test_${fn}_second():
            assert $fn("$b")[-1] == "$b"


        def test_${fn}_explicit_list():
            assert $fn("$a", ["$b"]) == ["$b", "$a"]
        """,
        fn=fn, a=a, b=b,
    )
    lines = code.splitlines()
    sig = 1
    first_assert = next(i for i, ln in enumerate(lines, 1) if ln.strip().startswith("assert"))
    fixes = [
        (sig, f"def {fn}(item, bucket=None):", None),
        (sig, f"def {fn}(item, bucket=list()):", "list_call_default"),
        (sig, f"def {fn}(item, bucket=()):", "tuple_default"),
        (sig, f"def {fn}(item, *, bucket=[]):", "keyword_only_default"),
        (first_assert, f'assert {fn}("{a}")[0] == "{a}"', "weaken_assertion"),
    ]
    symptom = (
        "каждый тест по отдельности проходит, в порядке файла проходят все, но при случайном порядке запуска "
        f"(pytest-randomly) иногда падает `test_{fn}_first`"
    )
    return lines, fixes, symptom


def _config_alias(rng: random.Random) -> tuple[list[str], list[tuple[int, str, str | None]], str]:
    base = rng.choice(_CONFIG_BASES)
    k1, k2 = rng.choice(_CONFIG_KEYS)
    fn = rng.choice(_NAMES_CFG)
    v1, v2 = rng.randint(2, 9), rng.randrange(10, 100, 5)
    alt = v2 + rng.choice([5, 10, 15])
    code = tpl(
        """
        $base = {"$k1": $v1, "$k2": $v2}


        def $fn(**override):
            cfg = $base
            cfg.update(override)
            return cfg


        def test_default_$k2():
            assert $fn()["$k2"] == $v2


        def test_custom_$k2():
            assert $fn($k2=$alt)["$k2"] == $alt
        """,
        base=base, k1=k1, k2=k2, v1=v1, v2=v2, fn=fn, alt=alt,
    )
    lines = code.splitlines()
    bug = next(i for i, ln in enumerate(lines, 1) if ln.strip() == f"cfg = {base}")
    fixes = [
        (bug, f"cfg = dict({base})", None),
        (bug, f"cfg = {base}[:]", "slice_copy_dict"),
        (bug, f"cfg = {base}.copy", "method_without_call"),
        (bug, f"cfg = {base} or {{}}", "or_empty_copy"),
        (1, f'{base} = {{"{k1}": {v1}, "{k2}": {v2}}}.copy()', "copy_at_definition"),
    ]
    symptom = (
        f"тесты проходят в порядке файла, но при случайном порядке запуска `test_default_{k2}` иногда падает "
        "со значением из соседнего теста"
    )
    return lines, fixes, symptom


def _module_fixture(rng: random.Random) -> tuple[list[str], list[tuple[int, str, str | None]], str]:
    name = rng.choice(_NAMES_CART)
    a, b = rng.sample(_ITEMS, 2)
    code = tpl(
        """
        @pytest.fixture(scope="module")
        def $name():
            return []


        def test_${name}_starts_empty($name):
            assert $name == []


        def test_${name}_add_item($name):
            $name.append("$a")
            assert "$a" in $name


        def test_${name}_add_two($name):
            $name.extend(["$a", "$b"])
            assert len($name) >= 2
        """,
        name=name, a=a, b=b,
    )
    lines = code.splitlines()
    fixes = [
        (1, "@pytest.fixture", None),
        (1, '@pytest.fixture(scope="session")', "session_scope"),
        (1, '@pytest.fixture(scope="module", autouse=True)', "autouse_flag"),
        (3, "return list()", "list_factory"),
    ]
    symptom = (
        f"при запуске по одному тест `test_{name}_starts_empty` проходит, в порядке файла тоже, но при случайном "
        "порядке запуска иногда падает"
    )
    return lines, fixes, symptom


_TEMPLATES = {
    "clock": _clock,
    "mutable_default": _mutable_default,
    "config_alias": _config_alias,
    "module_fixture": _module_fixture,
}


@register
class FlakyDiagnosis(BankFamily):
    id = "qa.flaky.diagnose"
    title = "Нестабильный тест: найти причину и исправить"
    specs = SPECS
    module = MODULE
    format = "bug_line"
    target_grade = "middle"
    position = 0.5
    c = 0.25
    time_limit_s = 150
    enemies = ("qa.fixture.order",)
    answer_kind = "line"
    measures = (
        "Диагностика «флаки»-тестов: зависимость от текущего времени, общее изменяемое состояние "
        "(изменяемый аргумент по умолчанию, псевдоним словаря, фикстура с широкой областью)."
    )
    misconceptions = {
        "replace_minute": "фиксация минут не делает время детерминированным: час всё ещё берётся из реальных часов",
        "utc_clock": "datetime.utcnow() — тоже текущее время: тест по-прежнему зависит от момента запуска",
        "change_production_code": "логика функции верна: нестабильность — в зависимости теста от текущего времени, "
        "а не в проверке часов",
        "list_call_default": "list() в значении по умолчанию тоже вычисляется один раз при определении функции — "
        "объект остаётся общим",
        "tuple_default": "кортеж неизменяем: у него нет append, функция начнёт падать при каждом вызове",
        "keyword_only_default": "аргумент только по ключевому слову не отменяет общий объект: он тот же и создаётся "
        "один раз",
        "slice_copy_dict": "срез [:] копирует список, но не словарь: dict не поддерживает срезы",
        "method_without_call": "без скобок получается сам метод, а не копия словаря: update вызвать не на чем",
        "or_empty_copy": "«X or {}» вернёт сам X, если он непустой: копии не создаётся, словарь по-прежнему общий",
        "weaken_assertion": "ослабить проверку — значит спрятать симптом: общее состояние остаётся, а тест "
        "проходит или падает по-прежнему в зависимости от порядка",
        "copy_at_definition": "копия при определении ничего не даёт: на неё всё так же ссылается cfg, и словарь "
        "один на все вызовы",
        "session_scope": "scope=\"session\" ещё шире module: список разделяют все тесты прогона",
        "autouse_flag": "autouse не меняет область видимости: список по-прежнему один на весь модуль",
        "list_factory": "list() вместо [] ничего не меняет: фикстура с scope=\"module\" вызывается один раз",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        name = rng.choice(list(_TEMPLATES))
        lines, fixes, symptom = _TEMPLATES[name](rng)
        wrong_pool = [f for f in fixes if f[2] is not None]
        fixes = [next(f for f in fixes if f[2] is None), *rng.sample(wrong_pool, 3)]
        # --- проверка исполнением (см. заголовок модуля)
        code = "\n".join(lines)
        if not _passes_default(code, name) or _all_pass(code, name):
            raise GenerationRetry  # нет «флаки»: в штатном режиме падает или стабилен при всех порядках
        verdicts = []
        for line_no, text, tag in fixes:
            fixed = _apply(lines, line_no, text)
            verdicts.append((tag, _all_pass(fixed, name)))
        if [t for t, ok in verdicts if ok] != [None] or any(ok for t, ok in verdicts if t is not None):
            raise GenerationRetry
        return self.line_instance(
            seed,
            rng,
            prompt=(
                f"Тест нестабилен: {symptom}. В какой строке причина нестабильности? Выберите строку и исправление, "
                "после которого результат не зависит ни от порядка запуска, ни от времени прогона. "
                "(Импорт pytest в файле есть, в листинге его нет.)"
            ),
            lines=lines,
            language="python",
            fixes=fixes,
            why=self._why(name),
        )

    @staticmethod
    def _why(name: str) -> str:
        return {
            "clock": "Тест берёт текущее время из datetime.now(), поэтому результат зависит от часа запуска. Время "
            "нужно зафиксировать (передать конкретную дату). Исправления с replace(minute=0) и utcnow() оставляют "
            "зависимость от реальных часов, а правка условия в функции чинит то, что не сломано.",
            "mutable_default": "Значение по умолчанию [] создаётся один раз при определении функции, поэтому все вызовы "
            "без явного списка делят один объект, и тест, ожидающий пустой список, падает после «соседа». Проверка "
            "`bucket is None` внутри не срабатывает, пока значение по умолчанию — не None.",
            "config_alias": "cfg = DEFAULTS не копирует словарь, а даёт на него вторую ссылку: update в одном тесте "
            "меняет значения по умолчанию для следующих. Нужна копия: dict(DEFAULTS).",
            "module_fixture": "Фикстура с scope=\"module\" создаётся один раз на файл, и один и тот же список "
            "разделяют все тесты; порядок запуска решает, кто увидит чужие данные. Нужна область по умолчанию "
            "(function): свежий список на каждый тест.",
        }[name]
