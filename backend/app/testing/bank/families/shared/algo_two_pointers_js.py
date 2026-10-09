"""algo.two_pointers.bug.js — JS-версия: найти ошибочную строку в алгоритме двух указателей и выбрать исправление.

Те же пять задач, что у algo.two_pointers.bug, на идиоматичном JS. Корректность конструктивная (spec-testing §3.8): эталон,
мутант и каждое исправление исполняются в Node на 300 случайных тестах; показанный кандидату упавший тест — реальный;
верное исправление восстанавливает эталон, каждое неверное валится хотя бы на одном тесте (иначе не предлагается).

Чем JS отличается и почему часть мутаций своя. Чтение за границей массива в JS не бросает исключение, а даёт undefined,
и арифметика с ним — NaN. Поэтому мутация Python-версии «hi = len(a)» в hasPairSum в JS безвредна (первая сумма NaN,
ветка else сдвигает hi на место — код молча становится верным), а исправления-«падения» превращаются в тихо неверный
результат. Безвредная мутация заменена равноценной («левый указатель с 1»), «падения» помечены как out_of_bounds. Добавлена
одна чисто JS-ловушка того же уровня: sort() без компаратора сортирует числа как строки.

Мутант может зациклиться (например, `||` вместо `&&` в merge, когда b кончился раньше): исполняемые копии получают
счётчик шагов в условиях циклов, и превышение лимита — это исход «не завершается». Кандидат видит код без счётчика.
Кандидатов (задача, мутация, тесты) разыгрывается несколько заранее; Node проверяет их по порядку и останавливается
на первом годном — так розыгрыш не зависит от результатов исполнения и генерация детерминирована.
"""

from __future__ import annotations

import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.testing.bank.base import Instance, Rendered, canonical, register
from app.testing.bank.execution import ExecutionError
from app.testing.bank.families.frontend_js._common import JsExecFamily, JsSpec
from app.testing.bank.families.shared import algo_two_pointers as py
from app.testing.bank.families.shared._common import build_choice, explain, norm_line_choice
from app.testing.bank.families.shared._js import JS_SPECS, js_list

_Mutation = py._Mutation  # line — индекс строки (с 0), bad — дефектная строка без отступа, wrong — неверные исправления


@dataclass(frozen=True)
class _JsProblem:
    fn: str
    doc: str
    lines: tuple[str, ...]
    names: tuple[dict[str, str], ...]
    gen: Callable[[random.Random], tuple]  # тесты — те же генераторы, что у Python-версии
    show: Callable[[tuple], str]
    run: str  # JS-выражение: вызвать __f на копии аргументов __args
    fmt: Callable[[Any], str]
    mutations: tuple[_Mutation, ...]
    verb: str = "вернула"


def _show(v: Any) -> str:
    """Значение из Node (после __enc) в записи JS."""
    if isinstance(v, dict) and "__num" in v:
        return str(v["__num"])
    if isinstance(v, dict) and v.get("__undef"):
        return "undefined"
    if isinstance(v, list):
        return "[" + ", ".join(_show(x) for x in v) + "]"
    return js_list(v)


_PY = {p.fn: p for p in py._PROBLEMS}

