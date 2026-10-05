"""ops.sh.exitcodes: коды возврата, списки && и ||, конвейеры, pipefail, set -e в bash.

Ключ — точная модель bash на ограниченной грамматике: списки && и || равноприоритетны и вычисляются слева направо;
статус конвейера — статус последней команды (с pipefail — самой правой ненулевой); `!` инвертирует статус; `$?` — статус
последнего завершённого конвейера. set -e не срабатывает на команде в списке && / || (кроме идущей после последнего
оператора), в условии if и под `!`. Тест сверяет модель с настоящим bash (если установлен).
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from typing import Any

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    OpsFamily,
    Scenario,
    ScenarioError,
)

_WORDS = ["ok", "fail", "yes", "no", "up", "down", "retry", "done", "skip", "run", "warn", "stop", "hit", "miss", "go",
          "wait", "mark", "pass", "late", "sent"]
_FUNC_RE = re.compile(r"^f\(\) \{ return (\d+); \}$")
_IF_RE = re.compile(r"^if (.+); then echo (\w+); else echo (\w+); fi$")
_OPS_RE = re.compile(r"\s(&&|\|\|)\s")
_EXIT_RE = re.compile(r"\(exit (\d+)\)")
_ECHO_RE = re.compile(r'echo (?:"([^"]*)"|(\w+))')


class _Abort(Exception):
    """set -e завершил скрипт."""


@dataclass(frozen=True)
class Seg:
    kind: str  # true | false | f | exit | echo
    arg: Any = None


@dataclass(frozen=True)
class Pipe:
    segs: tuple[Seg, ...]
    negated: bool


def parse_seg(text: str) -> Seg:
    text = text.strip()
    if text in ("true", "false", "f"):
        return Seg(text)
    m = _EXIT_RE.fullmatch(text)
    if m:
        return Seg("exit", int(m.group(1)))
    m = _ECHO_RE.fullmatch(text)
    if m:
        return Seg("echo", m.group(1) if m.group(1) is not None else m.group(2))
    raise ScenarioError(f"неизвестная команда: {text}")


def parse_pipe(text: str) -> Pipe:
    text = text.strip()
    negated = text.startswith("! ")
    if negated:
        text = text[2:]
    return Pipe(tuple(parse_seg(s) for s in re.split(r"\s\|\s", text)), negated)


def parse_list(text: str) -> tuple[list[Pipe], list[str]]:
    parts = _OPS_RE.split(text.strip())
    return [parse_pipe(p) for p in parts[0::2]], parts[1::2]


class Shell:
    """Модель bash для грамматики семейства. Флаги — модели заблуждений."""

    def __init__(
        self,
        *,
        pipefail_ignored: bool = False,
        pipe_first: bool = False,
        errexit_in_list: bool = False,
        bang_noop: bool = False,
        c_precedence: bool = False,
    ) -> None:
        self.pipefail_ignored, self.pipe_first = pipefail_ignored, pipe_first
        self.errexit_in_list, self.bang_noop, self.c_precedence = errexit_in_list, bang_noop, c_precedence
        self.out: list[str] = []
        self.status = 0
        self.errexit = False
        self.pipefail = False
        self.fn: int | None = None

    def seg_status(self, seg: Seg) -> int:
        if seg.kind in ("true", "echo"):
            return 0
        if seg.kind == "false":
            return 1
        if seg.kind == "exit":
            return int(seg.arg)
        if self.fn is None:
            raise ScenarioError("функция f не определена")
        return self.fn

    def run_pipe(self, pipe: Pipe) -> int:
        before = self.status
        sts = []
        for i, seg in enumerate(pipe.segs):
            if seg.kind == "echo" and i == len(pipe.segs) - 1:
                self.out.append(str(seg.arg).replace("$?", str(before)))
            sts.append(self.seg_status(seg))
        if self.pipefail and not self.pipefail_ignored:
            nonzero = [s for s in sts if s != 0]
            st = nonzero[-1] if nonzero else 0
        elif self.pipe_first:
            st = sts[0]
        else:
            st = sts[-1]
        if pipe.negated and not self.bang_noop:
            st = 0 if st != 0 else 1
        return st

    def _check_errexit(self, el: Pipe, status: int, idx: int, last: int, cond: bool) -> None:
        exempt = el.negated or cond or (idx < last and not self.errexit_in_list)
        if self.errexit and status != 0 and not exempt:
            raise _Abort

    def run_list(self, elems: list[Pipe], ops: list[str], *, cond: bool = False) -> None:
        if self.c_precedence:
            self._run_list_c(elems, ops, cond)
            return
        last = len(elems) - 1
        status = self.status
        for i, el in enumerate(elems):
            if i > 0:
                op = ops[i - 1]
                if (op == "&&" and status != 0) or (op == "||" and status == 0):
                    continue  # пропущено: статус списка не меняется
            self.status = status
            status = self.run_pipe(el)
            self.status = status
            self._check_errexit(el, status, i, last, cond)

    def _run_list_c(self, elems: list[Pipe], ops: list[str], cond: bool) -> None:
        """Заблуждение: && сильнее ||, то есть «a || b && c» вычисляется как «a || (b && c)»."""
        groups: list[list[Pipe]] = [[elems[0]]]
        for el, op in zip(elems[1:], ops, strict=True):
            if op == "||":
                groups.append([el])
            else:
                groups[-1].append(el)
        status, idx, last = self.status, 0, len(elems) - 1
        for gi, group in enumerate(groups):
            if gi > 0 and status == 0:
                idx += len(group)
                continue
            for j, el in enumerate(group):
                if j > 0 and status != 0:
                    idx += len(group) - j
                    break
                self.status = status
                status = self.run_pipe(el)
                self.status = status
                self._check_errexit(el, status, idx, last, cond)
                idx += 1

    def run_line(self, line: str) -> None:
        line = line.strip()
        if not line or line.startswith("#"):
            return
        if line == "set -e":
            self.errexit = True
            return
        if line == "set -o pipefail":
            self.pipefail = True
            return
        m = _FUNC_RE.match(line)
        if m:
            self.fn = int(m.group(1))
            return
        m = _IF_RE.match(line)
        if m:
            elems, ops = parse_list(m.group(1))
            self.run_list(elems, ops, cond=True)
            word = m.group(2) if self.status == 0 else m.group(3)
            self.out.append(word)
            self.status = 0
            return
        elems, ops = parse_list(line)
        self.run_list(elems, ops)


def run_script(code: str, **flags: bool) -> list[str]:
    sh = Shell(**flags)
    try:
        for line in code.splitlines():
            sh.run_line(line)
    except _Abort:
        pass
    return sh.out


@register
class ExitCodes(OpsFamily):
    id = "ops.sh.exitcodes"
    title = "bash: коды возврата, && / ||, pipefail и set -e"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "senior"
    position = 0.6
    time_limit_s = 180
    answer_kind = "output"
    language = "text"
    measures = (
        "Статусы команд и $?, равный приоритет && и || (слева направо), статус конвейера и pipefail, отрицание «!», "
        "исключения set -e (список, условие if, «!»)."
    )
    misconceptions = {
        "c_precedence": "в bash && и || равноприоритетны и вычисляются слева направо: «a || b && c» — это «(a || b) "
        "&& c», а не «a || (b && c)» как в языках с приоритетом &&",
        "pipefail_ignored": "с set -o pipefail статус конвейера — статус самой правой упавшей команды, а не только "
        "последней",
        "pipe_first": "статус конвейера — статус его последней команды (первая не в счёт), если нет pipefail",
        "errexit_in_list": "set -e не срабатывает на команде внутри списка && / || (кроме идущей после последнего "
        "оператора), в условии if и под «!»",
        "bang_noop": "«!» инвертирует статус конвейера: после ! false статус 0, после ! true — 1",
    }
    answer_hint = (
        "Введите всё, что напечатает скрипт, по порядку, через пробел или с новой строки (например, ok rc=3 no). "
        "Если скрипт завершится досрочно, укажите только то, что успело напечататься."
    )

    @staticmethod
    def _simple(rng: random.Random, *, silent: bool = False) -> str:
        pool = ["true", "false", "f", "f", f"(exit {rng.randint(2, 9)})", f"(exit {rng.randint(2, 9)})",
                f"(exit {rng.randint(2, 9)})"]
        if not silent:
            pool += [f"echo {rng.choice(_WORDS)}"] * 2
        return rng.choice(pool)

    def make(self, rng: random.Random) -> Scenario:  # noqa: C901
        words = rng.sample(_WORDS, 16)
        wi = iter(words)
        lines: list[str] = []
        errexit, pipefail = rng.random() < 0.3, rng.random() < 0.45
        if errexit:
            lines.append("set -e")
        if pipefail:
            lines.append("set -o pipefail")
        lines.append(f"f() {{ return {rng.randint(2, 9)}; }}")

        def simple(silent: bool = False) -> str:
            s = self._simple(rng, silent=silent)
            return f"echo {next(wi)}" if s.startswith("echo") else s

        def op() -> str:
            return rng.choice(["&&", "||"])

        def pipeline(last_any: bool = True) -> str:
            n = rng.randint(2, 3)
            return " | ".join([simple(True) for _ in range(n - 1)] + [simple(not last_any)])

        makers = {
            "or_and": lambda: f"{simple()} || {simple()} && echo {next(wi)}",
            "list3": lambda: f"{simple()} {op()} {simple()} {op()} {simple()}",
            "pipe": lambda: pipeline(),
            "bang": lambda: f"! {simple(True)} {op()} echo {next(wi)}",
            "if": lambda: f"if {pipeline()}; then echo {next(wi)}; else echo {next(wi)}; fi",
            # Показывает числовой статус: «(exit N) || echo "rc=$?"» печатает rc=N (и не прерывает скрипт при set -e).
            "show": lambda: rng.choice(
                [f"(exit {rng.randint(2, 9)})", "f", f"{simple(True)} | (exit {rng.randint(2, 9)})", f"{simple(True)} | f"]
            )
            + ' || echo "rc=$?"',
        }
        kinds = ["or_and", "pipe", "bang", "show", *rng.sample(["list3", "if", "show"], 2)]
        rng.shuffle(kinds)
        for k in kinds:
            lines.append(makers[k]())
            if rng.random() < 0.85:
                lines.append('echo "rc=$?"')
        code = "\n".join(lines)
        out = run_script(code)
        if not 4 <= len(out) <= 16:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return "Скрипт запущен как bash script.sh (bash 5). Что он напечатает в stdout?"

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        flags: dict[str, bool] = {} if mutant is None else {mutant: True}
        return "\n".join(run_script(code, **flags))

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Команды вычисляются сверху вниз. В списке && и || равноприоритетны и идут слева направо: правая часть "
            "выполняется по статусу накопленного результата. Статус конвейера — статус последней команды (с "
            "set -o pipefail — самой правой ненулевой), «!» инвертирует его, $? хранит статус последнего завершённого "
            "конвейера. set -e прерывает скрипт при ненулевом статусе, но не в списке && / || (кроме команды после "
            "последнего оператора), не в условии if и не под «!»."
        )
