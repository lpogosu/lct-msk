"""algo.two_pointers.* — найти ошибочную строку в алгоритме двух указателей и выбрать исправление.

Корректность конструктивная (spec-testing §3.8): эталон и мутант исполняются на случайных тестах,
показанный кандидату упавший тест — реальный; верное исправление проходит все тесты, каждое
неверное — валится хотя бы на одном (иначе вариант не предлагается).
"""

from __future__ import annotations

import copy
import random
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from app.testing.bank.base import Instance, Rendered, canonical, register
from app.testing.bank.families.shared._common import (
    PY_SPECS,
    LineChoiceFamily,
    build_choice,
    exec_namespace,
    explain,
)


@dataclass(frozen=True)
class _Mutation:
    line: int  # индекс строки (с 0)
    bad: str  # текст дефектной строки без отступа
    tag: str
    why: str  # пояснение; {L}/{R}/{K}/{I}/{J} подставляются именами переменных экземпляра
    wrong: tuple[tuple[str, str], ...]  # неверные исправления (текст, тег); проверяются тестами


@dataclass(frozen=True)
class _Problem:
    fn: str
    doc: str
    lines: tuple[str, ...]
    names: tuple[dict[str, str], ...]
    gen: Callable[[random.Random], tuple]
    show: Callable[[tuple], str]
    run: Callable[[Callable, tuple], Any]
    fmt: Callable[[Any], str]
    mutations: tuple[_Mutation, ...]
    verb: str = "вернула"


def _rand_list(rng: random.Random, lo: int, hi: int, max_len: int) -> list[int]:
    return [rng.randint(lo, hi) for _ in range(rng.randint(0, max_len))]


