"""Docker: итоговая команда процесса (ENTRYPOINT + CMD + аргументы docker run) и кеш слоёв при пересборке.

Ключи — точные модели по ПОКАЗАННОМУ тексту: таблица правил запуска (документация Dockerfile reference, раздел
«Understand how CMD and ENTRYPOINT interact») и модель кеша сборки: слой пересобирается, если изменилась его инструкция,
изменились файлы, которые она копирует (с учётом .dockerignore), либо пересобран любой слой выше.
"""

from __future__ import annotations

import json
import random
import re
import shlex
from typing import Any

from app.testing.bank.base import register
from app.testing.bank.families.devops._common import (
    MODULE,
    SPECS,
    GenerationRetry,
    OpsFamily,
    Scenario,
    ScenarioError,
    numeric_alternatives,
)

# ============================================================== ENTRYPOINT и CMD

_RUN_OPTS_WITH_VALUE = {"-p", "-e", "--name", "-v", "--entrypoint", "-w", "--network"}
_QUOTES = str.maketrans({'"': " ", "'": " ", "[": " ", "]": " ", ",": " ", "«": " ", "»": " "})
_IMAGES = ["shop-api", "orders-svc", "billing-worker", "catalog-web", "notify-bot", "report-gen", "auth-proxy"]
_REGISTRIES = ["", "", "registry.local/team/", "reg.example.org/ops/"]


def _exec_ep(rng: random.Random) -> list[str]:
    return rng.choice(
        [
            ["python", "-m", "app"],
            ["gunicorn", "app:app"],
            ["uvicorn", "main:app"],
            ["node", "server.js"],
            ["java", "-jar", "app.jar"],
            ["/usr/local/bin/run-worker"],
            ["celery", "-A", "tasks", "worker"],
            ["./start.sh"],
        ]
    )


def _arg_pool(rng: random.Random) -> list[list[str]]:
    port = rng.randint(1100, 9900)
    workers = rng.randint(2, 64)
    timeout = rng.randint(15, 600)
    return [
        ["--port", str(port)],
        ["--workers", str(workers)],
        ["--timeout", str(timeout)],
        ["--bind", f"0.0.0.0:{rng.randint(1100, 9900)}"],
        ["--retries", str(rng.randint(2, 99))],
        ["--concurrency", str(rng.randint(2, 96))],
    ]


def _parse_instruction(value: str) -> tuple[str, Any]:
    value = value.strip()
    if value.startswith("["):
        items = json.loads(value)
        if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
            raise ScenarioError("не JSON-массив строк")
        return "exec", items
    return "shell", value


def parse_entrypoint_case(code: str, *, first_wins: bool = False) -> dict[str, Any]:
    """Dockerfile-фрагмент и строка `$ docker run …` → ENTRYPOINT, CMD, аргументы запуска, --entrypoint."""
    entry: tuple[str, Any] | None = None
    cmd: tuple[str, Any] | None = None
    run: list[str] | None = None
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("ENTRYPOINT "):
            if not (first_wins and entry is not None):
                entry = _parse_instruction(line[len("ENTRYPOINT ") :])
        elif line.startswith("CMD "):
            if not (first_wins and cmd is not None):
                cmd = _parse_instruction(line[len("CMD ") :])
        elif line.startswith("$ docker run"):
            run = shlex.split(line[2:])[2:]
    if run is None:
        raise ScenarioError("нет строки docker run")
    override: str | None = None
    image_seen = False
    args: list[str] = []
    i = 0
    while i < len(run):
        tok = run[i]
        if image_seen:
            args.append(tok)
        elif tok in _RUN_OPTS_WITH_VALUE:
            if tok == "--entrypoint":
                override = run[i + 1]
            i += 1
        elif not tok.startswith("-"):
            image_seen = True
        i += 1
    return {"entry": entry, "cmd": cmd, "override": override, "args": args}


def _words(kind_value: tuple[str, Any]) -> list[str]:
    kind, value = kind_value
    return list(value) if kind == "exec" else value.split()


