"""ops.perm.umask: права новых файлов и каталогов при заданном umask, chmod без указания «для кого».

Ключ — точная битовая модель: файл создаётся с 0666, каталог с 0777, umask ВЫКЛЮЧАЕТ свои биты (mode & ~umask);
chmod без who подчиняется umask (GNU coreutils), с явным u/g/o/a — нет. Решатель разбирает показанный сеанс.
"""

from __future__ import annotations

import random
import re
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

_UMASK_DIGITS = [0, 0, 2, 2, 2, 7, 7, 3, 5, 4, 6, 1]
_FILES = ["notes.txt", "report.csv", "app.log", "deploy.sh", "backup.sh", "config.ini", "data.json", "build.sh",
          "key.pem", "run.sh", "export.csv", "token.txt", "init.sql", "worker.py"]
_DIRS = ["data", "logs", "uploads", "cache", "backup", "tmp", "reports", "builds", "static", "media"]
_FILE_CHMOD = ["+x", "+x", "a+x", "g+w", "o-r", "u+x", "g+x", "go-w", "o+r", "a-w"]
_DIR_CHMOD = ["g+w", "o-x", "o+w", "g-x", "a+w", "o-r"]
_SHIFT = {"u": 6, "g": 3, "o": 0}
_PERM = {"r": 4, "w": 2, "x": 1}
_CLAUSE = re.compile(r"^([ugoa]*)([+\-])([rwx]+)$")
_UMASK_LINE = re.compile(r"\bumask\s+([0-7]{3})\b")


def _bits(who: str, perms: str) -> int:
    value = sum(_PERM[p] for p in perms)
    classes = "ugo" if not who or "a" in who else who
    return sum(value << _SHIFT[c] for c in classes)


def apply_chmod(mode: int, clause: str, umask: int, *, nowho_unmasked: bool = False, a_masked: bool = False) -> int:
    """chmod CLAUSE: без who биты, закрытые umask, не затрагиваются (GNU), с явным who — затрагиваются."""
    m = _CLAUSE.match(clause)
    if not m:
        raise ScenarioError(f"не разобрана маска chmod: {clause}")
    who, op, perms = m.groups()
    bits = _bits(who, perms)
    masked = (not who and not nowho_unmasked) or (who == "a" and a_masked)
    if masked:
        bits &= ~umask
    return mode | bits if op == "+" else mode & ~bits


def simulate(
    code: str,
    *,
    subtract: bool = False,
    file_base: int = 0o666,
    nowho_unmasked: bool = False,
    first_umask_only: bool = False,
    a_masked: bool = False,
) -> dict[str, int]:
    """Выполнить показанный сеанс; вернуть права созданных объектов в порядке создания. Флаги — модели заблуждений."""
    umask = 0o022
    first: int | None = None
    modes: dict[str, int] = {}
    for line in code.splitlines():
        line = line.strip()
        if not line.startswith("$ "):
            continue
        for cmd in line[2:].split(" && "):
            words = cmd.split()
            if not words:
                continue
            if words[0] == "umask":
                value = int(words[1], 8)
                if first is None:
                    first = value
                if not (first_umask_only and first is not None and value != first):
                    umask = value
                continue
            eff = first if first_umask_only and first is not None else umask
            if words[0] in ("touch", "mkdir"):
                base = file_base if words[0] == "touch" else 0o777
                if subtract:
                    digits = [max(int(d) - int(u), 0) for d, u in zip(f"{base:03o}", f"{eff:03o}", strict=True)]
                    modes[words[1]] = int("".join(map(str, digits)), 8)
                else:
                    modes[words[1]] = base & ~eff
            elif words[0] == "chmod":
                modes[words[2]] = apply_chmod(
                    modes[words[2]], words[1], eff, nowho_unmasked=nowho_unmasked, a_masked=a_masked
                )
            else:
                raise ScenarioError(f"неизвестная команда: {words[0]}")
    return modes


def _fmt(modes: dict[str, int], asked: list[str]) -> str:
    return " ".join(f"{modes[n]:03o}" for n in asked)


def _render(
    plan: list[tuple[str, str, str | None]], umasks: list[str], switch_at: int, joined: list[bool]
) -> str:
    lines = [f"$ umask {umasks[0]}"]
    for i, (cmd, name, clause) in enumerate(plan):
        if i == switch_at:
            lines.append(f"$ umask {umasks[1]}")
        if clause and joined[i]:
            lines.append(f"$ {cmd} {name} && chmod {clause} {name}")
        else:
            lines.append(f"$ {cmd} {name}")
            if clause:
                lines.append(f"$ chmod {clause} {name}")
    return "\n".join(lines)


