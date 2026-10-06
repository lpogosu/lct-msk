"""Тесты пакета семейств shared (http_api, algo): ключи, дистракторы, нормализация, контрольные примеры.

Независимые оракулы: ключи Python-семейств сверяются с исполнением в подпроцессе (execution.run_python),
сценарные HTTP-семейства — с отдельной реализацией правил прямо в тесте.
"""

from __future__ import annotations

import json
import math
import os
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from app.testing.bank.base import Family, Instance, canonical
from app.testing.bank.execution import run_js, run_python
from app.testing.bank.families.shared import (
    algo_complexity,
    algo_two_pointers,
    http_cache,
    http_cors,
    http_idempotency,
    http_jwt,
)
from app.testing.bank.families.shared._common import exec_capture
from app.testing.bank.gates import check_family
from app.testing.bank.registry import all_families

BACKEND = Path(__file__).resolve().parents[1]
PKG = "app.testing.bank.families.shared"
SHARED = {fid: fam for fid, fam in sorted(all_families().items()) if type(fam).__module__.startswith(PKG)}
IDS = sorted(SHARED)
SEEDS = (0, 1, 7, 42, 199)


def _answers_from_canonical(inst: Instance, wrong: str):
    """Восстановить ответ кандидата из канонической строки дистрактора."""
    if inst.rendered.answer_mode in ("line_choice", "order"):
        return json.loads(wrong)
    return wrong


# ---------------------------------------------------------------- состав пакета


def test_package_composition():
    assert len(SHARED) == 16
    by_module = {m: [f for f in SHARED.values() if f.module == m] for m in ("http_api", "algo")}
    assert {m: len(v) for m, v in by_module.items()} == {"http_api": 8, "algo": 8}
    for fid, fam in SHARED.items():
        assert fam.specs[:2] == ("backend_python", "frontend_js") and set(fam.specs) <= {"backend_python", "frontend_js", "qa", "devops"}, fid
        prefix = "http." if fam.module == "http_api" else "algo."
        assert fid.startswith(prefix), fid
        assert 60 <= fam.time_limit_s <= 180, fid
    for fams in by_module.values():
        grades = [f.target_grade for f in fams]
        # смесь ≈ 15/30/30/25 на 8 семействах: 1 / 2 / 3 / 2
        assert (grades.count("intern"), grades.count("junior"), grades.count("middle"), grades.count("senior")) == (
            1, 2, 3, 2,
        )
        anchors = {f.target_grade for f in fams if f.is_anchor}
        assert anchors == {"intern", "junior", "middle", "senior"}  # по якорю на каждый порог
    mcq = [f for f in SHARED.values() if f.format == "mcq_concept"]
    assert len(mcq) / len(SHARED) <= 0.30
    for fid, fam in SHARED.items():
        for enemy in fam.enemies:
            assert enemy in SHARED and fid in SHARED[enemy].enemies, (fid, enemy)


def test_catalog_lists_every_family():
    catalog = (BACKEND / "app/testing/bank/families/shared/CATALOG.md").read_text(encoding="utf-8")
    for fid in IDS:
        assert f"`{fid}`" in catalog, fid


# ---------------------------------------------------------------- гейты и ключи


@pytest.mark.parametrize("fid", IDS)
def test_gates(fid):
    assert check_family(SHARED[fid]) == []