def _sh(value: str) -> list[str]:
    return ["/bin/sh", "-c", value]


def final_argv(case: dict[str, Any], mutant: str | None = None) -> list[str]:
    entry, cmd, override, args = case["entry"], case["cmd"], case["override"], case["args"]
    if mutant == "flag_ignored":
        override = None
    if mutant == "run_args_ignored":
        args = []
    if override is not None:
        keep_cmd = mutant == "entrypoint_flag_keeps_cmd" and not args and cmd is not None
        tail = args or (_cmd_part(cmd) if keep_cmd else [])
        return [override, *tail]
    cmd_part = list(args) if args else _cmd_part(cmd)
    if entry is None:
        if not cmd_part:
            raise ScenarioError("нечего запускать")
        if mutant == "args_appended_to_cmd" and args:
            return [*_cmd_part(cmd), *args]
        if mutant == "no_sh_wrap" and not args and cmd is not None and cmd[0] == "shell":
            return cmd[1].split()
        return cmd_part
    kind, value = entry
    if kind == "shell":
        if mutant == "shell_entrypoint_gets_cmd":
            return [*value.split(), *cmd_part]
        if mutant == "no_sh_wrap":
            return value.split()
        return _sh(value)
    if mutant == "args_replace_all" and args:
        return list(args)
    if mutant == "cmd_ignored" and not args:
        return list(value)
    if mutant == "args_appended_to_cmd" and args:
        return [*value, *_cmd_part(cmd), *args]
    if mutant == "shell_cmd_split" and not args and cmd is not None and cmd[0] == "shell":
        return [*value, *cmd[1].split()]
    if mutant == "reversed_order" and cmd_part:
        return [*cmd_part, *value]
    return [*value, *cmd_part]


def _cmd_part(cmd: tuple[str, Any] | None) -> list[str]:
    if cmd is None:
        return []
    kind, value = cmd
    return list(value) if kind == "exec" else _sh(value)


