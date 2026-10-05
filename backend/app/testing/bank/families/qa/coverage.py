"""qa_testing, покрытие: какие ветви условий не покрыты вызовами-тестами.

Ключ — трассировщик покрытия ветвей по AST (qa/_branch.py), исполняющий ПОКАЗАННЫЙ код. Номера строк печатаются в
левом поле листинга («12  if …»): решатель отбрасывает поле и разбирает код, двойник меняет число только в коде.
Дистракторы — те же прогоны под моделями заблуждений: покрытие строк, только «истинные» ветви, покрытие каждого
операнда and/or отдельно, ветви лишь недостижимых условий.
"""

from __future__ import annotations

import random
import re
from typing import Any

from app.testing.bank.base import register
from app.testing.bank.families.qa._branch import Trace, trace_code
from app.testing.bank.families.qa._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    Scenario,
    ScenarioError,
    ScenarioFamily,
)

GUTTER = 4  # «12  » — номер строки и два пробела

_NUM = {
    "weight": (1, 50), "qty": (1, 30), "age": (12, 80), "amount": (100, 5000), "distance": (1, 400),
    "points": (0, 100), "hours": (1, 24),
}
_FLAGS = ["express", "member", "urgent", "fragile"]
_STRS = {"zone": ["local", "region", "far"], "tier": ["basic", "plus", "pro"], "coupon": ["none", "SALE", "VIP"]}
_FUNCS = ["shipping_cost", "service_fee", "price_for", "bonus_for", "limit_for", "tariff_for"]
_RESULT = ["cost", "price", "fee", "total"]


def _with_gutter(code: str) -> str:
    return "\n".join(f"{i:2d}  {ln}".rstrip() if ln else f"{i:2d}" for i, ln in enumerate(code.splitlines(), 1))


def _strip_gutter(code: str) -> str:
    return "\n".join(ln[GUTTER:] for ln in code.splitlines())


