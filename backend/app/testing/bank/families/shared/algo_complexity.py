"""algo.complexity.* — асимптотика кода с «ловушками»: амортизация, гармонический ряд, геометрическая
прогрессия, рекурсия, линейный поиск в списке.

Ключ — точный решатель: у каждого каркаса известен класс сложности, последовательные блоки
складываются (берётся максимум). Каждый каркас дополнительно проверен эмпирически подсчётом
выполненных строк (tests/test_bank_shared.py).
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import PY_SPECS, ChoiceFamily, build_choice, explain

# Классы сложности в порядке роста; (степень n, степень log n), 2ⁿ — особый.
LABELS = ("O(log n)", "O(√n)", "O(n)", "O(n log n)", "O(n√n)", "O(n²)", "O(n² log n)", "O(n³)", "O(2ⁿ)")
_POWERS = {
    "O(log n)": (0.0, 1), "O(√n)": (0.5, 0), "O(n)": (1.0, 0), "O(n log n)": (1.0, 1), "O(n√n)": (1.5, 0),
    "O(n²)": (2.0, 0), "O(n² log n)": (2.0, 1), "O(n³)": (3.0, 0),
}
_BY_POWER = {v: k for k, v in _POWERS.items()}


def seq(*labels: str) -> str:
    """Последовательные блоки: итог — самый тяжёлый."""
    return max(labels, key=LABELS.index)


def mul(a: str, b: str) -> str | None:
    if a not in _POWERS or b not in _POWERS:
        return None
    (pa, la), (pb, lb) = _POWERS[a], _POWERS[b]
    return _BY_POWER.get((pa + pb, la + lb))


@dataclass(frozen=True)
class Skeleton:
    name: str
    code: str  # шаблон: {fn} {i} {j} {pre}
    label: str
    arg: str  # list | int | range — какой вход ждёт функция (для эмпирической проверки)
    n_def: str  # что такое n в формулировке
    misconceptions: tuple[tuple[str, str], ...]
    why: str
    pre: tuple[str, ...] = ("",)  # допустимые предварительные блоки (последовательная часть)


_PRE = {
    "": ("", "O(1)"),
    "sorted": ("    a = sorted(a)\n", "O(n log n)"),
    "filter": ("    a = [x for x in a if x >= 0]\n", "O(n)"),
    "sorted_note": ("    # a отсортирован по возрастанию\n", "O(1)"),
    "filter_items": ("    items = [x for x in items if x is not None]\n", "O(n)"),
}

SKELETONS = (
    Skeleton(
        "amortized_pointer",
        "def {fn}(a, d):\n{pre}    n = len(a)\n    total = 0\n    {j} = 0\n    for {i} in range(n):\n"
        "        while {j} < n and a[{j}] - a[{i}] <= d:\n            {j} += 1\n"
        "        total += {j} - {i} - 1\n    return total\n",
        "O(n)", "list", "n = len(a)",
        (("O(n²)", "nested_loops_multiply"), ("O(n log n)", "guess_log")),
        "указатель {j} только растёт и за всё время работы делает не больше n шагов, поэтому внутренний "
        "while суммарно выполняется O(n) раз (амортизированный анализ)",
        pre=("sorted_note", "sorted"),
    ),
    Skeleton(
        "harmonic",
        "def {fn}(n):\n    cnt = [0] * (n + 1)\n    for {i} in range(1, n + 1):\n"
        "        for {j} in range({i}, n + 1, {i}):\n            cnt[{j}] += 1\n    return cnt\n",
        "O(n log n)", "int", "n — аргумент",
        (("O(n²)", "nested_loops_multiply"), ("O(n)", "inner_ignored")),
        "при данном {i} внутренний цикл делает n/{i} шагов; n/1 + n/2 + … + n/n = n·H(n) ≈ n ln n "
        "(гармонический ряд)",
    ),
    Skeleton(
        "doubling_inner",
        "def {fn}(n):\n    total = 0\n    for {i} in range(n):\n        {j} = 1\n        while {j} < n:\n"
        "            total += 1\n            {j} *= 2\n    return total\n",
        "O(n log n)", "int", "n — аргумент",
        (("O(n²)", "nested_loops_multiply"), ("O(log n)", "outer_ignored"), ("O(n)", "inner_ignored")),
        "внешний цикл — n итераций, внутренний удваивает {j} и делает ⌈log₂ n⌉ шагов",
    ),
    Skeleton(
        "geometric_halving",
        "def {fn}(a):\n{pre}    size = len(a)\n    total = 0\n    while size > 0:\n"
        "        for {i} in range(size):\n            total += a[{i}]\n        size //= 2\n    return total\n",
        "O(n)", "list", "n = len(a)",
        (("O(n log n)", "levels_times_n"), ("O(log n)", "halving_is_log")),
        "проходов log n, но каждый вдвое короче предыдущего: n + n/2 + n/4 + … < 2n",
        pre=("", "filter", "sorted"),
    ),
    Skeleton(
        "divide_linear",
        "def {fn}(a):\n    if len(a) <= 1:\n        return 0\n    mid = len(a) // 2\n    s = 0\n    for x in a:\n"
        "        s += x\n    return s + {fn}(a[:mid]) + {fn}(a[mid:])\n",
        "O(n log n)", "list", "n = len(a)",
        (("O(n)", "recursion_levels_ignored"), ("O(n²)", "two_calls_square"), ("O(log n)", "halving_is_log")),
        "T(n) = 2T(n/2) + O(n): на каждом из log n уровней рекурсии суммарно O(n) работы (как в сортировке "
        "слиянием)",
    ),
    Skeleton(
        "divide_constant",
        "def {fn}(a, lo, hi):\n    # вызов: {fn}(a, 0, len(a)), n = len(a) ≥ 1\n    if hi - lo == 1:\n"
        "        return a[lo]\n    mid = (lo + hi) // 2\n    return {fn}(a, lo, mid) + {fn}(a, mid, hi)\n",
        "O(n)", "range", "n = len(a)",
        (("O(log n)", "halving_is_log"), ("O(n log n)", "levels_times_n")),
        "T(n) = 2T(n/2) + O(1): глубина log n, но вызовов 2n − 1 — по одному на каждый лист-элемент и "
        "внутренний узел",
    ),
    Skeleton(
        "list_membership",
        "def {fn}(items):\n{pre}    seen = []\n    out = []\n    for x in items:\n        if x not in seen:\n"
        "            seen.append(x)\n            out.append(x)\n    return out\n",
        "O(n²)", "list", "n = len(items), все элементы различны",
        (("O(n)", "in_is_constant"), ("O(n log n)", "guess_log")),
        "x not in seen для списка — линейный поиск: при различных элементах 0 + 1 + … + (n − 1) = O(n²) "
        "сравнений; с set было бы O(n)",
        pre=("", "filter_items"),
    ),
    Skeleton(
        "halving_linear",
        "def {fn}(a):\n    if len(a) <= 1:\n        return 0\n    s = sum(a)\n    return s + {fn}(a[: len(a) // 2])\n",
        "O(n)", "list", "n = len(a)",
        (("O(n log n)", "levels_times_n"), ("O(log n)", "halving_is_log")),
        "T(n) = T(n/2) + O(n): sum и срез стоят n + n/2 + n/4 + … < 2n",
    ),
    Skeleton(
        "sqrt_outer",
        "def {fn}(n):\n    total = 0\n    {i} = 1\n    while {i} * {i} <= n:\n        for {j} in range(n):\n"
        "            total += {j} % {i}\n        {i} += 1\n    return total\n",
        "O(n√n)", "int", "n — аргумент",
        (("O(n²)", "nested_loops_multiply"), ("O(n log n)", "guess_log")),
        "внешний цикл идёт, пока {i}² ≤ n, — это √n итераций, внутренний — n шагов",
    ),
    Skeleton(
        "double_recursion",
        "def {fn}(n):\n    if n == 0:\n        return 1\n    return {fn}(n - 1) + {fn}(n - 1)\n",
        "O(2ⁿ)", "small_int", "n — аргумент",
        (("O(n)", "linear_recursion_depth"), ("O(n²)", "two_calls_square")),
        "каждый вызов порождает два вызова с n − 1: на уровне k — 2ᵏ вызовов, всего 2ⁿ⁺¹ − 1",
    ),
    Skeleton(
        "three_sum",
        "def {fn}(a):\n    a = sorted(a)\n    n = len(a)\n    count = 0\n    for {i} in range(n):\n"
        "        lo, hi = {i} + 1, n - 1\n        while lo < hi:\n            s = a[{i}] + a[lo] + a[hi]\n"
        "            if s == 0:\n                count += 1\n                lo += 1\n                hi -= 1\n"
        "            elif s < 0:\n                lo += 1\n            else:\n                hi -= 1\n"
        "    return count\n",
        "O(n²)", "list", "n = len(a)",
        (("O(n³)", "three_pointers_cube"), ("O(n log n)", "inner_ignored")),
        "сортировка O(n log n), затем n итераций, в каждой lo и hi вместе проходят не больше n позиций — O(n²)",
    ),
)

HINTS = {
    "nested_loops_multiply": "вложенный цикл не всегда даёт произведение размеров: считайте, сколько раз "
    "суммарно выполнится тело внутреннего цикла.",
    "guess_log": "логарифм появляется, только когда размер задачи на каждом шаге уменьшается в константу раз.",
    "inner_ignored": "внутренний цикл тоже зависит от n — его нельзя считать константой.",
    "outer_ignored": "внешний цикл выполняется n раз, и каждый раз внутренний делает log n шагов.",
    "levels_times_n": "уровней log n, но работа на них убывает геометрически: n + n/2 + n/4 + … < 2n.",
    "halving_is_log": "деление пополам даёт глубину log n, но время определяется суммарной работой "
    "на всех уровнях.",
    "two_calls_square": "два рекурсивных вызова не возводят время в квадрат: важно, на какие части делится "
    "задача и сколько работы на уровне.",
    "recursion_levels_ignored": "работа O(n) выполняется на каждом из log n уровней рекурсии.",
    "in_is_constant": "x in list — линейный поиск по списку; O(1) в среднем даёт только set/dict.",
    "sequential_multiplied": "последовательные блоки складываются, а не перемножаются: итог определяет "
    "самая тяжёлая часть.",
    "sort_ignored": "sorted() стоит O(n log n) — дороже линейного прохода, и итог определяет она.",
    "linear_recursion_depth": "глубина рекурсии n, но каждый вызов порождает два — число вызовов удваивается "
    "на каждом уровне.",
    "three_pointers_cube": "внутренний цикл двух указателей линейный: lo и hi вместе проходят не больше n "
    "позиций.",
}

_FNS = ("solve", "process", "compute", "run", "work")
_IJ = (("i", "j"), ("p", "q"), ("k", "m"), ("u", "v"))


def build(sk: Skeleton, pre_name: str, fn: str, i: str, j: str) -> tuple[str, str, list[tuple[str, str]]]:
    """Код, ключевой класс и заблуждения для каркаса с предварительным блоком."""
    pre_code, pre_label = _PRE[pre_name]
    code = sk.code.format(fn=fn, i=i, j=j, pre=pre_code)
    label = seq(sk.label, pre_label) if pre_label != "O(1)" else sk.label
    wrong = list(sk.misconceptions)
    if pre_label not in ("O(1)",):
        prod = mul(sk.label, pre_label)
        if prod:
            wrong.insert(0, (prod, "sequential_multiplied"))
        if label != sk.label:
            wrong.insert(0, (sk.label, "sort_ignored"))
    return code, label, wrong


@register
class ComplexityTraps(ChoiceFamily):
    id = "algo.complexity.traps"
    title = "Асимптотика: амортизация, ряды, рекурсия"
    specs = PY_SPECS  # фронтендеру — JS-версия «<id>.js» (shared/_js.py)
    module = "algo"
    format = "complexity"
    target_grade = "senior"
    position = -0.5
    c = round(1 / 6, 3)
    time_limit_s = 120

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        sk = rng.choice(SKELETONS)
        pre_name = rng.choice(sk.pre)
        fn = rng.choice(_FNS)
        i, j = rng.choice(_IJ)
        code, label, wrong = build(sk, pre_name, fn, i, j)
        wrong = [(w, t) for w, t in wrong if w != label]
        # добиваем до 6 вариантов ближайшими по росту классами (без тега заблуждения)
        near = sorted((x for x in LABELS if x != label and x not in {w for w, _ in wrong}),
                      key=lambda x: (abs(LABELS.index(x) - LABELS.index(label)), rng.random()))
        wrong += [(x, "other") for x in near]
        options, key, distractors = build_choice(rng, label, wrong, 6)
        distractors = {k: v for k, v in distractors.items() if v != "other"}
        pre_note = ""
        if pre_name == "sorted":
            pre_note = " Плюс sorted() в начале — O(n log n); последовательные части складываются, берём большую."
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
            rendered=Rendered(prompt=prompt, code=code.rstrip("\n"), language="python", options=options,
                              answer_mode="choice"),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