@register
class EntrypointCmd(OpsFamily):
    id = "ops.docker.entrypoint"
    title = "CMD и ENTRYPOINT: какая команда запустится"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "junior"
    position = -0.3
    time_limit_s = 120
    is_anchor = True
    enemies = ("ops.docker.cache",)
    answer_kind = "output"
    language = "text"
    measures = (
        "Взаимодействие ENTRYPOINT и CMD, exec- и shell-форма, аргументы docker run вместо CMD, "
        "--entrypoint (сбрасывает CMD образа), последняя из повторных инструкций."
    )
    misconceptions = {
        "args_replace_all": "аргументы docker run заменяют только CMD: ENTRYPOINT в exec-форме остаётся и получает их",
        "args_appended_to_cmd": "аргументы docker run заменяют CMD целиком, а не дописываются после него",
        "cmd_ignored": "при ENTRYPOINT в exec-форме CMD не игнорируется: он даёт аргументы по умолчанию",
        "shell_entrypoint_gets_cmd": "ENTRYPOINT в shell-форме запускается как /bin/sh -c «строка» и не получает ни CMD, "
        "ни аргументов docker run",
        "shell_cmd_split": "CMD в shell-форме запускается через /bin/sh -c: оболочка становится аргументом ENTRYPOINT",
        "entrypoint_flag_keeps_cmd": "--entrypoint заменяет ENTRYPOINT и сбрасывает CMD образа: остаются только "
        "аргументы после имени образа",
        "first_wins": "из нескольких ENTRYPOINT или CMD в Dockerfile действует последний",
        "reversed_order": "ENTRYPOINT — начало команды, а CMD (или аргументы запуска) — её продолжение, не наоборот",
        "no_sh_wrap": "shell-форма запускается как /bin/sh -c «строка»: оболочка — процесс PID 1, а не сама команда",
        "flag_ignored": "флаг --entrypoint заменяет ENTRYPOINT образа, а не игнорируется",
        "run_args_ignored": "слова после имени образа в docker run заменяют CMD, они не игнорируются",
    }
    answer_hint = (
        "Введите команду PID 1 одной строкой, слова через пробел. Для shell-формы начните с /bin/sh -c и "
        "перечислите слова строки; кавычки и запятые можно не писать."
    )

    def normalize(self, answer: Any) -> Any:
        if not isinstance(answer, str):
            return answer
        return " ".join(answer.translate(_QUOTES).split())

    def make(self, rng: random.Random) -> Scenario:  # noqa: C901 — таблица вариантов
        variant = rng.choice(
            ["exec_exec", "exec_exec_args", "cmd_only_args", "shell_ep", "exec_shell_cmd", "ep_flag", "dup", "shell_cmd"]
        )
        ep, pool = _exec_ep(rng), _arg_pool(rng)
        cmd_args = rng.choice(pool)
        run_args = rng.choice([p for p in pool if p[0] != cmd_args[0]])
        image = rng.choice(_REGISTRIES) + rng.choice(_IMAGES) + f":{rng.randint(1, 4)}.{rng.randint(0, 9)}"

        def exec_form(words: list[str]) -> str:
            return json.dumps(words)

        lines = [f"FROM {rng.choice(['python:3.12-slim', 'node:20-alpine', 'eclipse-temurin:21-jre'])}",
                 f"WORKDIR /{rng.choice(['srv', 'app', 'opt/app'])}", "COPY . ."]
        opts = rng.choice(["--rm", "--rm -d", "-d", "--rm -it"])
        run = f"$ docker run {opts} "
        tail_args = ""
        if variant == "exec_exec":
            lines += [f"ENTRYPOINT {exec_form(ep)}", f"CMD {exec_form(cmd_args)}"]
        elif variant == "exec_exec_args":
            lines += [f"ENTRYPOINT {exec_form(ep)}", f"CMD {exec_form(cmd_args)}"]
            tail_args = " ".join(run_args)
        elif variant == "cmd_only_args":
            whole = rng.choice([["python", "app.py", *cmd_args], ["node", "server.js", *cmd_args]])
            lines += [f"CMD {exec_form(whole)}"]
            tail_args = " ".join(
                rng.choice(
                    [
                        ["python", "-m", "pytest", "--maxfail", str(rng.randint(2, 99))],
                        ["python", "manage.py", "check", "--timeout", str(rng.randint(10, 900))],
                        ["node", "worker.js", *run_args],
                    ]
                )
            )
        elif variant == "shell_ep":
            lines += [f"ENTRYPOINT {' '.join(ep)} {' '.join(cmd_args)}", f"CMD {exec_form(run_args)}"]
            tail_args = rng.choice(["", " ".join(run_args)])
        elif variant == "exec_shell_cmd":
            lines += [f"ENTRYPOINT {exec_form(ep)}", f"CMD {' '.join(cmd_args)}"]
        elif variant == "ep_flag":
            lines += [f"ENTRYPOINT {exec_form(ep)}", f"CMD {exec_form(cmd_args)}"]
            override = rng.choice(["/bin/sh", "/bin/echo", "env", f"/opt/jobs/run-{rng.randint(100, 999)}.sh"])
            run += f"--entrypoint {override} "
            numeric = any(c.isdigit() for c in override)
            tail_args = "" if numeric and rng.random() < 0.3 else " ".join(run_args)
        elif variant == "dup":
            other = rng.choice([p for p in pool if p[0] not in (cmd_args[0], run_args[0])])
            lines += [f"CMD {exec_form(other)}", f"ENTRYPOINT {exec_form(ep)}", f"CMD {exec_form(cmd_args)}"]
        else:  # shell_cmd
            lines += [f"CMD {' '.join(ep)} {' '.join(cmd_args)}"]
            tail_args = rng.choice(["", " ".join(run_args)])
        # Служебные строки: порядок «ENTRYPOINT раньше CMD» не обязателен, поэтому иногда меняем местами.
        if variant in ("exec_exec", "exec_exec_args", "exec_shell_cmd") and rng.random() < 0.3:
            lines[-2], lines[-1] = lines[-1], lines[-2]
        run += image + (f" {tail_args}" if tail_args else "")
        code = "\n".join(lines) + "\n\n" + run
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Образ собран из показанного Dockerfile, затем контейнер запущен показанной командой. "
            "Какая команда выполняется в контейнере как процесс PID 1?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        case = parse_entrypoint_case(code, first_wins=mutant == "first_wins")
        return " ".join(final_argv(case, mutant))

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Правила: если задан ENTRYPOINT в exec-форме, команда = ENTRYPOINT + (аргументы docker run, а если их нет — "
            "CMD). Аргументы docker run заменяют CMD целиком. ENTRYPOINT в shell-форме превращается в "
            "/bin/sh -c «строка» и не получает ни CMD, ни аргументов. CMD в shell-форме тоже запускается через "
            "/bin/sh -c. Флаг --entrypoint заменяет ENTRYPOINT и сбрасывает CMD образа. Из нескольких одноимённых "
            "инструкций действует последняя."
        )


