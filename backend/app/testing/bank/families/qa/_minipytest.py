"""Мини-раннер pytest для генерации ключей заданий QA (офлайн, только наш сгенерированный код).

Почему свой раннер, а не настоящий pytest: ключ считается при генерации пула тысячами экземпляров и должен быть
детерминированным, быстрым (доли миллисекунды) и без файлов/подпроцессов. Раннер воспроизводит ровно то подмножество
pytest 8–9, которое используют генераторы, и сверяется с настоящим pytest в tests/test_bank_qa.py на выборке сидов:
- сбор: функции test_* в порядке определения; @pytest.mark.parametrize (стопкой: нижний декоратор — «медленный»
  индекс, верхний меняется быстрее; id = значения снизу вверх через «-»), pytest.param(id=, marks=);
- метки skip / skipif / xfail (strict, raises);
- фикстуры: scope function / module, autouse, yield-фикстуры (teardown в обратном порядке), зависимости фикстур.
  Порядок установки — как в _pytest.fixtures: замыкание имён = autouse + аргументы теста + зависимости (в ширину),
  затем устойчивая сортировка по области видимости (module выше function); teardown функций после теста,
  module — после последнего теста файла;
- pytest.raises(match=), pytest.fail, pytest.skip.
Вывод print в тестах и фикстурах собирается в RunResult.out (как `pytest -s`).
"""

from __future__ import annotations

import ast
import builtins
import inspect
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

_SCOPE_RANK = {"function": 0, "class": 1, "module": 2, "package": 3, "session": 4}


class _Skipped(BaseException):
    pass


class _Failed(BaseException):
    pass


class MiniPytestError(Exception):
    """Раннер не поддерживает конструкцию или сценарий сломан (для генерации — перегенерировать)."""


@dataclass
class _Param:
    values: tuple[Any, ...]
    marks: list[_Mark]
    id: str | None = None


@dataclass
class _Mark:
    name: str
    args: tuple[Any, ...] = ()
    kwargs: dict[str, Any] = field(default_factory=dict)


def _store_mark(func: Any, mark: _Mark) -> None:
    marks = getattr(func, "pytestmark", [])
    func.pytestmark = [*marks, mark]


class _MarkNamespace:
    def __getattr__(self, name: str) -> Any:
        if name == "parametrize":
            return _parametrize
        return _MarkFactory(name)


class _MarkFactory:
    """pytest.mark.<name>: вызывается как pytest.mark.skip(reason=...) (даёт декоратор) или применяется напрямую."""

    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        if len(args) == 1 and not kwargs and callable(args[0]):
            _store_mark(args[0], _Mark(self.name))
            return args[0]
        mark = _Mark(self.name, args, kwargs)

        def deco(func: Any) -> Any:
            _store_mark(func, mark)
            return func

        deco.mark = mark  # type: ignore[attr-defined]
        return deco


def _parametrize(argnames: Any, argvalues: Any, ids: Sequence[str] | None = None) -> Callable[[Any], Any]:
    names = [a.strip() for a in argnames.split(",")] if isinstance(argnames, str) else list(argnames)
    params: list[_Param] = []
    for v in argvalues:
        if isinstance(v, _Param):
            params.append(v)
        else:
            params.append(_Param(tuple(v) if len(names) > 1 else (v,), []))
    mark = _Mark("parametrize", (names, params, list(ids) if ids else None))

    def deco(func: Any) -> Any:
        _store_mark(func, mark)
        return func

    return deco


def _as_mark(m: Any) -> _Mark:
    if isinstance(m, _MarkFactory):
        return _Mark(m.name)  # marks=pytest.mark.skip без скобок
    if hasattr(m, "mark"):
        return m.mark
    raise MiniPytestError(f"неизвестная метка {m!r}")


def _param(*values: Any, marks: Any = (), id: str | None = None) -> _Param:  # noqa: A002 — как в pytest
    mlist = list(marks) if isinstance(marks, list | tuple) else [marks]
    return _Param(tuple(values), [_as_mark(m) for m in mlist], id)


def _fixture(func: Any = None, *, scope: str = "function", autouse: bool = False, name: str | None = None) -> Any:
    def deco(f: Any) -> Any:
        f._fixture = {"scope": scope, "autouse": autouse, "name": name or f.__name__}  # noqa: SLF001
        return f

    return deco(func) if func is not None else deco


class _Raises:
    def __init__(self, exc: type[BaseException] | tuple[type[BaseException], ...], match: str | None = None) -> None:
        self.exc, self.match = exc, match

    def __enter__(self) -> _Raises:
        return self

    def __exit__(self, et: Any, ev: Any, tb: Any) -> bool:
        if et is None:
            raise _Failed("DID NOT RAISE")
        if not issubclass(et, self.exc):
            return False
        if self.match is not None and not re.search(self.match, str(ev)):
            raise AssertionError(f"Regex pattern did not match: {self.match!r}")
        return True


