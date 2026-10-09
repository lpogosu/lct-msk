"""algo.sort.stability.js — JS-версия: устойчивость сортировки, многоключевая сортировка двумя проходами.

Ловушка Python-версии — reverse=True. В JS его нет: по убыванию сортируют компаратором (b, a), и это тоже устойчиво —
Array.prototype.sort обязана быть устойчивой начиная с ES2019. Заблуждение «сортировка по убыванию переворачивает
равные» воплощено честным мутантом: sort по возрастанию + reverse() (он действительно переворачивает равные).
Остальные ловушки те же: главный ключ — последний проход; равные по ключу не упорядочиваются по имени.
Отбор экземпляров — по Python-двойнику (те же строки и проходы на Python), ключ и дистракторы — исполнением JS в Node.
"""

from __future__ import annotations

import json
import random

from app.testing.bank.base import Instance, Rendered, canonical, register
from app.testing.bank.families.frontend_js._common import JsExecFamily, JsSpec
from app.testing.bank.families.shared import algo_sorting as py
from app.testing.bank.families.shared._common import OrderFamily, exec_capture, explain, norm_line_text
from app.testing.bank.families.shared._js import JS_SPECS, require_same

HINTS = {
    "reverse_breaks_ties": "сортировка по убыванию (компаратор с переставленными a и b) тоже устойчива: равные "
    "по ключу элементы остаются в исходном порядке, а не переворачиваются (это не то же, что сортировка по "
    "возрастанию и reverse()).",
    "first_key_primary": "при сортировке несколькими проходами главный ключ — ПОСЛЕДНИЙ проход; предыдущие "
    "проходы определяют порядок только внутри равных по нему элементов.",
    "alphabetical_ties": "sort с компаратором сравнивает только ключи; равные по ключу записи не упорядочиваются по "
    "остальным полям, а сохраняют прежний порядок (сортировка устойчива).",
}


def _lines(out: str) -> list[str]:
    return [norm_line_text(x) for x in out.splitlines()]


