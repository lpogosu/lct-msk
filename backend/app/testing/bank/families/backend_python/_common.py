"""Общие помощники семейств backend_python (spec-testing §3.8).

Как получаем ключ — только исполнением, никогда не руками:
- Python: exec сгенерированного кода в этом же процессе, в чистом пространстве имён; вывод перехватывается
  подменой print в глобалах этого кода (не redirect_stdout: тот подменяет sys.stdout на весь процесс, и при
  генерации пула потоками вывод соседнего потока попал бы в чужой ключ). Это тот же CPython, что и в
  execution.run_python, только без подпроцесса: 200 клонов вместе с кодом-мутантами генерируются за доли
  секунды. Совпадение с подпроцессом и потокобезопасность проверяет тест пакета.
- asyncio: цикл событий с виртуальным временем. asyncio.sleep(0.3) не ждёт по-настоящему, часы цикла
  сдвигаются сразу к ближайшему таймеру; порядок срабатывания тот же, что и у реального цикла
  (в генераторах времена событий разнесены минимум на 0.1 с, совпадений нет). Глобальную политику
  asyncio не трогаем: коду подсовывается модуль-обёртка asyncio, у которой run() создаёт свой цикл.
- SQL: sqlite в памяти (execution.run_sql).
- Shell и git: точные решатели (модель конвейера, таблица дескрипторов, граф коммитов) в модулях семейств.

Дистракторы — тоже результат исполнения: код с типичным заблуждением (мутант) запускается так же,
его вывод становится неверным ответом с тегом заблуждения.
"""

from __future__ import annotations

import asyncio
import builtins
import io
import random
import re
import selectors
import textwrap
import types
from abc import abstractmethod
from collections.abc import Callable, Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from string import Template
from typing import Any, ClassVar

from app.testing.bank.base import Family, Instance, Option, Rendered, canonical

SPECS: tuple[str, ...] = ("backend_python",)
OPTION_IDS = "abcdefgh"

OUTPUT_HINT = (
    "Введите вывод программы так, как его напечатает Python. "
    "Переводы строк можно заменить пробелами, лишние пробелы не важны, кавычки — любые."
)


class GenerationRetry(Exception):
    """Параметры дали неоднозначное задание — перегенерировать тем же (продолжающимся) rng: детерминизм сохраняется."""


def rng_for(family_id: str, seed: int) -> random.Random:
    """Генератор клона. id в зерне — чтобы у разных семейств с одним seed не совпадали параметры."""
    return random.Random(f"{family_id}#{seed}")


def plural(n: int, one: str, few: str, many: str) -> str:
    """Русское согласование: plural(2, "строку", "строки", "строк") → «2 строки»."""
    n10, n100 = abs(n) % 10, abs(n) % 100
    word = one if n10 == 1 and n100 != 11 else few if 2 <= n10 <= 4 and not 12 <= n100 <= 14 else many
    return f"{n} {word}"


def tpl(text: str, **kw: Any) -> str:
    """Шаблон кода через $-подстановку: фигурные скобки Python/SQL не нужно экранировать."""
    return textwrap.dedent(Template(text).substitute(**kw)).strip("\n")


# ---------------------------------------------------------------- исполнение Python


class _InstantSelector(selectors.SelectSelector):
    """Селектор без ожидания: вместо сна сдвигает виртуальные часы цикла на timeout."""

    def __init__(self, clock: list[float]) -> None:
        super().__init__()
        self._clock = clock

    def select(self, timeout: float | None = None):  # type: ignore[override]
        if timeout is None:
            # Нет ни готовых задач, ни таймеров — реальный цикл повис бы навсегда.
            raise RuntimeError("эталонный код ждёт вечно: нет ни одного таймера")
        if timeout > 0:
            self._clock[0] += timeout
        return []


class _VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self) -> None:
        self._vclock = [0.0]
        super().__init__(selector=_InstantSelector(self._vclock))

    def time(self) -> float:
        return self._vclock[0]


def _virtual_run(main: Any, *, debug: bool | None = None) -> Any:
    """asyncio.run для эталонного кода: свой цикл с виртуальным временем, без глобального состояния."""

    def go() -> Any:
        with asyncio.Runner(debug=debug, loop_factory=_VirtualTimeLoop) as runner:
            return runner.run(main)

    if asyncio._get_running_loop() is None:
        return go()
    # Генерацию вызвали из async-кода (например, обработчик API): в этом потоке цикл уже крутится,
    # и второй run() в нём невозможен — исполняем в отдельном потоке.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(go).result()


