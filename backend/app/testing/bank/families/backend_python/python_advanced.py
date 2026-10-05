"""python_core, Middle и Senior: значения по умолчанию, сложность операций с контейнерами, try/finally,
позднее связывание замыканий, генераторы, dataclass(order=True), MRO и super(), asyncio-задачи.
"""

from __future__ import annotations

import random

from app.testing.bank.base import Instance, register
from app.testing.bank.families.backend_python._common import (
    OUTPUT_HINT,
    SPECS,
    BankFamily,
    GenerationRetry,
    exec_py,
    norm_output,
    tpl,
    try_exec_py,
)

_WORDS = ["users", "orders", "stock", "prices", "cart", "auth", "geo", "promo", "search", "media"]


def _fill(lines: list[str], idx: int, new: str) -> str:
    out = list(lines)
    out[idx] = new
    return "\n".join(out)


# ============================================================== Middle


@register
class MutableDefault(BankFamily):
    id = "py.defaults.mutable"
    title = "Изменяемое значение аргумента по умолчанию"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "middle"
    position = -0.5
    time_limit_s = 100
    is_anchor = True
    enemies = ("py.closures.late_binding",)
    measures = "Значение по умолчанию вычисляется один раз при def; общий объект между вызовами; идентичность объектов."
    misconceptions = {
        "fresh_default": "значение по умолчанию вычисляется один раз при определении функции, и все вызовы без "
        "аргумента делят один и тот же объект",
        "explicit_shared": "при явно переданном аргументе функция работает с ним, общий объект по умолчанию не меняется",
        "snapshot_return": "функция возвращает сам общий объект, а не копию: следующие вызовы меняют и ранее "
        "полученный результат",
    }

    _LIST = [("record", "points", "history"), ("track", "value", "seen"), ("add_score", "score", "log"),
             ("collect", "item", "bucket")]
    _DICT = [("remember", "user", "score", "cache"), ("register", "name", "port", "registry"),
             ("memo", "key", "value", "store")]

    def build(self, rng: random.Random, seed: int) -> Instance:
        if rng.random() < 0.5:
            fn, val, acc = rng.choice(self._LIST)
            p = rng.sample(range(1, 100), 4)
            head = [f"def {fn}({val}, {acc}=[]):", f"    {acc}.append({val})", f"    return {acc}"]
            calls = [f"a = {fn}({p[0]})", f"b = {fn}({p[1]})", f"c = {fn}({p[2]}, [])", f"d = {fn}({p[3]})",
                     "print(a, c, len(b), a is d)"]
            fresh = [f"def {fn}({val}, {acc}=None):", f"    if {acc} is None:", f"        {acc} = []",
                     f"    {acc}.append({val})", f"    return {acc}"]
            snapshot = [*head[:2], f"    return list({acc})"]
            explicit = list(calls)
            explicit[2] = f"c = {fn}({p[2]})"
        else:
            fn, k, v, acc = rng.choice(self._DICT)
            names = rng.sample(["anna", "oleg", "vera", "gleb", "kira", "lev", "mila", "yana"], 3)
            s = rng.sample(range(1, 100), 3)
            head = [f"def {fn}({k}, {v}, {acc}={{}}):", f"    {acc}[{k}] = {v}", f"    return {acc}"]
            calls = [f'a = {fn}("{names[0]}", {s[0]})', f'b = {fn}("{names[1]}", {s[1]}, {{}})',
                     f'c = {fn}("{names[2]}", {s[2]})', "print(a, b, len(c), a is c)"]
            fresh = [f"def {fn}({k}, {v}, {acc}=None):", f"    if {acc} is None:", f"        {acc} = {{}}",
                     f"    {acc}[{k}] = {v}", f"    return {acc}"]
            snapshot = [*head[:2], f"    return dict({acc})"]
            explicit = list(calls)
            explicit[1] = f'b = {fn}("{names[1]}", {s[1]})'
        code = "\n".join([*head, "", *calls])
        wrongs = [
            (exec_py("\n".join([*fresh, "", *calls])), "fresh_default"),
            (exec_py("\n".join([*head, "", *explicit])), "explicit_shared"),
            (exec_py("\n".join([*snapshot, "", *calls])), "snapshot_return"),
        ]
        return self.text_instance(
            seed,
            prompt="Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                "Значение по умолчанию создаётся один раз — при выполнении def — и хранится в функции. Все вызовы "
                "без этого аргумента меняют один и тот же объект и возвращают его же; явно переданный объект "
                "с ним не связан. Правильный шаблон — None по умолчанию и новый объект внутри функции."
            ),
            answer_hint=OUTPUT_HINT,
        )