@pytest.mark.parametrize("fid", IDS)
def test_key_and_wrong_answers(fid):
    fam: Family = SHARED[fid]
    for seed in SEEDS:
        inst = fam.generate(seed)
        r = inst.rendered
        assert fam.check(inst, inst.key) == (True, None)
        assert inst.explanation and re.search(r"[А-Яа-яЁё]", inst.explanation)
        assert inst.key_hash == fam.generate(seed).key_hash
        # каждый дистрактор распознаётся как неверный ответ со своим тегом заблуждения
        for wrong, tag in inst.distractors.items():
            ok, got_tag = fam.check(inst, _answers_from_canonical(inst, wrong))
            assert not ok and got_tag == tag, (seed, wrong)
        # произвольный неверный ответ
        if r.answer_mode == "text":
            assert fam.check(inst, "0")[0] is False
            assert fam.check(inst, "")[0] is False
        elif r.answer_mode == "choice":
            other = next(o.id for o in r.options if o.id != inst.key)
            assert fam.check(inst, other)[0] is False
            assert fam.check(inst, inst.key.upper()) == (True, None)
            assert len(r.options) >= 4 and math.isclose(fam.c, 1 / len(r.options), abs_tol=1e-3)
        elif r.answer_mode == "line_choice":
            line = inst.key["line"]
            assert fam.check(inst, {"line": line % len(r.lines) + 1, "fix": inst.key["fix"]})[0] is False
            assert fam.check(inst, {"line": str(line), "fix": inst.key["fix"].upper()}) == (True, None)
            assert 1 <= line <= len(r.lines) and len(r.options) == 4
            # исправления указывают на строку → угадывание = 1 из k исправлений (spec-testing §3.1)
            assert math.isclose(fam.c, 1 / len(r.options), abs_tol=1e-3)
        elif r.answer_mode == "order":
            assert fam.check(inst, list(reversed(inst.key)))[0] is False
            idx = [list(r.lines).index(x) for x in inst.key]  # ответ индексами строк (с 0)
            assert fam.check(inst, idx) == (True, None)
        if r.answer_mode in ("text", "order"):
            assert fam.c == 0.0


@pytest.mark.parametrize("fid", IDS)
def test_generation_is_fast(fid):
    fam = SHARED[fid]
    t0 = time.perf_counter()
    for seed in range(30):
        fam.generate(1000 + seed)
    assert (time.perf_counter() - t0) / 30 < 0.05


def test_deterministic_across_processes():
    """Пул экземпляров генерируется в другом процессе: результат не должен зависеть от PYTHONHASHSEED."""
    snippet = (
        "import json\n"
        "from app.testing.bank.registry import all_families\n"
        "from app.testing.bank.base import canonical\n"
        f"fams = {{k: v for k, v in all_families().items() if type(v).__module__.startswith({PKG!r})}}\n"
        "print(json.dumps({k: [canonical(f.generate(s).key) + '|' + canonical(f.generate(s).rendered.to_public())"
        " for s in range(4)] for k, f in fams.items()}))\n"
    )
    env = {**os.environ, "PYTHONHASHSEED": "4242", "PYTHONPATH": str(BACKEND), "PYTHONIOENCODING": "utf-8"}
    out = subprocess.run([sys.executable, "-c", snippet], cwd=BACKEND, env=env, capture_output=True, text=True,
                         encoding="utf-8", timeout=120, check=True).stdout
    other = json.loads(out)
    for fid, fam in SHARED.items():
        mine = [canonical(fam.generate(s).key) + "|" + canonical(fam.generate(s).rendered.to_public()) for s in range(4)]
        assert other[fid] == mine, fid


# ---------------------------------------------------------------- ключ = исполнение кода


_EXEC_FAMILIES = [
    "algo.stack_queue.trace", "algo.binary_search.trace", "algo.hash.linear_probing", "algo.graph.dfs_stack",
    "algo.dp.knapsack_1d", "http.ratelimit.token_bucket",
]


@pytest.mark.parametrize("fid", _EXEC_FAMILIES)
def test_key_equals_subprocess_execution(fid):
    fam = SHARED[fid]
    for seed in (3, 77):
        inst = fam.generate(seed)
        out = run_python(inst.rendered.code)
        assert out == exec_capture(inst.rendered.code)
        assert fam.normalize(out) == inst.key


