"""algo.hash.linear_probing.js — JS-версия хеш-таблицы с открытой адресацией: коллизии, линейное пробирование,
повторный ключ. Отбор экземпляров — по Python-двойнику (шаблон algo.hash.linear_probing), ключ и дистракторы —
исполнением JS в Node (shared/_js.py).
"""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, register
from app.testing.bank.families.frontend_js._common import JsSpec, fill
from app.testing.bank.families.shared import algo_hashing as py
from app.testing.bank.families.shared._common import exec_capture, explain, norm_int_seq, text_distractors
from app.testing.bank.families.shared._js import JS_SPECS, JsTextIntsFamily, js_list, require_same

_TEMPLATE = """const SIZE = <<size>>;
const <<table>> = new Array(SIZE).fill(null);

function put(key) {
  let i = key % SIZE;
  while (<<table>>[i] !== null && <<table>>[i] !== key) {
    i = (i + 1) % SIZE;
  }
  <<table>>[i] = key;
  return i;
}

console.log(<<keys>>.map((k) => put(k)).join(" "));"""

HINTS = {
    "no_probing": "при коллизии ключ не перезаписывает соседа — ищется следующая свободная ячейка "
    "(линейное пробирование).",
    "duplicate_new_slot": "повторная вставка того же ключа находит его прежнюю ячейку (условие "
    "table[i] !== key) — дубликат не занимает новое место.",
    "probe_backwards": "пробирование идёт вперёд: i = (i + 1) % SIZE, с переходом с конца таблицы в начало.",
}
_TAGS = ("no_probing", "duplicate_new_slot", "probe_backwards")


@register
class LinearProbingJs(JsTextIntsFamily):
    id = "algo.hash.linear_probing.js"
    title = "Хеш-таблица на JS: линейное пробирование и повторный ключ"
    specs = JS_SPECS
    module = "algo"
    format = "predict_output"
    # Шкала frontend_js: грейд и pos перенесены с Python-версии algo.hash.linear_probing — пересчитать по данным.
    target_grade = "junior"
    position = 0.5
    time_limit_s = 120

    def build(self, rng: random.Random) -> JsSpec:
        table = rng.choice(("table", "slots", "buckets", "cells"))
        while True:  # те же розыгрыши, что у Python-версии
            size = rng.choice((7, 11, 13))
            n_unique = 5 if size == 7 else 6
            # домашние ячейки с намеренными коллизиями, одна — в конце таблицы (проверка перехода по модулю)
            homes = [size - 1, size - 1] + [rng.randrange(size) for _ in range(n_unique - 2)]
            if len(set(homes)) > n_unique - 2:
                homes[2] = homes[3]
            keys: list[int] = []
            for h in homes:
                options = [h + size * m for m in range(1, 100) if 10 <= h + size * m <= 99]
                keys.append(rng.choice([o for o in options if o not in keys]))
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
            # отбор: все три заблуждения дают разные ответы (Python-двойник, как в оригинале)
            py_code = py._TEMPLATE.format(size=size, table="t", keys=seq)
            py_key = norm_int_seq(exec_capture(py_code))
            cond = "while t[i] is not None and t[i] != key:"
            py_mutants = (py_code.replace(cond, "while False:"), py_code.replace(cond, "while t[i] is not None:"),
                          py_code.replace("i = (i + 1) % SIZE", "i = (i - 1) % SIZE"))
            alt = [(exec_capture(c), t) for c, t in zip(py_mutants, _TAGS, strict=True)]
            if len(text_distractors(py_key, alt, norm_int_seq)) == 3:
                break
        code = fill(_TEMPLATE, size=size, table=table, keys=js_list(seq))
        cond_js = f"while ({table}[i] !== null && {table}[i] !== key) {{"
        progs = {
            "key": code,
            "no_probing": code.replace(cond_js, "while (false) {"),
            "duplicate_new_slot": code.replace(cond_js, f"while ({table}[i] !== null) {{"),
            # % в JS сохраняет знак делимого (−1 % 7 = −1): чтобы мутант «пробирование назад» честно шёл по кругу,
            # а не уходил в индекс −1, прибавляем SIZE
            "probe_backwards": code.replace("i = (i + 1) % SIZE;", "i = (i - 1 + SIZE) % SIZE;"),
        }
        return JsSpec(code, progs, {"size": size, "seq": seq, "py_key": py_key})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        size, seq = d["size"], d["seq"]
        key = norm_int_seq(outs["key"])
        require_same(self.id, seed, "ключ", key, d["py_key"])
        distractors = text_distractors(key, [(outs[t], t) for t in _TAGS], norm_int_seq)
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
                code=spec.code,
                language="javascript",
                answer_mode="text",
                answer_hint="Индексы ячеек одной строкой через пробел — так, как их напечатает console.log(… .join(\" \")).",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
