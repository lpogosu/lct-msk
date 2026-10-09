"""algo.complexity.traps.js — JS-версия асимптотики с «ловушками»: амортизация, гармонический ряд, геометрическая
прогрессия, рекурсия, линейный поиск в массиве.

Ключ — тот же точный решатель, что у algo.complexity.traps (классы сложности каркасов, сложение последовательных
блоков), каркасы — идиоматичный JS той же сложности (includes вместо `in`, slice вместо среза, sort с компаратором
вместо sorted). Каждый JS-каркас проверен эмпирически подсчётом выполненных операторов в Node
(tests/test_bank_shared_js.py).
"""

from __future__ import annotations

import dataclasses
import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared import algo_complexity as py
from app.testing.bank.families.shared._common import ChoiceFamily, build_choice, explain
from app.testing.bank.families.shared._js import JS_SPECS

# Каркасы JS: {fn} {i} {j} {pre}; метаданные (класс, заблуждения, разбор) — от Python-каркаса с тем же именем.
_CODE = {
    "amortized_pointer": (
        "function {fn}(a, d) {{\n{pre}  const n = a.length;\n  let total = 0;\n  let {j} = 0;\n"
        "  for (let {i} = 0; {i} < n; {i}++) {{\n    while ({j} < n && a[{j}] - a[{i}] <= d) {{\n      {j}++;\n    }}\n"
        "    total += {j} - {i} - 1;\n  }}\n  return total;\n}}\n"
    ),
    "harmonic": (
        "function {fn}(n) {{\n  const cnt = new Array(n + 1).fill(0);\n  for (let {i} = 1; {i} <= n; {i}++) {{\n"
        "    for (let {j} = {i}; {j} <= n; {j} += {i}) {{\n      cnt[{j}] += 1;\n    }}\n  }}\n  return cnt;\n}}\n"
    ),
    "doubling_inner": (
        "function {fn}(n) {{\n  let total = 0;\n  for (let {i} = 0; {i} < n; {i}++) {{\n    let {j} = 1;\n"
        "    while ({j} < n) {{\n      total += 1;\n      {j} *= 2;\n    }}\n  }}\n  return total;\n}}\n"
    ),
    "geometric_halving": (
        "function {fn}(a) {{\n{pre}  let size = a.length;\n  let total = 0;\n  while (size > 0) {{\n"
        "    for (let {i} = 0; {i} < size; {i}++) {{\n      total += a[{i}];\n    }}\n    size = Math.floor(size / 2);\n"
        "  }}\n  return total;\n}}\n"
    ),
    "divide_linear": (
        "function {fn}(a) {{\n  if (a.length <= 1) {{\n    return 0;\n  }}\n  const mid = Math.floor(a.length / 2);\n"
        "  let s = 0;\n  for (const x of a) {{\n    s += x;\n  }}\n"
        "  return s + {fn}(a.slice(0, mid)) + {fn}(a.slice(mid));\n}}\n"
    ),
    "divide_constant": (
        "function {fn}(a, lo, hi) {{\n  // вызов: {fn}(a, 0, a.length), n = a.length ≥ 1\n  if (hi - lo === 1) {{\n"
        "    return a[lo];\n  }}\n  const mid = Math.floor((lo + hi) / 2);\n  return {fn}(a, lo, mid) + {fn}(a, mid, hi);\n}}\n"
    ),
    "list_membership": (
        "function {fn}(items) {{\n{pre}  const seen = [];\n  const out = [];\n  for (const x of items) {{\n"
        "    if (!seen.includes(x)) {{\n      seen.push(x);\n      out.push(x);\n    }}\n  }}\n  return out;\n}}\n"
    ),
    "halving_linear": (
        "function {fn}(a) {{\n  if (a.length <= 1) {{\n    return 0;\n  }}\n  const s = a.reduce((acc, x) => acc + x, 0);\n"
        "  return s + {fn}(a.slice(0, Math.floor(a.length / 2)));\n}}\n"
    ),
    "sqrt_outer": (
        "function {fn}(n) {{\n  let total = 0;\n  let {i} = 1;\n  while ({i} * {i} <= n) {{\n"
        "    for (let {j} = 0; {j} < n; {j}++) {{\n      total += {j} % {i};\n    }}\n    {i} += 1;\n  }}\n  return total;\n}}\n"
    ),
    "double_recursion": (
        "function {fn}(n) {{\n  if (n === 0) {{\n    return 1;\n  }}\n  return {fn}(n - 1) + {fn}(n - 1);\n}}\n"
    ),
    "three_sum": (
        "function {fn}(a) {{\n  a = [...a].sort((x, y) => x - y);\n  const n = a.length;\n  let count = 0;\n"
        "  for (let {i} = 0; {i} < n; {i}++) {{\n    let lo = {i} + 1;\n    let hi = n - 1;\n    while (lo < hi) {{\n"
        "      const s = a[{i}] + a[lo] + a[hi];\n      if (s === 0) {{\n        count += 1;\n        lo += 1;\n"
        "        hi -= 1;\n      }} else if (s < 0) {{\n        lo += 1;\n      }} else {{\n        hi -= 1;\n      }}\n"
        "    }}\n  }}\n  return count;\n}}\n"
    ),
}