def test_exec_capture_is_thread_safe():
    """Пул могут генерировать потоками внутри API: перехват вывода не должен трогать sys.stdout процесса."""
    from concurrent.futures import ThreadPoolExecutor

    stdout_before = sys.stdout
    assert exec_capture('print(1, 2, sep="-", end="!")\nprint("x")') == "1-2!x"
    assert sys.stdout is stdout_before
    jobs = [(fid, seed) for fid in _EXEC_FAMILIES + ["algo.sort.stability"] for seed in range(300, 340)]
    sequential = [canonical(SHARED[fid].generate(seed).key) for fid, seed in jobs]
    with ThreadPoolExecutor(max_workers=8) as pool:
        parallel = list(pool.map(lambda job: canonical(SHARED[job[0]].generate(job[1]).key), jobs))
    assert parallel == sequential


def test_sort_stability_key_equals_subprocess():
    fam = SHARED["algo.sort.stability"]
    for seed in (5, 99):
        inst = fam.generate(seed)
        printed = run_python(inst.rendered.code).splitlines()
        assert [" ".join(x.split()) for x in printed] == inst.key
        assert sorted(inst.key) == sorted(inst.rendered.lines)


def test_run_python_reads_cyrillic_output_regardless_of_console_codepage():
    """`-I` игнорирует PYTHONIOENCODING: без флага `-X utf8` в Windows дочерний Python пишет cp1251, а мы читаем UTF-8."""
    assert run_python('print("привет, Ж")') == "привет, Ж"
    assert run_python("print(chr(0x416))") == "Ж"


def test_run_js_reads_cyrillic_output():
    if shutil.which("node") is None:
        pytest.skip("node не найден в PATH")
    assert run_js('console.log("привет, Ж")') == "привет, Ж"


def test_sort_stability_shown_order_is_never_the_answer():
    # строки показываются в исходном порядке — «ничего не переставлять» не должно быть верным ответом
    fam = SHARED["algo.sort.stability"]
    for seed in range(400):
        inst = fam.generate(seed)
        assert fam.check(inst, list(inst.rendered.lines))[0] is False, seed


# ---------------------------------------------------------------- нормализация ответов


def test_normalization_is_forgiving():
    fam = SHARED["http.pagination.offset_math"]
    inst = fam.generate(11)
    assert fam.check(inst, inst.key.replace(" ", ", "))[0]
    assert fam.check(inst, "Ответ: " + inst.key.replace(" ", "; "))[0]
    for seed in range(40):  # нумерация пунктов из формулировки и запись кортежем
        inst = fam.generate(seed)
        a, b, c = inst.key.split()
        assert fam.check(inst, f"1) {a} 2) {b} 3) {c}")[0], seed
        assert fam.check(inst, f"1){a}, 2){b}, 3){c}")[0], seed
        assert fam.check(inst, f"({a}, {b}, {c})")[0], seed

    fam = SHARED["http.status.scenario"]
    inst = fam.generate(11)
    assert fam.check(inst, inst.key.replace(" ", ","))[0]
    assert fam.check(inst, [int(x) for x in inst.key.split()])[0]

    fam = SHARED["http.cache.revalidation"]
    inst = fam.generate(12)
    pairs = inst.key.split()
    assert fam.check(inst, " ".join(reversed(pairs)))[0]  # порядок пар не важен
    assert fam.check(inst, " ".join(p.replace(":", " ") for p in pairs))[0]  # без двоеточий
    assert fam.check(inst, ", ".join(p.replace(":", " — ") for p in pairs))[0]
    assert fam.check(inst, " ".join(pairs[1:]))[0]  # 0:200 названа в подсказке — её можно не писать
    assert not fam.check(inst, " ".join(pairs[:-1]))[0]

    fam = SHARED["http.idempotency.retry_cart"]
    inst = fam.generate(12)
    shuffled = list(reversed(inst.key.split()))
    cyr = " ".join(shuffled).lower().replace("a", "а").replace("e", "е").replace("c", "с")  # кириллические двойники
    assert fam.check(inst, cyr)[0]
    assert fam.check(inst, ", ".join(s.replace("=", " × ") for s in shuffled))[0]

    fam = SHARED["algo.graph.dfs_stack"]
    inst = fam.generate(12)
    assert fam.check(inst, inst.key.lower().replace(" ", ", "))[0]
    assert fam.check(inst, inst.key.replace(" ", ""))[0]
    assert fam.check(inst, "Ответ: " + inst.key)[0]  # русские слова не превращаются в «вершины» O, E
    assert fam.check(inst, "Порядок обхода — " + " → ".join(inst.key.split()))[0]

    fam = SHARED["algo.dp.knapsack_1d"]
    inst = fam.generate(12)
    assert fam.check(inst, "[" + inst.key.replace(" ", ", ") + "]")[0]


