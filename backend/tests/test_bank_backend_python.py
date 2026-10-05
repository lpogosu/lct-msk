"""Семейства пакета backend_python (python_core, sql, git_linux).

Что проверяем:
- гейты CI и проверку ключа и каждого дистрактора через check();
- нормализацию ответов;
- независимую сверку ключей: подпроцесс run_python (asyncio — в реальном времени), пересчёт SQL по показанной
  таблице, план sqlite, настоящий bash для конвейера и перенаправлений, реплей истории в настоящем git;
- эмпирическую асимптотику каждого каркаса задания на сложность;
- детерминизм при другом PYTHONHASHSEED, скорость генерации и состав банка (грейды, якоря, доля MCQ).
"""

from __future__ import annotations

import asyncio
import gc
import json
import math
import os
import random
import re
import shlex
import shutil
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import pytest

from app.testing.bank.base import GRADES, REGISTRY, canonical
from app.testing.bank.execution import run_python, run_sql
from app.testing.bank.families.backend_python import _common, git_linux, python_advanced, python_basics
from app.testing.bank.families.backend_python import sql as sql_families
from app.testing.bank.gates import check_family

PKG = "app.testing.bank.families.backend_python."
assert python_basics and sql_families  # модули импортированы ради регистрации семейств
FAMS = sorted((fid, f) for fid, f in REGISTRY.items() if type(f).__module__.startswith(PKG))
IDS = [fid for fid, _ in FAMS]
BY_ID = dict(FAMS)
BACKEND_DIR = Path(__file__).resolve().parents[1]
CATALOG = BACKEND_DIR / "app" / "testing" / "bank" / "families" / "backend_python" / "CATALOG.md"
GIT = shutil.which("git")


def _find_bash() -> str | None:
    """Настоящий bash: системный (Linux/macOS) или из Git for Windows. WSL-обёртку System32\\bash.exe не берём —
    она по-своему разворачивает окружение."""
    found = shutil.which("bash")
    if found and "system32" not in found.lower():
        return found
    if GIT:
        root = Path(GIT).resolve().parent.parent
        for cand in (root / "usr" / "bin" / "bash.exe", root / "bin" / "bash.exe"):
            if cand.exists():
                return str(cand)
    return None


BASH = _find_bash()


def _answers_from_distractors(inst):
    """Распаковать канонический неверный ответ обратно в форму, которую пришлёт клиент."""
    mode = inst.rendered.answer_mode
    for wrong, tag in inst.distractors.items():
        yield (json.loads(wrong) if mode in ("line_choice", "order") else wrong), tag


def _some_wrong_answer(inst):
    r, key = inst.rendered, inst.key
    if r.answer_mode == "text":
        return key + " 1"
    if r.answer_mode == "choice":
        return next(o.id for o in r.options if o.id != key)
    if r.answer_mode == "line_choice":
        return {"line": key["line"] + 1, "fix": key["fix"]}
    return list(reversed(key))


# ---------------------------------------------------------------- состав банка


def test_inventory_matches_assignment():
    assert len(FAMS) == 28
    per_module = Counter(f.module for _, f in FAMS)
    assert per_module == {"python_core": 14, "sql": 9, "git_linux": 5}
    grades = {m: Counter(f.target_grade for _, f in FAMS if f.module == m) for m in per_module}
    # ≈ стажёр 15% / junior 30% / middle 30% / senior 25% внутри модуля
    assert grades["python_core"] == {"intern": 2, "junior": 4, "middle": 4, "senior": 4}
    assert grades["sql"] == {"intern": 1, "junior": 3, "middle": 3, "senior": 2}
    assert grades["git_linux"] == {"intern": 1, "junior": 2, "middle": 1, "senior": 1}
    for fid, f in FAMS:
        prefix = {"python_core": ("py.",), "sql": ("sql.",), "git_linux": ("git.", "sh.")}[f.module]
        assert fid.startswith(prefix), fid
        assert f.specs == ("backend_python",)
        assert 60 <= f.time_limit_s <= 180
        assert -1 <= f.position <= 1 and f.target_grade in GRADES
        assert f.measures and f.misconceptions
        for e in f.enemies:  # враги симметричны и существуют
            assert fid in BY_ID[e].enemies
    # Якорь на каждом пороге (по грейду) в python_core и sql; в git_linux — стажёр, junior, middle.
    anchors = {(f.module, f.target_grade) for _, f in FAMS if f.is_anchor}
    assert {("python_core", g) for g in GRADES} <= anchors
    assert {("sql", g) for g in GRADES} <= anchors
    assert {("git_linux", g) for g in ("intern", "junior", "middle")} <= anchors
    # Доля семейств с выбором варианта (answer_mode = choice) ≤ 30%, mcq_concept — не больше 30% семейств.
    choice_like = [fid for fid, f in FAMS if f.generate(0).rendered.answer_mode == "choice"]
    assert len(choice_like) / len(FAMS) <= 0.30, choice_like
    assert sum(f.format == "mcq_concept" for _, f in FAMS) / len(FAMS) <= 0.30