class _Gen:
    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.nums = rng.sample(list(_NUM), rng.randint(1, 2))
        self.flags = rng.sample(_FLAGS, rng.randint(0, 1))
        self.strs = rng.sample(list(_STRS), rng.randint(0, 1))
        self.params = [*self.nums, *self.flags, *self.strs]
        rng.shuffle(self.params)
        self.res = rng.choice(_RESULT)
        self.used: set[str] = set()

    def simple(self) -> tuple[str, str]:
        """(условие, параметр)"""
        p = self.rng.choice(self.params)
        if p in _NUM:
            lo, hi = _NUM[p]
            t = self.rng.randint(lo + (hi - lo) // 5, hi - (hi - lo) // 5)
            return f"{p} {self.rng.choice(['>', '>=', '<', '<='])} {t}", p
        if p in _STRS:
            v = self.rng.choice(_STRS[p])
            return f'{p} {self.rng.choice(["==", "!="])} "{v}"', p
        return (p if self.rng.random() < 0.7 else f"not {p}"), p

    def cond(self) -> str:
        """Условие, которого в функции ещё не было (повторы выглядят неестественно)."""
        for _ in range(30):
            a, pa = self.simple()
            c = a
            if self.rng.random() < 0.35 and len(self.params) > 1:
                for _ in range(8):
                    b, pb = self.simple()
                    if pb != pa:
                        c = f"{a} {self.rng.choice(['and', 'or'])} {b}"
                        break
            if c not in self.used:
                self.used.add(c)
                return c
        raise GenerationRetry

    def stmt(self) -> str:
        r, res = self.rng, self.res
        k = r.random()
        if k < 0.3:
            return f"{res} += {r.randrange(5, 300, 5)}"
        if k < 0.55:
            return f"{res} -= {r.randrange(5, 100, 5)}"
        if k < 0.75:
            return f"{res} = {res} * {r.choice([80, 90, 110, 120, 150])} // 100"
        if k < 0.88:
            return f"{res} = max({res}, {r.randrange(50, 200, 10)})"
        return f"{res} = min({res}, {r.randrange(300, 900, 50)})"

    def body(self, indent: str) -> list[str]:
        return [indent + self.stmt() for _ in range(1 if self.rng.random() < 0.65 else 2)]

    def block(self, depth: int = 0) -> list[str]:
        r = self.rng
        kind = r.choices(["if", "if_else", "if_elif", "nested", "guard"], weights=[3, 2, 3, 1, 1])[0]
        if depth > 0 and kind in ("nested", "guard"):
            kind = "if"
        out: list[str]
        if kind == "if":
            out = [f"if {self.cond()}:", *self.body("    ")]
        elif kind == "if_else":
            out = [f"if {self.cond()}:", *self.body("    "), "else:", *self.body("    ")]
        elif kind == "if_elif":
            out = [f"if {self.cond()}:", *self.body("    "), f"elif {self.cond()}:", *self.body("    ")]
            if r.random() < 0.4:
                out += ["else:", *self.body("    ")]
        elif kind == "nested":
            inner = self.block(depth + 1)
            out = [f"if {self.cond()}:", *("    " + ln for ln in inner)]
        else:
            out = [f"if {self.cond()}:", f"    return {r.randrange(0, 200, 10)}"]
        return out

    def arg(self, p: str) -> str:
        r = self.rng
        if p in _NUM:
            return str(r.randint(*_NUM[p]))
        if p in _STRS:
            return f'"{r.choice(_STRS[p])}"'
        return r.choice(["True", "False"])


@register
class BranchCoverage(ScenarioFamily):
    id = "qa.coverage.branch"
    title = "Покрытие ветвей: что не проверено вызовами"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "senior"
    position = 0.2
    time_limit_s = 180
    is_anchor = True
    answer_kind = "branches"
    language = "text"
    measures = (
        "Покрытие ветвей (branch coverage): исход каждого if/elif — отдельная ветвь, составное условие — одно условие, "
        "неявный else тоже ветвь; отличие от покрытия строк и покрытия условий."
    )
    misconceptions = {
        "statement_coverage": "покрытие строк ≠ покрытие ветвей: условие без else считается выполненным, даже если ни разу "
        "не было ложным",
        "then_only": "у каждого условия две ветви — «истина» и «ложь»; неявный else (условие не сработало) тоже "
        "нужно покрыть",
        "atom_conditions": "составное условие (and/or) в покрытии ветвей — одно условие с двумя исходами; "
        "покрытие каждого операнда — другая, более строгая метрика",
        "only_unreached": "непокрытыми бывают и ветви условий, до которых вызовы дошли только в одну сторону",
    }
    answer_hint = (
        "Введите ветви в формате «12F 15T» (номер строки условия из левого поля листинга и исход: T — условие истинно, "
        "F — ложно) по возрастанию номеров; если непокрытых ветвей нет — «нет»."
    )

    def make(self, rng: random.Random) -> Scenario:
        g = _Gen(rng)
        head = f"def {rng.choice(_FUNCS)}({', '.join(g.params)}):"
        base = rng.randrange(100, 500, 10)
        lines = [head, f"    {g.res} = {base}"]
        for _ in range(rng.randint(3, 4)):
            lines += ["    " + ln for ln in g.block()]
        lines += [f"    return {g.res}", "", ""]
        fn = head[4 : head.index("(")]
        for _ in range(rng.randint(2, 4)):
            lines.append(f"{fn}({', '.join(g.arg(p) for p in g.params)})")
        return Scenario(_with_gutter("\n".join(lines)))

    def prompt(self, sc: Scenario) -> str:
        return (
            "В листинге — функция и вызовы в конце файла, которые играют роль тестов (слева — номера строк). Ветвь — "
            "исход условия в if/elif: «истина» (T) или «ложь» (F) на строке этого условия; составное условие с and/or "
            "считается одним условием, а если else нет — исход «ложь» всё равно ветвь. Какие ветви не покрыты "
            "ни одним вызовом?"
        )

    def _trace(self, code: str) -> Trace:
        try:
            tr = trace_code(_strip_gutter(code))
        except Exception as e:  # noqa: BLE001 — любая ошибка разбора/исполнения = негодный сценарий
            raise ScenarioError(f"{type(e).__name__}: {e}") from e
        if not tr.ifs:
            raise ScenarioError("нет условий")
        return tr

    @staticmethod
    def _fmt(items: list[tuple[int, bool]]) -> str:
        return " ".join(f"{line}{'T' if o else 'F'}" for line, o in items)

    def solve(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        tr = self._trace(code)
        unc = tr.uncovered_branches()
        if mutant is None:
            covered, total = tr.totals()
            if not 2 <= len(unc) <= 5 or covered == 0:
                return None  # вырожденно: почти всё покрыто, ничего не покрыто или слишком много непокрытого
            return self._fmt(unc)
        if mutant == "statement_coverage":
            lines = tr.unexecuted_statements()
            return " ".join(map(str, lines)) if lines else "нет"
        if mutant == "then_only":
            return self._fmt([b for b in unc if b[1]]) or "нет"
        if mutant == "atom_conditions":
            return self._fmt(tr.uncovered_atoms()) or "нет"
        if mutant == "only_unreached":
            seen_ids = {i for i, _ in tr.br_seen}
            lines = sorted(line for i, line, _n in tr.ifs if i not in seen_ids)
            return self._fmt([(line, o) for line in lines for o in (True, False)]) or "нет"
        return None

    def why(self, sc: Scenario, raw_key: str) -> str:
        tr = self._trace(sc.code)
        covered, total = tr.totals()
        return (
            f"У {len(tr.ifs)} условий {total} ветвей (по две на условие). Вызовы покрыли {covered}; не покрыты: "
            f"{raw_key}. Покрытие строк тут обманчиво: строка с условием выполняется при любом исходе, но неявный else "
            "и обе стороны составного условия нужно проверять по исходам."
        )

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        pos = 0
        for ln in code.split("\n"):
            for m in re.finditer(r"(?<![\w.])\d+(?![\w.])", ln[GUTTER:]):
                spans.append((pos + GUTTER + m.start(), pos + GUTTER + m.end()))
            pos += len(ln) + 1
        return spans

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: Any) -> list[str]:
        """Новые значения — рядом с другими числами листинга: порог у аргумента (и наоборот) меняет исход условия."""
        from app.testing.anticheat import literals as lit  # noqa: PLC0415

        old = code[span[0] : span[1]]
        if not old.isdigit() or old.startswith("0") or int(old) < p.min_value:
            return []
        v = int(old)
        near = {int(code[a:b]) + d for a, b in self.twin_spans(sc, code) for d in (-1, 0, 1)}
        out = []
        for n in sorted(near):
            s = str(n)
            if (
                n != v
                and n >= p.min_value
                and p.rel_change_min <= abs(n - v) / v <= p.rel_change_max
                and abs(len(s) - len(old)) <= p.max_length_delta
                and lit.glyph_ok(old, s, p.confusable_pairs)
            ):
                out.append(s)
        rng.shuffle(out)
        out = [*out, *(x for x in self._propose(old, rng, p) if x not in out)]
        # оставляем только значения, которые меняют ответ: так попытки двойника не тратятся на «пустые» правки
        base = self.solve(sc, code)
        flipping = []
        for s in out[:16]:
            try:
                if self.solve(sc, code[: span[0]] + s + code[span[1] :]) not in (base, None):
                    flipping.append(s)
            except ScenarioError:
                continue
        return flipping