@register
class UmaskModes(OpsFamily):
    id = "ops.perm.umask"
    title = "umask: права новых файлов и каталогов"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "intern"
    position = 0.2
    time_limit_s = 100
    is_anchor = True
    answer_kind = "ints"
    language = "text"
    measures = (
        "Права при создании: файл 0666, каталог 0777, umask выключает биты; chmod без «кого» подчиняется umask, "
        "chmod a+x — нет; новое значение umask действует на последующие команды."
    )
    misconceptions = {
        "subtract_digits": "umask не вычитается цифра за цифрой: он выключает биты (6 & ~1 = 6, а не 5; 6 & ~3 = 4, "
        "а не 3)",
        "file_base_777": "обычный файл создаётся с правами 666, а не 777: бит исполнения сам не появляется",
        "plus_x_all": "chmod +x без указания, для кого, подчиняется umask: биты исполнения, закрытые umask, не "
        "включаются",
        "first_umask_only": "каждая команда umask действует на всё, что создано после неё: новое значение заменяет "
        "прежнее",
        "a_x_masked": "chmod с явным «кого» (a+x, g+w…) umask не учитывает: маской фильтруется только запись без «кого»",
    }
    answer_hint = "Введите права в виде трёх цифр для каждого объекта через пробел, в порядке, как они названы в вопросе."

    def normalize(self, answer: Any) -> Any:
        """Права — токены из трёх цифр: «20» и «020» равны, лишний нулевой разряд спецбитов («0640») отбрасывается."""
        if not isinstance(answer, str):
            return answer
        found = re.findall(r"\d+", answer)
        return " ".join(t[-3:].zfill(3) for t in found) if found else answer.strip()

    def make(self, rng: random.Random) -> Scenario:
        files = rng.sample(_FILES, 3)
        dirs = rng.sample(_DIRS, 2)
        plan: list[tuple[str, str, str | None]] = [("touch", files[0], None), ("mkdir", dirs[0], None)]
        for kind in rng.sample(["file_x", "file_clause", "dir_clause"], 2):
            if kind == "file_x":
                plan.append(("touch", files[1], rng.choice(["+x", "+x", "a+x"])))
            elif kind == "dir_clause":
                plan.append(("mkdir", dirs[1], rng.choice(_DIR_CHMOD)))
            else:
                plan.append(("touch", files[2], rng.choice(_FILE_CHMOD)))
        rng.shuffle(plan)
        umasks = ["".join(str(rng.choice(_UMASK_DIGITS)) for _ in range(3)) for _ in range(2)]
        two = umasks[0] != umasks[1] and rng.random() < 0.45
        switch_at = rng.randint(1, len(plan) - 1) if two else -1
        joined = [rng.random() < 0.5 for _ in plan]
        code = _render(plan, umasks, switch_at, joined)
        modes = simulate(code)
        # Каждая команда chmod должна менять права (иначе пункт вырождается).
        for i, (cmd, name, clause) in enumerate(plan):
            if clause:
                bare = [*plan[:i], (cmd, name, None), *plan[i + 1 :]]
                if simulate(_render(bare, umasks, switch_at, joined)).get(name) == modes[name]:
                    raise GenerationRetry
        return Scenario(code, {"asked": [name for _, name, _ in plan]})

    def prompt(self, sc: Scenario) -> str:
        names = ", ".join(f"«{n}»" for n in sc.data["asked"])
        return (
            "В пустом рабочем каталоге в bash выполнен сеанс команд (результат ls не показан, ошибок нет). "
            f"Какие права в восьмеричной записи получат: {names}?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        asked: list[str] = sc.data["asked"]
        flags: dict[str, Any] = {
            None: {},
            "subtract_digits": {"subtract": True},
            "file_base_777": {"file_base": 0o777},
            "plus_x_all": {"nowho_unmasked": True},
            "first_umask_only": {"first_umask_only": True},
            "a_x_masked": {"a_masked": True},
        }[mutant]
        return _fmt(simulate(code, **flags), asked)

    def why(self, sc: Scenario, raw_key: str) -> str:
        first = _UMASK_LINE.search(sc.code)
        um = first.group(1) if first else "022"
        return (
            f"Обычный файл создаётся с правами 666, каталог — с 777; umask {um} выключает соответствующие биты: "
            f"файл получает 666 & ~{um} = {0o666 & ~int(um, 8):03o}, каталог — 777 & ~{um} = {0o777 & ~int(um, 8):03o}. "
            "Если umask в сеансе менялся, действует то значение, что было на момент создания. chmod без «кого» "
            "(chmod +x) не включает биты, закрытые umask; chmod с явным u/g/o/a (chmod a+x, g+w) их включает."
        )

    # Двойник: меняем одну цифру в одном из umask (три цифры, ведущий ноль допустим — общий мутатор такие не берёт).
    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        return [m.span(1) for m in _UMASK_LINE.finditer(code)]

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: Any) -> list[str]:
        from app.testing.anticheat import literals as lit  # noqa: PLC0415

        old = code[span[0] : span[1]]
        out: list[str] = []
        for _ in range(40):
            pos = rng.randrange(3)
            digit = str(rng.choice([d for d in range(8) if str(d) != old[pos]]))
            new = old[:pos] + digit + old[pos + 1 :]
            if new != old and new not in out and lit.glyph_ok(old, new, p.confusable_pairs):
                out.append(new)
        return out