@pytest.mark.parametrize("fid", IDS)
def test_guessing_parameter_matches_answer_mode(fid):
    fam = BY_ID[fid]
    inst = fam.generate(0)
    r = inst.rendered
    if r.answer_mode in ("text", "order"):
        assert fam.c == 0
    elif r.answer_mode == "choice":
        assert fam.c == pytest.approx(1 / len(r.options), abs=1e-3)
    else:  # клик по строке + выбор исправления: текст исправления указывает строку → 1 из числа исправлений
        assert fam.c == pytest.approx(1 / len(r.options), abs=1e-3)


def test_catalog_lists_every_family():
    text = CATALOG.read_text(encoding="utf-8")
    for fid in IDS:
        assert f"`{fid}`" in text, fid


# ---------------------------------------------------------------- гейты, ключ и дистракторы


@pytest.mark.parametrize("fid", IDS)
def test_family_gates(fid):
    errors = check_family(BY_ID[fid])
    assert not errors, errors


@pytest.mark.parametrize("fid", IDS)
def test_key_passes_wrong_fails_distractors_tagged(fid):
    fam = BY_ID[fid]
    for seed in range(10):
        inst = fam.generate(seed)
        assert fam.check(inst, inst.key) == (True, None)
        assert fam.check(inst, _some_wrong_answer(inst))[0] is False
        assert inst.distractors, f"seed={seed}: у экземпляра нет ни одного дистрактора"
        for answer, tag in _answers_from_distractors(inst):
            assert fam.check(inst, answer) == (False, tag)
            assert tag in fam.misconceptions
        assert "Верный ответ" in inst.explanation
        public = json.dumps(inst.rendered.to_public(), ensure_ascii=False)
        assert "distractor" not in public and "misconception" not in public
        if inst.rendered.answer_mode == "text":
            assert inst.key not in fam.normalize(inst.rendered.prompt)