# ---------------------------------------------------------------- независимые оракулы HTTP


def test_pagination_oracle():
    fam = SHARED["http.pagination.offset_math"]
    for seed in range(60):
        inst = fam.generate(seed)
        total = int(re.search(r'"total": (\d+)', inst.rendered.code).group(1))
        limit = int(re.search(r'"limit": (\d+)', inst.rendered.code).group(1))
        page = int(re.search(r"получить страницу (\d+)", inst.rendered.prompt).group(1))
        pages = math.ceil(total / limit)
        assert inst.key == f"{(page - 1) * limit} {pages} {total - (pages - 1) * limit}"


def _status_oracle(code: str, prompt: str) -> list[int]:
    """Независимая реализация правил сервиса из формулировки (RFC 9110)."""
    admin = re.search(r"tok_(\w+) — администратор", prompt).group(1)
    table = {}
    for m in re.finditer(r'^(\d+)\s+(\w+)\s+"([^"]+)"\s+("[0-9a-f]{4}")$', code, re.M):
        table[m.group(1)] = (m.group(2), m.group(3), m.group(4))
    codes = []
    for block in re.split(r"\n\s*\n", code.split("Запросы:\n", 1)[1]):
        lines = [x.strip() for x in block.strip().splitlines()]
        method, path = lines[0].split(") ", 1)[1].split(" ", 1)
        hdr = {k: v for k, v in (x.split(": ", 1) for x in lines[1:] if ": " in x and not x.startswith("{"))}
        body = next((x for x in lines[1:] if x.startswith("{") or x.startswith("title=")), "")
        user = hdr.get("Authorization", "").replace("Bearer tok_", "")
        parts = path.strip("/").split("/")
        if not user:
            codes.append(401)
        elif len(parts) == 3:  # коллекция
            if method != "POST":
                codes.append(405)
            elif hdr.get("Content-Type") != "application/json":
                codes.append(415)
            else:
                title = json.loads(body)["title"]
                codes.append(409 if any(t == title for _, t, _ in table.values()) else 201)
        elif parts[3] not in table:
            codes.append(404)
        elif method not in ("GET", "PUT", "DELETE"):
            codes.append(405)
        else:
            author, _, etag = table[parts[3]]
            if user not in (author, admin):
                codes.append(403)
            elif "If-None-Match" in hdr:
                codes.append(304 if hdr["If-None-Match"] == etag else 200)
            elif "If-Match" in hdr and hdr["If-Match"] != etag:
                codes.append(412)
            else:
                codes.append({"GET": 200, "PUT": 200, "DELETE": 204}[method])
    return codes


def test_status_oracle():
    fam = SHARED["http.status.scenario"]
    for seed in range(120):
        inst = fam.generate(seed)
        assert " ".join(map(str, _status_oracle(inst.rendered.code, inst.rendered.prompt))) == inst.key, seed