_PROBLEMS = (
    _JsProblem(
        fn="countPairs",
        doc="Функция countPairs(a, limit) должна вернуть число пар индексов i < j, для которых a[i] + a[j] ≤ limit.",
        lines=(
            "function countPairs(a, limit) {",
            "  a = [...a].sort((x, y) => x - y);",
            "  let {L} = 0, {R} = a.length - 1;",
            "  let count = 0;",
            "  while ({L} < {R}) {",
            "    if (a[{L}] + a[{R}] <= limit) {",
            "      count += {R} - {L};",
            "      {L} += 1;",
            "    } else {",
            "      {R} -= 1;",
            "    }",
            "  }",
            "  return count;",
            "}",
        ),
        names=({"L": "lo", "R": "hi"}, {"L": "left", "R": "right"}, {"L": "i", "R": "j"}),
        gen=_PY["count_pairs"].gen,
        show=lambda args: f"countPairs({js_list(args[0])}, {args[1]})",
        run="__f([...__args[0]], __args[1])",
        fmt=_show,
        mutations=(
            _Mutation(6, "count += 1;", "count_one_pair",
                      "если a[{L}] + a[{R}] ≤ limit, то a[{L}] с любым элементом на позициях {L} + 1 … {R} тоже "
                      "даёт сумму ≤ limit — это {R} − {L} пар, а не одна",
                      (("count += {R} - {L} + 1;", "off_by_one"), ("count += {R};", "wrong_formula"),
                       ("count += a.length - {L} - 1;", "wrong_formula"))),
            _Mutation(5, "if (a[{L}] + a[{R}] < limit) {", "strict_inequality",
                      "по условию сумма может быть равна limit, поэтому сравнение нестрогое: <=",
                      (("if (a[{L}] + a[{R}] >= limit) {", "wrong_condition"),
                       ("if (a[{L}] + a[{R}] <= limit + 1) {", "off_by_one"),
                       ("if (a[{L}] + a[{R}] > limit) {", "wrong_condition"))),
            _Mutation(7, "{R} -= 1;", "wrong_pointer",
                      "все пары с a[{L}] уже посчитаны — дальше сдвигается левый указатель",
                      (("{L} += 2;", "off_by_one"), ("[{L}, {R}] = [{L} + 1, {R} - 1];", "wrong_pointer"),
                       ("{R} -= 2;", "wrong_pointer"))),
            _Mutation(2, "let {L} = 1, {R} = a.length - 1;", "skip_first",
                      "левый указатель начинается с 0, иначе наименьший элемент не попадает ни в одну пару",
                      (("let {L} = 0, {R} = a.length - 2;", "off_by_one"), ("let {L} = 1, {R} = a.length;", "off_by_one"),
                       ("let {L} = -1, {R} = a.length - 1;", "out_of_bounds"))),
            _Mutation(1, "a = [...a].sort();", "sort_without_comparator",
                      "sort() без компаратора сравнивает числа как строки ([9, 10] превращается в [10, 9]), и указатели "
                      "идут по неотсортированному массиву; числа сортируют с компаратором (x, y) => x − y",
                      (("a = [...a].sort((x, y) => y - x);", "wrong_condition"), ("a = [...a];", "wrong_formula"),
                       ("a = [...a].map(Number).sort();", "sort_without_comparator"))),
        ),
    ),
    _JsProblem(
        fn="dedupSorted",
        doc="Функция dedupSorted(a) получает отсортированный массив и на месте переносит различные значения в начало: "
        "после вызова a.slice(0, k) содержит все различные значения по возрастанию, функция возвращает k.",
        lines=(
            "function dedupSorted(a) {",
            "  if (a.length === 0) {",
            "    return 0;",
            "  }",
            "  let {K} = 1;",
            "  for (let i = 1; i < a.length; i++) {",
            "    if (a[i] !== a[{K} - 1]) {",
            "      a[{K}] = a[i];",
            "      {K} += 1;",
            "    }",
            "  }",
            "  return {K};",
            "}",
        ),
        names=({"K": "k"}, {"K": "write"}, {"K": "size"}),
        gen=_PY["dedup_sorted"].gen,
        show=lambda args: f"для a = {js_list(args[0])} вызов dedupSorted(a)",
        run="((b) => { const k = __f(b); return Number.isInteger(k) ? [k, b.slice(0, k)] : [k, null]; })([...__args[0]])",
        fmt=lambda r: f"k = {_show(r[0])}, a.slice(0, k) = {_show(r[1])}",
        verb="дал",
        mutations=(
            _Mutation(6, "if (a[i] !== a[{K}]) {", "compare_wrong_slot",
                      "сравнивать нужно с последним записанным уникальным значением a[{K} − 1]; a[{K}] — "
                      "ячейка, которая ещё не перезаписана",
                      (("if (a[i] === a[{K} - 1]) {", "wrong_condition"), ("if (a[i] !== a[{K} + 1]) {", "off_by_one"),
                       ("if (a[i] > a[{K}]) {", "wrong_condition"))),
            _Mutation(4, "let {K} = 0;", "start_zero",
                      "первый элемент всегда уникален и уже стоит на месте, поэтому счётчик начинается с 1",
                      (("let {K} = 2;", "off_by_one"), ("let {K} = a.length;", "wrong_formula"),
                       ("let {K} = -1;", "off_by_one"))),
            _Mutation(5, "for (let i = 1; i < a.length - 1; i++) {", "skip_last",
                      "условие i < a.length − 1 не доходит до последнего элемента: чтобы обработать его, нужно "
                      "i < a.length",
                      (("for (let i = 2; i < a.length; i++) {", "off_by_one"),
                       ("for (let i = 1; i <= a.length; i++) {", "out_of_bounds"),
                       ("for (let i = 0; i < a.length - 1; i++) {", "off_by_one"))),
            _Mutation(7, "a[i] = a[{K}];", "assignment_direction",
                      "уникальное значение переносится в позицию {K}: a[{K}] = a[i]; обратное присваивание "
                      "портит ещё не обработанные элементы",
                      (("a[{K} - 1] = a[i];", "off_by_one"), ("a[{K}] = a[i - 1];", "off_by_one"),
                       ("a[{K} + 1] = a[i];", "out_of_bounds"))),
        ),
    ),
    _JsProblem(
        fn="merge",
        doc="Функция merge(a, b) получает два отсортированных массива и должна вернуть один отсортированный массив "
        "из всех их элементов.",
        lines=(
            "function merge(a, b) {",
            "  let {I} = 0, {J} = 0;",
            "  const out = [];",
            "  while ({I} < a.length && {J} < b.length) {",
            "    if (a[{I}] <= b[{J}]) {",
            "      out.push(a[{I}]);",
            "      {I} += 1;",
            "    } else {",
            "      out.push(b[{J}]);",
            "      {J} += 1;",
            "    }",
            "  }",
            "  out.push(...a.slice({I}));",
            "  out.push(...b.slice({J}));",
            "  return out;",
            "}",
        ),
        names=({"I": "i", "J": "j"}, {"I": "ia", "J": "ib"}, {"I": "p", "J": "q"}),
        gen=_PY["merge"].gen,
        show=lambda args: f"merge({js_list(args[0])}, {js_list(args[1])})",
        run="__f([...__args[0]], [...__args[1]])",
        fmt=_show,
        mutations=(
            _Mutation(3, "while ({I} < a.length || {J} < b.length) {", "or_instead_of_and",
                      "сравнивать элементы можно, только пока не исчерпан ни один из массивов; хвосты "
                      "дописываются после цикла",
                      (("while ({I} <= a.length && {J} <= b.length) {", "out_of_bounds"),
                       ("while ({I} < a.length && {J} < a.length) {", "wrong_condition"),
                       ("while ({I} < b.length && {J} < a.length) {", "wrong_condition"))),
            _Mutation(13, "out.push(...b.slice({I}));", "wrong_tail_index",
                      "хвост массива b начинается с его собственного индекса, а не с индекса по a",
                      (("out.push(...b);", "wrong_formula"), ("out.push(...b.slice({J} + 1));", "off_by_one"),
                       ("out.push(...a.slice({J}));", "wrong_formula"))),
            _Mutation(9, "{I} += 1;", "advance_wrong_pointer",
                      "в выход ушёл элемент b — сдвигать нужно указатель по b",
                      (("{J} += 2;", "off_by_one"), ("[{I}, {J}] = [{I} + 1, {J} + 1];", "wrong_pointer"),
                       ("{J} = b.length;", "wrong_formula"))),
            _Mutation(4, "if (a[{I}] >= b[{J}]) {", "comparison_flipped",
                      "первым в выход идёт меньший элемент: берём a[{I}], если a[{I}] <= b[{J}]",
                      (("if (a[{I}] > b[{J}]) {", "wrong_condition"), ("if (a[{I}] <= b[{J}] - 1) {", "off_by_one"),
                       ("if (a[{I}] <= b[{J}] + 1) {", "off_by_one"), ("if (b[{J}] <= a[{I}]) {", "wrong_condition"))),
        ),
    ),
    _JsProblem(
        fn="hasPairSum",
        doc="Функция hasPairSum(a, target) получает массив, отсортированный по возрастанию, и должна вернуть true, "
        "если в нём есть два элемента с разными индексами и суммой target.",
        lines=(
            "function hasPairSum(a, target) {",
            "  let {L} = 0, {R} = a.length - 1;",
            "  while ({L} < {R}) {",
            "    const s = a[{L}] + a[{R}];",
            "    if (s === target) {",
            "      return true;",
            "    }",
            "    if (s < target) {",
            "      {L} += 1;",
            "    } else {",
            "      {R} -= 1;",
            "    }",
            "  }",
            "  return false;",
            "}",
        ),
        names=({"L": "lo", "R": "hi"}, {"L": "left", "R": "right"}, {"L": "i", "R": "j"}),
        gen=_PY["has_pair_sum"].gen,
        show=lambda args: f"hasPairSum({js_list(args[0])}, {args[1]})",
        run="__f([...__args[0]], __args[1])",
        fmt=_show,
        mutations=(
            _Mutation(2, "while ({L} <= {R}) {", "same_element_twice",
                      "при {L} === {R} это один и тот же элемент, а пара должна состоять из двух разных позиций",
                      (("while ({L} < {R} - 1) {", "off_by_one"), ("while ({L} <= {R} + 1) {", "off_by_one"),
                       ("while ({R} > 0) {", "wrong_condition"))),
            _Mutation(7, "if (s > target) {", "pointer_direction",
                      "если сумма меньше нужной, её надо увеличить — сдвинуть левый указатель к большим элементам",
                      (("if (s >= target) {", "wrong_condition"), ("if (s < target - 1) {", "off_by_one"),
                       ("if (a[{L}] < a[{R}]) {", "wrong_condition"))),
            # вместо «hi = len(a)» Python-версии: в JS эта ошибка ненаблюдаема (см. docstring модуля)
            _Mutation(1, "let {L} = 1, {R} = a.length - 1;", "skip_first",
                      "левый указатель начинается с 0, иначе наименьший элемент не участвует ни в одной паре",
                      (("let {L} = 0, {R} = a.length - 2;", "off_by_one"), ("let {L} = 1, {R} = a.length;", "off_by_one"),
                       ("let {L} = -1, {R} = a.length - 1;", "out_of_bounds"))),
        ),
    ),
    _JsProblem(
        fn="longestWindow",
        doc="Функция longestWindow(a, limit) получает массив неотрицательных чисел и должна вернуть длину самого "
        "длинного непрерывного отрезка с суммой не больше limit.",
        lines=(
            "function longestWindow(a, limit) {",
            "  let best = 0;",
            "  let total = 0;",
            "  let {L} = 0;",
            "  for (let {R} = 0; {R} < a.length; {R}++) {",
            "    total += a[{R}];",
            "    while (total > limit) {",
            "      total -= a[{L}];",
            "      {L} += 1;",
            "    }",
            "    best = Math.max(best, {R} - {L} + 1);",
            "  }",
            "  return best;",
            "}",
        ),
        names=({"L": "lo", "R": "hi"}, {"L": "left", "R": "right"}, {"L": "start", "R": "end"}),
        gen=_PY["longest_window"].gen,
        show=lambda args: f"longestWindow({js_list(args[0])}, {args[1]})",
        run="__f([...__args[0]], __args[1])",
        fmt=_show,
        mutations=(
            _Mutation(10, "best = Math.max(best, {R} - {L});", "window_length_off_by_one",
                      "в окне [{L}, {R}] включительно {R} − {L} + 1 элементов",
                      (("best = Math.max(best, {R} - {L} + 2);", "off_by_one"),
                       ("best = Math.max(best, {R} + 1);", "wrong_formula"), ("best = {R} - {L} + 1;", "wrong_formula"))),
            _Mutation(6, "while (total >= limit) {", "strict_vs_nonstrict",
                      "отрезок с суммой ровно limit допустим — сжимать окно нужно, только пока total > limit",
                      (("while (total > limit + 1) {", "off_by_one"),
                       ("while (total > limit && {L} < {R}) {", "wrong_condition"),
                       ("while (total - a[{L}] > limit) {", "wrong_condition"))),
            _Mutation(7, "total -= a[{R}];", "subtract_wrong_end",
                      "сжимая окно слева, вычитаем уходящий из окна элемент a[{L}]",
                      (("total -= a[{L} + 1];", "off_by_one"), ("total -= a[{L} - 1];", "off_by_one"),
                       ("total = 0;", "wrong_formula"))),
        ),
    ),
)