# ============================================================== кеш слоёв

_HEADER_LINE = re.compile(r"^# Изменение: (?:правка инструкции в строке (\d+)|изменён файл (\S+))\s*$", re.M)
_STEP_LINE = re.compile(r"^\s*(\d+): (FROM|WORKDIR|ENV|COPY|ADD|RUN|EXPOSE|CMD|ENTRYPOINT)\s+(.*?)\s*#\s*(\d+) с\s*$", re.M)
_TIME_SPAN = re.compile(r"#\s*(\d+) с")

_TEMPLATES: dict[str, dict[str, Any]] = {
    "python": {
        "steps": [
            ("FROM", "python:3.12-slim", 3), ("WORKDIR", "/app", 1), ("COPY", "requirements.txt .", 2),
            ("RUN", "pip install -r requirements.txt", 48), ("COPY", ". .", 4), ("RUN", "python -m compileall .", 12),
            ("EXPOSE", "8000", 1), ("CMD", '["python", "main.py"]', 1),
        ],
        "files": ["requirements.txt", "main.py", "app/views.py", "app/models.py", "tests/test_api.py", "README.md",
                  "docs/guide.rst", "debug.log"],
    },
    "node": {
        "steps": [
            ("FROM", "node:20-alpine", 4), ("WORKDIR", "/srv", 1), ("COPY", "package.json package-lock.json ./", 2),
            ("RUN", "npm ci", 55), ("COPY", "src/ ./src/", 3), ("COPY", "public/ ./public/", 2),
            ("RUN", "npm run build", 31), ("EXPOSE", "3000", 1), ("CMD", '["node", "dist/server.js"]', 1),
        ],
        "files": ["package.json", "package-lock.json", "src/index.ts", "src/routes/api.ts", "public/logo.svg",
                  "tests/api.test.ts", "CHANGELOG.md", "docs/setup.rst", "debug.log"],
    },
    "go": {
        "steps": [
            ("FROM", "golang:1.22-alpine", 5), ("WORKDIR", "/src", 1), ("COPY", "go.mod go.sum ./", 2),
            ("RUN", "go mod download", 22), ("COPY", "cmd/ ./cmd/", 2), ("COPY", "internal/ ./internal/", 3),
            ("RUN", "go build -o /bin/app ./cmd/app", 40), ("EXPOSE", "8080", 1), ("CMD", '["/bin/app"]', 1),
        ],
        "files": ["go.mod", "go.sum", "cmd/app/main.go", "internal/store/db.go", "internal/api/handler.go",
                  "tests/e2e_test.go", "README.md", "docs/design.rst", "debug.log"],
    },
    "dot": {
        "steps": [
            ("FROM", "python:3.12-slim", 3), ("WORKDIR", "/app", 1), ("ENV", "APP_MODE=prod", 1),
            ("COPY", ". .", 4), ("RUN", "pip install .", 36), ("RUN", "python -m app.check", 9),
            ("EXPOSE", "9000", 1), ("CMD", '["python", "-m", "app"]', 1),
        ],
        "files": ["setup.py", "app/core.py", "app/api/routes.py", "tests/test_core.py", "README.md", "docs/index.rst",
                  "debug.log"],
    },
}
_IGNORE_POOL = ["tests/", "docs/", "*.md", "*.log", ".git/"]