def _skip(reason: str = "") -> None:
    raise _Skipped(reason)


def _fail(msg: str = "") -> None:
    raise _Failed(msg)


class _PytestShim:
    mark = _MarkNamespace()
    fixture = staticmethod(_fixture)
    param = staticmethod(_param)
    raises = _Raises
    skip = staticmethod(_skip)
    fail = staticmethod(_fail)


# ---------------------------------------------------------------- результат


@dataclass
class Item:
    nodeid: str
    outcome: str  # passed | failed | skipped | xfailed | xpassed | error
    error: str = ""
    soft_failures: int = 0


@dataclass
class RunResult:
    items: list[Item]
    out: list[str]

    def counts(self) -> dict[str, int]:
        c: dict[str, int] = {}
        for it in self.items:
            c[it.outcome] = c.get(it.outcome, 0) + 1
        return c

    def summary(self) -> str:
        """Итоговая строка pytest без времени: «2 failed, 3 passed, 1 skipped»."""
        c = self.counts()
        order = ("failed", "passed", "skipped", "xfailed", "xpassed", "error")
        return ", ".join(f"{c[k]} {k}" for k in order if k in c) or "no tests ran"

    def names(self, outcome: str) -> list[str]:
        return [it.nodeid for it in self.items if it.outcome == outcome]


# ---------------------------------------------------------------- AST: «мягкие» assert (модель заблуждения)


class _SoftAsserts(ast.NodeTransformer):
    def visit_Assert(self, node: ast.Assert) -> ast.AST:  # noqa: N802
        call = ast.Call(func=ast.Name(id="__soft__", ctx=ast.Load()), args=[node.test], keywords=[])
        return ast.copy_location(ast.Expr(value=call), node)


class _KeepAsserts(ast.NodeTransformer):
    """Модель заблуждения «тест проверяет только первый/последний assert»: остальные assert верхнего уровня убираем."""

    def __init__(self, which: str) -> None:
        self.which = which

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:  # noqa: N802
        if node.name.startswith("test"):
            idx = [i for i, st in enumerate(node.body) if isinstance(st, ast.Assert)]
            if len(idx) > 1:
                keep = idx[0] if self.which == "first" else idx[-1]
                node.body = [st if i not in idx or i == keep else ast.Pass() for i, st in enumerate(node.body)]
        return node


# ---------------------------------------------------------------- раннер


@dataclass
class _FixtureDef:
    name: str
    func: Callable[..., Any]
    scope: str
    autouse: bool
    argnames: list[str]
    is_gen: bool


def _idval(val: Any, argname: str, idx: int) -> str:
    if isinstance(val, str):
        return val
    if val is None or isinstance(val, bool | int | float):
        return str(val)
    return f"{argname}{idx}"


def _collect_params(func: Any) -> list[tuple[list[str], list[_Param], list[str] | None]]:
    marks = [m for m in getattr(func, "pytestmark", []) if m.name == "parametrize"]
    return [(m.args[0], m.args[1], m.args[2]) for m in marks]


def _expand(func: Any, base: str) -> list[tuple[str, dict[str, Any], list[_Mark]]]:
    """Все варианты теста: (nodeid, значения аргументов, метки)."""
    calls: list[tuple[list[str], dict[str, Any], list[_Mark]]] = [([], {}, [])]
    for names, params, ids in _collect_params(func):  # от нижнего декоратора к верхнему
        new = []
        for cid, vals, marks in calls:
            for i, p in enumerate(params):
                pid = (ids[i] if ids else None) or p.id or "-".join(
                    _idval(v, n, i) for v, n in zip(p.values, names, strict=True)
                )
                new.append((cid + [pid], {**vals, **dict(zip(names, p.values, strict=True))}, marks + p.marks))
        calls = new
    return [(f"{base}[{'-'.join(c)}]" if c else base, v, m) for c, v, m in calls]