HINTS = {
    **{k: v for k, v in py.HINTS.items() if k != "crash"},
    "out_of_bounds": "индекс выходит за границу массива: в JS такое чтение не падает, а даёт undefined, и арифметика "
    "с ним даёт NaN — результат молча портится.",
    "sort_without_comparator": "sort() без компаратора сравнивает элементы как строки («10» < «9»), даже если это "
    "числа; для чисел нужен компаратор (x, y) => x − y.",
}

_N_TESTS = py._N_TESTS
_N_CANDIDATES = 3  # розыгрышей (задача, мутация, тесты) на экземпляр; Node берёт первый годный
_STEP_LIMIT = 100_000  # шагов циклов на один вызов: больше — исход «не завершается»
_WHILE = re.compile(r"^(\s*)while \((.*)\) \{$")
_FOR = re.compile(r"^(\s*)for \(([^;]*); ([^;]*); ([^)]*)\) \{$")

# Раннер: для каждого кандидата исполняет эталон, мутант и исправления на тестах; останавливается на первом годном
# (мутант валится хотя бы на одном тесте, неверных исправлений, которые валятся, — не меньше трёх).
_HARNESS = """const __C = __DATA__;
const __LIMIT = __STEPS__;
let __steps = 0;
function __tick() { if (++__steps > __LIMIT) throw new Error("__hang__"); return true; }
function __enc(v) {
  if (Array.isArray(v)) return Array.from(v, __enc);
  if (typeof v === "number" && !Number.isFinite(v)) return { __num: String(v) };
  if (v === undefined) return { __undef: true };
  return v;
}
function __compile(lines, fn) { return new Function("__tick", lines.join("\\n") + "\\nreturn " + fn + ";")(__tick); }
function __outcome(f, run, args) {
  __steps = 0;
  try { return JSON.stringify(["ok", __enc(run(f, JSON.parse(JSON.stringify(args))))]); }
  catch (e) { return JSON.stringify(["error", e && e.message === "__hang__" ? "hang" : (e && e.name) || "Error"]); }
}
const __res = [];
for (const c of __C) {
  const run = new Function("__f", "__args", "return " + c.run + ";");
  const ref = __compile(c.ref, c.fn);
  const expected = c.tests.map((t) => __outcome(ref, run, t));
  const failing = (lines) => {
    const f = __compile(lines, c.fn);
    const out = [];
    c.tests.forEach((t, i) => { const o = __outcome(f, run, t); if (o !== expected[i]) out.push([i, JSON.parse(o), JSON.parse(expected[i])]); });
    return out;
  };
  const bad = failing(c.bad);
  const fixes = c.fixes.map((ls) => failing(ls).length > 0);
  __res.push({ bad, fixes });
  if (bad.length > 0 && fixes.filter(Boolean).length >= 3) break;
}
console.log(JSON.stringify(__res));"""


