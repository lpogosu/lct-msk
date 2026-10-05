"""python_core, Стажёр и Junior: срезы, словари, ссылки и копии, включения, сортировка, форматирование.

Во всех семействах формат predict_output с вводом ответа (c = 0): ключ — stdout исполненного кода,
дистракторы — stdout кода-мутанта, в котором воплощено типичное заблуждение.
"""

from __future__ import annotations

import ast
import random
from typing import Any

from app.testing.bank.base import Instance, register
from app.testing.bank.families.backend_python._common import (
    OUTPUT_HINT,
    SPECS,
    BankFamily,
    GenerationRetry,
    exec_py,
    norm_output,
    plural,
)

_PEOPLE = ["anna", "boris", "vera", "gleb", "dina", "egor", "zoya", "ilya", "kira", "lev", "mila", "oleg",
           "rita", "timur", "yana", "pavel", "sasha", "nina"]


def _digits(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


# ============================================================== Стажёр


@register
class SlicingString(BankFamily):
    id = "py.slicing.string"
    title = "Срезы строки: разбор трек-номера"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "intern"
    position = 0.0
    time_limit_s = 75
    is_anchor = True
    measures = "Индексация с нуля, невключаемая правая граница среза, отрицательные индексы."
    misconceptions = {
        "stop_inclusive": "правая граница среза считается включённой, а символ с этим индексом в срез не входит",
        "one_based": "индексы отсчитываются с 1, а в Python первый символ имеет индекс 0",
    }

    _CC = ["RU", "KZ", "BY", "AM", "UZ", "KG"]
    _CITY = ["MSK", "SPB", "KZN", "NSK", "EKB", "SAM", "OMS", "KRD", "UFA", "PRM", "VLG", "TMN"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        var = rng.choice(["track", "parcel", "waybill", "shipment", "barcode"])
        s = f"{rng.choice(self._CC)}-{_digits(rng, 5)}-{rng.choice(self._CITY)}-{rng.randint(1, 99):02d}"
        a = rng.randint(1, 8)
        b = a + rng.randint(3, 5)
        k = rng.randint(2, 5)
        i = rng.randint(1, 5)
        j = rng.randint(3, 8)
        if len(s) - j - i < 3:
            raise GenerationRetry

        def code_for(a_: int, b_: int, i_: int, j_: int) -> str:
            return f'{var} = "{s}"\nprint({var}[{a_}:{b_}], {var}[-{k}:], {var}[{i_}:-{j_}])'

        code = code_for(a, b, i, j)
        key = exec_py(code)
        wrongs = [
            (exec_py(code_for(a, b + 1, i, j - 1)), "stop_inclusive"),
            (exec_py(code_for(a - 1, b - 1, i - 1, j)), "one_based"),
        ]
        return self.text_instance(
            seed,
            prompt="Сервис доставки разбирает трек-номер на части. Что напечатает программа?",
            code=code,
            language="python",
            raw_key=key,
            wrongs=wrongs,
            why=(
                "Индексы считаются с 0. В срезе s[a:b] символ с индексом b не входит. "
                f"s[-{k}:] — последние {plural(k, 'символ', 'символа', 'символов')}; "
                "s[i:-j] — от индекса i до j-го символа с конца, не включая его."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class DictBasics(BankFamily):
    id = "py.dict.basics"
    title = "Словарь остатков: get, update, pop"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "intern"
    position = 0.5
    time_limit_s = 90
    measures = "Базовые операции словаря: +=, get со значением по умолчанию, update (замена), pop (удаление)."
    misconceptions = {
        "update_adds": "update не складывает значения, а заменяет значение существующего ключа",
        "pop_keeps": "pop удаляет ключ из словаря и возвращает его значение — после него get вернёт значение по умолчанию",
        "augassign_replaces": "stock[key] += x прибавляет к старому значению, а не записывает x",
    }

    _GOODS = ["apple", "pear", "plum", "kiwi", "fig", "lime", "mango", "peach", "grape", "melon", "lemon", "cherry"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        a, b, c, n1, n2 = rng.sample(self._GOODS, 5)
        va, vb, vc = rng.sample(range(2, 40), 3)
        x, y = rng.randint(2, 15), rng.randint(2, 15)
        z, w = rng.randint(1, 40), rng.randint(1, 40)
        if z == vb:
            raise GenerationRetry
        miss = rng.choice([-1, 0])
        lines = [
            f'stock = {{"{a}": {va}, "{b}": {vb}, "{c}": {vc}}}',
            f'stock["{a}"] += {x}',
            f'stock["{n1}"] = stock.get("{n1}", 0) + {y}',
            f'stock.update({{"{b}": {z}, "{n2}": {w}}})',
            f'removed = stock.pop("{c}", 0)',
            f'print(len(stock), stock["{b}"], stock["{a}"], stock.get("{c}", {miss}), removed)',
        ]

        def mutate(idx: int, new: str) -> str:
            ls = list(lines)
            ls[idx] = new
            return "\n".join(ls)

        code = "\n".join(lines)
        wrongs = [
            (exec_py(mutate(3, f'for key, qty in {{"{b}": {z}, "{n2}": {w}}}.items():\n'
                               f'    stock[key] = stock.get(key, 0) + qty')), "update_adds"),
            (exec_py(mutate(4, f'removed = stock.get("{c}", 0)')), "pop_keeps"),
            (exec_py(mutate(1, f'stock["{a}"] = {x}')), "augassign_replaces"),
        ]
        return self.text_instance(
            seed,
            prompt="Склад ведёт остатки в словаре. Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                "get возвращает значение по умолчанию, если ключа нет; update добавляет новые ключи и ЗАМЕНЯЕТ "
                "значения существующих; pop удаляет ключ и возвращает его значение."
            ),
            answer_hint=OUTPUT_HINT,
        )


# ============================================================== Junior


@register
class AliasingCopy(BankFamily):
    id = "py.aliasing.copy"
    title = "Ссылки и копии списка"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "junior"
    position = -0.5
    time_limit_s = 90
    is_anchor = True
    measures = "Присваивание как ссылка на тот же объект, поверхностная копия, += (изменение на месте) против x = x + [...]."
    misconceptions = {
        "assign_copies": "присваивание b = a не копирует список: обе переменные ссылаются на один объект",
        "copy_is_alias": "срез / list() / .copy() создают новый список, изменения копии не видны в оригинале",
        "plus_eq_rebinds": "для списка += изменяет объект на месте (как extend), поэтому изменение видно через все ссылки",
        "plus_mutates": "a = a + [...] создаёт новый список и перепривязывает имя, старый объект не меняется",
    }

    _NAMES = [("cart", "shared", "snapshot"), ("queue", "alias", "backup"), ("items", "ref", "draft"),
              ("prices", "view", "saved"), ("tasks", "same", "frozen")]
    _COPIES = ["{x}[:]", "list({x})", "{x}.copy()"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        A, B, C = rng.choice(self._NAMES)
        p, q, r, s, t, u = rng.sample(range(1, 100), 6)
        copy_expr = rng.choice(self._COPIES).format(x=A)
        inplace = rng.random() < 0.5
        plus_line = f"{A} += [{u}]" if inplace else f"{A} = {A} + [{u}]"
        other_plus = f"{A} = {A} + [{u}]" if inplace else f"{A} += [{u}]"
        lines = [
            f"{A} = [{p}, {q}, {r}]",
            f"{B} = {A}",
            f"{C} = {copy_expr}",
            f"{B}.append({s})",
            f"{C}[0] = {t}",
            plus_line,
            f"print({A}, {B}, {C})",
        ]

        def mutate(idx: int, new: str) -> str:
            ls = list(lines)
            ls[idx] = new
            return "\n".join(ls)

        code = "\n".join(lines)
        wrongs = [
            (exec_py(mutate(1, f"{B} = {A}[:]")), "assign_copies"),
            (exec_py(mutate(2, f"{C} = {A}")), "copy_is_alias"),
            (exec_py(mutate(5, other_plus)), "plus_eq_rebinds" if inplace else "plus_mutates"),
        ]
        return self.text_instance(
            seed,
            prompt="Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                f"{B} = {A} — вторая ссылка на тот же список; {C} = {copy_expr} — новый список. "
                + (f"{A} += [...] меняет список на месте, это видно и через {B}."
                   if inplace else f"{A} = {A} + [...] создаёт новый список, {B} по-прежнему указывает на старый.")
            ),
            answer_hint=OUTPUT_HINT,
        )


def _norm_dict_output(answer: Any) -> Any:
    """Словарь можно ввести в любом порядке ключей: приводим к отсортированной записи."""
    s = norm_output(answer)
    if not isinstance(s, str) or len(s) > 600 or not s.startswith("{"):
        return s
    try:
        val = ast.literal_eval(s)
    except (ValueError, SyntaxError, MemoryError, RecursionError):
        return s
    if isinstance(val, dict):
        items = sorted(val.items(), key=lambda kv: repr(kv[0]))
        return "{" + ",".join(f"{k!r}:{v!r}" for k, v in items) + "}"
    return s


@register
class ComprehensionFilter(BankFamily):
    id = "py.comprehension.filter"
    title = "Словарное включение с фильтром и повторами ключей"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "junior"
    position = 0.0
    time_limit_s = 100
    measures = "Порядок вычисления dict comprehension: фильтр до записи, повторный ключ перезаписывает значение."
    misconceptions = {
        "first_wins": "при повторе ключа в словарном включении остаётся последнее записанное значение, а не первое",
        "filter_ignored": "условие if отбрасывает элемент до записи в словарь, поэтому неподходящие значения не попадают",
        "accumulate": "включение не суммирует значения по ключу — каждое присваивание заменяет предыдущее",
    }

    _NAMES = [("orders", "name", "amount", "big"), ("payments", "user", "total", "large"),
              ("sessions", "login", "minutes", "long_ones"), ("tickets", "agent", "score", "best")]

    def normalize(self, answer: Any) -> Any:
        return _norm_dict_output(answer)

    def build(self, rng: random.Random, seed: int) -> Instance:
        coll, kname, vname, res = rng.choice(self._NAMES)
        thr = rng.randrange(40, 160, 10)
        x, y, z, w = rng.sample(_PEOPLE, 4)

        def hi() -> int:
            return rng.randint(thr + 1, thr + 250)

        def lo() -> int:
            return rng.randint(5, thr - 1)

        x1, x2 = rng.sample(range(thr + 1, thr + 250), 2)
        y_pass_last = rng.random() < 0.5
        y_entries = [(y, lo()), (y, hi())] if y_pass_last else [(y, hi()), (y, lo())]
        # Перемешиваем позиции, сохраняя относительный порядок повторов одного ключа (он и проверяется).
        pos = list(range(6))
        rng.shuffle(pos)
        px, py_ = sorted(pos[0:2]), sorted(pos[2:4])
        placed: dict[int, tuple[str, int]] = {
            px[0]: (x, x1), px[1]: (x, x2), py_[0]: y_entries[0], py_[1]: y_entries[1],
            pos[4]: (z, hi()), pos[5]: (w, lo()),
        }
        data = ", ".join(f'("{n}", {v})' for n, v in (placed[i] for i in range(6)))
        head = f"{coll} = [{data}]"
        code = f"{head}\n{res} = {{{kname}: {vname} for {kname}, {vname} in {coll} if {vname} > {thr}}}\nprint({res})"
        first_wins = (
            f"{head}\n{res} = {{}}\nfor {kname}, {vname} in {coll}:\n"
            f"    if {vname} > {thr} and {kname} not in {res}:\n        {res}[{kname}] = {vname}\nprint({res})"
        )
        no_filter = f"{head}\n{res} = {{{kname}: {vname} for {kname}, {vname} in {coll}}}\nprint({res})"
        accumulate = (
            f"{head}\n{res} = {{}}\nfor {kname}, {vname} in {coll}:\n"
            f"    if {vname} > {thr}:\n        {res}[{kname}] = {res}.get({kname}, 0) + {vname}\nprint({res})"
        )
        wrongs = [
            (exec_py(first_wins), "first_wins"),
            (exec_py(no_filter), "filter_ignored"),
            (exec_py(accumulate), "accumulate"),
        ]
        return self.text_instance(
            seed,
            prompt="Что напечатает программа? Порядок ключей словаря в ответе не важен.",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                "Включение идёт по списку слева направо: сначала проверяется условие, затем пара записывается "
                "в словарь; повторный ключ перезаписывает прежнее значение."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class SortingKey(BankFamily):
    id = "py.sorting.key"
    title = "Сортировка по составному ключу"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "junior"
    position = 0.3
    time_limit_s = 120
    measures = "sorted с key-кортежем: порядок полей, минус для убывания, вторичный ключ при равенстве первичного."
    misconceptions = {
        "minus_ignored": "минус перед числом в ключе разворачивает порядок по этому полю — сортировка по убыванию",
        "secondary_ignored": "второй элемент кортежа-ключа сравнивается при равенстве первого, а не игнорируется",
        "reverse_all": "минус разворачивает только одно поле; reverse=True развернул бы и вторичный ключ тоже",
    }

    _TEAMS = ["backend", "data", "frontend", "qa", "devops"]
    _VARS = [("staff", "e"), ("people", "p"), ("team", "m"), ("devs", "d")]

    def build(self, rng: random.Random, seed: int) -> Instance:
        coll, v = rng.choice(self._VARS)
        names = rng.sample(_PEOPLE, 6)
        variant = rng.choice(["score_name", "team_score"])
        if variant == "score_name":
            scores = rng.sample(range(1, 30), 5)
            # Ровно одна пара с равным баллом в верхней части; в исходном списке она идёт «против алфавита».
            tie = sorted(scores, reverse=True)[rng.randint(0, 2)]
            scores.append(tie)
            teams = [rng.choice(self._TEAMS) for _ in range(6)]
            rows = list(zip(names, teams, scores, strict=True))
            rng.shuffle(rows)
            tied = [i for i, r in enumerate(rows) if r[2] == tie]
            i1, i2 = tied
            if rows[i1][0] < rows[i2][0]:
                rows[i1], rows[i2] = (rows[i2][0], rows[i1][1], rows[i1][2]), (rows[i1][0], rows[i2][1], rows[i2][2])
            key_ok = f"lambda {v}: (-{v}[2], {v}[0])"
            muts = [
                (f"key=lambda {v}: ({v}[2], {v}[0])", "minus_ignored"),
                (f"key=lambda {v}: -{v}[2]", "secondary_ignored"),
                (f"key=lambda {v}: ({v}[2], {v}[0]), reverse=True", "reverse_all"),
            ]
            task = "по убыванию баллов, при равенстве — по имени"
        else:
            teams3 = rng.sample(self._TEAMS, 3)
            teams = [teams3[0], teams3[0], teams3[1], teams3[1], teams3[2], rng.choice(teams3)]
            scores = rng.sample(range(1, 30), 6)
            rows = list(zip(names, teams, scores, strict=True))
            rng.shuffle(rows)
            key_ok = f"lambda {v}: ({v}[1], -{v}[2])"
            muts = [
                (f"key=lambda {v}: ({v}[1], {v}[2])", "minus_ignored"),
                (f"key=lambda {v}: {v}[1]", "secondary_ignored"),
                (f"key=lambda {v}: ({v}[1], {v}[2]), reverse=True", "reverse_all"),
            ]
            task = "по команде, внутри команды — по убыванию баллов"
        data = "\n".join(f'    ("{n}", "{t}", {s}),' for n, t, s in rows)
        head = f"{coll} = [\n{data}\n]"
        tail = f"print([{v}[0] for {v} in ranked[:4]])"
        code = f"{head}\nranked = sorted({coll}, key={key_ok})\n{tail}"
        wrongs = [(exec_py(f"{head}\nranked = sorted({coll}, {m})\n{tail}"), tag) for m, tag in muts]
        key = exec_py(code)
        if sum(1 for w, _ in wrongs if norm_output(w) != norm_output(key)) < 2:
            raise GenerationRetry  # вторичный ключ должен влиять на ответ
        return self.text_instance(
            seed,
            prompt=f"Список сотрудников (имя, команда, баллы) сортируют {task}. Что напечатает программа?",
            code=code,
            language="python",
            raw_key=key,
            wrongs=wrongs,
            why=(
                "Кортежи сравниваются поэлементно: второй элемент ключа учитывается только при равенстве первого. "
                "Минус перед числом даёт убывание только по этому полю; sorted устойчива."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class FormatSpec(BankFamily):
    id = "py.format.spec"
    title = "f-строки: спецификаторы формата в отчёте"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "junior"
    position = 0.6
    time_limit_s = 120
    measures = "Спецификаторы формата: дополнение нулями, разделитель тысяч, проценты, явный знак."
    misconceptions = {
        "zero_pad_ignored": ":03d дополняет число нулями слева до ширины 3",
        "comma_ignored": "запятая в :,.2f вставляет разделитель тысяч",
        "percent_no_multiply": ":.1% умножает значение на 100 и добавляет знак процента",
        "plus_ignored": ":+d печатает знак и у положительных чисел",
    }

    _PREFIX = ["A", "B", "K", "M", "X", "R", "T"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        sku = f"{rng.choice(self._PREFIX)}{rng.randint(10, 99)}"
        qty = rng.randint(1, 99)
        cents = rng.randint(100_000, 99_999_999)
        total = f"{cents // 100}.{cents % 100:02d}"
        share_i = rng.randint(1000, 9999)
        if share_i % 10 == 5:  # избегаем «ровно посередине» при округлении до 0.1%
            raise GenerationRetry
        share = f"0.{share_i}"
        delta = rng.choice([d for d in range(-40, 41) if d != 0])
        if rng.random() < 0.5:
            delta = abs(delta)
        head = f'sku = "{sku}"\nqty = {qty}\ntotal = {total}\nshare = {share}\ndelta = {delta}'
        specs = {"q": "{qty:03d}", "t": "{total:,.2f}", "s": "{share:.1%}", "d": "{delta:+d}"}

        def line(**over: str) -> str:
            sp = {**specs, **over}
            return f'{head}\nprint(f"{{sku}}: {sp["q"]} | {sp["t"]} | {sp["s"]} | {sp["d"]}")'

        code = line()
        wrongs = [
            (exec_py(line(q="{qty:d}")), "zero_pad_ignored"),
            (exec_py(line(t="{total:.2f}")), "comma_ignored"),
            (exec_py(line(s="{share:.1f}%")), "percent_no_multiply"),
            (exec_py(line(d="{delta:d}")), "plus_ignored"),
        ]
        return self.text_instance(
            seed,
            prompt="Строка отчёта собирается f-строкой. Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                ":03d — целое шириной 3 с нулями; :,.2f — два знака после точки и запятая между тысячами; "
                ":.1% — умножить на 100, один знак, знак %; :+d — знак у любого числа."
            ),
            answer_hint=OUTPUT_HINT,
        )
