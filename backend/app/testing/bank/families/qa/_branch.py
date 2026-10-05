"""Трассировщик покрытия ветвей по AST (свой, в духе coverage.py): ключи заданий про покрытие считает он.

Исходный код разбирается `ast`, в каждое условие if/elif вставляется вызов-свидетель `__br__(id, условие)`, который
записывает исход (истина/ложь) и возвращает значение условия без изменений; перед каждым оператором тела функции
вставляется отметка `__st__(строка)`; операнды and/or внутри условия оборачиваются `__atom__` (порядок вычислений и
короткое замыкание сохраняются). Затем код исполняется (определения функций + вызовы-«тесты» на уровне модуля).

Определения (как в условии задания):
- ветвь — исход «истина» (T) или «ложь» (F) условия if/elif на данной строке; составное условие (and/or) — одно условие;
- покрытие ветвей = покрытые исходы / все исходы (2 на каждое условие);
- модели заблуждений: покрытие строк (statement coverage), покрытие условий (каждый операнд and/or — отдельно).
Сверяется в tests/test_bank_qa.py независимым подсчётом через sys.settrace.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Trace:
    ifs: list[tuple[int, int, int]] = field(default_factory=list)  # (id, строка условия, число операндов)
    br_seen: set[tuple[int, bool]] = field(default_factory=set)
    atom_seen: set[tuple[int, int, bool]] = field(default_factory=set)
    st_seen: set[int] = field(default_factory=set)
    st_lines: set[int] = field(default_factory=set)

    def uncovered_branches(self) -> list[tuple[int, bool]]:
        """[(строка, исход)] непокрытых ветвей по возрастанию строки, T раньше F."""
        out = [(line, o) for i, line, _n in self.ifs for o in (True, False) if (i, o) not in self.br_seen]
        return sorted(out, key=lambda t: (t[0], not t[1]))

    def uncovered_atoms(self) -> list[tuple[int, bool]]:
        out: set[tuple[int, bool]] = set()
        for i, line, n in self.ifs:
            if n <= 1:  # условие без and/or: операнд = само условие
                out.update((line, o) for o in (True, False) if (i, o) not in self.br_seen)
                continue
            for k in range(n):
                out.update((line, o) for o in (True, False) if (i, k, o) not in self.atom_seen)
        return sorted(out, key=lambda t: (t[0], not t[1]))

    def unexecuted_statements(self) -> list[int]:
        return sorted(self.st_lines - self.st_seen)

    def totals(self) -> tuple[int, int]:
        total = 2 * len(self.ifs)
        return total - len(self.uncovered_branches()), total


class _Instrument(ast.NodeTransformer):
    def __init__(self) -> None:
        self.ifs: list[tuple[int, int, int]] = []
        self.st_lines: set[int] = set()

    @staticmethod
    def _call(name: str, *args: ast.expr) -> ast.expr:
        return ast.Call(func=ast.Name(id=name, ctx=ast.Load()), args=list(args), keywords=[])

    def _block(self, body: list[ast.stmt]) -> list[ast.stmt]:
        out: list[ast.stmt] = []
        for st in body:
            self.st_lines.add(st.lineno)
            marker = ast.Expr(value=self._call("__st__", ast.Constant(st.lineno)))
            out.append(ast.copy_location(marker, st))
            out.append(self.visit(st))
        return out

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.AST:  # noqa: N802
        node.body = self._block(node.body)
        return node

    def visit_If(self, node: ast.If) -> ast.AST:  # noqa: N802
        node.body = self._block(node.body)
        node.orelse = self._block(node.orelse)
        i = len(self.ifs)
        test = node.test
        n_atoms = 1
        if isinstance(test, ast.BoolOp):
            n_atoms = len(test.values)
            test.values = [
                self._call("__atom__", ast.Constant(i), ast.Constant(k), v) for k, v in enumerate(test.values)
            ]
        node.test = self._call("__br__", ast.Constant(i), test)
        self.ifs.append((i, node.lineno, n_atoms))
        return node


def trace_code(code: str) -> Trace:
    """Исполнить код (определения и вызовы) под инструментацией и вернуть сведения о покрытии."""
    tree = ast.parse(code)
    instr = _Instrument()
    tree = ast.fix_missing_locations(instr.visit(tree))
    tr = Trace(ifs=instr.ifs, st_lines=instr.st_lines)

    def br(i: int, value: Any) -> Any:
        tr.br_seen.add((i, bool(value)))
        return value

    def atom(i: int, k: int, value: Any) -> Any:
        tr.atom_seen.add((i, k, bool(value)))
        return value

    def st(line: int) -> None:
        tr.st_seen.add(line)

    ns: dict[str, Any] = {"__name__": "__cov__", "__br__": br, "__atom__": atom, "__st__": st}
    exec(compile(tree, "<coverage>", "exec", dont_inherit=True), ns)  # noqa: S102 — только наш код
    return tr
