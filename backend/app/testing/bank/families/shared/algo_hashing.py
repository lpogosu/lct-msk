"""algo.hash.* — хеш-таблица с открытой адресацией: коллизии, линейное пробирование, повторный ключ."""

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

_TEMPLATE = """SIZE = {size}
{table} = [None] * SIZE


def put(key):
    i = key % SIZE
    while {table}[i] is not None and {table}[i] != key:
        i = (i + 1) % SIZE
    {table}[i] = key
    return i


print(*[put(k) for k in {keys}])
"""

HINTS = {
    "no_probing": "при коллизии ключ не перезаписывает соседа — ищется следующая свободная ячейка "
    "(линейное пробирование).",
    "duplicate_new_slot": "повторная вставка того же ключа находит его прежнюю ячейку (условие "
    "table[i] != key) — дубликат не занимает новое место.",
    "probe_backwards": "пробирование идёт вперёд: i = (i + 1) % SIZE, с переходом с конца таблицы в начало.",
}


@register
class LinearProbing(TextIntsFamily):
    id = "algo.hash.linear_probing"
    title = "Хеш-таблица: линейное пробирование и повторный ключ"
    specs = SPECS
    module = "algo"
    format = "predict_output"
    target_grade = "junior"
    position = 0.5
    time_limit_s = 120

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        table = rng.choice(("table", "slots", "buckets", "cells"))
        while True:
            size = rng.choice((7, 11, 13))
            n_unique = 5 if size == 7 else 6
            # домашние ячейки с намеренными коллизиями, одна — в конце таблицы (проверка перехода по модулю)
            homes = [size - 1, size - 1] + [rng.randrange(size) for _ in range(n_unique - 2)]
            if len(set(homes)) > n_unique - 2:
                homes[2] = homes[3]
            keys: list[int] = []
            for h in homes:
                options = [h + size * m for m in range(1, 100) if 10 <= h + size * m <= 99]
                k = rng.choice([o for o in options if o not in keys])
                keys.append(k)
            rng.shuffle(keys)
            slots, tab = [], [None] * size
            for k in keys:
                i = k % size
                while tab[i] is not None:
                    i = (i + 1) % size
                tab[i] = k
                slots.append(i)
            displaced = [k for k, i in zip(keys, slots, strict=True) if i != k % size]
            wrapped = any(i < k % size for k, i in zip(keys, slots, strict=True))
            if len(displaced) < 2 or not wrapped:
                continue
            dup = rng.choice(displaced)  # повтор смещённого ключа: «новая ячейка» дала бы другой ответ
            pos = rng.randint(keys.index(dup) + 1, len(keys))
            seq = keys[:pos] + [dup] + keys[pos:]
            code = _TEMPLATE.format(size=size, table=table, keys=seq)
            key = norm_int_seq(exec_capture(code))
            cond = f"while {table}[i] is not None and {table}[i] != key:"
            mutants = {
                "no_probing": code.replace(cond, "while False:"),
                "duplicate_new_slot": code.replace(cond, f"while {table}[i] is not None:"),
                "probe_backwards": code.replace("i = (i + 1) % SIZE", "i = (i - 1) % SIZE"),
            }
            distractors = text_distractors(key, [(exec_capture(c), t) for t, c in mutants.items()], norm_int_seq)
            if len(distractors) == 3:
                break

        steps, tab = [], [None] * size
        for k in seq:
            i = h = k % size
            path = [i]
            while tab[i] is not None and tab[i] != k:
                i = (i + 1) % size
                path.append(i)
            note = "тот же ключ уже лежит здесь" if tab[i] == k else "свободно"
            tab[i] = k
            steps.append(f"{k}: {k} % {size} = {h}" + (f" → {' → '.join(map(str, path[1:]))}" if len(path) > 1 else "")
                         + f" ({note})")
        body = "\n".join(steps) + f"\nВывод: {key}."
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt="Что выведет программа?",
                code=code.rstrip("\n"),
                language="python",
                answer_mode="text",
                answer_hint="Индексы ячеек одной строкой через пробел — так, как их напечатает print(*...).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
