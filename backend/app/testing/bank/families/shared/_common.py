"""Помощники пакета shared: исполнение эталонного кода, нормализация ответов, сборка вариантов.

Ключи считаются исполнением НАШЕГО сгенерированного кода (spec-testing §3.8). Для Python-кода
исполняем его в процессе (exec + перехват stdout) — это тот же интерпретатор, что и в
execution.run_python, только без запуска подпроцесса на каждый экземпляр (< 50 мс на экземпляр).
Эквивалентность с run_python проверяется в tests/test_bank_shared.py.
"""

from __future__ import annotations

import builtins
import io
import random
import re
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from app.testing.bank.base import Family, Instance, Option, canonical

SPECS: tuple[str, ...] = ("backend_python", "frontend_js")
# Семейства с кодом на Python фронтендеру не выдаются: у каждого есть JS-версия `<id>.js` для шкалы frontend_js
# (shared/_js.py, CATALOG.md). Общими для обеих шкал остаются задания без кода на Python (текст сценария, fetch на JS).
PY_SPECS: tuple[str, ...] = ("backend_python",)
OPTION_IDS = "abcdefgh"


# ---------------------------------------------------------------- исполнение


def exec_capture(code: str) -> str:
    """Выполнить сгенерированный Python-код и вернуть stdout без завершающего перевода строки.

    Вывод перехватывается подменой print в глобалах исполняемого кода, а не redirect_stdout:
    тот подменяет sys.stdout на весь процесс, и print из соседнего потока (например, при генерации
    пула внутри API) попал бы в ключ. Сгенерированный код пишет в stdout только через print.
    """
    buf = io.StringIO()

    def _print(*args: Any, sep: str | None = " ", end: str | None = "\n", file: Any = None, flush: bool = False) -> None:
        if file is not None:  # явный поток (stderr и т.п.) — как у настоящего print
            builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
            return
        buf.write((" " if sep is None else sep).join(map(str, args)) + ("\n" if end is None else end))

    exec(compile(code, "<bank-shared>", "exec"), {"__name__": "__main__", "print": _print})  # noqa: S102 — свой код
    return buf.getvalue().rstrip("\n")


def exec_namespace(code: str) -> dict[str, Any]:
    """Выполнить определения (функции/классы) и вернуть пространство имён."""
    ns: dict[str, Any] = {"__name__": "__bank__"}
    exec(compile(code, "<bank-shared>", "exec"), ns)  # noqa: S102 — свой код
    return ns


# ---------------------------------------------------------------- нормализация

_INT = re.compile(r"-?\d+")
_WS = re.compile(r"\s+")
# Кириллические двойники латинских букв: кандидат мог набрать «А» вместо «A».
_LOOKALIKE = str.maketrans("АВЕКМНОРСТУХаеорсухк", "ABEKMHOPCTYXaeopcyxk")


def unlookalike(s: str) -> str:
    return s.translate(_LOOKALIKE)


def norm_int_seq(answer: Any) -> Any:
    """Последовательность целых чисел → «1 2 3» (скобки, запятые и лишний текст игнорируются)."""
    if isinstance(answer, list | tuple):
        answer = " ".join(str(x) for x in answer)
    if not isinstance(answer, str):
        return answer
    nums = _INT.findall(answer)
    if not nums:
        return _WS.sub(" ", answer.strip())
    return " ".join(str(int(x)) for x in nums)


def norm_choice(answer: Any) -> Any:
    if isinstance(answer, str):
        return answer.strip().lower()
    return answer


def norm_line_choice(answer: Any) -> Any:
    """{"line": номер строки с 1, "fix": id варианта исправления}."""
    if isinstance(answer, list | tuple) and len(answer) == 2:
        answer = {"line": answer[0], "fix": answer[1]}
    if isinstance(answer, dict):
        try:
            return {"line": int(answer.get("line")), "fix": str(answer.get("fix")).strip().lower()}
        except (TypeError, ValueError):
            return answer
    return answer


def norm_line_text(s: str) -> str:
    return _WS.sub(" ", s.strip())


# ---------------------------------------------------------------- варианты ответа


def build_choice(
    rng: random.Random, correct: str, wrong: Iterable[tuple[str, str]], k: int
) -> tuple[tuple[Option, ...], str, dict[str, str]]:
    """Собрать k вариантов: верный + первые k−1 различных неверных (текст, тег). Порядок — по seed."""
    seen = {correct}
    picked: list[tuple[str, str]] = []
    for text, tag in wrong:
        if text in seen:
            continue
        seen.add(text)
        picked.append((text, tag))
        if len(picked) == k - 1:
            break
    if len(picked) < k - 1:
        raise ValueError("недостаточно различных дистракторов")
    items: list[tuple[str, str]] = [(correct, ""), *picked]
    rng.shuffle(items)
    options = tuple(Option(OPTION_IDS[i], text) for i, (text, _) in enumerate(items))
    key = next(OPTION_IDS[i] for i, (text, tag) in enumerate(items) if not tag)
    distractors = {OPTION_IDS[i]: tag for i, (_, tag) in enumerate(items) if tag}
    return options, key, distractors


def text_distractors(key: str, candidates: Iterable[tuple[str, str]], norm: Callable[[Any], Any]) -> dict[str, str]:
    """Нормализованные неверные ответы → тег заблуждения (первый тег побеждает, ключ исключается)."""
    out: dict[str, str] = {}
    for raw, tag in candidates:
        val = canonical(norm(raw))
        if val != key and val not in out:
            out[val] = tag
    return out


# ---------------------------------------------------------------- объяснения


def explain(body: str, hints: dict[str, str], tags: Iterable[str]) -> str:
    """Разбор: решение + «типичные ошибки» по тегам дистракторов этого экземпляра."""
    used: list[str] = []
    for tag in tags:
        for t in tag.split("+"):
            if t in hints and t not in used:
                used.append(t)
    if not used:
        return body
    lines = [body, "", "Типичные ошибки:"]
    lines += [f"— {hints[t]}" for t in used]
    return "\n".join(lines)


# ---------------------------------------------------------------- базовые классы форматов


class TextIntsFamily(Family):
    """Ответ — последовательность целых чисел, набранная кандидатом."""

    c = 0.0

    def normalize(self, answer: Any) -> Any:
        return norm_int_seq(answer)


class ChoiceFamily(Family):
    def normalize(self, answer: Any) -> Any:
        return norm_choice(answer)


class LineChoiceFamily(Family):
    def normalize(self, answer: Any) -> Any:
        return norm_line_choice(answer)


class OrderFamily(Family):
    """Упорядочивание (parsons). Ключ — список строк rendered.lines в верном порядке.

    Ответ принимается как список строк или как список индексов в rendered.lines (с 0).
    """

    c = 0.0

    def normalize(self, answer: Any) -> Any:
        if isinstance(answer, str):
            answer = [s for s in answer.splitlines() if s.strip()]
        if isinstance(answer, list | tuple):
            return [norm_line_text(x) if isinstance(x, str) else x for x in answer]
        return answer

    def check(self, inst: Instance, answer: Any) -> tuple[bool, str | None]:
        a = self.normalize(answer)
        if isinstance(a, list) and a and all(isinstance(x, int) and not isinstance(x, bool) for x in a):
            lines = inst.rendered.lines
            if all(0 <= x < len(lines) for x in a):
                a = [norm_line_text(lines[x]) for x in a]
        if canonical(a) == canonical(inst.key):
            return True, None
        return False, inst.distractors.get(canonical(a))


def pick_distinct(rng: random.Random, pool: Sequence[Any], k: int) -> list[Any]:
    return rng.sample(list(pool), k)