def test_cache_control_example():
    # контрольный пример: max-age 180, изменение на сервере в t = 707
    times = [0, 60, 235, 445, 590, 855]
    assert http_cache._fmt(http_cache._simulate(times, 180, 707, "correct")) == "0:200 235:304 445:304 855:200"
    assert http_cache._fmt(http_cache._simulate(times, 180, 707, "no_refresh_after_304")) == (
        "0:200 235:304 445:304 590:304 855:200"
    )
    assert http_cache._fmt(http_cache._simulate(times, 180, 707, "no_conditional")) == "0:200 235:200 445:200 855:200"


def test_cache_oracle_from_rendering():
    fam = SHARED["http.cache.revalidation"]
    for seed in range(60):
        inst = fam.generate(seed)
        code = inst.rendered.code
        max_age = int(re.search(r"max-age=(\d+)", code).group(1))
        change = int(re.search(r"t = (\d+) с — данные", code).group(1))
        times = [int(x) for x in code.rsplit(":", 1)[1].split(",")]
        out, fresh_from, version = ["0:200"], 0, 0
        for t in times[1:]:
            if t - fresh_from < max_age:
                continue
            now_version = int(t >= change)
            out.append(f"{t}:{304 if now_version == version else 200}")
            version, fresh_from = now_version, t
        assert " ".join(out) == inst.key, seed


def test_cors_preflight_control_table():
    # контрольная таблица по Fetch Standard: CORS-safelisted методы/заголовки/Content-Type
    expected = {
        "get_plain": False, "get_auth": True, "get_custom": True, "get_accept": False, "get_credentials": False,
        "get_cache_control": True, "post_json": True, "post_form": False, "post_text": False, "post_urlenc": False,
        "post_urlenc_cred": False, "delete": True, "put_json": True, "patch_json": True, "head": False,
    }
    assert {r.kind: http_cors._preflight(r) for r in http_cors._POOL} == expected
    by_kind = {r.kind: r for r in http_cors._POOL}
    assert http_cors._preflight(by_kind["post_json"], "json_simple") is False
    assert http_cors._preflight(by_kind["get_auth"], "auth_simple") is False
    assert http_cors._preflight(by_kind["get_credentials"], "credentials_preflight") is True
    assert http_cors._preflight(by_kind["post_text"], "post_always") is True


def test_jwt_control_example():
    # вход 10:00:00, TTL 600, leeway 30, refresh в 10:05:00, logout в 10:07:00
    ctx = http_jwt._Timeline(t1=36300, t2=36420, exp1=36600, exp2=36900, leeway=30)
    reqs = [
        (36400, "old_access_valid", "access₁"),  # A: старый access после refresh → ок
        (36500, "new_access_after_logout", "access₂"),  # B: access после logout → ок (stateless)
        (36620, "old_access_leeway", "access₁"),  # C: exp + 20 с при leeway 30 → ок
        (36640, "old_access_expired", "access₁"),  # D: exp + 40 с → 401
        (36700, "old_refresh_reuse", "refresh₁"),  # E: повтор использованного refresh → отказ
    ]
    assert http_jwt._answer(reqs, "correct", ctx) == "A, B, C"
    assert http_jwt._answer(reqs, "no_leeway", ctx) == "A, B"
    assert http_jwt._answer(reqs, "logout_revokes_access", ctx) == "A"
    assert http_jwt._answer(reqs, "rotation_ignored", ctx) == "A, B, C, E"


def test_idempotency_control_example():
    Op = http_idempotency._Op
    initial = {"TEA-204": 2}
    ops = [
        Op("post", "PEN-512", 2, True),  # POST без ключа ×2 → +4
        Op("put", "TEA-204", 5, True),  # PUT ×2 → 5
        Op("post_key", "CUP-118", 1, True, "k1"),  # POST с ключом ×2 → +1
        Op("delete", "CUP-118", 0, True),  # DELETE ×2 → удалена
        Op("post_key", "CUP-118", 3, False, "k1"),  # тот же ключ — сервер вернёт сохранённый ответ
    ]
    fmt, sim = http_idempotency._fmt, http_idempotency._simulate
    assert fmt(sim(initial, ops, "correct")) == "PEN-512=4 TEA-204=5"
    assert fmt(sim(initial, ops, "retries_ignored")) == "PEN-512=2 TEA-204=5"
    assert fmt(sim(initial, ops, "put_as_add")) == "PEN-512=4 TEA-204=12"