def test_normalization_is_tolerant_but_exact():
    alias = BY_ID["py.aliasing.copy"]
    inst = alias.generate(3)
    raw = _common.exec_py(inst.rendered.code)
    spaced = raw.replace(", ", " ,  ").replace("[", " [ ")
    assert alias.check(inst, spaced)[0] and alias.check(inst, raw.replace("] ", "]\n"))[0]
    assert not alias.check(inst, raw.replace("[", "(").replace("]", ")"))[0]

    comp = BY_ID["py.comprehension.filter"]
    inst = comp.generate(5)
    shown = dict(reversed(list(eval(_common.exec_py(inst.rendered.code)).items())))  # noqa: S307 — наш вывод
    assert comp.check(inst, str(shown).replace("'", '"'))[0]

    dm = BY_ID["py.defaults.mutable"]
    inst = dm.generate(1)
    assert dm.check(inst, _common.exec_py(inst.rendered.code).replace("'", "«", 1).replace("'", "»", 1))[0]

    assert _common.norm_output("['a', \"b\"]  1\n2") == "['a','b']1 2"
    assert _common.norm_number("12 000,00") == "12000" == _common.norm_number("12000.0")
    assert _common.norm_number(12.5) == "12.5" and _common.norm_number("−7") == "-7"
    assert _common.norm_numbers("4  3; 7000.0") == "4 3 7000"
    # Запятая-разделитель тысяч (англ. запись) против десятичной запятой (рус.).
    assert _common.norm_number("1,446,200") == "1446200" == _common.norm_number("1 446 200")
    assert _common.norm_number("64,120") == "64120"
    assert _common.norm_number("7,5") == "7.5" and _common.norm_number("0,125") == "0.125"
    assert _common.norm_numbers("6, 5, 24,912") == "6 5 24912"
    assert _common.plural(1, "строку", "строки", "строк") == "1 строку"
    assert _common.plural(3, "строку", "строки", "строк") == "3 строки"
    assert _common.plural(12, "строку", "строки", "строк") == "12 строк"

    lb = BY_ID["py.closures.late_binding"]
    inst = lb.generate(2)
    assert lb.check(inst, {"line": str(inst.key["line"]), "fix": inst.key["fix"].upper()})[0]
    assert not lb.check(inst, {"line": "x", "fix": "a"})[0]

    wf = BY_ID["git.workflow.order"]
    inst = wf.generate(4)
    lines = list(inst.rendered.lines)
    assert wf.check(inst, [lines.index(s) for s in inst.key])[0]
    assert wf.check(inst, [str(lines.index(s)) for s in inst.key])[0]
    assert not wf.check(inst, [99] * len(lines))[0]
    assert sorted(lines) == sorted(inst.key) and lines != inst.key


# ---------------------------------------------------------------- Python: независимое исполнение


PY_OUTPUT = [fid for fid, f in FAMS if f.module == "python_core" and f.format == "predict_output"]


def _run_python_full_env(code: str) -> str:
    """Как execution.run_python, но с окружением родителя: asyncio на Windows без SYSTEMROOT не поднимает сокеты."""
    env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONHASHSEED": "0"}
    proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=60)
    assert proc.returncode == 0, proc.stderr[-1000:]
    return proc.stdout.rstrip("\n")


@pytest.mark.parametrize("fid", PY_OUTPUT)
def test_inprocess_exec_matches_subprocess(fid):
    """Ключ из exec в процессе совпадает с выводом `python -I -c` в отдельном процессе (asyncio — реальные таймеры)."""
    fam = BY_ID[fid]
    is_async = fid == "py.asyncio.tasks"
    for seed in (0, 1, 2) if is_async else (0, 1, 2, 3):
        inst = fam.generate(seed)
        out = _run_python_full_env(inst.rendered.code) if is_async else run_python(inst.rendered.code, timeout=30)
        assert fam.normalize(out) == inst.key