# Стоимость примитивов в худшем случае (CPython, wiki TimeComplexity), как степени (n^a · log^b n).
_PRIM: dict[str, tuple[int, int]] = {
    "const": (0, 0), "list_in": (1, 0), "set_in": (0, 0), "set_add": (0, 0), "append": (0, 0),
    "pop0": (1, 0), "popleft": (0, 0), "insert0": (1, 0), "insort": (1, 0), "heappush": (0, 1),
    "heappop": (0, 1), "sorted": (1, 1), "dict_get": (0, 0), "dict_set": (0, 0), "prefix_slice": (1, 0),
    "max_list": (1, 0), "copy": (1, 0), "set_build": (1, 0),
}
_LOOP_N = (1, 0)
_ONCE = (0, 0)
_CLASSES = ["O(1)", "O(log n)", "O(n)", "O(n log n)", "O(n²)", "O(n³)"]
_CLASS_OF = {(0, 0): "O(1)", (0, 1): "O(log n)", (1, 0): "O(n)", (1, 1): "O(n log n)", (2, 0): "O(n²)", (3, 0): "O(n³)"}

# (имена функции, код с $fn/$arg/$x, фазы [(число итераций, [примитивы тела])], {неверный класс: тег})
_SNIPPETS: list[tuple[list[str], str, list[tuple[tuple[int, int], list[str]]], dict[str, str]]] = [
    (["unique_emails", "dedupe", "distinct_ids"], """
        def $fn($arg):
            seen = []
            for $x in $arg:
                if $x not in seen:
                    seen.append($x)
            return seen
     """, [(_LOOP_N, ["list_in", "append"])], {"O(n)": "list_in_constant"}),
    (["unique_emails", "dedupe", "distinct_ids"], """
        def $fn($arg):
            seen = set()
            result = []
            for $x in $arg:
                if $x not in seen:
                    seen.add($x)
                    result.append($x)
            return result
     """, [(_LOOP_N, ["set_in", "set_add", "append"])], {"O(n²)": "set_in_linear"}),
    (["drain_jobs", "process_queue", "consume"], """
        def $fn($arg):
            queue = list($arg)
            done = 0
            while queue:
                $x = queue.pop(0)
                done += $x
            return done
     """, [(_ONCE, ["copy"]), (_LOOP_N, ["pop0"])], {"O(n)": "pop0_constant"}),
    (["drain_jobs", "process_queue", "consume"], """
        from collections import deque

        def $fn($arg):
            queue = deque($arg)
            done = 0
            while queue:
                $x = queue.popleft()
                done += $x
            return done
     """, [(_ONCE, ["copy"]), (_LOOP_N, ["popleft"])], {"O(n²)": "popleft_linear"}),
    (["newest_first", "reverse_feed", "stack_events"], """
        def $fn($arg):
            result = []
            for $x in $arg:
                result.insert(0, $x)
            return result
     """, [(_LOOP_N, ["insert0"])], {"O(n)": "insert_front_constant"}),
    (["keep_sorted", "ordered_scores", "insert_all"], """
        import bisect

        def $fn($arg):
            ordered = []
            for $x in $arg:
                bisect.insort(ordered, $x)
            return ordered
     """, [(_LOOP_N, ["insort"])], {"O(n log n)": "insort_logarithmic", "O(n)": "insort_constant"}),
    (["heap_sort", "by_priority", "schedule"], """
        import heapq

        def $fn($arg):
            heap = []
            for $x in $arg:
                heapq.heappush(heap, $x)
            return [heapq.heappop(heap) for _ in range(len(heap))]
     """, [(_LOOP_N, ["heappush"]), (_LOOP_N, ["heappop"])], {"O(n)": "heap_constant", "O(n²)": "heap_linear"}),
    (["rank_positions", "positions", "rank_of"], """
        def $fn($arg):
            ordered = sorted($arg)
            index = {value: pos for pos, value in enumerate(ordered)}
            return [index[$x] for $x in $arg]
     """, [(_ONCE, ["sorted"]), (_LOOP_N, ["dict_set"]), (_LOOP_N, ["dict_get"])],
     {"O(n)": "sort_ignored", "O(n²)": "lookup_linear"}),
    (["running_max", "peaks", "best_so_far"], """
        def $fn($arg):
            best = []
            for i in range(len($arg)):
                best.append(max($arg[: i + 1]))
            return best
     """, [(_LOOP_N, ["prefix_slice", "max_list"])], {"O(n)": "builtin_constant"}),
    (["most_common", "top_value", "mode"], """
        def $fn($arg):
            counts = {}
            for $x in $arg:
                counts[$x] = counts.get($x, 0) + 1
            return max(counts, key=counts.get)
     """, [(_LOOP_N, ["dict_get", "dict_set"]), (_ONCE, ["max_list"])], {"O(n²)": "dict_linear"}),
    (["drop_banned", "filter_blocked", "allowed"], """
        def $fn($arg, banned):
            blocked = set(banned)
            return [$x for $x in $arg if $x not in blocked]
     """, [(_ONCE, ["set_build"]), (_LOOP_N, ["set_in"])], {"O(n²)": "in_always_linear"}),
]