_PROBLEMS = (
    _Problem(
        fn="count_pairs",
        doc="Функция count_pairs(a, limit) должна вернуть число пар индексов i < j, для которых "
        "a[i] + a[j] ≤ limit.",
        lines=(
            "def count_pairs(a, limit):",
            "    a = sorted(a)",
            "    {L}, {R} = 0, len(a) - 1",
            "    count = 0",
            "    while {L} < {R}:",
            "        if a[{L}] + a[{R}] <= limit:",
            "            count += {R} - {L}",
            "            {L} += 1",
            "        else:",
            "            {R} -= 1",
            "    return count",
        ),
        names=({"L": "lo", "R": "hi"}, {"L": "left", "R": "right"}, {"L": "i", "R": "j"}),
        gen=lambda rng: (_rand_list(rng, 0, 15, 8), rng.randint(0, 25)),
        show=lambda args: f"count_pairs({args[0]}, {args[1]})",
        run=lambda f, args: f(list(args[0]), args[1]),
        fmt=repr,
        mutations=(
            _Mutation(6, "count += 1", "count_one_pair",
                      "если a[{L}] + a[{R}] ≤ limit, то a[{L}] с любым элементом на позициях {L} + 1 … {R} тоже "
                      "даёт сумму ≤ limit — это {R} − {L} пар, а не одна",
                      (("count += {R} - {L} + 1", "off_by_one"), ("count += {R}", "wrong_formula"),
                       ("count += len(a) - {L} - 1", "wrong_formula"))),
            _Mutation(5, "if a[{L}] + a[{R}] < limit:", "strict_inequality",
                      "по условию сумма может быть равна limit, поэтому сравнение нестрогое: <=",
                      (("if a[{L}] + a[{R}] >= limit:", "wrong_condition"),
                       ("if a[{L}] + a[{R}] <= limit + 1:", "off_by_one"),
                       ("if a[{L}] + a[{R}] > limit:", "wrong_condition"))),
            _Mutation(7, "{R} -= 1", "wrong_pointer",
                      "все пары с a[{L}] уже посчитаны — дальше сдвигается левый указатель",
                      (("{L} += 2", "off_by_one"), ("{L}, {R} = {L} + 1, {R} - 1", "wrong_pointer"),
                       ("{R} -= 2", "wrong_pointer"))),
            _Mutation(2, "{L}, {R} = 1, len(a) - 1", "skip_first",
                      "левый указатель начинается с 0, иначе наименьший элемент не попадает ни в одну пару",
                      (("{L}, {R} = 0, len(a)", "crash"), ("{L}, {R} = 1, len(a)", "crash"),
                       ("{L}, {R} = 0, len(a) - 2", "off_by_one"))),
        ),
    ),
    _Problem(
        fn="dedup_sorted",
        doc="Функция dedup_sorted(a) получает отсортированный список и на месте переносит различные "
        "значения в начало: после вызова a[:k] содержит все различные значения по возрастанию, "
        "функция возвращает k.",
        lines=(
            "def dedup_sorted(a):",
            "    if not a:",
            "        return 0",
            "    {K} = 1",
            "    for i in range(1, len(a)):",
            "        if a[i] != a[{K} - 1]:",
            "            a[{K}] = a[i]",
            "            {K} += 1",
            "    return {K}",
        ),
        names=({"K": "k"}, {"K": "write"}, {"K": "size"}),
        gen=lambda rng: (sorted(_rand_list(rng, 0, 6, 9)),),
        show=lambda args: f"для a = {args[0]} вызов dedup_sorted(a)",
        run=lambda f, args: _run_dedup(f, args[0]),
        fmt=lambda r: f"k={r[0]!r}, a[:k]={r[1]!r}",
        verb="дал",
        mutations=(
            _Mutation(5, "if a[i] != a[{K}]:", "compare_wrong_slot",
                      "сравнивать нужно с последним записанным уникальным значением a[{K} − 1]; a[{K}] — "
                      "ячейка, которая ещё не перезаписана",
                      (("if a[i] == a[{K} - 1]:", "wrong_condition"), ("if a[i] != a[{K} + 1]:", "off_by_one"),
                       ("if a[i] > a[{K}]:", "wrong_condition"))),
            _Mutation(3, "{K} = 0", "start_zero",
                      "первый элемент всегда уникален и уже стоит на месте, поэтому счётчик начинается с 1",
                      (("{K} = 2", "off_by_one"), ("{K} = len(a)", "wrong_formula"), ("{K} = -1", "off_by_one"))),
            _Mutation(4, "for i in range(1, len(a) - 1):", "skip_last",
                      "range не включает правую границу: чтобы обработать последний элемент, нужен "
                      "range(1, len(a))",
                      (("for i in range(2, len(a)):", "off_by_one"), ("for i in range(1, len(a) + 1):", "crash"),
                       ("for i in range(len(a) - 1):", "off_by_one"))),
            _Mutation(6, "a[i] = a[{K}]", "assignment_direction",
                      "уникальное значение переносится в позицию {K}: a[{K}] = a[i]; обратное присваивание "
                      "портит ещё не обработанные элементы",
                      (("a[{K} - 1] = a[i]", "off_by_one"), ("a[{K}] = a[i - 1]", "off_by_one"),
                       ("a[{K} + 1] = a[i]", "crash"))),
        ),
    ),
    _Problem(
        fn="merge",
        doc="Функция merge(a, b) получает два отсортированных списка и должна вернуть один "
        "отсортированный список из всех их элементов.",
        lines=(
            "def merge(a, b):",
            "    {I} = {J} = 0",
            "    out = []",
            "    while {I} < len(a) and {J} < len(b):",
            "        if a[{I}] <= b[{J}]:",
            "            out.append(a[{I}])",
            "            {I} += 1",
            "        else:",
            "            out.append(b[{J}])",
            "            {J} += 1",
            "    out.extend(a[{I}:])",
            "    out.extend(b[{J}:])",
            "    return out",
        ),
        names=({"I": "i", "J": "j"}, {"I": "ia", "J": "ib"}, {"I": "p", "J": "q"}),
        gen=lambda rng: (sorted(_rand_list(rng, 0, 12, 6)), sorted(_rand_list(rng, 0, 12, 6))),
        show=lambda args: f"merge({args[0]}, {args[1]})",
        run=lambda f, args: f(list(args[0]), list(args[1])),
        fmt=repr,
        mutations=(
            _Mutation(3, "while {I} < len(a) or {J} < len(b):", "or_instead_of_and",
                      "сравнивать элементы можно, только пока не исчерпан ни один из списков; хвосты "
                      "дописываются после цикла",
                      (("while {I} <= len(a) and {J} <= len(b):", "crash"),
                       ("while {I} < len(a) and {J} < len(a):", "wrong_condition"),
                       ("while {I} < len(b) and {J} < len(a):", "wrong_condition"))),
            _Mutation(11, "out.extend(b[{I}:])", "wrong_tail_index",
                      "хвост списка b начинается с его собственного индекса, а не с индекса по a",
                      (("out.extend(b)", "wrong_formula"), ("out.extend(b[{J} + 1:])", "off_by_one"),
                       ("out.extend(a[{J}:])", "wrong_formula"))),
            _Mutation(9, "{I} += 1", "advance_wrong_pointer",
                      "в выход ушёл элемент b — сдвигать нужно указатель по b",
                      (("{J} += 2", "off_by_one"), ("{I}, {J} = {I} + 1, {J} + 1", "wrong_pointer"),
                       ("{J} = len(b)", "wrong_formula"))),
            _Mutation(4, "if a[{I}] >= b[{J}]:", "comparison_flipped",
                      "первым в выход идёт меньший элемент: берём a[{I}], если a[{I}] <= b[{J}]",
                      (("if a[{I}] > b[{J}]:", "wrong_condition"), ("if a[{I}] <= b[{J}] - 1:", "off_by_one"),
                       ("if a[{I}] <= b[{J}] + 1:", "off_by_one"), ("if b[{J}] <= a[{I}]:", "wrong_condition"))),
        ),
    ),
    _Problem(
        fn="has_pair_sum",
        doc="Функция has_pair_sum(a, target) получает список, отсортированный по возрастанию, и должна "
        "вернуть True, если в нём есть два элемента с разными индексами и суммой target.",
        lines=(
            "def has_pair_sum(a, target):",
            "    {L}, {R} = 0, len(a) - 1",
            "    while {L} < {R}:",
            "        s = a[{L}] + a[{R}]",
            "        if s == target:",
            "            return True",
            "        if s < target:",
            "            {L} += 1",
            "        else:",
            "            {R} -= 1",
            "    return False",
        ),
        names=({"L": "lo", "R": "hi"}, {"L": "left", "R": "right"}, {"L": "i", "R": "j"}),
        gen=lambda rng: _gen_pair_sum(rng),
        show=lambda args: f"has_pair_sum({args[0]}, {args[1]})",
        run=lambda f, args: f(list(args[0]), args[1]),
        fmt=repr,
        mutations=(
            _Mutation(2, "while {L} <= {R}:", "same_element_twice",
                      "при {L} == {R} это один и тот же элемент, а пара должна состоять из двух разных позиций",
                      (("while {L} < {R} - 1:", "off_by_one"), ("while {L} <= {R} + 1:", "crash"),
                       ("while {R} > 0:", "wrong_condition"))),
            _Mutation(6, "if s > target:", "pointer_direction",
                      "если сумма меньше нужной, её надо увеличить — сдвинуть левый указатель к большим "
                      "элементам",
                      (("if s >= target:", "wrong_condition"), ("if s < target - 1:", "off_by_one"),
                       ("if a[{L}] < a[{R}]:", "wrong_condition"), ("if s <= target:", "wrong_condition"))),
            _Mutation(1, "{L}, {R} = 0, len(a)", "hi_out_of_range",
                      "последний индекс списка — len(a) − 1",
                      (("{L}, {R} = 1, len(a) - 1", "off_by_one"), ("{L}, {R} = 0, len(a) - 2", "off_by_one"),
                       ("{L}, {R} = 0, len(a) + 1", "crash"))),
        ),
    ),
    _Problem(
        fn="longest_window",
        doc="Функция longest_window(a, limit) получает список неотрицательных чисел и должна вернуть длину "
        "самого длинного непрерывного отрезка с суммой не больше limit.",
        lines=(
            "def longest_window(a, limit):",
            "    best = 0",
            "    total = 0",
            "    {L} = 0",
            "    for {R} in range(len(a)):",
            "        total += a[{R}]",
            "        while total > limit:",
            "            total -= a[{L}]",
            "            {L} += 1",
            "        best = max(best, {R} - {L} + 1)",
            "    return best",
        ),
        names=({"L": "lo", "R": "hi"}, {"L": "left", "R": "right"}, {"L": "start", "R": "end"}),
        gen=lambda rng: (_rand_list(rng, 0, 9, 8), rng.randint(0, 20)),
        show=lambda args: f"longest_window({args[0]}, {args[1]})",
        run=lambda f, args: f(list(args[0]), args[1]),
        fmt=repr,
        mutations=(
            _Mutation(9, "best = max(best, {R} - {L})", "window_length_off_by_one",
                      "в окне [{L}, {R}] включительно {R} − {L} + 1 элементов",
                      (("best = max(best, {R} - {L} + 2)", "off_by_one"), ("best = max(best, {R} + 1)", "wrong_formula"),
                       ("best = {R} - {L} + 1", "wrong_formula"))),
            _Mutation(6, "while total >= limit:", "strict_vs_nonstrict",
                      "отрезок с суммой ровно limit допустим — сжимать окно нужно, только пока total > limit",
                      (("while total > limit + 1:", "off_by_one"),
                       ("while total > limit and {L} < {R}:", "wrong_condition"),
                       ("while total - a[{L}] > limit:", "wrong_condition"))),
            _Mutation(7, "total -= a[{R}]", "subtract_wrong_end",
                      "сжимая окно слева, вычитаем уходящий из окна элемент a[{L}]",
                      (("total -= a[{L} + 1]", "off_by_one"), ("total -= a[{L} - 1]", "off_by_one"),
                       ("total = 0", "wrong_formula"))),
        ),
    ),
)


