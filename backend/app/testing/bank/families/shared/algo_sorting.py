"""algo.sort.* — устойчивость сортировки: многоключевая сортировка двумя проходами и reverse=True."""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, Rendered, canonical, register
from app.testing.bank.families.shared._common import (
    PY_SPECS,
    OrderFamily,
    exec_capture,
    explain,
    norm_line_text,
)

_NAMES = (
    "Ann", "Bob", "Kim", "Leo", "Max", "Mia", "Ola", "Pat", "Rex", "Sam", "Tom", "Uma", "Vic", "Zoe",
    "Ivy", "Eva", "Dan", "Gus", "Liv", "Ned", "Ray", "Amy",
)
_GROUPS = ("web", "ml", "mobile", "devops", "data", "qa")
_VARS = ("rows", "team", "board", "results")

HINTS = {
    "reverse_breaks_ties": "reverse=True сохраняет устойчивость: равные по ключу элементы остаются в исходном "
    "порядке, а не переворачиваются (это не то же, что sort() + reverse()).",
    "first_key_primary": "при сортировке несколькими проходами главный ключ — ПОСЛЕДНИЙ проход; предыдущие "
    "проходы определяют порядок только внутри равных по нему элементов.",
    "alphabetical_ties": "sort с key сравнивает только ключи; равные по ключу записи не упорядочиваются по "
    "остальным полям, а сохраняют прежний порядок (сортировка устойчива).",
}


@register
class SortStability(OrderFamily):
    id = "algo.sort.stability"
    title = "Устойчивая сортировка: два прохода и reverse=True"
    specs = PY_SPECS  # фронтендеру — JS-версия «<id>.js» (shared/_js.py)
    module = "algo"
    format = "parsons"
    target_grade = "middle"
    position = -0.5
    time_limit_s = 120

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        var = rng.choice(_VARS)
        while True:
            names = rng.sample(_NAMES, 7)
            groups = rng.sample(_GROUPS, rng.choice((2, 3)))
            scores = rng.sample(range(5, 21), 3)
            rows = [(n, rng.choice(groups), rng.choice(scores)) for n in names]
            if rng.random() < 0.5:  # вариант A: баллы по убыванию, затем группа по возрастанию
                p1 = f"{var}.sort(key=lambda r: r[2], reverse=True)"
                p2 = f"{var}.sort(key=lambda r: r[1])"
                rev_line, rev_fix = p1, f"{var}.sort(key=lambda r: r[2])\n{var}.reverse()"
                story = "сначала по баллам (по убыванию), затем по направлению (по алфавиту)"
            else:  # вариант B: баллы по возрастанию, затем группа по убыванию
                p1 = f"{var}.sort(key=lambda r: r[2])"
                p2 = f"{var}.sort(key=lambda r: r[1], reverse=True)"
                rev_line, rev_fix = p2, f"{var}.sort(key=lambda r: r[1])\n{var}.reverse()"
                story = "сначала по баллам (по возрастанию), затем по направлению (в обратном алфавитном порядке)"
            data = "".join(f'    ("{n}", "{g}", {s}),\n' for n, g, s in rows)
            head = f"{var} = [\n{data}]\n"
            tail = f"\nfor name, track, score in {var}:\n    print(name, track, score)\n"
            code = head + p1 + "\n" + p2 + "\n" + tail
            key = [norm_line_text(x) for x in exec_capture(code).splitlines()]
            mutants = {
                "reverse_breaks_ties": code.replace(rev_line + "\n", rev_fix + "\n"),
                "first_key_primary": head + p2 + "\n" + p1 + "\n" + tail,
                "alphabetical_ties": head + f"{var}.sort(key=lambda r: r[0])\n" + p1 + "\n" + p2 + "\n" + tail,
            }
            alt = {t: [norm_line_text(x) for x in exec_capture(c).splitlines()] for t, c in mutants.items()}
            lines = tuple(f"{n} {g} {s}" for n, g, s in rows)
            # строки показываются в исходном порядке: если ответ с ним совпадает, «ничего не трогать» верно
            if (all(v != key for v in alt.values()) and len({canonical(v) for v in alt.values()}) == 3
                    and key != list(lines)):
                break

        distractors = {canonical(v): t for t, v in alt.items()}
        prompt = (
            "Таблицу участников хакатона (имя, направление, баллы) сортируют двумя проходами — "
            f"{story}. Расположите строки в том порядке, в котором их напечатает программа."
        )
        body = (
            "sort в Python устойчив, в том числе с reverse=True: равные по ключу элементы сохраняют "
            "взаимный порядок. Поэтому второй проход задаёт главный ключ, а первый — порядок внутри групп; "
            "при равных баллах порядок такой же, как в исходном списке.\nПравильный порядок:\n" + "\n".join(key)
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=code.rstrip("\n"),
                language="python",
                lines=lines,
                answer_mode="order",
                answer_hint="Расставьте строки сверху вниз в порядке вывода программы.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