class MiniPytest:
    def __init__(
        self,
        source: str,
        *,
        soft_asserts: bool = False,
        keep_asserts: str | None = None,
        quirks: frozenset[str] = frozenset(),
        extra_globals: dict[str, Any] | None = None,
    ) -> None:
        self.quirks = quirks
        self.out: list[str] = []
        self._buf = ""
        self.soft_asserts = soft_asserts
        self._soft = 0
        ns: dict[str, Any] = {"__name__": "test_module", "pytest": _PytestShim(), "print": self._print}
        ns["__soft__"] = self._soft_assert
        ns.update(extra_globals or {})
        tree = ast.parse(source)
        if keep_asserts:
            tree = ast.fix_missing_locations(_KeepAsserts(keep_asserts).visit(tree))
        if soft_asserts:
            tree = ast.fix_missing_locations(_SoftAsserts().visit(tree))
        try:
            exec(compile(tree, "<qa-tests>", "exec", dont_inherit=True), ns)  # noqa: S102 — только наш код
        except Exception as e:  # noqa: BLE001
            raise MiniPytestError(f"ошибка при импорте модуля тестов: {type(e).__name__}: {e}") from e
        self.ns = ns
        self.fixtures: dict[str, _FixtureDef] = {}
        self.tests: list[tuple[str, Any]] = []
        for name, obj in list(ns.items()):
            if not callable(obj) or isinstance(obj, type) or not inspect.isfunction(obj):
                continue
            meta = getattr(obj, "_fixture", None)
            if meta:
                args = [p for p in inspect.signature(obj).parameters]
                self.fixtures[meta["name"]] = _FixtureDef(
                    meta["name"], obj, meta["scope"], meta["autouse"], args, inspect.isgeneratorfunction(obj)
                )
            elif name.startswith("test"):
                self.tests.append((name, obj))

    # --- вывод и мягкие assert
    def _print(self, *args: Any, sep: str | None = " ", end: str | None = "\n", file: Any = None, flush: bool = False) -> None:
        if file is not None:
            builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
            return
        self._buf += (" " if sep is None else sep).join(map(str, args)) + ("\n" if end is None else end)
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            self.out.append(line)

    def _soft_assert(self, value: Any) -> None:
        if not value:
            self._soft += 1

    # --- фикстуры
    def closure(self, argnames: list[str]) -> list[str]:
        """Замыкание имён фикстур теста. pytest ≥ 8.4: autouse + аргументы, обход в глубину (зависимости сразу за
        фикстурой); модель «bfs_closure» — порядок pytest 7–8.3 (в ширину). Задания генерируются так, чтобы оба
        порядка давали один и тот же вывод (см. FixtureOrder.make)."""
        autouse = [n for n in sorted(self.fixtures) if self.fixtures[n].autouse]
        initial = list(dict.fromkeys([*autouse, *argnames]))
        names: list[str] = []
        if "bfs_closure" in self.quirks:
            names = list(initial)
            last = -1
            while last != len(names):
                last = len(names)
                for n in list(names):
                    fd = self.fixtures.get(n)
                    if fd:
                        for a in fd.argnames:
                            if a not in names:
                                names.append(a)
        else:

            def visit(n: str) -> None:
                if n in names:
                    return
                names.append(n)
                fd = self.fixtures.get(n)
                if fd:
                    for a in fd.argnames:
                        visit(a)

            for n in initial:
                visit(n)
        if "no_scope_sort" in self.quirks:
            return names

        def scope_of(n: str) -> int:
            fd = self.fixtures.get(n)
            return _SCOPE_RANK[fd.scope] if fd else 0

        return sorted(names, key=scope_of, reverse=True)  # устойчивая: равные области сохраняют порядок

    def _items(self, names: Sequence[str]) -> list[tuple[str, Any, dict[str, Any], list[_Mark]]]:
        funcs = dict(self.tests)
        out = []
        for name in names:
            func = funcs[name]
            for nodeid, values, marks in _expand(func, name):
                out.append((nodeid, func, values, [*getattr(func, "pytestmark", []), *marks]))
        return out

    def run(
        self,
        order: Sequence[str] | None = None,
        *,
        stop_on_first_failure: bool = False,
    ) -> RunResult:
        names = [n for n, _ in self.tests]
        if order is not None:
            if sorted(order) != sorted(names):
                raise MiniPytestError("order должен быть перестановкой имён тестов")
            names = list(order)
        planned = self._items(names)
        # Модель заблуждения: module-фикстура разбирается сразу после последнего теста, который её использует.
        last_use: dict[str, int] = {}
        if "module_teardown_after_last_use" in self.quirks:
            for i, (_, func, values, _m) in enumerate(planned):
                for n in self.closure([p for p in inspect.signature(func).parameters if p not in values]):
                    fd = self.fixtures.get(n)
                    if fd and fd.scope == "module":
                        last_use[n] = i
        items: list[Item] = []
        module_fins: list[tuple[str, Callable[[], None]]] = []
        module_cache: dict[str, Any] = {}
        for i, (nodeid, func, values, marks) in enumerate(planned):
            items.append(self._run_item(nodeid, func, values, marks, module_cache, module_fins))
            for n in [n for n, j in last_use.items() if j == i]:
                for entry in [f for f in module_fins if f[0] == n]:
                    entry[1]()
                    module_fins.remove(entry)
                module_cache.pop(n, None)
            if stop_on_first_failure and items[-1].outcome in ("failed", "error"):
                break
        order_fins = module_fins if "fifo_teardown" in self.quirks else module_fins[::-1]
        for _, fin in order_fins:
            fin()
        if self._buf:
            self.out.append(self._buf)
            self._buf = ""
        return RunResult(items, self.out)

    def _run_item(
        self,
        nodeid: str,
        func: Any,
        values: dict[str, Any],
        marks: list[_Mark],
        module_cache: dict[str, Any],
        module_fins: list[tuple[str, Callable[[], None]]],
    ) -> Item:
        for m in marks:
            if m.name == "skip" or (m.name == "skipif" and m.args and bool(m.args[0])):
                return Item(nodeid, "skipped")
        xfail = next((m for m in marks if m.name == "xfail"), None)
        cache: dict[str, Any] = {}
        stack: list[Callable[[], None]] = []
        self._soft = 0
        arg_names = [p for p in inspect.signature(func).parameters if p not in values]
        module_like = ("module", "package", "session") if "no_module_cache" not in self.quirks else ()

        def get(name: str) -> Any:
            if name in values:
                return values[name]
            fd = self.fixtures.get(name)
            if fd is None:
                raise MiniPytestError(f"неизвестная фикстура {name}")
            shared = fd.scope in module_like
            store = module_cache if shared else cache
            if name in store:
                return store[name]
            kwargs = {a: get(a) for a in fd.argnames}
            if fd.is_gen:
                gen = fd.func(**kwargs)
                value = next(gen)

                def fin(g: Any = gen) -> None:
                    try:
                        next(g)
                    except StopIteration:
                        pass

                if shared:
                    module_fins.append((name, fin))
                else:
                    stack.append(fin)
            else:
                value = fd.func(**kwargs)
            store[name] = value
            return value

        outcome, error = "passed", ""
        try:
            for n in self.closure(arg_names):
                if n not in values:
                    get(n)
            call_kwargs = {a: get(a) for a in inspect.signature(func).parameters}
            try:
                func(**call_kwargs)
            except _Skipped:
                outcome = "skipped"
            except _Failed as e:
                outcome, error = "failed", f"Failed: {e}"
            except AssertionError as e:
                outcome, error = "failed", f"AssertionError: {e}" if str(e) else "AssertionError"
            except Exception as e:  # noqa: BLE001 — любое исключение тела теста = failed
                outcome, error = "failed", f"{type(e).__name__}: {e}"
        except _Skipped:
            outcome = "skipped"
        except Exception as e:  # noqa: BLE001 — исключение при установке фикстуры = error
            outcome, error = "error", f"{type(e).__name__}: {e}"
        # teardown функций: LIFO (в модели заблуждения «fifo_teardown» — в порядке установки); исключение = error
        while stack:
            try:
                (stack.pop(0) if "fifo_teardown" in self.quirks else stack.pop())()
            except Exception as e:  # noqa: BLE001
                outcome, error = "error", f"{type(e).__name__}: {e}"
        soft = self._soft + (1 if self.soft_asserts and outcome == "failed" else 0)
        if self.soft_asserts and outcome == "passed" and soft:
            outcome = "failed"
        if xfail is not None and outcome in ("failed", "passed"):
            raises = xfail.kwargs.get("raises")
            if outcome == "failed" and (raises is None or error.split(":")[0] in _names(raises)):
                outcome = "xfailed"
            elif outcome == "passed":
                outcome = "failed" if xfail.kwargs.get("strict") else "xpassed"
        return Item(nodeid, outcome, error, soft)


def _names(raises: Any) -> set[str]:
    seq = raises if isinstance(raises, tuple) else (raises,)
    return {e.__name__ for e in seq}


def run_tests(
    source: str,
    *,
    order: Sequence[str] | None = None,
    stop_on_first_failure: bool = False,
    soft_asserts: bool = False,
    keep_asserts: str | None = None,
    quirks: frozenset[str] = frozenset(),
    extra_globals: dict[str, Any] | None = None,
) -> RunResult:
    """Выполнить модуль тестов и вернуть результат (исключения раннера — MiniPytestError).

    keep_asserts="first"|"last" — модель заблуждения (в каждом тесте остаётся один assert).
    quirks — модели заблуждений о фикстурах: no_scope_sort, fifo_teardown, no_module_cache,
    module_teardown_after_last_use. extra_globals — имена, доступные коду теста (например, подменённые часы).
    """
    return MiniPytest(
        source, soft_asserts=soft_asserts, keep_asserts=keep_asserts, quirks=quirks, extra_globals=extra_globals
    ).run(order, stop_on_first_failure=stop_on_first_failure)
