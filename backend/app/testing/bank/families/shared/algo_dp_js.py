"""algo.dp.knapsack_1d.js — JS-версия одномерной таблицы ДП для рюкзака: направление цикла по вместимости решает всё.

Отбор экземпляров (в том числе отсев вырожденных таблиц) — по Python-двойнику (шаблон algo.dp.knapsack_1d), ключ и
дистракторы — исполнением JS в Node (shared/_js.py).
"""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import JsSpec, fill
from app.testing.bank.families.shared import algo_dp as py
from app.testing.bank.families.shared._common import exec_capture, explain, norm_int_seq, text_distractors
from app.testing.bank.families.shared._js import JS_SPECS, JsTextIntsFamily, js_list, require_same

_TEMPLATE = """function <<fn>>(items, capacity) {
  const <<dp>> = new Array(capacity + 1).fill(0);
  for (const [weight, value] of items) {
    <<loop>>
      <<dp>>[c] = Math.max(<<dp>>[c], <<dp>>[c - weight] + value);
    }
  }
  return <<dp>>;
}

console.log(<<fn>>(<<items>>, <<capacity>>).join(" "));"""
_FORWARD = "for (let c = weight; c <= capacity; c++) {"
_BACKWARD = "for (let c = capacity; c >= weight; c--) {"
_OFF = {True: "for (let c = weight + 1; c <= capacity; c++) {", False: "for (let c = capacity; c > weight; c--) {"}

HINTS = {
    "direction_confusion": "направление внутреннего цикла меняет задачу: по возрастанию c ячейка dp[c − weight] "
    "уже учитывает текущий предмет (его можно брать много раз), по убыванию — ещё нет (каждый предмет "
    "не более одного раза).",
    "range_off_by_one": "вместимость c = weight тоже нужно обработать: предмет помещается в рюкзак ровно "
    "такой вместимости.",
    "overwrite_no_max": "Math.max нужен, чтобы не ухудшить уже найденное значение dp[c]: брать предмет выгодно "
    "не всегда.",
}
_TAGS = ("direction_confusion", "range_off_by_one", "overwrite_no_max")


@register
class KnapsackTableJs(JsTextIntsFamily):
    id = "algo.dp.knapsack_1d.js"
    title = "ДП на JS: таблица рюкзака и направление цикла"
    specs = JS_SPECS
    module = "algo"
    format = "predict_output"
    # Шкала frontend_js: грейд, pos и якорь перенесены с Python-версии algo.dp.knapsack_1d — пересчитать по данным.
    target_grade = "senior"
    position = 0.0
    time_limit_s = 180
    is_anchor = True

    def build(self, rng: random.Random) -> JsSpec:
        fn = rng.choice(("bestValue", "maxValue", "pack", "plan"))
        dp = rng.choice(("dp", "best", "table"))
        forward = rng.random() < 0.5
        py_loop, py_other = (py._FORWARD, py._BACKWARD) if forward else (py._BACKWARD, py._FORWARD)
        py_off = "range(weight + 1, capacity + 1)" if forward else "range(capacity, weight, -1)"
        while True:  # те же розыгрыши и тот же отбор, что у Python-версии
            weights = rng.sample(range(2, 6), 3)
            items = [(w, rng.randint(3, 30)) for w in weights]
            capacity = rng.randint(6, 9)

            def py_run(its: list[tuple[int, int]], loop: str = py_loop, cap: int = capacity) -> str:
                return exec_capture(py._TEMPLATE.format(fn="f", dp="dp", loop=loop, items=its, capacity=cap))

            py_key = norm_int_seq(py_run(items))
            py_code = py._TEMPLATE.format(fn="f", dp="dp", loop=py_loop, items=items, capacity=capacity)
            alt = [
                (py_run(items, py_other), "direction_confusion"),
                (py_run(items, py_off), "range_off_by_one"),
                (exec_capture(py_code.replace("max(dp[c], dp[c - weight] + value)", "dp[c - weight] + value")),
                 "overwrite_no_max"),
            ]
            distractors = text_distractors(py_key, alt, norm_int_seq)
            # вырожденные таблицы (оптимум везде даёт один предмет) не берём: задача должна требовать
            # настоящего сравнения вариантов в ячейках
            singles = {norm_int_seq(py_run([it])) for it in items}
            if "direction_confusion" in distractors.values() and len(distractors) == 3 and py_key not in singles:
                break
        loop = _FORWARD if forward else _BACKWARD
        code = fill(_TEMPLATE, fn=fn, dp=dp, loop=loop, items=js_list([list(it) for it in items]), capacity=capacity)
        progs = {
            "key": code,
            "direction_confusion": code.replace(loop, _BACKWARD if forward else _FORWARD),
            "range_off_by_one": code.replace(loop, _OFF[forward]),
            "overwrite_no_max": code.replace(f"Math.max({dp}[c], {dp}[c - weight] + value)", f"{dp}[c - weight] + value"),
        }
        return JsSpec(code, progs, {"dp": dp, "forward": forward, "items": items, "capacity": capacity, "py_key": py_key})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        dp, forward, items, capacity = d["dp"], d["forward"], d["items"], d["capacity"]
        key = norm_int_seq(outs["key"])
        require_same(self.id, seed, "ключ", key, d["py_key"])
        distractors = text_distractors(key, [(outs[t], t) for t in _TAGS], norm_int_seq)
        kind = (
            f"цикл по c идёт по возрастанию, поэтому {dp}[c − weight] уже может включать текущий предмет — "
            "каждый предмет можно брать сколько угодно раз (неограниченный рюкзак)"
            if forward
            else f"цикл по c идёт по убыванию, поэтому {dp}[c − weight] ещё относится к предыдущим предметам — "
            "каждый предмет берётся не более одного раза (рюкзак 0/1)"
        )
        rows, table = [], [0] * (capacity + 1)
        for w, v in items:
            rng_c = range(w, capacity + 1) if forward else range(capacity, w - 1, -1)
            for c in rng_c:
                table[c] = max(table[c], table[c - w] + v)
            rows.append(f"после предмета (вес {w}, ценность {v}): {js_list(table)}")
        body = f"{kind[0].upper()}{kind[1:]}.\n" + "\n".join(rows) + f"\nВывод: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt="Что выведет программа?",
                code=spec.code,
                language="javascript",
                answer_mode="text",
                answer_hint="Все значения таблицы через пробел — так, как их напечатает console.log(… .join(\" \")).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
