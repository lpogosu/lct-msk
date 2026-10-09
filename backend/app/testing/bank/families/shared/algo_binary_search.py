"""algo.binary_search.* — трассировка двоичного поиска границы (lower/upper bound) с повторами."""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import (
    PY_SPECS,
    TextIntsFamily,
    exec_capture,
    explain,
    norm_int_seq,
    text_distractors,
)

_TEMPLATE = """def {fn}(a, x):
    lo, hi = 0, len(a)
    while lo < hi:
        mid = (lo + hi) // 2
        print(a[mid], end=" ")
        if a[mid] {op} x:
            lo = mid + 1
        else:
            hi = mid
    return lo


{arr} = {values}
print({fn}({arr}, {x}))
"""

HINTS = {
    "bound_confusion": "сравнение < ищет первую позицию с a[i] ≥ x (lower bound), сравнение <= — первую с "
    "a[i] > x (upper bound); при повторах ответы различаются.",
    "closed_hi": "границы полуинтервала [lo, hi): hi начинается с len(a), а не с len(a) − 1 — от этого "
    "зависят все средние точки.",
}


@register
class BinarySearchTrace(TextIntsFamily):
    id = "algo.binary_search.trace"
    title = "Двоичный поиск: какие элементы проверит и что вернёт"
    specs = PY_SPECS  # фронтендеру — JS-версия «<id>.js» (shared/_js.py)
    module = "algo"
    format = "predict_output"
    target_grade = "junior"
    position = 0.0
    time_limit_s = 120
    is_anchor = True

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        fn = rng.choice(("search", "find_pos", "bound", "locate"))
        arr = rng.choice(("a", "prices", "scores", "ts"))
        op = rng.choice(("<", "<="))
        while True:
            n = rng.randint(10, 14)
            v, values = rng.randint(1, 20), []
            for _ in range(n):
                values.append(v)
                v += rng.choice((0, 0, 1, 2, 3, 4, 5, 6))
            distinct = sorted(set(values))
            dups = [d for d in distinct if values.count(d) >= 2]
            if rng.random() < 0.7 and dups:
                x = rng.choice(dups)  # искомое повторяется — граница важна
            else:
                gaps = [d + 1 for d in distinct if d + 1 not in distinct and d + 1 < distinct[-1]]
                if not gaps:
                    continue
                x = rng.choice(gaps)
            code = _TEMPLATE.format(fn=fn, arr=arr, values=values, x=x, op=op)
            key = norm_int_seq(exec_capture(code))
            flipped = code.replace(f"if a[mid] {op} x:", f"if a[mid] {'<=' if op == '<' else '<'} x:")
            closed = code.replace("lo, hi = 0, len(a)", "lo, hi = 0, len(a) - 1")
            alt = [(exec_capture(flipped), "bound_confusion"), (exec_capture(closed), "closed_hi")]
            distractors = text_distractors(key, alt, norm_int_seq)
            if distractors:
                break

        lo, hi, steps = 0, len(values), []
        while lo < hi:
            mid = (lo + hi) // 2
            go_right = values[mid] < x if op == "<" else values[mid] <= x
            steps.append(f"lo={lo}, hi={hi} → mid={mid}, a[mid]={values[mid]} → "
                         + ("lo = mid + 1" if go_right else "hi = mid"))
            lo, hi = (mid + 1, hi) if go_right else (lo, mid)
        what = "первая позиция с a[i] ≥ x" if op == "<" else "первая позиция с a[i] > x"
        body = "\n".join(steps) + f"\nФункция возвращает lo = {lo} ({what}). Вывод: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt="Что выведет программа?",
                code=code.rstrip("\n"),
                language="python",
                answer_mode="text",
                answer_hint="Все числа одной строкой через пробел, в порядке вывода.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