@register
class SortStabilityJs(JsExecFamily, OrderFamily):
    id = "algo.sort.stability.js"
    title = "Устойчивая сортировка в JS: два прохода и сортировка по убыванию"
    specs = JS_SPECS
    module = "algo"
    format = "parsons"
    # Шкала frontend_js: грейд и pos перенесены с Python-версии algo.sort.stability — пересчитать по данным.
    target_grade = "middle"
    position = -0.5
    time_limit_s = 120

    def build(self, rng: random.Random) -> JsSpec:
        var = rng.choice(py._VARS)
        while True:  # те же розыгрыши и тот же отбор, что у Python-версии
            names = rng.sample(py._NAMES, 7)
            groups = rng.sample(py._GROUPS, rng.choice((2, 3)))
            scores = rng.sample(range(5, 21), 3)
            rows = [(n, rng.choice(groups), rng.choice(scores)) for n in names]
            variant_a = rng.random() < 0.5
            # Python-двойник (как в оригинале): ключ и три заблуждения; отбор — все различны и ответ ≠ исходный порядок
            if variant_a:  # баллы по убыванию, затем направление по возрастанию
                p1, p2 = "rows.sort(key=lambda r: r[2], reverse=True)", "rows.sort(key=lambda r: r[1])"
                rev_line, rev_fix = p1, "rows.sort(key=lambda r: r[2])\nrows.reverse()"
            else:  # баллы по возрастанию, затем направление по убыванию
                p1, p2 = "rows.sort(key=lambda r: r[2])", "rows.sort(key=lambda r: r[1], reverse=True)"
                rev_line, rev_fix = p2, "rows.sort(key=lambda r: r[1])\nrows.reverse()"
            head = "rows = [\n" + "".join(f'    ("{n}", "{g}", {s}),\n' for n, g, s in rows) + "]\n"
            tail = "\nfor name, track, score in rows:\n    print(name, track, score)\n"
            py_code = head + p1 + "\n" + p2 + "\n" + tail
            py_key = _lines(exec_capture(py_code))
            py_alt = {
                "reverse_breaks_ties": _lines(exec_capture(py_code.replace(rev_line + "\n", rev_fix + "\n"))),
                "first_key_primary": _lines(exec_capture(head + p2 + "\n" + p1 + "\n" + tail)),
                "alphabetical_ties": _lines(exec_capture(head + "rows.sort(key=lambda r: r[0])\n" + p1 + "\n" + p2 + "\n" + tail)),
            }
            lines = [f"{n} {g} {s}" for n, g, s in rows]
            # строки показываются в исходном порядке: если ответ с ним совпадает, «ничего не трогать» верно
            if (all(v != py_key for v in py_alt.values()) and len({canonical(v) for v in py_alt.values()}) == 3
                    and py_key != lines):
                break

        def cmp(field: str, desc: bool) -> str:
            a, b = ("b", "a") if desc else ("a", "b")
            if field == "score":
                return f"{var}.sort((a, b) => {a}.score - {b}.score);"
            return f"{var}.sort((a, b) => {a}.{field}.localeCompare({b}.{field}));"

        if variant_a:
            j1, j2 = cmp("score", True), cmp("track", False)
            j_rev, j_fix = j1, cmp("score", False) + f"\n{var}.reverse();"
            story = "сначала по баллам (по убыванию), затем по направлению (по алфавиту)"
        else:
            j1, j2 = cmp("score", False), cmp("track", True)
            j_rev, j_fix = j2, cmp("track", False) + f"\n{var}.reverse();"
            story = "сначала по баллам (по возрастанию), затем по направлению (в обратном алфавитном порядке)"
        data = "".join(f"  {{ name: {json.dumps(n)}, track: {json.dumps(g)}, score: {s} }},\n" for n, g, s in rows)
        j_head = f"const {var} = [\n{data}];\n"
        j_tail = f"\nfor (const {{ name, track, score }} of {var}) {{\n  console.log(name, track, score);\n}}\n"
        code = j_head + j1 + "\n" + j2 + "\n" + j_tail
        progs = {
            "key": code,
            "reverse_breaks_ties": code.replace(j_rev + "\n", j_fix + "\n"),
            "first_key_primary": j_head + j2 + "\n" + j1 + "\n" + j_tail,
            "alphabetical_ties": j_head + cmp("name", False) + "\n" + j1 + "\n" + j2 + "\n" + j_tail,
        }
        return JsSpec(code.rstrip("\n"), progs, {"lines": lines, "story": story, "py_key": py_key})

    def assemble(self, seed: int, spec: JsSpec, outs: dict[str, str]) -> Instance:
        d = spec.data
        key = _lines(outs["key"])
        require_same(self.id, seed, "порядок строк", key, d["py_key"])
        alt = {t: _lines(outs[t]) for t in ("reverse_breaks_ties", "first_key_primary", "alphabetical_ties")}
        distractors = {canonical(v): t for t, v in alt.items() if v != key}
        prompt = (
            "Таблицу участников хакатона (имя, направление, баллы) сортируют двумя проходами — "
            f"{d['story']}. Расположите строки в том порядке, в котором их напечатает программа."
        )
        body = (
            "Array.prototype.sort устойчива (так требует стандарт начиная с ES2019), в том числе при сортировке по "
            "убыванию компаратором: равные по ключу элементы сохраняют взаимный порядок. Поэтому второй проход задаёт "
            "главный ключ, а первый — порядок внутри групп; при равных баллах порядок такой же, как в исходном массиве."
            "\nПравильный порядок:\n" + "\n".join(key)
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code=spec.code,
                language="javascript",
                lines=tuple(d["lines"]),
                answer_mode="order",
                answer_hint="Расставьте строки сверху вниз в порядке вывода программы.",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, distractors.values()),
        )
