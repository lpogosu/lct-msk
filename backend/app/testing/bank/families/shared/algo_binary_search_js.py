"""algo.binary_search.trace.js — JS-версия трассировки двоичного поиска границы (lower/upper bound) с повторами.

Задача та же, что у algo.binary_search.trace: какие элементы проверит поиск и что вернёт. Отбор экземпляров — по
Python-двойнику (шаблон оригинального семейства), ключ и дистракторы — исполнением JS в Node (shared/_js.py).
"""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import JsSpec, fill
from app.testing.bank.families.shared import algo_binary_search as py
from app.testing.bank.families.shared._common import exec_capture, explain, norm_int_seq, text_distractors
from app.testing.bank.families.shared._js import JS_SPECS, JsTextIntsFamily, js_list, require_same

_TEMPLATE = """function <<fn>>(a, x) {
  let lo = 0;
  let hi = a.length;
  while (lo < hi) {
    const mid = Math.floor((lo + hi) / 2);
    console.log(a[mid]);
    if (a[mid] <<op>> x) {
      lo = mid + 1;
    } else {
      hi = mid;
    }
  }
  return lo;
}

const <<arr>> = <<values>>;
console.log(<<fn>>(<<arr>>, <<x>>));"""

HINTS = {
    "bound_confusion": "сравнение < ищет первую позицию с a[i] ≥ x (lower bound), сравнение <= — первую с "
    "a[i] > x (upper bound); при повторах ответы различаются.",
    "closed_hi": "границы полуинтервала [lo, hi): hi начинается с a.length, а не с a.length − 1 — от этого "
    "зависят все средние точки.",
}


@register
class BinarySearchTraceJs(JsTextIntsFamily):
    id = "algo.binary_search.trace.js"
    title = "Двоичный поиск на JS: какие элементы проверит и что вернёт"
    specs = JS_SPECS
    module = "algo"
    format = "predict_output"
    # Шкала frontend_js: грейд, pos и якорь перенесены с Python-версии algo.binary_search.trace — пересчитать по данным.
    target_grade = "junior"
    position = 0.0
    time_limit_s = 120
    is_anchor = True

    def build(self, rng: random.Random) -> JsSpec:
        fn = rng.choice(("search", "findPos", "bound", "locate"))
        arr = rng.choice(("a", "prices", "scores", "ts"))
        op = rng.choice(("<", "<="))
        while True:  # те же розыгрыши, что у Python-версии; вырожденные (оба заблуждения дают ключ) — заново
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
            py_code = py._TEMPLATE.format(fn="f", arr="a", values=values, x=x, op=op)
            py_key = norm_int_seq(exec_capture(py_code))
            flipped = py_code.replace(f"if a[mid] {op} x:", f"if a[mid] {'<=' if op == '<' else '<'} x:")
            closed = py_code.replace("lo, hi = 0, len(a)", "lo, hi = 0, len(a) - 1")
            alt = [(exec_capture(flipped), "bound_confusion"), (exec_capture(closed), "closed_hi")]
            if text_distractors(py_key, alt, norm_int_seq):
                break
        code = fill(_TEMPLATE, fn=fn, arr=arr, values=js_list(values), x=x, op=op)
        other = "<=" if op == "<" else "<"
        progs = {
            "key": code,
            "bound_confusion": code.replace(f"if (a[mid] {op} x) {{", f"if (a[mid] {other} x) {{"),
            "closed_hi": code.replace("let hi = a.length;", "let hi = a.length - 1;"),
        }
        return JsSpec(code, progs, {"values": values, "x": x, "op": op, "py_key": py_key})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        values, x, op = d["values"], d["x"], d["op"]
        key = norm_int_seq(outs["key"])
        require_same(self.id, seed, "ключ", key, d["py_key"])
        distractors = text_distractors(
            key, [(outs["bound_confusion"], "bound_confusion"), (outs["closed_hi"], "closed_hi")], norm_int_seq
        )
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
                code=spec.code,
                language="javascript",
                answer_mode="text",
                answer_hint="Все выведенные числа одной строкой через пробел, в порядке вывода.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