def _glob_match(pattern: str, path: str) -> bool:
    """Сопоставление в духе filepath.Match: «*» не пересекает «/»; пустой хвост каталога — префикс."""
    rx = "".join("[^/]*" if c == "*" else "[^/]" if c == "?" else re.escape(c) for c in pattern)
    return re.fullmatch(rx, path) is not None


def _ignored(path: str, patterns: list[str]) -> bool:
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif _glob_match(pat, path) or path.startswith(pat + "/"):
            return True
    return False


def _covers(sources: list[str], path: str) -> bool:
    for src in sources:
        if src in (".", "./"):
            return True
        if src.endswith("/"):
            if path.startswith(src[2:] if src.startswith("./") else src):
                return True
        elif _glob_match(src, path):
            return True
    return False


def parse_cache_case(code: str) -> dict[str, Any]:
    head = _HEADER_LINE.search(code)
    if not head:
        raise ScenarioError("нет строки «Изменение»")
    steps = [(int(m[1]), m[2], m[3], int(m[4])) for m in _STEP_LINE.finditer(code)]
    if not steps or [s[0] for s in steps] != list(range(1, len(steps) + 1)):
        raise ScenarioError("нумерация шагов нарушена")
    ignore: list[str] = []
    if "# .dockerignore" in code:
        tail = code.split("# .dockerignore", 1)[1]
        ignore = [ln.strip() for ln in tail.splitlines() if ln.strip() and not ln.strip().startswith("#")]
    return {
        "line": int(head[1]) if head[1] else None,
        "file": head[2],
        "steps": steps,
        "ignore": ignore,
    }


def rebuilt_steps(case: dict[str, Any], mutant: str | None = None) -> tuple[list[int], int]:
    steps = case["steps"]
    ignore = [] if mutant == "dockerignore_ignored" else case["ignore"]
    first: int | None = None
    if case["line"] is not None:
        if not 1 <= case["line"] <= len(steps):
            raise ScenarioError("такой строки нет")
        first = case["line"]
    else:
        path = case["file"]
        if not _ignored(path, ignore):
            for n, instr, arg, _t in steps:
                if instr in ("COPY", "ADD") and _covers(arg.split()[:-1], path):
                    first = n
                    break
    if first is None:
        return [], 0
    if mutant == "only_changed_step":
        chosen = [first]
    elif mutant == "from_start":
        chosen = [s[0] for s in steps]
    elif mutant == "after_only":
        chosen = [s[0] for s in steps if s[0] > first]
    elif mutant == "run_cached":
        chosen = [s[0] for s in steps if s[0] >= first and (s[0] == first or s[1] in ("COPY", "ADD", "FROM"))]
    else:
        chosen = [s[0] for s in steps if s[0] >= first]
    return chosen, sum(steps[n - 1][3] for n in chosen)