def _gen_pair_sum(rng: random.Random) -> tuple[list[int], int]:
    """Тесты для has_pair_sum: чаще всего цель достижима — иначе ошибки в ветках почти не видны."""
    a = sorted(_rand_list(rng, 0, 15, 8))
    r = rng.random()
    if len(a) >= 2 and r < 0.5:
        i, j = sorted(rng.sample(range(len(a)), 2))
        return a, a[i] + a[j]
    if a and r < 0.7:
        return a, 2 * rng.choice(a)  # ловушка «один элемент дважды»
    return a, rng.randint(0, 30)


def _run_dedup(f: Callable, a: list[int]) -> Any:
    b = list(a)
    k = f(b)
    return (k, b[:k]) if isinstance(k, int) else (k, None)


HINTS = {
    "off_by_one": "граница сдвинута на единицу — проверьте крайние случаи: пустой список, один элемент, "
    "равенство.",
    "wrong_condition": "это условие меняет смысл проверки — на части входов алгоритм идёт не в ту ветку.",
    "wrong_pointer": "сдвигается не тот указатель (или оба сразу) — часть элементов пропускается.",
    "wrong_formula": "формула не соответствует инварианту алгоритма — на части входов результат неверен.",
    "crash": "такая строка выходит за границу списка — на части входов IndexError.",
}