class _AsyncioShim(types.ModuleType):
    """Модуль asyncio для исполняемого кода: всё как в настоящем, кроме run()."""

    def __init__(self) -> None:
        super().__init__("asyncio")
        self.run = _virtual_run

    def __getattr__(self, name: str) -> Any:
        return getattr(asyncio, name)


_ASYNCIO_SHIM = _AsyncioShim()


def _import_with_shim(name: str, globals_: Any = None, locals_: Any = None, fromlist: Any = (), level: int = 0) -> Any:
    if name == "asyncio" and level == 0:
        return _ASYNCIO_SHIM
    return builtins.__import__(name, globals_, locals_, fromlist, level)


def exec_py(code: str) -> str:
    """Выполнить наш сгенерированный Python-код и вернуть stdout без завершающего перевода строки.

    Потокобезопасно: вывод перехватывается подменой print в глобалах исполняемого кода (код пишет
    в stdout только через print), asyncio-код получает собственный цикл с виртуальным временем.
    """
    buf = io.StringIO()

    def _print(*args: Any, sep: str | None = " ", end: str | None = "\n", file: Any = None, flush: bool = False) -> None:
        if file is not None:  # явный поток (stderr и т.п.) — как у настоящего print
            builtins.print(*args, sep=sep, end=end, file=file, flush=flush)
            return
        buf.write((" " if sep is None else sep).join(map(str, args)) + ("\n" if end is None else end))

    namespace: dict[str, Any] = {"__name__": "__bank__", "print": _print}
    if "asyncio" in code:
        namespace["__builtins__"] = {**vars(builtins), "__import__": _import_with_shim}
    # dont_inherit: не переносить `from __future__ import annotations` этого модуля в исполняемый код —
    # иначе семантика отличалась бы от `python -c` (dataclass получил бы строковые аннотации).
    exec(compile(code, "<bank>", "exec", dont_inherit=True), namespace)  # noqa: S102 — только наш код
    return buf.getvalue().rstrip("\n")


def try_exec_py(code: str) -> str | None:
    """То же, но исключение → None (код-мутант с заблуждением вправе падать)."""
    try:
        return exec_py(code)
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- нормализация ответов

_WS = re.compile(r"\s+")
_TIGHT = re.compile(r"\s*([\[\](){},:;|=])\s*")
_TRANSLATE = str.maketrans(
    {
        '"': "'", "«": "'", "»": "'", "“": "'", "”": "'", "„": "'", "‘": "'", "’": "'",
        " ": " ", " ": " ", " ": " ", "−": "-", "–": "-",
    }
)


def norm_output(answer: Any) -> Any:
    """Вывод программы: кавычки любые, пробелы схлопнуты, вокруг скобок/запятых/двоеточий не важны."""
    if not isinstance(answer, str):
        return answer
    s = _WS.sub(" ", answer.translate(_TRANSLATE).strip())
    return _TIGHT.sub(r"\1", s)


_THOUSANDS_COMMAS = re.compile(r"-?[1-9]\d{0,2}(?:,\d{3})+")


def _num_token(tok: str) -> str:
    # «1,446,200» / «64,120» — запятые-разделители тысяч (англ. привычка); «7,5» и «12000,00» — десятичная запятая.
    t = tok.replace(",", "") if _THOUSANDS_COMMAS.fullmatch(tok) else tok.replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        return tok
    if not d.is_finite():
        return tok
    if d == d.to_integral_value():
        return str(int(d))
    return format(d.normalize(), "f")


def norm_number(answer: Any) -> Any:
    """Одно число: «12 000», «12000.0», «12000,00» → «12000»."""
    if not isinstance(answer, str):
        if isinstance(answer, int | float) and not isinstance(answer, bool):
            return _num_token(repr(answer))
        return answer
    s = _WS.sub("", answer.translate(_TRANSLATE))
    return _num_token(s)


def norm_numbers(answer: Any) -> Any:
    """Несколько чисел через пробел (или «;»): каждое приводится к канонической записи."""
    if not isinstance(answer, str):
        return answer
    s = answer.translate(_TRANSLATE).replace(";", " ")
    return " ".join(_num_token(t) for t in s.split())


def norm_choice(answer: Any) -> Any:
    return answer.strip().lower() if isinstance(answer, str) else answer


def norm_line_choice(answer: Any) -> Any:
    """{"line": 4, "fix": "b"}; номер строки — как в редакторе, с 1. Строки-числа приводим к int."""
    if isinstance(answer, dict):
        try:
            return {"line": int(answer.get("line")), "fix": str(answer.get("fix")).strip().lower()}
        except (TypeError, ValueError):
            return answer
    return answer