_PH = re.compile(r"\{([A-Z])\}")


def _sub(text: str, names: dict[str, str]) -> str:
    """Подставить имена переменных экземпляра вместо {L}/{R}/{K}/{I}/{J} (str.format не годится: в JS свои скобки)."""
    return _PH.sub(lambda m: names[m.group(1)], text)


def _guard(lines: list[str]) -> list[str]:
    """Счётчик шагов в условиях циклов — только в исполняемой копии (кандидат видит код без него)."""
    out = []
    for ln in lines:
        m = _WHILE.match(ln)
        if m:
            ln = f"{m.group(1)}while (__tick() && ({m.group(2)})) {{"
        else:
            m = _FOR.match(ln)
            if m:
                ln = f"{m.group(1)}for ({m.group(2)}; __tick() && ({m.group(3)}); {m.group(4)}) {{"
        out.append(ln)
    return out


def _patched(good: list[str], line: int, text: str) -> list[str]:
    indent = good[line][: len(good[line]) - len(good[line].lstrip())]
    out = list(good)
    out[line] = indent + text
    return out


def _ok(res: dict[str, Any]) -> bool:
    return bool(res["bad"]) and sum(res["fixes"]) >= 3


@register
class TwoPointersBugJs(JsExecFamily):
    id = "algo.two_pointers.bug.js"
    title = "Два указателя на JS: найти ошибочную строку и исправить"
    specs = JS_SPECS
    module = "algo"
    format = "bug_line"
    # Шкала frontend_js: грейд, pos и c перенесены с Python-версии algo.two_pointers.bug — пересчитать по данным.
    target_grade = "middle"
    position = 0.5
    # spec-testing §3.1: тексты исправлений указывают на дефектную строку — угадывание = выбор 1 из 4 исправлений.
    c = 0.25
    time_limit_s = 150

    def normalize(self, answer: Any) -> Any:
        return norm_line_choice(answer)

    def build(self, rng: random.Random) -> JsSpec:
        cands, meta = [], []
        for _ in range(_N_CANDIDATES):
            pi = rng.randrange(len(_PROBLEMS))
            prob = _PROBLEMS[pi]
            names = rng.choice(prob.names)
            mi = rng.randrange(len(prob.mutations))
            mut = prob.mutations[mi]
            good = [_sub(ln, names) for ln in prob.lines]
            bad = _patched(good, mut.line, _sub(mut.bad, names))
            wrong = [(_sub(text, names), tag) for text, tag in mut.wrong]
            tests = [prob.gen(rng) for _ in range(_N_TESTS)]
            cands.append({
                "fn": prob.fn, "run": prob.run, "ref": _guard(good), "bad": _guard(bad),
                "fixes": [_guard(_patched(good, mut.line, text)) for text, _ in wrong], "tests": tests,
            })
            meta.append({"pi": pi, "mi": mi, "names": names, "good": good, "bad": bad, "wrong": wrong,
                         "tests": tests, "shuffle": rng.getrandbits(48)})
        program = _HARNESS.replace("__STEPS__", str(_STEP_LIMIT)).replace(
            "__DATA__", json.dumps(cands, ensure_ascii=False, separators=(",", ":")))
        return JsSpec("", {"check": program}, {"meta": meta})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        results = json.loads(outs["check"])
        k = next((i for i, r in enumerate(results) if _ok(r)), None)
        if k is None:
            raise ExecutionError(f"{self.id} seed={seed}: ни один из {_N_CANDIDATES} кандидатов не прошёл проверку")
        m, res = spec.data["meta"][k], results[k]
        prob = _PROBLEMS[m["pi"]]
        mut = prob.mutations[m["mi"]]
        names, good, bad_lines, tests = m["names"], m["good"], m["bad"], m["tests"]
        wrong = [w for w, failed in zip(m["wrong"], res["fixes"], strict=True) if failed]
        fails = {i: (got, exp) for i, got, exp in res["bad"]}
        # показываем наглядный упавший тест: без падения/зацикливания, если можно; длина ближе к 5
        shown = min(fails, key=lambda i: (fails[i][0][0] == "error", abs(len(tests[i][0]) - 5), repr(tests[i])))
        got, exp = fails[shown]
        if got[0] == "error":
            got_s = "не завершается (бесконечный цикл)" if got[1] == "hang" else f"падает с {got[1]}"
        else:
            got_s = f"{prob.verb} {prob.fmt(got[1])}"
        example = f"{prob.show(tests[shown])} {got_s}; ожидалось {prob.fmt(exp[1])}."

        correct = good[mut.line].strip()
        options, fix_id, opt_tags = build_choice(random.Random(m["shuffle"]), correct, wrong, 4)
        line_no = mut.line + 1
        key = {"line": line_no, "fix": fix_id}
        distractors = {canonical({"line": line_no, "fix": oid}): tag for oid, tag in opt_tags.items()}
        lines = tuple(bad_lines)
        prompt = (
            f"{prob.doc}\n\nВ реализации ровно одна ошибочная строка. Тест упал:\n{example}\n\n"
            "Укажите ошибочную строку и выберите, чем её заменить."
        )
        body = (
            f"Ошибка в строке {line_no}: «{_sub(mut.bad, names)}» — {_sub(mut.why, names)}. "
            f"Правильно: «{correct}»."
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code="\n".join(lines),
                language="javascript",
                options=options,
                lines=lines,
                answer_mode="line_choice",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, opt_tags.values()),
        )