_N_TESTS = 300


def _outcome(fn: Callable, prob: _Problem, args: tuple) -> Any:
    try:
        return ("ok", prob.run(fn, copy.deepcopy(args)))
    except Exception as e:  # noqa: BLE001 — падение мутанта — тоже результат
        return ("error", type(e).__name__)


def _compile(prob: _Problem, lines: list[str]) -> Callable:
    return exec_namespace("\n".join(lines) + "\n")[prob.fn]


def _failing(prob: _Problem, lines: list[str], tests: list[tuple], expected: list[Any]) -> list[int]:
    """Номера тестов, на которых код из lines расходится с эталоном."""
    fn = _compile(prob, lines)
    return [i for i, t in enumerate(tests) if _outcome(fn, prob, t) != expected[i]]


@register
class TwoPointersBug(LineChoiceFamily):
    id = "algo.two_pointers.bug"
    title = "Два указателя: найти ошибочную строку и исправить"
    specs = PY_SPECS  # фронтендеру — JS-версия «<id>.js» (shared/_js.py)
    module = "algo"
    format = "bug_line"
    target_grade = "middle"
    position = 0.5
    # spec-testing §3.1: c — вероятность угадать. Тексты исправлений («count += …», «size = …») указывают
    # на дефектную строку, так что угадывание — выбор 1 из 4 исправлений, а не 1 из (строк × 4).
    c = 0.25
    time_limit_s = 150

    def generate(self, seed: int) -> Instance:
        rng = random.Random(seed)
        while True:
            prob = rng.choice(_PROBLEMS)
            names = rng.choice(prob.names)
            mut = rng.choice(prob.mutations)
            good = [ln.format(**names) for ln in prob.lines]
            indent = good[mut.line][: len(good[mut.line]) - len(good[mut.line].lstrip())]
            bad_lines = list(good)
            bad_lines[mut.line] = indent + mut.bad.format(**names)
            tests = [prob.gen(rng) for _ in range(_N_TESTS)]
            ref = _compile(prob, good)
            expected = [_outcome(ref, prob, t) for t in tests]
            fails = _failing(prob, bad_lines, tests, expected)
            wrong: list[tuple[str, str]] = []
            for text, tag in mut.wrong:
                cand = list(good)
                cand[mut.line] = indent + text.format(**names)
                if _failing(prob, cand, tests, expected):  # неверное исправление должно реально ломать тесты
                    wrong.append((text.format(**names), tag))
            # мутант обязан ломать тест, а вариантов исправления должно быть 4 — иначе другой дефект
            if fails and len(wrong) >= 3:
                break
        bad_fn = _compile(prob, bad_lines)
        # показываем наглядный упавший тест: без падения, если можно; длина ближе к 5
        shown = min(
            fails,
            key=lambda i: (_outcome(bad_fn, prob, tests[i])[0] == "error", abs(len(tests[i][0]) - 5), repr(tests[i])),
        )
        got = _outcome(bad_fn, prob, tests[shown])
        got_s = f"падает с {got[1]}" if got[0] == "error" else f"{prob.verb} {prob.fmt(got[1])}"
        example = f"{prob.show(tests[shown])} {got_s}; ожидалось {prob.fmt(expected[shown][1])}."

        correct = good[mut.line].strip()
        options, fix_id, opt_tags = build_choice(rng, correct, wrong, 4)
        line_no = mut.line + 1
        key = {"line": line_no, "fix": fix_id}
        distractors = {canonical({"line": line_no, "fix": oid}): tag for oid, tag in opt_tags.items()}

        lines = tuple(bad_lines)
        prompt = (
            f"{prob.doc}\n\nВ реализации ровно одна ошибочная строка. Тест упал:\n{example}\n\n"
            "Укажите ошибочную строку и выберите, чем её заменить."
        )
        body = (
            f"Ошибка в строке {line_no}: «{mut.bad.format(**names)}» — {mut.why.format(**names)}. "
            f"Правильно: «{correct}»."
        )
        return Instance(
            family_id=self.id,
            seed=seed,
            rendered=Rendered(
                prompt=prompt,
                code="\n".join(lines),
                language="python",
                options=options,
                lines=lines,
                answer_mode="line_choice",
            ),
            key=key,
            distractors=distractors,
            explanation=explain(body, HINTS, opt_tags.values()),
        )