def test_exec_is_thread_safe_and_leaves_no_global_state():
    """Пул могут генерировать потоками внутри API: перехват вывода и цикл asyncio не должны быть общими на процесс."""
    from concurrent.futures import ThreadPoolExecutor

    policy_before, stdout_before = type(asyncio.get_event_loop_policy()), sys.stdout
    assert _common.exec_py('print(1, 2, sep="-", end="!")\nprint("x")') == "1-2!x"
    jobs = [(fid, seed) for fid in PY_OUTPUT for seed in range(300, 320)]
    sequential = [canonical(BY_ID[fid].generate(seed).key) for fid, seed in jobs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        parallel = list(pool.map(lambda job: canonical(BY_ID[job[0]].generate(job[1]).key), jobs))
    assert parallel == sequential
    assert sys.stdout is stdout_before and type(asyncio.get_event_loop_policy()) is policy_before


def test_asyncio_family_generates_inside_running_loop():
    """Генерация из async-кода (обработчик API): эталонный asyncio.run не должен падать на «уже запущенном цикле»."""
    fam = BY_ID["py.asyncio.tasks"]
    expected = fam.generate(5).key

    async def inside() -> str:
        return fam.generate(5).key

    assert asyncio.run(inside()) == expected


def test_bug_line_fix_verified_in_subprocess():
    fam = BY_ID["py.closures.late_binding"]
    for seed in range(6):
        inst = fam.generate(seed)
        m = re.search(r"Ожидаемый вывод: (.*), фактический: (.*)\. Найдите", inst.rendered.prompt)
        expected, actual = m.group(1), m.group(2)
        lines = list(inst.rendered.lines)
        assert run_python("\n".join(lines)) == actual
        ln = inst.key["line"]
        fix = next(o.text for o in inst.rendered.options if o.id == inst.key["fix"])
        lines[ln - 1] = re.match(r"\s*", lines[ln - 1]).group(0) + fix
        assert run_python("\n".join(lines)) == expected


def test_complexity_solver_control_examples():
    expected = ["O(n²)", "O(n)", "O(n²)", "O(n)", "O(n²)", "O(n²)", "O(n log n)", "O(n log n)", "O(n²)", "O(n)", "O(n)"]
    got = [python_advanced._CLASS_OF[python_advanced._total_cost(p)] for _, _, p, _ in python_advanced._SNIPPETS]
    assert got == expected


def _best_time(fn, args, reps: int = 3) -> float:
    best = math.inf
    for _ in range(reps):
        gc.disable()
        try:
            t = time.perf_counter()
            fn(*args)
            best = min(best, time.perf_counter() - t)
        finally:
            gc.enable()
    return best


@pytest.mark.parametrize("idx", range(len(python_advanced._SNIPPETS)))
def test_complexity_snippet_scales_as_solved(idx):
    """Эмпирика: при росте n в 4 раза квадратичный каркас замедляется в ~16 раз, остальные — в ~4–5."""
    _, body, phases, _ = python_advanced._SNIPPETS[idx]
    code = _common.tpl(body, fn="f", arg="items", x="item")
    ns: dict = {}
    exec(compile(code, "<snippet>", "exec", dont_inherit=True), ns)  # noqa: S102 — наш каркас
    quadratic = python_advanced._CLASS_OF[python_advanced._total_cost(phases)] == "O(n²)"
    # Квадратичность через сдвиг памяти (pop(0), insert(0), insort) видна только на больших n:
    # на малых доминируют накладные расходы вызова.
    memmove = any(p in ("pop0", "insert0", "insort") for _, prims in phases for p in prims)
    n1 = (8000 if memmove else 1500) if quadratic else 20000

    def args(n: int) -> tuple:
        data = list(range(n))
        random.Random(n).shuffle(data)
        return (data, data[: n // 2]) if "banned" in code else (data,)

    for _attempt in range(3):  # защита от шума планировщика ОС
        ratio = _best_time(ns["f"], args(4 * n1)) / _best_time(ns["f"], args(n1))
        if (ratio > 9) == quadratic:
            break
    assert (ratio > 9) == quadratic, f"каркас {idx}: отношение времён {ratio:.1f}"


# ---------------------------------------------------------------- SQL: пересчёт по показанной таблице


def _setup_from_rendered(table: dict) -> str:
    stmts = []
    for t in table.get("tables") or [table]:
        stmts.append(f"CREATE TABLE {t['name']} ({', '.join(t['columns'])});")
        for row in t["rows"]:
            vals = ", ".join(
                "NULL" if v is None else str(v) if isinstance(v, int) else "'" + v.replace("'", "''") + "'"
                for v in row
            )
            stmts.append(f"INSERT INTO {t['name']} VALUES ({vals});")
    return "\n".join(stmts)


SQL_RESULT = [fid for fid, f in FAMS if f.format == "sql_result"]


@pytest.mark.parametrize("fid", SQL_RESULT)
def test_sql_key_recomputed_from_shown_table(fid):
    fam = BY_ID[fid]
    for seed in range(8):
        inst = fam.generate(seed)
        rows = run_sql(_setup_from_rendered(inst.rendered.table), inst.rendered.code)
        if inst.rendered.answer_mode == "text":
            row = rows[0]
            raw = str(row[1]) if fid == "sql.group.having" else " ".join(str(v) for v in row)
            assert fam.normalize(raw) == inst.key
        else:
            value = rows[0][0] if len(rows) == 1 and len(rows[0]) == 1 else len(rows)
            assert next(o.text for o in inst.rendered.options if o.id == inst.key) == str(value)


def test_index_key_is_the_only_index_search():
    fam = BY_ID["sql.index.composite"]
    for seed in range(12):
        inst = fam.generate(seed)
        idx = re.search(r"CREATE INDEX (\w+)", inst.rendered.code).group(1)
        for opt in inst.rendered.options:
            plan = " ".join(r[-1] for r in run_sql(inst.rendered.code, "EXPLAIN QUERY PLAN " + opt.text.rstrip(";")))
            assert (plan.startswith("SEARCH") and idx in plan) == (opt.id == inst.key), opt.text


# ---------------------------------------------------------------- shell и git: модели против настоящих


def test_shell_models_control_examples():
    p = git_linux.perms_from_octal("644")
    assert git_linux.to_octal(git_linux.apply_chmod(p, [("u", "+", "x"), ("o", "-", "r")])) == "740"
    assert git_linux.to_octal(git_linux.apply_chmod(p, [("g", "=", "rw")])) == "664"
    assert git_linux.to_symbolic(git_linux.perms_from_octal("750")) == "-rwxr-x---"
    assert git_linux.to_octal(git_linux.apply_chmod(git_linux.perms_from_octal("755"), [("g", "=", "w"), ("o", "-", "x")])) == "724"
    # Режим без прав владельца записывается тремя цифрами с ведущим нулём (как в `chmod 056`).
    assert git_linux.to_octal(git_linux.apply_chmod(git_linux.perms_from_octal("754"), [("u", "-", "rwx"), ("o", "+", "w")])) == "056"


def test_chmod_asks_for_mode_notation_not_stat_output():
    """stat -c '%a' печатает режим без ведущих нулей, поэтому спрашиваем запись режима из трёх цифр, а не вывод stat."""
    fam = BY_ID["sh.chmod.octal"]
    leading_zero_keys = 0
    for seed in range(200):
        inst = fam.generate(seed)
        assert "%a" not in inst.rendered.code and "три цифры" in inst.rendered.prompt
        assert all(re.fullmatch(r"[0-7]{3}", o.text) for o in inst.rendered.options)
        leading_zero_keys += next(o.text for o in inst.rendered.options if o.id == inst.key).startswith("0")
    assert leading_zero_keys > 0  # такие режимы встречаются — и теперь их ключ корректен
    assert git_linux.sh_uniq_c(["a", "a", "b", "a"]) == ["      2 a", "      1 b", "      1 a"]
    assert git_linux.sh_sort_n(["      1 b", "      2 a", "      1 c"], reverse=True) == [
        "      2 a", "      1 c", "      1 b"]
    cmds = [("a", "trunc_out"), ("b", "dup_then_append"), ("c", "append_both"), ("d", "append_err")]
    assert git_linux.simulate_redirects(cmds, ("out", "err")) == ["out:a", "out:b", "out:c", "err:c", "err:d"]
    assert git_linux.simulate_redirects(cmds, ("out", "err"), dup_order_ignored=True)[-3:] == [
        "out:c", "err:c", "err:d"]


def _bash(command: str, cwd: Path) -> str:
    proc = subprocess.run(
        [BASH, "-c", "export PATH=/usr/bin:/bin:$PATH; export LC_ALL=C; " + command],
        cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


@pytest.mark.skipif(BASH is None, reason="bash не установлен")
def test_pipeline_model_matches_bash(tmp_path):
    fam = BY_ID["sh.pipeline.uniq"]
    for seed in range(8):
        inst = fam.generate(seed)
        code = inst.rendered.code.splitlines()
        log, cmd = code[1:-1], code[-1].removeprefix("$ ")
        (tmp_path / "access.log").write_text("\n".join(log) + "\n", encoding="utf-8", newline="\n")
        assert fam.normalize(_bash(cmd, tmp_path)) == inst.key
        # Модели заблуждений тоже совпадают с реальным поведением изменённых команд.
        no_sort = cmd.replace("| sort | uniq", "| uniq", 1)
        assert fam.check(inst, _bash(no_sort, tmp_path)) == (False, "uniq_without_sort")
        ascending = _bash(cmd.replace("sort -rn", "sort -n"), tmp_path)
        assert fam.normalize(ascending) in inst.distractors


@pytest.mark.skipif(BASH is None, reason="bash не установлен")
def test_redirect_model_matches_bash(tmp_path):
    fam = BY_ID["sh.redirect.order"]
    for seed in range(8):
        inst = fam.generate(seed)
        work = tmp_path / f"s{seed}"
        work.mkdir()
        (work / "script.sh").write_text(inst.rendered.code + "\n", encoding="utf-8", newline="\n")
        _bash("bash script.sh >/dev/null 2>&1", work)
        fname = re.search(r"в файле (\S+) после", inst.rendered.prompt).group(1)
        content = (work / fname).read_text(encoding="utf-8")
        assert fam.normalize(content) == inst.key


@pytest.mark.skipif(GIT is None, reason="git не установлен")
def test_git_range_replayed_in_real_git(tmp_path):
    fam = BY_ID["git.range.dots"]
    empty_cfg = tmp_path / "gitconfig"
    empty_cfg.write_text("", encoding="utf-8")
    env = {
        **os.environ,
        # Изолированный одноразовый репозиторий: без пользовательских и системных настроек git.
        "GIT_CONFIG_GLOBAL": str(empty_cfg), "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "bank", "GIT_AUTHOR_EMAIL": "bank@example.com",
        "GIT_COMMITTER_NAME": "bank", "GIT_COMMITTER_EMAIL": "bank@example.com",
    }
    for seed in range(6):
        inst = fam.generate(seed)
        repo = tmp_path / f"r{seed}"
        repo.mkdir()

        def git(*args: str, repo: Path = repo) -> str:
            proc = subprocess.run([GIT, *args], cwd=repo, env=env, capture_output=True, text=True, encoding="utf-8")
            assert proc.returncode == 0, proc.stderr
            return proc.stdout

        git("init", "-q", "-b", "main")
        git("commit", "-q", "--allow-empty", "-m", "init")
        *script, log_cmd = [ln for ln in inst.rendered.code.splitlines() if ln.startswith("git ")]
        for line in script:
            args = shlex.split(line)[1:]
            extra = {"commit": ["-q", "--allow-empty"], "merge": ["-q", "--no-edit"], "switch": ["-q"]}[args[0]]
            git(args[0], *extra, *args[1:])
        got = set(git(*shlex.split(log_cmd)[1:], "--format=%s").splitlines())
        shown = next(o.text for o in inst.rendered.options if o.id == inst.key)
        assert got == (set() if shown == "(пустой вывод)" else set(shown.split(", ")))


# ---------------------------------------------------------------- детерминизм и скорость


def _fingerprints(seeds: range) -> dict[str, list[str]]:
    out = {}
    for fid, fam in FAMS:
        out[fid] = []
        for s in seeds:
            inst = fam.generate(s)
            r = inst.rendered
            out[fid].append(canonical([r.prompt, r.code, [o.text for o in r.options], list(r.lines), r.table,
                                       inst.key, inst.distractors]))
    return out


def test_generation_independent_of_hash_seed():
    script = (
        "import importlib, json, sys\n"
        "sys.path.insert(0, 'tests')\n"
        "for m in ('python_basics', 'python_advanced', 'sql', 'git_linux'):\n"
        "    importlib.import_module('app.testing.bank.families.backend_python.' + m)\n"
        "from test_bank_backend_python import _fingerprints\n"
        "sys.stdout.write(json.dumps(_fingerprints(range(3)), ensure_ascii=True))\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": "777", "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, "-c", script], cwd=BACKEND_DIR, env=env, capture_output=True,
                          text=True, encoding="utf-8", timeout=300)
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert json.loads(proc.stdout) == _fingerprints(range(3))


def test_generation_is_fast():
    start, n = time.perf_counter(), 0
    for _, fam in FAMS:
        for seed in range(500, 520):
            fam.generate(seed)
            n += 1
    assert (time.perf_counter() - start) / n < 0.05