def test_ratelimit_control_example():
    fam = SHARED["http.ratelimit.token_bucket"]
    inst = fam.generate(3)
    # контрольный пример с ручным расчётом: capacity 3, rate 0.5, t = 0..6 подряд
    code = re.sub(r"TokenBucket\(capacity=\d+, rate=[\d.]+, now=\d+\)", "TokenBucket(capacity=3, rate=0.5, now=0)",
                  inst.rendered.code)
    code = re.sub(r"for t in \[[^\]]*\]:", "for t in [0, 1, 2, 3, 4, 5, 6]:", code)
    # токены перед решением: 3, 2.5, 2, 1.5, 1, 0.5, 1 → отказ только в t = 5
    assert run_python(code) == "5"


# ---------------------------------------------------------------- два указателя: корректность конструкции


def test_two_pointers_mutants_and_fixes_verified():
    rng = random.Random(2026)
    for prob in algo_two_pointers._PROBLEMS:
        for names in prob.names:
            good = [ln.format(**names) for ln in prob.lines]
            tests = [prob.gen(rng) for _ in range(400)]
            ref = algo_two_pointers._compile(prob, good)
            expected = [algo_two_pointers._outcome(ref, prob, t) for t in tests]
            for mut in prob.mutations:
                mut.why.format(**names)  # пояснение ссылается только на известные имена переменных
                indent = good[mut.line][: len(good[mut.line]) - len(good[mut.line].lstrip())]
                bad = list(good)
                bad[mut.line] = indent + mut.bad.format(**names)
                assert algo_two_pointers._failing(prob, bad, tests, expected), (prob.fn, mut.tag)


def test_two_pointers_instances_fix_restores_reference():
    fam = SHARED["algo.two_pointers.bug"]
    for seed in range(25):
        inst = fam.generate(seed)
        lines = list(inst.rendered.lines)
        prob = next(p for p in algo_two_pointers._PROBLEMS if lines[0].startswith(f"def {p.fn}("))
        line = inst.key["line"] - 1
        fixes = {o.id: o.text for o in inst.rendered.options}
        indent = lines[line][: len(lines[line]) - len(lines[line].lstrip())]
        fixed = list(lines)
        fixed[line] = indent + fixes[inst.key["fix"]]
        # верное исправление восстанавливает эталонный код задачи дословно
        assert any(fixed == [ln.format(**n) for ln in prob.lines] for n in prob.names), seed
        assert sum(a != b for a, b in zip(fixed, lines, strict=True)) == 1
        rng = random.Random(seed)
        tests = [prob.gen(rng) for _ in range(2000)]
        ref = algo_two_pointers._compile(prob, fixed)
        expected = [algo_two_pointers._outcome(ref, prob, t) for t in tests]
        assert algo_two_pointers._failing(prob, lines, tests, expected), seed  # показанный код действительно с ошибкой
        for oid, text in fixes.items():
            if oid != inst.key["fix"]:
                cand = list(lines)
                cand[line] = indent + text
                assert algo_two_pointers._failing(prob, cand, tests, expected), (seed, text)
        # показанный в формулировке упавший тест воспроизводится
        assert "ожидалось" in inst.rendered.prompt
        # разбор говорит именами переменных из показанного кода, а не абстрактными L/R/k
        assert not re.search(r"\{[A-Z]\}|\[[LRKIJ][\],\s]|\b[LR] − ", inst.explanation), seed


# ---------------------------------------------------------------- асимптотика: эмпирическая проверка каркасов