def _total_cost(phases: list[tuple[tuple[int, int], list[str]]]) -> tuple[int, int]:
    """Точный решатель: сумма фаз = максимум по фазам; фаза = итерации × самый дорогой примитив тела."""
    best = (0, 0)
    for (ia, ib), prims in phases:
        pa, pb = max(_PRIM[p] for p in prims)
        best = max(best, (ia + pa, ib + pb))
    return best


@register
class ComplexityContainers(BankFamily):
    id = "py.complexity.containers"
    title = "Сложность кода с контейнерами Python"
    specs = SPECS
    module = "python_core"
    format = "complexity"
    target_grade = "middle"
    position = -0.2
    c = 1 / 6
    time_limit_s = 90
    answer_kind = "choice"
    measures = "Стоимость операций list/set/dict/deque/heapq/bisect внутри цикла и итоговая асимптотика."
    misconceptions = {
        "list_in_constant": "x in list — линейный поиск, внутри цикла это даёт O(n²)",
        "set_in_linear": "проверка x in set и add — O(1) в среднем, цикл остаётся линейным",
        "pop0_constant": "list.pop(0) сдвигает все элементы — O(n) на вызов",
        "popleft_linear": "deque.popleft() — O(1), в отличие от list.pop(0)",
        "insert_front_constant": "list.insert(0, x) сдвигает все элементы — O(n) на вызов",
        "insort_logarithmic": "bisect.insort ищет место за O(log n), но вставка в список сдвигает элементы — O(n)",
        "insort_constant": "bisect.insort вставляет в список со сдвигом элементов — O(n) на вызов",
        "heap_constant": "heappush/heappop — O(log n), n таких операций дают O(n log n)",
        "heap_linear": "операции кучи логарифмические, а не линейные",
        "sort_ignored": "sorted() — O(n log n), это доминирует над линейными проходами",
        "lookup_linear": "обращение к словарю по ключу — O(1) в среднем",
        "builtin_constant": "срез и max() по префиксу — O(i) на итерации, в сумме O(n²)",
        "dict_linear": "get и запись в словарь — O(1) в среднем",
        "in_always_linear": "оператор in для множества — O(1); множество строится один раз за O(n)",
    }

    _ARGS = ["items", "values", "events", "ids", "records", "rows", "emails", "orders"]
    _VARS = ["item", "x", "value", "elem"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        names, body, phases, wrong_tags = rng.choice(_SNIPPETS)
        code = tpl(body, fn=rng.choice(names), arg=rng.choice(self._ARGS), x=rng.choice(self._VARS))
        correct = _CLASS_OF[_total_cost(phases)]
        return self.choice_instance(
            seed,
            rng,
            prompt=(
                "Оцените время работы функции в худшем случае. n — длина входной коллекции (если есть второй "
                "аргумент, его длина не больше n); операции словаря и множества считайте O(1) в среднем."
            ),
            correct=correct,
            wrongs=list(wrong_tags.items()),
            fixed_options=_CLASSES,
            code=code,
            language="python",
            why=(
                "Складываем фазы и берём самую дорогую; внутри цикла по n элементам стоимость тела умножается на n. "
                "list: in, pop(0), insert(0, …) — O(n); set/dict: in, add, get — O(1); deque.popleft — O(1); "
                "heappush/heappop — O(log n); sorted — O(n log n)."
            ),
            answer_hint="Выберите класс сложности.",
        )


@register
class ExceptionsFinally(BankFamily):
    id = "py.exceptions.finally"
    title = "try / except / else / finally при return"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "middle"
    position = 0.2
    time_limit_s = 120
    measures = "Порядок выполнения try/except/else/finally; finally выполняется и при return; else — только без исключения."
    misconceptions = {
        "finally_skipped_on_return": "finally выполняется всегда, в том числе когда блок try или except "
        "завершился через return",
        "else_after_return": "else выполняется, только если try дошёл до конца без исключения; return внутри "
        "try его пропускает",
        "finally_before_except": "finally выполняется после обработчика except, а не перед ним",
    }

    _FN = ["charge", "reserve", "withdraw", "refund"]

    @staticmethod
    def _model(amounts: list[int], limit: int, mode: str) -> str:
        """Точная модель заблуждения: какие записи попадут в log при неверном понимании семантики."""
        out: list[str] = []
        for a in amounts:
            if a <= 0:
                path = ["fin", f"err{a}"] if mode == "finally_before_except" else [f"err{a}", "fin"]
            elif a > limit:
                path = ["else", "fin"] if mode == "else_after_return" else ["fin"]
            else:
                path = [f"ok{a}", "else", "fin"]
            if mode == "finally_skipped_on_return":
                path = [p for p in path if p != "fin"]
            out += path
        return " ".join(out)

    def build(self, rng: random.Random, seed: int) -> Instance:
        fn = rng.choice(self._FN)
        limit = rng.randrange(200, 901, 50)
        amounts = [rng.randint(1, limit - 1), rng.randint(-50, 0), rng.randint(limit + 1, limit + 400)]
        rng.shuffle(amounts)
        code = tpl(
            """
            log = []

            def $fn(amount, limit=$limit):
                try:
                    if amount <= 0:
                        raise ValueError(amount)
                    if amount > limit:
                        return "limit"
                    log.append(f"ok{amount}")
                except ValueError:
                    log.append(f"err{amount}")
                    return "bad"
                else:
                    log.append("else")
                    return "paid"
                finally:
                    log.append("fin")

            for amount in ($a0, $a1, $a2):
                $fn(amount)
            print(*log)
            """,
            fn=fn, limit=limit, a0=amounts[0], a1=amounts[1], a2=amounts[2],
        )
        key = exec_py(code)
        if norm_output(key) != norm_output(self._model(amounts, limit, "correct")):
            raise AssertionError(f"{self.id}: модель разошлась с исполнением")  # страховка самой модели
        wrongs = [(self._model(amounts, limit, m), m) for m in self.misconceptions]
        return self.text_instance(
            seed,
            prompt="Что напечатает программа?",
            code=code,
            language="python",
            raw_key=key,
            wrongs=wrongs,
            why=(
                "finally выполняется при любом выходе из try: после обычного завершения, после return и после "
                "обработки исключения (уже после except). else выполняется, только если try дошёл до конца без "
                "исключения; ранний return из try его пропускает."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class ClosuresLateBinding(BankFamily):
    id = "py.closures.late_binding"
    title = "Позднее связывание в замыканиях: найдите ошибку"
    specs = SPECS
    module = "python_core"
    format = "bug_line"
    target_grade = "middle"
    position = 0.5
    # Текст исправления сам указывает на строку, которую он заменяет, поэтому угадывание сводится к выбору
    # 1 из 4 исправлений, а не 1 из 7×4 (spec-testing §3.1; так же в пакетах shared и frontend_js).
    c = 0.25
    time_limit_s = 150
    answer_kind = "line"
    enemies = ("py.defaults.mutable",)
    measures = "Замыкание захватывает переменную, а не значение; фиксация значения через аргумент по умолчанию."
    misconceptions = {
        "syntax_not_binding": "другая запись того же выражения не меняет момент, когда читается переменная цикла",
        "copy_iterable": "дело не в списке, по которому идёт цикл: все lambda читают одну и ту же переменную цикла "
        "уже после его окончания",
        "param_without_default": "параметр без значения по умолчанию придётся передавать при вызове — вызов с одним "
        "аргументом упадёт с TypeError",
        "container_blamed": "контейнер для функций ни при чём: ошибка в том, что lambda читает переменную при вызове",
    }

    _V1 = [("build_formatters", "formatters", "level", "text"), ("make_prefixers", "prefixers", "tag", "msg")]
    _V2 = [("build_pricers", "pricers", "pct", "price"), ("make_markups", "markups_fns", "rate", "amount")]
    _V3 = [("make_rules", "rules", "limit", "value"), ("build_checks", "checks", "cap", "x")]
    _LEVELS = ["info", "warn", "error", "debug", "audit", "trace"]
    _MSGS = ["disk91", "cpu77", "timeout", "retry3", "queue12", "mem64"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        variant = rng.randrange(3)
        if variant == 0:
            fn, coll, var, arg = rng.choice(self._V1)
            levels = rng.sample(self._LEVELS, 3)
            pick = rng.choice(levels[:2])
            msg = rng.choice(self._MSGS)
            lines = [
                f"def {fn}(names):",
                f"    {coll} = {{}}",
                f"    for {var} in names:",
                f'        {coll}[{var}] = lambda {arg}: f"{{{var}}}:{{{arg}}}"',
                f"    return {coll}",
                f"fmt = {fn}({levels!r})",
                f'print(fmt["{pick}"]("{msg}"))',
            ]
            expected = f"{pick}:{msg}"
            fixes = [
                (4, f'        {coll}[{var}] = lambda {arg}, {var}={var}: f"{{{var}}}:{{{arg}}}"', None),
                (4, f'        {coll}[{var}] = lambda {arg}: {var} + ":" + {arg}', "syntax_not_binding"),
                (3, f"    for {var} in list(names):", "copy_iterable"),
                (4, f'        {coll}[{var}] = lambda {arg}, {var}: f"{{{var}}}:{{{arg}}}"', "param_without_default"),
                (2, f"    {coll} = dict.fromkeys(names)", "container_blamed"),
            ]
            task = f"вернуть форматтер для каждого уровня логирования; fmt[\"{pick}\"] должен ставить префикс «{pick}»"
        elif variant == 1:
            fn, coll, var, arg = rng.choice(self._V2)
            m = rng.sample(range(5, 60, 5), 3)
            p = rng.randrange(100, 1000, 10)
            lines = [
                f"def {fn}(rates):",
                f"    {coll} = []",
                f"    for {var} in rates:",
                f"        {coll}.append(lambda {arg}: {arg} * (100 + {var}) // 100)",
                f"    return {coll}",
                f"retail, wholesale, partner = {fn}({m!r})",
                f"print(retail({p}), wholesale({p}))",
            ]
            expected = f"{p * (100 + m[0]) // 100} {p * (100 + m[1]) // 100}"
            fixes = [
                (4, f"        {coll}.append(lambda {arg}, {var}={var}: {arg} * (100 + {var}) // 100)", None),
                (4, f"        {coll}.append(lambda {arg}: (100 + {var}) * {arg} // 100)", "syntax_not_binding"),
                (3, f"    for {var} in list(rates):", "copy_iterable"),
                (4, f"        {coll}.append(lambda {arg}, {var}: {arg} * (100 + {var}) // 100)", "param_without_default"),
                (2, f"    {coll} = list()", "container_blamed"),
            ]
            task = "вернуть функцию наценки для каждой ставки из списка (по порядку)"
        else:
            fn, coll, var, arg = rng.choice(self._V3)
            limits = rng.sample(range(10, 200, 5), 3)
            lo, hi = min(limits), max(limits)
            # Значение строго между крайними лимитами: часть правил пройдёт, часть нет, а при ошибке все
            # правила сравнивают с последним лимитом и дают одинаковый ответ — вывод заведомо отличается.
            v = rng.choice([x for x in range(lo + 1, hi) if x not in limits])
            fields = rng.sample(["age", "items", "discount", "weight", "retries", "length"], 3)
            pairs = list(zip(fields, limits, strict=True))
            lines = [
                f"def {fn}(limits):",
                f"    {coll} = []",
                f"    for name, {var} in limits:",
                f"        {coll}.append(lambda {arg}: {arg} <= {var})",
                f"    return {coll}",
                f"{coll} = {fn}({pairs!r})",
                f"print([rule({v}) for rule in {coll}])",
            ]
            expected = str([v <= lim for lim in limits])
            fixes = [
                (4, f"        {coll}.append(lambda {arg}, {var}={var}: {arg} <= {var})", None),
                (4, f"        {coll}.append(lambda {arg}: not {arg} > {var})", "syntax_not_binding"),
                (3, f"    for name, {var} in list(limits):", "copy_iterable"),
                (4, f"        {coll}.append(lambda {arg}, {var}: {arg} <= {var})", "param_without_default"),
                (2, f"    {coll} = list()", "container_blamed"),
            ]
            task = "проверить значение каждым правилом «не больше лимита» из списка"
        actual = try_exec_py("\n".join(lines))
        if actual is None or actual == expected:
            raise GenerationRetry
        # Ключ — исполнением: верно то исправление, после которого программа печатает ожидаемое.
        chosen = [fixes[0], *rng.sample(fixes[1:], 3)]
        verified: list[tuple[int, str, str | None]] = []
        for line_no, text, tag in chosen:
            out = try_exec_py(_fill(lines, line_no - 1, text))
            ok = out == expected
            if ok != (tag is None):
                raise AssertionError(f"{self.id}: исправление «{text.strip()}» проверено исполнением неверно")
            verified.append((line_no, text, tag))
        return self.line_instance(
            seed,
            rng,
            prompt=(
                f"Функция должна {task}. Ожидаемый вывод: {expected}, фактический: {actual}. "
                "Найдите строку с ошибкой и выберите исправление."
            ),
            lines=lines,
            language="python",
            fixes=verified,
            why=(
                "lambda запоминает не значение, а саму переменную цикла и читает её в момент вызова — когда цикл "
                "уже закончился, поэтому все функции видят последнее значение. Аргумент по умолчанию вычисляется "
                "при создании lambda и фиксирует текущее значение."
            ),
        )


# ============================================================== Senior


@register
class GeneratorsLazy(BankFamily):
    id = "py.generators.lazy"
    title = "Ленивый конвейер генераторов"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "senior"
    position = -0.6
    time_limit_s = 150
    measures = "Ленивость генераторов: чередование стадий конвейера, остановка по break, продолжение с места остановки."
    misconceptions = {
        "eager": "генератор ничего не выполняет при создании: код стадий запускается только при запросе "
        "следующего элемента",
        "stage_complete": "стадии не отрабатывают целиком по очереди — элементы проходят конвейер по одному",
        "restart": "next() продолжает тот же генератор с места остановки, а не начинает чтение заново",
    }

    _NAMES = [("read", "keep", "take"), ("fetch", "only_big", "first_n"), ("scan", "over", "head")]

    def build(self, rng: random.Random, seed: int) -> Instance:
        rd, kp, tk = rng.choice(self._NAMES)
        limit = rng.randrange(30, 70, 5)
        rows = rng.sample(range(10, 100), 7)
        passing = [r for r in rows if r > limit]
        if not 3 <= len(passing) <= 5:
            raise GenerationRetry
        rows_s = ", ".join(map(str, rows))
        read_gen = f"""
def {rd}(rows):
    for row in rows:
        print(f"r{{row}}")
        yield row
"""
        keep_gen = f"""
def {kp}(items, limit):
    for item in items:
        if item > limit:
            print(f"k{{item}}")
            yield item
"""
        take_fn = f"""
def {tk}(items, n):
    out = []
    for item in items:
        out.append(item)
        if len(out) == n:
            break
    return out
"""
        source = f"{kp}({rd}([{rows_s}]), {limit})"

        def main_part(pipeline_expr: str = source, next_arg: str = "pipeline") -> str:
            return (
                f"\npipeline = {pipeline_expr}\nprint(\"start\")\nprint({tk}(pipeline, 2))\n"
                f"print(next({next_arg}, None))\n"
            )

        main = main_part()
        code = (read_gen + keep_gen + take_fn + main).strip("\n")
        read_list = f"""
def {rd}(rows):
    out = []
    for row in rows:
        print(f"r{{row}}")
        out.append(row)
    return out
"""
        keep_list = f"""
def {kp}(items, limit):
    out = []
    for item in items:
        if item > limit:
            print(f"k{{item}}")
            out.append(item)
    return out
"""
        read_stage = f"""
def {rd}(rows):
    buf = []
    for row in rows:
        print(f"r{{row}}")
        buf.append(row)
    yield from buf
"""
        eager = read_list + keep_list + take_fn + main_part(pipeline_expr=f"iter({source})")
        restart = read_gen + keep_gen + take_fn + main_part(next_arg=source)
        wrongs = [
            (exec_py(eager), "eager"),
            (exec_py(read_stage + keep_gen + take_fn + main), "stage_complete"),
            (exec_py(restart), "restart"),
        ]
        return self.text_instance(
            seed,
            prompt="Данные обрабатываются конвейером генераторов. Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                "Создание генератора ничего не выполняет. Каждый запрос элемента протягивает ровно столько строк "
                "через все стадии, сколько нужно для одного результата, поэтому печать стадий чередуется. "
                "После break генератор остаётся на месте и next() продолжает с него."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class DataclassOrder(BankFamily):
    id = "py.dataclasses.order"
    title = "dataclass(order=True) и field(compare=False)"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "senior"
    position = -0.3
    time_limit_s = 150
    measures = "Сравнение dataclass как кортежей полей в порядке объявления, исключение полей compare=False, устойчивость sort."
    misconceptions = {
        "compare_false_ignored": "поле с compare=False не участвует ни в сортировке, ни в ==; при равенстве "
        "остальных полей сохраняется исходный порядок (sort устойчива)",
        "first_field_only": "order=True сравнивает все поля с compare=True по порядку объявления, а не только первое",
        "eq_identity": "== у dataclass сравнивает значения полей, а не идентичность объектов",
    }

    _SHAPES = [
        ("Ticket", "priority", "created", "title", ["login", "billing", "search", "export", "upload", "reports", "auth"]),
        ("Job", "level", "eta", "name", ["backup", "reindex", "cleanup", "digest", "sync", "thumbs", "billing"]),
        ("Task", "rank", "due", "label", ["deploy", "review", "migrate", "audit", "release", "hotfix", "docs"]),
    ]

    def build(self, rng: random.Random, seed: int) -> Instance:
        cls, f1, f2, f3, pool = rng.choice(self._SHAPES)
        titles = rng.sample(pool, 5)
        p_tie, p_pair = rng.sample([1, 2, 3], 2)
        c_tie = rng.randint(5, 60)
        c_hi, c_lo = sorted(rng.sample(range(5, 60), 2), reverse=True)
        p_other = rng.choice([1, 2, 3])
        c_other = rng.randint(5, 60)
        if (p_other, c_other) in {(p_tie, c_tie), (p_pair, c_hi), (p_pair, c_lo)}:
            raise GenerationRetry
        t_big, t_small = sorted(titles[:2], reverse=True)  # в паре-ничьей первым идёт «больший» по алфавиту
        slots = rng.sample(range(5), 5)
        tie_pos = sorted(slots[:2])
        pair_pos = sorted(slots[2:4])
        items: dict[int, tuple[int, int, str]] = {
            tie_pos[0]: (p_tie, c_tie, t_big), tie_pos[1]: (p_tie, c_tie, t_small),
            pair_pos[0]: (p_pair, c_hi, titles[2]), pair_pos[1]: (p_pair, c_lo, titles[3]),
            slots[4]: (p_other, c_other, titles[4]),
        }
        ordered = [items[i] for i in range(5)]
        srt = sorted(ordered, key=lambda t: (t[0], t[1]))
        i_t = [k for k, t in enumerate(srt) if t[2] in (t_big, t_small)]
        if rng.random() < 0.5:
            i, j = i_t
        else:
            others = [k for k in range(5) if k not in i_t]
            i, j = sorted([rng.choice(i_t), rng.choice(others)])
        data = ", ".join(f'{cls}({p}, {c}, "{t}")' for p, c, t in ordered)

        def make(f2_decl: str, f3_decl: str, cmp: str = "==") -> str:
            return tpl(
                """
                from dataclasses import dataclass, field

                @dataclass(order=True)
                class $cls:
                    $f1: int
                    $f2decl
                    $f3decl

                queue = [$data]
                queue.sort()
                print([t.$f3 for t in queue])
                print(queue[$i] $cmp queue[$j])
                """,
                cls=cls, f1=f1, f2decl=f2_decl, f3decl=f3_decl, f3=f3, data=data, i=i, j=j, cmp=cmp,
            )

        ok2, ok3 = f"{f2}: int", f"{f3}: str = field(compare=False)"
        code = make(ok2, ok3)
        wrongs = [
            (exec_py(make(ok2, f"{f3}: str")), "compare_false_ignored"),
            (exec_py(make(f"{f2}: int = field(compare=False)", ok3)), "first_field_only"),
            (exec_py(make(ok2, ok3, "is")), "eq_identity"),
        ]
        return self.text_instance(
            seed,
            prompt="Очередь задач сортируется средствами dataclass. Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                f"order=True сравнивает объекты как кортежи полей в порядке объявления ({f1}, {f2}); поле "
                f"{f3} с compare=False не участвует ни в <, ни в ==. list.sort устойчива: при равных ключах "
                "сохраняется исходный порядок."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class MroSuper(BankFamily):
    id = "py.mro.super"
    title = "Кооперативное наследование: MRO и super()"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "senior"
    position = 0.2
    time_limit_s = 150
    is_anchor = True
    measures = "Линеаризация C3 (MRO) при ромбовидном наследовании; super() — следующий класс в MRO, а не родитель."
    misconceptions = {
        "super_is_parent": "super() вызывает следующий класс в MRO объекта, а не родителя, указанного в объявлении "
        "класса, поэтому отрабатывают все миксины",
        "bases_reversed": "MRO идёт по базовым классам слева направо, как они перечислены в объявлении",
        "post_order_ignored": "миксин, который приписывает своё имя после вызова super(), добавляет его в конец "
        "строки — уже после всей остальной цепочки",
    }

    _BASES = ["Storage", "BaseRepo", "Handler", "Service"]
    _METHODS = ["save", "handle", "process", "render"]
    _MIXINS = ["Audit", "Cache", "Retry", "Metrics", "Lock", "Trace", "Validate", "Log", "Auth", "Limit"]
    _FINAL = ["UserRepo", "OrderService", "Api", "Worker", "Gateway", "Billing", "Notifier"]

    def build(self, rng: random.Random, seed: int) -> Instance:
        base, m, final = rng.choice(self._BASES), rng.choice(self._METHODS), rng.choice(self._FINAL)
        mixins = rng.sample(self._MIXINS, 3)
        post = rng.randrange(3)

        def src(*, parent_calls: bool = False, reverse: bool = False, post_idx: int | None = post) -> str:
            parts = [f"class {base}:\n    def {m}(self):\n        return \"{base}\"\n"]
            for k, name in enumerate(mixins):
                call = f"{base}.{m}(self)" if parent_calls else f"super().{m}()"
                body = f'{call} + ">{name}"' if k == post_idx else f'"{name}>" + {call}'
                parts.append(f"class {name}Mixin({base}):\n    def {m}(self):\n        return {body}\n")
            order = list(reversed(mixins)) if reverse else mixins
            bases = ", ".join(f"{n}Mixin" for n in order)
            call = f"{mixins[0]}Mixin.{m}(self)" if parent_calls else f"super().{m}()"
            parts.append(f"class {final}({bases}):\n    def {m}(self):\n        return \"{final}>\" + {call}\n")
            parts.append(f"print({final}().{m}())")
            return "\n".join(parts)

        code = src()
        wrongs = [
            (exec_py(src(parent_calls=True)), "super_is_parent"),
            (exec_py(src(reverse=True)), "bases_reversed"),
            (exec_py(src(post_idx=None)), "post_order_ignored"),
        ]
        return self.text_instance(
            seed,
            prompt="Что напечатает программа?",
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                f"MRO класса {final}: {final}, затем миксины слева направо, и только потом общий {base}. "
                "super() передаёт вызов следующему классу в этом списке, поэтому каждый миксин вызывается ровно один раз. "
                "Миксин, который дописывает имя после super(), получает строку всей оставшейся цепочки и добавляет "
                "своё имя в её конец."
            ),
            answer_hint=OUTPUT_HINT,
        )


@register
class AsyncioTasks(BankFamily):
    id = "py.asyncio.tasks"
    title = "asyncio: create_task, await и gather"
    specs = SPECS
    module = "python_core"
    format = "predict_output"
    target_grade = "senior"
    position = 0.6
    time_limit_s = 180
    measures = "Когда стартует задача asyncio, прямой await корутины, параллельное ожидание и порядок результатов gather."
    misconceptions = {
        "task_starts_immediately": "create_task только планирует задачу: она начнёт выполняться, когда текущая "
        "корутина отдаст управление на await",
        "direct_await_deferred": "await корутины (не задачи) выполняет её сразу, до первой паузы — раньше уже "
        "запланированных задач",
        "coroutine_not_task": "create_task запускает задачу параллельно с основной корутиной, не дожидаясь "
        "gather",
        "gather_completion_order": "gather возвращает результаты в порядке аргументов, а не в порядке завершения",
    }

    def build(self, rng: random.Random, seed: int) -> Instance:
        a, b, cname = rng.sample(_WORDS, 3)
        d1, d2, d3 = rng.randint(1, 6), rng.randint(1, 4), rng.randint(1, 4)
        # Времена завершения A=d1, B=d2, C=d2+d3 (в десятых секунды) должны различаться — иначе порядок не определён.
        if d1 in (d2, d2 + d3):
            raise GenerationRetry
        s1, s2, s3 = rng.sample(range(1, 100), 3)
        create = f'asyncio.create_task(load("{a}", {d1 / 10}, {s1}))'
        gather = f'asyncio.gather(task, load("{cname}", {d3 / 10}, {s3}))'

        def make(*, create_line: str = f"    task = {create}", extra: str = "",
                 first: str = f'await load("{b}", {d2 / 10}, {s2})',
                 gather_line: str = f"    second, third = await {gather}") -> str:
            return "\n".join([
                "import asyncio",
                "",
                "async def load(name, delay, size):",
                '    print(f"start:{name}")',
                "    await asyncio.sleep(delay)",
                '    print(f"done:{name}")',
                "    return size",
                "",
                "async def main():",
                create_line,
                *([extra] if extra else []),
                '    print("created")',
                f"    first = {first}",
                gather_line,
                "    print(first, second, third)",
                "",
                "asyncio.run(main())",
            ])

        code = make()
        wrongs = [
            (exec_py(make(extra="    await asyncio.sleep(0)")), "task_starts_immediately"),
            (exec_py(make(first=f'await asyncio.create_task(load("{b}", {d2 / 10}, {s2}))')), "direct_await_deferred"),
            (exec_py(make(create_line=f'    task = load("{a}", {d1 / 10}, {s1})')), "coroutine_not_task"),
            (exec_py(make(gather_line=(
                f'    second, third = [await f for f in asyncio.as_completed([task, load("{cname}", {d3 / 10}, {s3})])]'
            ))), "gather_completion_order"),
        ]
        return self.text_instance(
            seed,
            prompt=(
                "Сервис загружает данные из нескольких источников. Что напечатает программа? "
                "asyncio.sleep(x) — пауза x секунд; время на сами print не учитывайте."
            ),
            code=code,
            language="python",
            raw_key=exec_py(code),
            wrongs=wrongs,
            why=(
                "create_task лишь ставит задачу в очередь цикла событий — она стартует, когда main впервые отдаст "
                "управление. await корутины выполняет её тело сразу, до первой паузы, поэтому её start печатается раньше "
                "старта задачи. Дальше события идут по времени завершения пауз. gather ждёт все аргументы и "
                "возвращает результаты в порядке аргументов."
            ),
            answer_hint=OUTPUT_HINT,
        )