# Формулировки, в которых есть синтаксис Python, — в записи JS; остальное у каркасов общее.
_N_DEF = {"n = len(a)": "n = a.length", "n = len(items), все элементы различны": "n = items.length, все элементы различны"}
_WHY = {
    "list_membership": "seen.includes(x) для массива — линейный поиск: при различных элементах 0 + 1 + … + (n − 1) = "
    "O(n²) сравнений; с Set (has) было бы O(n)",
    "halving_linear": "T(n) = T(n/2) + O(n): reduce и slice стоят n + n/2 + n/4 + … < 2n",
}

SKELETONS = tuple(
    dataclasses.replace(sk, code=_CODE[sk.name], n_def=_N_DEF.get(sk.n_def, sk.n_def), why=_WHY.get(sk.name, sk.why))
    for sk in py.SKELETONS
)

_PRE = {
    "": ("", "O(1)"),
    "sorted": ("  a = [...a].sort((x, y) => x - y);\n", "O(n log n)"),
    "filter": ("  a = a.filter((x) => x >= 0);\n", "O(n)"),
    "sorted_note": ("  // a отсортирован по возрастанию\n", "O(1)"),
    "filter_items": ("  items = items.filter((x) => x !== null);\n", "O(n)"),
}

HINTS = {
    **py.HINTS,
    "in_is_constant": "includes() у массива — линейный поиск; O(1) в среднем дают только Set и Map (has).",
    "sort_ignored": "сортировка sort() стоит O(n log n) — дороже линейного прохода, и итог определяет она.",
}

_FNS = ("solve", "compute", "run", "work", "handle")


def build(sk: py.Skeleton, pre_name: str, fn: str, i: str, j: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Код, ключевой класс и заблуждения для JS-каркаса с предварительным блоком (алгебра — как у Python-версии)."""
    pre_code, pre_label = _PRE[pre_name]
    code = sk.code.format(fn=fn, i=i, j=j, pre=pre_code)
    label = py.seq(sk.label, pre_label) if pre_label != "O(1)" else sk.label
    wrong = list(sk.misconceptions)
    if pre_label != "O(1)":
        prod = py.mul(sk.label, pre_label)
        if prod:
            wrong.insert(0, (prod, "sequential_multiplied"))
        if label != sk.label:
            wrong.insert(0, (sk.label, "sort_ignored"))
    return code, label, wrong


@register
class ComplexityTrapsJs(ChoiceFamily):
    id = "algo.complexity.traps.js"
    title = "Асимптотика кода на JS: амортизация, ряды, рекурсия"
    specs = JS_SPECS
    module = "algo"
    format = "complexity"
    # Шкала frontend_js: грейд, pos и c перенесены с Python-версии algo.complexity.traps — пересчитать по данным.
    target_grade = "senior"
    position = -0.5
    c = round(1 / 6, 3)
    time_limit_s = 120

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        sk = rng.choice(SKELETONS)
        pre_name = rng.choice(sk.pre)
        fn = rng.choice(_FNS)
        i, j = rng.choice(py._IJ)
        code, label, wrong = build(sk, pre_name, fn, i, j)
        wrong = [(w, t) for w, t in wrong if w != label]
        # добиваем до 6 вариантов ближайшими по росту классами (без тега заблуждения)
        near = sorted((x for x in py.LABELS if x != label and x not in {w for w, _ in wrong}),
                      key=lambda x: (abs(py.LABELS.index(x) - py.LABELS.index(label)), rng.random()))
        wrong += [(x, "other") for x in near]
        options, key, distractors = build_choice(rng, label, wrong, 6)
        distractors = {k: v for k, v in distractors.items() if v != "other"}
        pre_note = ""
        if pre_name == "sorted":
            pre_note = " Плюс сортировка в начале — O(n log n); последовательные части складываются, берём большую."
        elif pre_name in ("filter", "filter_items"):
            pre_note = " Фильтрация в начале — O(n), она не меняет итог."
        body = f"{sk.why.format(i=i, j=j)}.{pre_note} Итог: {label}."
        prompt = (
            f"Оцените время работы функции {fn} в худшем случае ({sk.n_def}). "
            "Выберите самую точную оценку."
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(prompt=prompt, code=code.rstrip("\n"), language="javascript", options=options,
                              answer_mode="choice"),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
