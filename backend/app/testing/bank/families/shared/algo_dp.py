"""algo.dp.* — одномерная таблица ДП для рюкзака: направление цикла по вместимости решает всё."""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.shared._common import (
    SPECS,
    TextIntsFamily,
    exec_capture,
    explain,
    norm_int_seq,
    text_distractors,
)

_TEMPLATE = """def {fn}(items, capacity):
    {dp} = [0] * (capacity + 1)
    for weight, value in items:
        for c in {loop}:
            {dp}[c] = max({dp}[c], {dp}[c - weight] + value)
    return {dp}


print({fn}({items}, {capacity}))
"""
_FORWARD = "range(weight, capacity + 1)"
_BACKWARD = "range(capacity, weight - 1, -1)"

HINTS = {
    "direction_confusion": "направление внутреннего цикла меняет задачу: по возрастанию c ячейка dp[c − weight] "
    "уже учитывает текущий предмет (его можно брать много раз), по убыванию — ещё нет (каждый предмет "
    "не более одного раза).",
    "range_off_by_one": "вместимость c = weight тоже нужно обработать: предмет помещается в рюкзак ровно "
    "такой вместимости.",
    "overwrite_no_max": "max нужен, чтобы не ухудшить уже найденное значение dp[c]: брать предмет выгодно "
    "не всегда.",
}


@register
class KnapsackTable(TextIntsFamily):
    id = "algo.dp.knapsack_1d"
    title = "ДП: таблица рюкзака и направление цикла"
    specs = SPECS
    module = "algo"
    format = "predict_output"
    target_grade = "senior"
    position = 0.0
    time_limit_s = 180
    is_anchor = True

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        fn = rng.choice(("best_value", "max_value", "pack", "plan"))
        dp = rng.choice(("dp", "best", "table"))
        forward = rng.random() < 0.5
        loop, other = (_FORWARD, _BACKWARD) if forward else (_BACKWARD, _FORWARD)
        off = "range(weight + 1, capacity + 1)" if forward else "range(capacity, weight, -1)"
        while True:
            weights = rng.sample(range(2, 6), 3)
            items = [(w, rng.randint(3, 30)) for w in weights]
            capacity = rng.randint(6, 9)
            code = _TEMPLATE.format(fn=fn, dp=dp, loop=loop, items=items, capacity=capacity)
            key = norm_int_seq(exec_capture(code))
            mutants = {
                "direction_confusion": code.replace(loop, other),
                "range_off_by_one": code.replace(loop, off),
                "overwrite_no_max": code.replace(f"max({dp}[c], {dp}[c - weight] + value)", f"{dp}[c - weight] + value"),
            }
            distractors = text_distractors(
                key, [(exec_capture(c), t) for t, c in mutants.items()], norm_int_seq
            )
            # вырожденные таблицы (оптимум везде даёт один предмет) не берём: задача должна требовать
            # настоящего сравнения вариантов в ячейках
            singles = {
                norm_int_seq(exec_capture(_TEMPLATE.format(fn=fn, dp=dp, loop=loop, items=[it], capacity=capacity)))
                for it in items
            }
            if "direction_confusion" in distractors.values() and len(distractors) == 3 and key not in singles:
                break

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
            rows.append(f"после предмета (вес {w}, ценность {v}): {table}")
        body = f"{kind[0].upper()}{kind[1:]}.\n" + "\n".join(rows) + f"\nВывод: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt="Что выведет программа?",
                code=code.rstrip("\n"),
                language="python",
                answer_mode="text",
                answer_hint="Список целиком, как его напечатает print: [0, …]. Достаточно чисел через пробел.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