@register
class DockerLayerCache(OpsFamily):
    id = "ops.docker.cache"
    title = "Кеш слоёв Docker: что пересоберётся"
    specs = SPECS
    module = MODULE
    format = "predict_output"
    target_grade = "junior"
    position = 0.1
    time_limit_s = 120
    enemies = ("ops.docker.entrypoint",)
    answer_kind = "ints"
    language = "text"
    measures = (
        "Инвалидация кеша сборки: изменённая инструкция и все следующие; COPY/ADD зависят от содержимого "
        "копируемых файлов с учётом .dockerignore; порядок слоёв и время сборки."
    )
    misconceptions = {
        "only_changed_step": "после изменённого слоя кеш недействителен для всех следующих слоёв, а не только для него",
        "dockerignore_ignored": "файл из .dockerignore не попадает в контекст сборки: его изменение кеш не сбрасывает",
        "from_start": "слои выше изменённого берутся из кеша: пересборка идёт только с первого изменённого слоя",
        "after_only": "изменённая инструкция тоже выполняется заново, а не только следующие за ней",
        "run_cached": "RUN, стоящий после сброшенного слоя, не берётся из кеша, даже если его текст не менялся",
    }
    answer_hint = (
        "Введите через пробел номера шагов, которые выполнятся заново (по возрастанию), затем суммарное время этих "
        "шагов и, последним числом, суммарное время шагов, взятых из кеша, в секундах. Если не пересоберётся ничего, "
        "номера не пишите: введите 0 и время шагов из кеша."
    )

    def make(self, rng: random.Random) -> Scenario:
        tpl_name = rng.choice(list(_TEMPLATES))
        t = _TEMPLATES[tpl_name]
        steps = list(t["steps"])
        n = len(steps)
        lines = []
        for i, (instr, arg, base) in enumerate(steps, start=1):
            small = instr in ("EXPOSE", "CMD", "WORKDIR", "ENV")
            sec = rng.randint(1, 40) if small else rng.randint(max(3, base // 3), base * 9)
            lines.append(f"{i}: {instr} {arg}   # {sec} с")
        ignore = rng.sample(_IGNORE_POOL, rng.randint(1, 3))
        if rng.random() < 0.5:
            line_no = rng.randint(2, n - 3)
            header = f"# Изменение: правка инструкции в строке {line_no}"
        else:
            path = rng.choice(t["files"])
            header = f"# Изменение: изменён файл {path}"
        code = f"{header}\n# Dockerfile\n" + "\n".join(lines) + "\n# .dockerignore\n" + "\n".join(ignore)
        # «Ничего не пересобирается» (файл из .dockerignore) — поучительный, но редкий случай: иначе ключ «0» повторяется.
        if not rebuilt_steps(parse_cache_case(code))[0] and rng.random() < 0.9:
            raise GenerationRetry
        return Scenario(code)

    def prompt(self, sc: Scenario) -> str:
        return (
            "Образ уже собирался, кеш сборки (BuildKit) сохранён. Показано, что изменили в проекте, Dockerfile с "
            "номерами шагов и временем выполнения каждого шага без кеша, а также .dockerignore. "
            "Какие шаги выполнятся заново при следующей сборке, сколько секунд они займут и сколько секунд сэкономит кеш?"
        )

    def solve_impl(self, sc: Scenario, code: str, mutant: str | None = None) -> str | None:
        case = parse_cache_case(code)
        chosen, total = rebuilt_steps(case, mutant)
        cached = sum(s[3] for s in case["steps"]) - total
        return f"{' '.join(map(str, chosen))} {total} {cached}" if chosen else f"0 {cached}"

    def why(self, sc: Scenario, raw_key: str) -> str:
        return (
            "Слой берётся из кеша, пока не изменились его инструкция, содержимое файлов, которые копирует COPY/ADD "
            "(файлы из .dockerignore в контекст не попадают), и все слои выше. Первый сброшенный слой и каждый "
            "следующий выполняются заново; остальные берутся из кеша и экономят своё время."
        )

    def twin_spans(self, sc: Scenario, code: str) -> list[tuple[int, int]]:
        spans = [m.span(1) for m in _HEADER_LINE.finditer(code) if m.group(1)]
        spans += [m.span(1) for m in _TIME_SPAN.finditer(code)]
        return spans

    def twin_values(self, sc: Scenario, code: str, span: tuple[int, int], rng: random.Random, p: Any) -> list[str]:
        old = code[span[0] : span[1]]
        in_header = code[: span[0]].rstrip().endswith("строке") or code[max(0, span[0] - 25) : span[0]].endswith("в строке ")
        hi = 9 if in_header else 120
        return numeric_alternatives(old, rng, p, 2, hi)