def _instrumented(code: str) -> str:
    # встроенные функции на C трассировкой не видны — подменяем их Python-версиями той же сложности
    return (code.replace("sum(a)", "_sum(a)").replace("sorted(a)", "_sorted(a)")
            .replace("x not in seen", "not _contains(seen, x)"))


def _helpers() -> dict:
    def _sum(a):
        s = 0
        for x in a:
            s += x
        return s

    def _contains(seq, x):
        for y in seq:
            if y == x:
                return True
        return False

    def _sorted(a):
        if len(a) <= 1:
            return list(a)
        mid = len(a) // 2
        left, right = _sorted(a[:mid]), _sorted(a[mid:])
        out, i, j = [], 0, 0
        while i < len(left) and j < len(right):
            if left[i] <= right[j]:
                out.append(left[i])
                i += 1
            else:
                out.append(right[j])
                j += 1
        return out + left[i:] + right[j:]

    return {"_sum": _sum, "_sorted": _sorted, "_contains": _contains}


def _count_lines(fn, *args) -> int:
    count = 0

    def tracer(frame, event, arg):
        nonlocal count
        if event == "line":
            count += 1
        return tracer

    sys.settrace(tracer)
    try:
        fn(*args)
    finally:
        sys.settrace(None)
    return count


def _expected_ratio(label: str, n1: int, n2: int) -> float:
    if label == "O(2ⁿ)":
        return 2.0 ** (n2 - n1)
    p, lg = algo_complexity._POWERS[label]
    return (n2 / n1) ** p * (math.log(n2) / math.log(n1)) ** lg


@pytest.mark.parametrize("sk", algo_complexity.SKELETONS, ids=[s.name for s in algo_complexity.SKELETONS])
def test_complexity_skeleton_empirical(sk):
    pre = next(p for p in sk.pre if algo_complexity._PRE[p][1] in ("O(1)",)) if sk.pre != ("",) else ""
    code, label, _ = algo_complexity.build(sk, pre, "solve", "i", "j")
    assert label == sk.label
    ns = _helpers()
    exec(compile(_instrumented(code), "<sk>", "exec"), ns)  # noqa: S102 — свой код
    fn = ns["solve"]
    rng = random.Random(1)
    heavy = algo_complexity.LABELS.index(label) >= algo_complexity.LABELS.index("O(n√n)")
    n1, n2 = (64, 256) if heavy else (256, 1024)
    if sk.arg == "small_int":
        n1, n2 = 8, 10

    def run(n: int) -> int:
        if sk.arg in ("int", "small_int"):
            return _count_lines(fn, n)
        data = rng.sample(range(-4 * n, 4 * n), n)  # различные элементы — худший случай для list_membership
        if sk.name == "amortized_pointer":
            return _count_lines(fn, sorted(data), 8)
        if sk.arg == "range":
            return _count_lines(fn, data, 0, len(data))
        return _count_lines(fn, data)

    measured = run(n2) / run(n1)
    candidates = [lb for lb in algo_complexity.LABELS if lb != "O(2ⁿ)" or sk.arg == "small_int"]
    best = min(candidates, key=lambda lb: abs(math.log(measured) - math.log(_expected_ratio(lb, n1, n2))))
    assert best == label, (sk.name, measured)


def test_complexity_algebra():
    assert algo_complexity.seq("O(n)", "O(n log n)") == "O(n log n)"
    assert algo_complexity.seq("O(n²)", "O(n)") == "O(n²)"
    assert algo_complexity.mul("O(n)", "O(n log n)") == "O(n² log n)"
    assert algo_complexity.mul("O(n)", "O(n)") == "O(n²)"
    sk = next(s for s in algo_complexity.SKELETONS if s.name == "amortized_pointer")
    _, label, wrong = algo_complexity.build(sk, "sorted", "f", "i", "j")
    assert label == "O(n log n)" and ("O(n)", "sort_ignored") in wrong