def norm_order(answer: Any) -> Any:
    if isinstance(answer, list | tuple):
        return [_WS.sub(" ", str(x).strip()) for x in answer]
    return answer


NORMALIZERS: dict[str, Callable[[Any], Any]] = {
    "output": norm_output,
    "number": norm_number,
    "numbers": norm_numbers,
    "choice": norm_choice,
    "line": norm_line_choice,
    "order": norm_order,
}


def show(raw: str) -> str:
    """Как показать ответ в разборе: многострочный вывод — через « / »."""
    return "«" + " / ".join(line.strip() for line in raw.strip().splitlines()) + "»"


# ---------------------------------------------------------------- базовый класс семейств пакета


class BankFamily(Family):
    """База семейств пакета: детерминированная генерация с перегенерацией и сборщики экземпляров."""

    measures: ClassVar[str] = ""  # что измеряет — для каталога и лаборатории
    misconceptions: ClassVar[dict[str, str]] = {}  # тег дистрактора → объяснение заблуждения (рус.)
    answer_kind: ClassVar[str] = "output"  # ключ NORMALIZERS
    max_attempts: ClassVar[int] = 200

    def generate(self, seed: int) -> Instance:
        rng = rng_for(self.id, seed)
        for _ in range(self.max_attempts):
            try:
                return self.build(rng, seed)
            except GenerationRetry:
                continue
        raise RuntimeError(f"{self.id}: не удалось сгенерировать однозначный экземпляр для seed={seed}")

    @abstractmethod
    def build(self, rng: random.Random, seed: int) -> Instance:
        """Собрать экземпляр или бросить GenerationRetry."""

    def normalize(self, answer: Any) -> Any:
        return NORMALIZERS[self.answer_kind](answer)

    def check(self, inst: Instance, answer: Any) -> tuple[bool, str | None]:
        # Упорядочивание можно прислать и индексами строк rendered.lines (с 0) — как в остальных пакетах банка.
        if (
            self.answer_kind == "order"
            and isinstance(answer, list | tuple)
            and answer
            and all(isinstance(x, int) or (isinstance(x, str) and x.strip().isdigit()) for x in answer)
        ):
            lines = inst.rendered.lines
            idx = [int(x) for x in answer]
            if any(not 0 <= i < len(lines) for i in idx):
                return False, None
            answer = [lines[i] for i in idx]
        return super().check(inst, answer)

    # --- разбор
    def _explain(self, why: str, right: str, wrong: Sequence[tuple[str, str]]) -> str:
        parts = [why.strip(), f"Верный ответ: {right}."]
        if wrong:
            parts.append("Типичные ошибки:")
            parts += [f"• {shown} — {self.misconceptions[tag]}." for shown, tag in wrong]
        return "\n".join(parts)

    # --- ввод ответа (c = 0)
    def text_instance(
        self,
        seed: int,
        *,
        prompt: str,
        code: str | None,
        language: str | None,
        raw_key: str,
        wrongs: Iterable[tuple[str | None, str]],
        why: str,
        answer_hint: str,
        table: dict[str, Any] | None = None,
    ) -> Instance:
        key = self.normalize(raw_key)
        if not key:
            raise GenerationRetry
        distractors: dict[str, str] = {}
        shown: list[tuple[str, str]] = []
        for raw, tag in wrongs:
            if raw is None:
                continue
            norm = self.normalize(raw)
            if not norm or norm == key or norm in distractors:
                continue
            distractors[norm] = tag
            shown.append((show(raw), tag))
        rendered = Rendered(
            prompt=prompt, code=code, language=language, table=table, answer_mode="text", answer_hint=answer_hint
        )
        return Instance(self.id, seed, rendered, key, distractors, self._explain(why, show(raw_key), shown))

    # --- выбор одного варианта (c = 1/k)
    def choice_instance(
        self,
        seed: int,
        rng: random.Random,
        *,
        prompt: str,
        correct: str,
        wrongs: Iterable[tuple[str, str]],
        why: str,
        k: int = 4,
        code: str | None = None,
        language: str | None = None,
        table: dict[str, Any] | None = None,
        shuffle: bool = True,
        fixed_options: Sequence[str] | None = None,
        answer_hint: str | None = None,
    ) -> Instance:
        if fixed_options is not None:
            # Фиксированная шкала (например, классы O(·)): порядок естественный, теги — на неверных классах.
            texts = list(fixed_options)
            tags = dict(wrongs)
            entries = [(t, tags.get(t)) for t in texts]
            if correct not in texts:
                raise ValueError(f"{self.id}: ключ {correct!r} не входит в фиксированные варианты")
        else:
            seen = {correct}
            picked: list[tuple[str, str]] = []
            for text, tag in wrongs:
                if text in seen:
                    continue
                seen.add(text)
                picked.append((text, tag))
                if len(picked) == k - 1:
                    break
            if len(picked) < k - 1:
                raise GenerationRetry
            entries = [(correct, None), *picked]
            if shuffle:
                rng.shuffle(entries)
        options = tuple(Option(OPTION_IDS[i], text) for i, (text, _) in enumerate(entries))
        key = next(OPTION_IDS[i] for i, (text, _) in enumerate(entries) if text == correct)
        distractors = {OPTION_IDS[i]: tag for i, (text, tag) in enumerate(entries) if tag and text != correct}
        shown = [(f"вариант «{text}»", tag) for text, tag in entries if tag and text != correct]
        rendered = Rendered(
            prompt=prompt,
            code=code,
            language=language,
            options=options,
            table=table,
            answer_mode="choice",
            answer_hint=answer_hint or "Выберите один вариант.",
        )
        return Instance(self.id, seed, rendered, key, distractors, self._explain(why, f"«{correct}»", shown))

    # --- клик по строке + выбор исправления
    def line_instance(
        self,
        seed: int,
        rng: random.Random,
        *,
        prompt: str,
        lines: Sequence[str],
        language: str,
        fixes: Sequence[tuple[int, str, str | None]],
        why: str,
    ) -> Instance:
        """fixes: (номер строки с 1, текст исправленной строки, тег заблуждения или None у верного)."""
        entries = list(fixes)
        rng.shuffle(entries)
        options = tuple(Option(OPTION_IDS[i], text.strip()) for i, (_, text, _) in enumerate(entries))
        correct = [(i, ln) for i, (ln, _, tag) in enumerate(entries) if tag is None]
        if len(correct) != 1:
            raise ValueError(f"{self.id}: должно быть ровно одно верное исправление")
        ci, cline = correct[0]
        key = {"line": cline, "fix": OPTION_IDS[ci]}
        distractors: dict[str, str] = {}
        shown: list[tuple[str, str]] = []
        for i, (ln, text, tag) in enumerate(entries):
            if tag is None:
                continue
            for line_no in {ln, cline}:
                distractors[canonical({"line": line_no, "fix": OPTION_IDS[i]})] = tag
            shown.append((f"исправление «{text.strip()}»", tag))
        rendered = Rendered(
            prompt=prompt,
            code="\n".join(lines),
            language=language,
            options=options,
            lines=tuple(lines),
            answer_mode="line_choice",
            answer_hint="Нажмите на строку с ошибкой (нумерация с 1, как в редакторе) и выберите исправление.",
        )
        right = f"строка {cline}, исправление «{entries[ci][1].strip()}»"
        return Instance(self.id, seed, rendered, key, distractors, self._explain(why, right, shown))

    # --- упорядочивание (parsons), единственный верный порядок
    def order_instance(
        self,
        seed: int,
        rng: random.Random,
        *,
        prompt: str,
        steps: Sequence[str],
        swap_tags: dict[int, str],
        why: str,
        language: str = "text",
    ) -> Instance:
        """steps — верный порядок; swap_tags[i] — тег ошибки «шаги i и i+1 переставлены»."""
        key = [_WS.sub(" ", s.strip()) for s in steps]
        if len(set(key)) != len(key):
            raise GenerationRetry  # фрагменты должны различаться, иначе ответ неоднозначен
        shuffled = list(key)
        while shuffled == key:
            rng.shuffle(shuffled)
        distractors: dict[str, str] = {}
        shown: list[tuple[str, str]] = []
        for i, tag in sorted(swap_tags.items()):
            wrong = list(key)
            wrong[i], wrong[i + 1] = wrong[i + 1], wrong[i]
            distractors[canonical(wrong)] = tag
            shown.append((f"«{key[i + 1]}» раньше «{key[i]}»", tag))
        rendered = Rendered(
            prompt=prompt,
            language=language,
            lines=tuple(shuffled),
            answer_mode="order",
            answer_hint="Перетащите строки в порядке выполнения: сверху — первая команда.",
        )
        right = " → ".join(key)
        return Instance(self.id, seed, rendered, key, distractors, self._explain(why, right, shown))
