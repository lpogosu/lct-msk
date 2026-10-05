"""Тесты пакета qa (модуль qa_testing): ключи, дистракторы, нормализация, контрольные примеры, двойники.

Независимые оракулы:
- мини-раннер pytest (qa/_minipytest.py) сверяется с НАСТОЯЩИМ pytest в подпроцессе: статусы, id параметров, порядок
  print фикстур, а нестабильность шаблонов «флаки» — прогоном настоящего pytest в разных порядках;
- трассировщик покрытия ветвей (qa/_branch.py) — с независимым подсчётом через sys.settrace;
- проверки HTTP-контракта — с исполнением в подпроцессе (execution.run_python);
- SQL-ключи — повторный расчёт по ПОКАЗАННОЙ таблице; граничные значения, классы эквивалентности и серьёзность —
  арифметика по тексту условия.
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from app.testing.anticheat.executor import setup_sql as setup_from_tables
from app.testing.anticheat.executor import tables_from_rendered
from app.testing.bank.base import GRADES, Family, canonical
from app.testing.bank.execution import run_python, run_sql
from app.testing.bank.families._scenario import (
    Scenario,
    norm_branches,
    norm_datetimes,
    norm_fraction,
    norm_ints,
    norm_latin,
    norm_pytest_summary,
)
from app.testing.bank.families.qa import _branch, _minipytest, coverage, design, flaky
from app.testing.bank.gates import check_family
from app.testing.bank.registry import all_families

BACKEND = Path(__file__).resolve().parents[1]
PKG = "app.testing.bank.families.qa"
QA = {fid: fam for fid, fam in sorted(all_families().items()) if type(fam).__module__.startswith(PKG)}
IDS = sorted(QA)
SEEDS = (0, 1, 7, 42, 199)
TEXT_IDS = [i for i in IDS if QA[i].generate(0).rendered.answer_mode == "text"]


def _wrong_answer(inst, wrong: str):
    """Ответ кандидата по канонической строке дистрактора (для line_choice — словарь)."""
    return json.loads(wrong) if inst.rendered.answer_mode in ("line_choice", "order") else wrong


# ---------------------------------------------------------------- состав пакета


def test_package_composition():
    assert len(QA) == 11
    grades = [f.target_grade for f in QA.values()]
    # ≈ стажёр 15% / junior 30% / middle 30% / senior 25%: на 11 семействах 2 / 3 / 3 / 3 (≈ 18 / 27 / 27 / 27%)
    assert [grades.count(g) for g in GRADES] == [2, 3, 3, 3]
    assert {f.target_grade for f in QA.values() if f.is_anchor} == set(GRADES)  # по якорю на каждый порог
    for fid, fam in QA.items():
        assert fid.startswith("qa.") and fam.module == "qa_testing" and fam.specs == ("qa",), fid
        assert 60 <= fam.time_limit_s <= 180, fid
        assert -1 <= fam.position <= 1 and fam.target_grade in GRADES, fid
        assert fam.measures and len(fam.misconceptions) >= 3, fid
        for enemy in fam.enemies:  # враги существуют; внутри пакета симметричны
            assert enemy in all_families(), (fid, enemy)
            if enemy in QA:
                assert fid in QA[enemy].enemies, (fid, enemy)
    # доля семейств с угадыванием (c > 0) ≤ 30%: выбор из 4 либо клик по строке
    guessing = [fid for fid, f in QA.items() if f.c > 0]
    assert len(guessing) / len(QA) <= 0.30, guessing
    assert sum(f.format == "mcq_concept" for f in QA.values()) / len(QA) <= 0.30
    assert {f.format for f in QA.values()} == {"predict_output", "sql_result", "bug_line", "mcq_concept"}


@pytest.mark.parametrize("fid", IDS)
def test_guessing_parameter_matches_answer_mode(fid):
    fam, r = QA[fid], QA[fid].generate(0).rendered
    if r.answer_mode == "text":
        assert fam.c == 0
    else:
        assert fam.c == pytest.approx(1 / len(r.options), abs=1e-3)


def test_catalog_lists_every_family():
    text = (BACKEND / "app/testing/bank/families/qa/CATALOG.md").read_text(encoding="utf-8")
    for fid in IDS:
        assert f"`{fid}`" in text, fid


def test_shared_families_open_to_qa():
    reg = all_families()
    expected = {
        "sql.order.limit_offset", "sql.join.left_rows", "sql.aggregate.null", "sql.group.having", "sql.distinct.null",
        "sql.join.on_vs_where", "sql.subquery.not_in_null", "git.workflow.order", "sh.pipeline.uniq", "sh.redirect.order",
        "http.pagination.offset_math", "http.status.scenario", "http.rest.route_review",
        "http.idempotency.retry_cart", "http.jwt.lifecycle", "http.ratelimit.token_bucket",
    }
    assert {fid for fid, f in reg.items() if "qa" in f.specs and fid not in QA} == expected
    for fid in expected:
        assert reg[fid].specs[0] == "backend_python", fid  # исходная специализация не потеряна


# ---------------------------------------------------------------- гейты, ключи, дистракторы


@pytest.mark.parametrize("fid", IDS)
def test_family_passes_ci_gates(fid):
    assert check_family(QA[fid]) == []


@pytest.mark.parametrize("fid", IDS)
def test_key_and_distractors_pass_check(fid):
    fam = QA[fid]
    for seed in SEEDS:
        inst = fam.generate(seed)
        assert fam.check(inst, inst.key) == (True, None)
        assert inst.distractors, (fid, seed)
        for wrong, tag in inst.distractors.items():
            assert wrong != canonical(inst.key)
            assert fam.check(inst, _wrong_answer(inst, wrong)) == (False, tag), (fid, seed, wrong)
        assert inst.explanation and "Верный ответ" in inst.explanation
        assert inst.rendered.prompt and not set(inst.rendered.prompt) & set("{}$")


@pytest.mark.parametrize("fid", IDS)
def test_generation_is_deterministic_and_varied(fid):
    fam = QA[fid]
    a, b = fam.generate(5), fam.generate(5)
    assert a.rendered == b.rendered and a.key == b.key and a.distractors == b.distractors
    assert fam.generate(5).rendered != fam.generate(6).rendered
    # каждый экземпляр — не меньше двух разных дистракторов (иначе задание слишком «угадываемо»)
    assert all(len(fam.generate(s).distractors) >= 2 for s in range(30))


def test_generation_is_independent_of_hash_seed():
    """Ключи и условия не зависят от PYTHONHASHSEED (порядок множеств): иначе пул нельзя воспроизвести по seed."""
    code = """
import hashlib, json
from app.testing.bank.registry import all_families
out = {}
for fid, f in sorted(all_families().items()):
    if fid.startswith('qa.'):
        for s in (0, 3, 11):
            i = f.generate(s)
            blob = i.rendered.prompt + str(i.rendered.code) + str(i.key) + json.dumps(sorted(i.distractors))
            out[f'{fid}#{s}'] = hashlib.sha256(blob.encode()).hexdigest()[:12]
print(json.dumps(out))
"""
    results = []
    for hash_seed in ("0", "12345"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, env=env, capture_output=True, text=True,
                              encoding="utf-8", timeout=120)
        assert proc.returncode == 0, proc.stderr[-500:]
        results.append(json.loads(proc.stdout))
    assert results[0] == results[1] and len(results[0]) == 3 * len(QA)


def test_generation_is_fast_enough_for_pool_building():
    t0 = time.perf_counter()
    for fam in QA.values():
        for s in range(20):
            fam.generate(s)
    assert time.perf_counter() - t0 < 20


# ---------------------------------------------------------------- нормализаторы


def test_normalizers_accept_reasonable_answer_forms():
    assert norm_ints("17, 18 и 65; 66") == norm_ints([17, 18, 65, 66]) == "17 18 65 66"
    assert norm_ints("[ -3 4 ]") == "-3 4"
    assert norm_pytest_summary("3 passed, 2 failed in 0.02s") == "2 failed, 3 passed"
    assert norm_pytest_summary("1 failed, 2 passed, 1 skipped, 1 xfailed, 1 xpassed") == (
        "1 failed, 2 passed, 1 skipped, 1 xfailed, 1 xpassed"
    )
    assert norm_pytest_summary("2 errors") == "2 error"
    assert norm_datetimes("02.03.2026 9:30; 2026-03-02 10:00") == "2026-03-02 09:30 ; 2026-03-02 10:00"
    assert norm_fraction("5 из 8") == norm_fraction("5 / 8") == "5/8"
    assert norm_branches("12F, 15 T") == norm_branches("12-ложь 15:истина") == "12F 15T"
    assert norm_branches("15Т 12F") == "12F 15T"  # кириллическая «Т»
    assert norm_branches("нет") == norm_branches("") == "нет"
    assert norm_branches("9 10") == "9 10"  # ответ без исходов остаётся как есть (это ответ про строки, а не ветви)
    assert norm_latin("test_а, test_b") == "test_a test_b"  # кириллическая «а» → латиница


def test_branches_family_accepts_spaced_and_comma_separated_answers():
    fam = QA["qa.coverage.branch"]
    inst = fam.generate(0)
    spaced = re.sub(r"(\d+)([TF])", r"\1 \2,", inst.key)
    assert fam.check(inst, spaced) == (True, None)


# ---------------------------------------------------------------- qa.bva.points: контрольные примеры


@pytest.mark.parametrize(
    ("phrase", "expected"),
    [
        ("от 18 до 65 включительно", "17 18 65 66"),
        ("больше 18 и меньше 65", "18 19 64 65"),
        ("не меньше 18 и меньше 65", "17 18 64 65"),
        ("больше 18 и не больше 65", "18 19 65 66"),
    ],
)
def test_bva_control_examples(phrase, expected):
    fam = QA["qa.bva.points"]
    code = f"Поле «Возраст клиента» — целое число.\nДопустимые значения: {phrase}."
    sc = Scenario(code)
    assert fam.normalize(fam.solve(sc, code)) == expected
    mis = fam.solve(sc, code, "strict_bound_as_valid")
    # «больше 18» понято как «не меньше 18» — заблуждение меняет ответ только при строгой границе
    assert (mis is None) == phrase.startswith("от") and mis != expected


def test_bva_generated_ranges_are_consistent():
    fam = QA["qa.bva.points"]
    for seed in range(60):
        nums = [int(x) for x in fam.generate(seed).key.split()]
        assert nums == sorted(nums) and len(nums) == 4
        assert nums[1] - nums[0] == 1 and nums[3] - nums[2] == 1 and nums[2] - nums[1] >= 2


# ---------------------------------------------------------------- qa.equiv.min_tests и серьёзность: оракулы по тексту


def _min_tests_from_text(code: str) -> int:
    valid, invalid = [], []
    for line in code.splitlines()[1:]:
        if "целое от" in line:
            valid.append(1)
            invalid.append(2)
        elif "одно из значений:" in line:
            valid.append(len(line.split("одно из значений:")[1].split("(")[0].split(",")))
            invalid.append(1)
        elif "необязательное" in line:
            valid.append(2)
            invalid.append(1)
        else:
            valid.append(1)
            invalid.append(1)
    return max(valid) + sum(invalid)


def test_equivalence_classes_control_example():
    code = (
        "Форма «Оформление заявки»\n1. Возраст покупателя — целое от 18 до 65.\n"
        "2. Способ оплаты — одно из значений: карта, СБП, наличные (для каждого своя логика).\n"
        "3. Промокод — необязательное: пусто или ровно 8 символов.\n4. Адрес — обязательное, непустая строка."
    )
    assert _min_tests_from_text(code) == 3 + (2 + 1 + 1 + 1)  # допустимых: max(1,3,2,1); недопустимых: 2+1+1+1


def test_equivalence_key_matches_independent_count_from_shown_spec():
    fam = QA["qa.equiv.min_tests"]
    for seed in range(40):
        inst = fam.generate(seed)
        correct = next(o.text for o in inst.rendered.options if o.id == inst.key)
        assert int(correct) == _min_tests_from_text(inst.rendered.code), seed


def test_severity_priority_key_matches_reverse_lookup():
    fam = QA["qa.bug.severity_priority"]
    for seed in range(40):
        inst = fam.generate(seed)
        impact = re.search(r"Дефект: (.*)\.\nКонтекст", inst.rendered.code).group(1)
        context = re.search(r"Контекст: (.*)\.$", inst.rendered.code).group(1)
        sev = next(k for k, v in design._IMPACT.items() if impact in v)
        pri = next(k for k, v in design._CONTEXT.items() if context in v)
        assert next(o.text for o in inst.rendered.options if o.id == inst.key) == f"{sev} / {pri}"


# ---------------------------------------------------------------- мини-раннер pytest против настоящего pytest

_OUTCOME_LINE = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\b(?: \[\d+\])? ?(\S*)", re.M)


def _real_pytest(source: str, tmp: Path, name: str, *args: str, summary: bool = False) -> subprocess.CompletedProcess:
    f = tmp / f"test_{name}.py"
    f.write_text("import pytest\n" + source + "\n", encoding="utf-8")
    cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "-s", "--tb=no", "--no-header"]
    if summary:
        cmd.append("-rA")
    return subprocess.run([*cmd, *args, f.name], cwd=tmp, capture_output=True, text=True, encoding="utf-8", timeout=60)


def _outcomes(stdout: str, name: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for kind, node in _OUTCOME_LINE.findall(stdout):
        if node.startswith(f"test_{name}.py::"):
            out.setdefault(kind, []).append(node.split("::", 1)[1])
    return out


def _events(stdout: str) -> list[str]:
    """print из фикстур и тестов без значков прогресса pytest (в сценариях, где все тесты проходят, это только «.»)."""
    toks: list[str] = []
    for line in stdout.splitlines():
        line = line.lstrip(".")
        if line and not re.match(r"^\d+ \w+", line):
            toks += line.split()
    return toks


def _parallel(fn, items, workers: int = 6):
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, items))


def test_miniptest_matches_real_pytest_on_failed_names(tmp_path):
    fam = QA["qa.pytest.failed"]

    def run(seed):
        inst = fam.generate(seed)
        proc = _real_pytest(inst.rendered.code, tmp_path, f"f{seed}", summary=True)
        return inst, _outcomes(proc.stdout, f"f{seed}")

    for inst, real in _parallel(run, range(8)):
        assert fam.normalize(" ".join(real.get("FAILED", []))) == inst.key, inst.rendered.code


def test_miniptest_matches_real_pytest_on_parametrize(tmp_path):
    fam = QA["qa.pytest.parametrize"]

    def run(seed):
        inst = fam.generate(seed)
        proc = _real_pytest(inst.rendered.code, tmp_path, f"p{seed}", summary=True)
        return inst, _outcomes(proc.stdout, f"p{seed}"), proc.stdout

    kinds = set()
    for inst, real, stdout in _parallel(run, range(14)):
        skipped = int(m.group(1)) if (m := re.search(r"(\d+) skipped", stdout.splitlines()[-1])) else 0
        total = sum(len(v) for v in real.values()) + skipped
        assert fam.normalize(" ".join([str(total), *real.get("FAILED", [])])) == inst.key, inst.rendered.code
        kinds.add("skip" in inst.rendered.code)
    assert kinds == {True, False}  # проверены оба вида: стопка декораторов и строки с skip/xfail


def test_miniptest_matches_real_pytest_on_fixture_order(tmp_path):
    fam = QA["qa.fixture.order"]

    def run(seed):
        inst = fam.generate(seed)
        proc = _real_pytest(inst.rendered.code, tmp_path, f"x{seed}")
        return inst, _events(proc.stdout)

    for inst, events in _parallel(run, range(10)):
        assert fam.normalize(" ".join(events)) == inst.key, inst.rendered.code


def test_miniptest_feature_matrix_matches_real_pytest(tmp_path):
    """Параметризация, skip/xfail, autouse, module-фикстуры, raises — вся поверхность раннера."""
    src = '''
@pytest.fixture(scope="module")
def db():
    print("db:up")
    yield 1
    print("db:down")

@pytest.fixture
def user(db):
    print("user:up")
    yield 2
    print("user:down")

@pytest.fixture(autouse=True)
def tick():
    print("tick")
    yield
    print("tock")

@pytest.mark.parametrize("x", [0, 1])
@pytest.mark.parametrize("y", [2, 3])
def test_a(x, y, user):
    print("a", x, y)
    assert x + y != 4

def test_b(db):
    with pytest.raises(ValueError, match="bad"):
        raise ValueError("so bad")

@pytest.mark.skip(reason="no")
def test_c():
    assert False

@pytest.mark.xfail
def test_d():
    assert 1 == 2

@pytest.mark.xfail
def test_e():
    assert 1 == 1

@pytest.mark.parametrize("v", [pytest.param(1, id="one"), pytest.param(2, marks=pytest.mark.skip), 3])
def test_f(v):
    assert v != 3

def test_g(user, tick):
    raise ZeroDivisionError("zz")
'''
    mine = _minipytest.run_tests(src)
    proc = _real_pytest(src, tmp_path, "matrix", summary=True)
    real = {k: sorted(v) for k, v in _outcomes(proc.stdout, "matrix").items()}
    assert real == {
        "PASSED": sorted(mine.names("passed")),
        "FAILED": sorted(mine.names("failed")),
        "XFAIL": sorted(mine.names("xfailed")),
        "XPASS": sorted(mine.names("xpassed")),
    }
    assert mine.counts()["skipped"] == 2 and "2 skipped" in proc.stdout
    assert mine.out[0] == "db:up" and mine.out[-1] == "db:down"
    assert mine.summary() == proc.stdout.strip().splitlines()[-1].split(" in ")[0]


def test_miniptest_quirks_change_the_log():
    src = '''
@pytest.fixture(scope="module")
def db():
    print("db:up")
    yield
    print("db:down")

@pytest.fixture
def user(db):
    print("user:up")
    yield
    print("user:down")

@pytest.fixture(autouse=True)
def tick():
    print("tick")
    yield
    print("tock")

def test_a(user, db):
    print("a")

def test_b():
    print("b")
'''
    base = _minipytest.run_tests(src).out
    assert base == ["db:up", "tick", "user:up", "a", "user:down", "tock", "tick", "b", "tock", "db:down"]
    assert _minipytest.run_tests(src, quirks=frozenset({"no_scope_sort"})).out[:3] == ["tick", "db:up", "user:up"]
    assert _minipytest.run_tests(src, quirks=frozenset({"fifo_teardown"})).out[4:6] == ["tock", "user:down"]
    early = _minipytest.run_tests(src, quirks=frozenset({"module_teardown_after_last_use"})).out
    assert early.index("db:down") < early.index("b")  # module-фикстура закрыта сразу после последнего «своего» теста
    assert _minipytest.run_tests(src, quirks=frozenset({"no_module_cache"})).out.count("db:up") == 1  # test_b без db
    two = src.replace("def test_b():", "def test_b(db):")
    assert _minipytest.run_tests(two, quirks=frozenset({"no_module_cache"})).out.count("db:up") == 2


def test_miniptest_parametrize_ids_and_order():
    src = '''
@pytest.mark.parametrize("x", [0, 1])
@pytest.mark.parametrize("y", [2, 3])
def test_p(x, y):
    pass
'''
    res = _minipytest.run_tests(src)
    assert [i.nodeid for i in res.items] == ["test_p[2-0]", "test_p[2-1]", "test_p[3-0]", "test_p[3-1]"]
    assert res.summary() == "4 passed"


def test_miniptest_stop_on_first_failure_and_soft_asserts():
    src = "def test_a():\n    assert 1\n\ndef test_b():\n    assert 0\n    assert 0\n\ndef test_c():\n    assert 0\n"
    assert _minipytest.run_tests(src).summary() == "2 failed, 1 passed"
    assert _minipytest.run_tests(src, stop_on_first_failure=True).summary() == "1 failed, 1 passed"
    soft = _minipytest.run_tests(src, soft_asserts=True)
    assert sum(i.soft_failures for i in soft.items) == 3 and soft.summary() == "2 failed, 1 passed"
    assert _minipytest.run_tests(src, keep_asserts="first").names("failed") == ["test_b", "test_c"]


# ---------------------------------------------------------------- qa.flaky.diagnose против настоящего pytest


def _template_of(code: str) -> str:
    if "datetime.now()" in code:
        return "clock"
    if 'scope="module"' in code:
        return "module_fixture"
    if "bucket=[]" in code:
        return "mutable_default"
    return "config_alias"


def _apply_option(inst, option_id: str, line_no: int) -> str:
    text = next(o.text for o in inst.rendered.options if o.id == option_id)
    return flaky._apply(list(inst.rendered.lines), line_no, text)


@pytest.mark.parametrize("template", ["mutable_default", "config_alias", "module_fixture"])
def test_flaky_templates_verified_by_real_pytest(template, tmp_path):
    fam = QA["qa.flaky.diagnose"]
    inst = next(i for i in (fam.generate(s) for s in range(80)) if _template_of(i.rendered.code) == template)
    buggy = inst.rendered.code
    names = re.findall(r"^def (test_\w+)", buggy, re.M)
    fixed = _apply_option(inst, inst.key["fix"], inst.key["line"])

    def run(arg):
        code, order, tag = arg
        f = tmp_path / f"test_{tag}.py"
        f.write_text("import pytest\n" + code + "\n", encoding="utf-8")
        nodes = [f"test_{tag}.py::{n}" for n in order]
        cmd = [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", "-q", "--tb=no", "--no-header", *nodes]
        return subprocess.run(cmd, cwd=tmp_path, capture_output=True, text=True, timeout=60).returncode

    rev, rot = list(reversed(names)), [*names[1:], names[0]]
    rc = _parallel(run, [(buggy, names, "b0"), (buggy, rev, "b1"), (fixed, names, "f0"), (fixed, rev, "f1"),
                         (fixed, rot, "f2")])
    assert rc[0] == 0, "в порядке файла тесты проходят"
    assert rc[1] != 0, "в обратном порядке — падают: это и есть «флаки»"
    assert rc[2] == rc[3] == rc[4] == 0, "верное исправление проходит при любом порядке"


def test_flaky_clock_template_is_verified_by_fake_clock():
    fam = QA["qa.flaky.diagnose"]
    inst = next(i for i in (fam.generate(s) for s in range(80)) if _template_of(i.rendered.code) == "clock")
    code = inst.rendered.code
    assert flaky._passes_default(code, "clock") and not flaky._all_pass(code, "clock")
    assert flaky._all_pass(_apply_option(inst, inst.key["fix"], inst.key["line"]), "clock")


def test_flaky_every_instance_has_exactly_one_stable_fix():
    fam = QA["qa.flaky.diagnose"]
    templates: Counter[str] = Counter()
    for seed in range(60):
        inst = fam.generate(seed)
        name = _template_of(inst.rendered.code)
        templates[name] += 1
        lines = list(inst.rendered.lines)
        options = {o.id: o.text for o in inst.rendered.options}
        pairs = {(inst.key["line"], inst.key["fix"])}
        for k in inst.distractors:
            d = json.loads(k)
            pairs.add((d["line"], d["fix"]))
        stable = [(ln, fx) for ln, fx in pairs if flaky._all_pass(flaky._apply(lines, ln, options[fx]), name)]
        assert stable == [(inst.key["line"], inst.key["fix"])], (seed, name)
    assert set(templates) == {"clock", "module_fixture", "mutable_default", "config_alias"}


# ---------------------------------------------------------------- qa.coverage.branch: независимый подсчёт через sys.settrace


def _oracle_uncovered(plain: str) -> list[tuple[int, bool]]:
    tree = ast.parse(plain)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def after(stmt):
        parent = parents[stmt]
        for field in ("body", "orelse"):
            seq = getattr(parent, field, None)
            if isinstance(seq, list) and stmt in seq:
                i = seq.index(stmt)
                if i + 1 < len(seq):
                    return seq[i + 1].lineno
                break
        return "RET" if isinstance(parent, ast.FunctionDef) else after(parent)

    targets = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            targets[node.lineno] = (node.body[0].lineno, node.orelse[0].lineno if node.orelse else after(node))
    events: list[tuple[int, object]] = []

    def tracer(frame, event, arg):
        if frame.f_code.co_filename != "<oracle>":
            return None
        if event == "line":
            events.append((id(frame), frame.f_lineno))
        elif event == "return":
            events.append((id(frame), "RET"))
        return tracer

    code = compile(plain, "<oracle>", "exec")
    sys.settrace(tracer)
    try:
        exec(code, {"__name__": "__oracle__"})  # noqa: S102 — наш собственный сгенерированный код
    finally:
        sys.settrace(None)
    seen: set[tuple[int, bool]] = set()
    last: dict[int, object] = {}
    for fid, ev in events:
        prev = last.get(fid)
        if prev in targets:
            true_t, false_t = targets[prev]
            if ev == true_t:
                seen.add((prev, True))
            elif ev == false_t:
                seen.add((prev, False))
        last[fid] = ev
    return sorted(
        ((line, o) for line in targets for o in (True, False) if (line, o) not in seen), key=lambda t: (t[0], not t[1])
    )


def test_branch_tracer_matches_settrace_oracle():
    fam = QA["qa.coverage.branch"]
    for seed in range(40):
        inst = fam.generate(seed)
        plain = coverage._strip_gutter(inst.rendered.code)
        expected = _oracle_uncovered(plain)
        assert fam.normalize(coverage.BranchCoverage._fmt(expected)) == inst.key, (seed, plain)
        assert len(expected) >= 2


def test_branch_coverage_control_example():
    code = (
        "def f(x, y):\n    r = 0\n    if x > 5:\n        r += 1\n    elif x > 2:\n        r += 2\n"
        "    if y and x:\n        r += 3\n    return r\n\n\nf(9, 0)\nf(1, 1)\n"
    )
    tr = _branch.trace_code(code)
    # строка 3 (x > 5): T из f(9, 0), F из f(1, 1); строка 5 (x > 2): F из f(1, 1), T не покрыта;
    # строка 7 (y and x): F из f(9, 0) (y = 0), T из f(1, 1) — обе покрыты
    assert tr.uncovered_branches() == [(5, True)]
    assert tr.totals() == (5, 6)
    assert _oracle_uncovered(code) == [(5, True)]
    assert (5, True) in tr.uncovered_atoms()
    assert tr.unexecuted_statements() == [6]  # покрытие строк видит r += 2; ветвь F у x > 2 — это уже не строка


def test_coverage_gutter_roundtrip():
    code = "def f(x):\n    if x:\n        return 1\n\n\nf(2)"
    assert coverage._strip_gutter(coverage._with_gutter(code)) == code


# ---------------------------------------------------------------- qa.api.contract_checks и qa.sql.data_checks: оракулы


def test_api_contract_key_matches_subprocess_execution():
    fam = QA["qa.api.contract_checks"]
    for seed in range(12):
        inst = fam.generate(seed)
        assert fam.normalize(run_python(inst.rendered.code)) == inst.key
        assert inst.rendered.language == "python" and "import" not in inst.rendered.code


def test_mock_key_matches_subprocess_with_real_imports():
    """Листинг без импортов + настоящий импорт unittest.mock в чистом подпроцессе дают тот же вывод, что и ключ."""
    fam = QA["qa.mock.calls"]
    kinds = set()
    for seed in range(12):
        inst = fam.generate(seed)
        program = "from unittest.mock import Mock, call, patch\n" + inst.rendered.code
        # обычное окружение, а не урезанное run_python: unittest.mock импортирует asyncio, которому в Windows нужен SYSTEMROOT
        proc = subprocess.run([sys.executable, "-c", program], capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert proc.returncode == 0, proc.stderr[-300:]
        assert fam.normalize(proc.stdout.strip()) == inst.key
        kinds.add("side_effect" in inst.rendered.code)
    assert kinds == {True, False}  # оба шаблона: side_effect и стопка patch


def test_sql_checks_key_is_recomputed_from_the_shown_tables():
    fam = QA["qa.sql.data_checks"]
    shown = set()
    for seed in range(60):
        inst = fam.generate(seed)
        rows = run_sql(setup_from_tables(tables_from_rendered(inst.rendered.table)), inst.rendered.code)
        assert fam.normalize(" ".join(str(v) for r in rows for v in r)) == inst.key
        assert inst.rendered.language == "sql"
        shown.add(tuple(t.name for t in tables_from_rendered(inst.rendered.table)))
    assert {("tasks",), ("stock",), ("tickets",), ("limits", "payments"), ("orders", "items")} <= shown


# ---------------------------------------------------------------- двойник в буфере


def _one_token_diff(shown: str, clip: str) -> tuple[str, str]:
    i = 0
    while i < min(len(shown), len(clip)) and shown[i] == clip[i]:
        i += 1
    j = 0
    while j < min(len(shown), len(clip)) - i and shown[len(shown) - 1 - j] == clip[len(clip) - 1 - j]:
        j += 1
    return shown[i : len(shown) - j], clip[i : len(clip) - j]


@pytest.mark.parametrize("fid", TEXT_IDS)
def test_twin_share_gates_and_determinism(fid):
    fam: Family = QA[fid]
    made, n = 0, (12 if fid == "qa.api.contract_checks" else 30)  # у API-семейства каждый двойник — запуск Python
    for seed in range(n):
        inst = fam.generate(seed)
        twin = fam.twin(inst)
        if twin is None:
            continue
        made += 1
        assert twin.twin_key != canonical(inst.key) and twin.twin_key_hash != inst.key_hash
        assert twin.twin_key not in inst.distractors
        old, new = _one_token_diff(twin.shown_text, twin.clipboard_text)
        assert old in twin.changed_literal.from_ and new in twin.changed_literal.to  # ровно один токен
        assert all(twin.gates.values()) and twin.variant_code == inst.variant_code
        again = fam.twin(inst)
        assert again is not None and again.twin_key == twin.twin_key and again.clipboard_text == twin.clipboard_text
        assert set(twin.client_clip("rid")) == {"render_id", "variant_code", "text", "map", "version"}  # ключа нет
    assert made / n >= 0.9, (fid, made, n)


@pytest.mark.parametrize("fid", [i for i in IDS if i not in TEXT_IDS])
def test_twin_absent_for_choice_formats(fid):
    fam = QA[fid]
    assert fam.twin(fam.generate(0)) is None


def test_scenario_twin_keys_match_real_pytest(tmp_path):
    """Ключ двойника для тестов pytest подтверждён настоящим pytest на копии из буфера."""
    fam = QA["qa.pytest.failed"]

    def run(seed):
        twin = fam.twin(fam.generate(seed))
        proc = _real_pytest(twin.twin_rendered.code, tmp_path, f"t{seed}", summary=True)
        return twin, _outcomes(proc.stdout, f"t{seed}")

    for twin, real in _parallel(run, range(6)):
        assert fam.normalize(" ".join(real.get("FAILED", []))) == twin.twin_key


def test_twin_keys_match_independent_oracles():
    cov = QA["qa.coverage.branch"]  # покрытие ветвей: оракул settrace на тексте из буфера
    for seed in range(15):
        twin = cov.twin(cov.generate(seed))
        if twin is not None:
            plain = coverage._strip_gutter(twin.twin_rendered.code)
            assert cov.normalize(coverage.BranchCoverage._fmt(_oracle_uncovered(plain))) == twin.twin_key
    api = QA["qa.api.contract_checks"]  # проверки API: подпроцесс исполняет скопированный код
    for seed in range(5):
        twin = api.twin(api.generate(seed))
        assert twin is not None and api.normalize(run_python(twin.twin_rendered.code)) == twin.twin_key
    sql = QA["qa.sql.data_checks"]  # SQL: sqlite исполняет запрос на таблицах из буфера
    for seed in range(10):
        twin = sql.twin(sql.generate(seed))
        assert twin is not None
        rows = run_sql(setup_from_tables(tables_from_rendered(twin.twin_rendered.table)), twin.twin_rendered.code)
        assert sql.normalize(" ".join(str(v) for r in rows for v in r)) == twin.twin_key
    bva = QA["qa.bva.points"]  # граничные значения: арифметика по тексту из буфера
    for seed in range(10):
        twin = bva.twin(bva.generate(seed))
        assert twin is not None
        lo_v, hi_v = design._bva_valid_range(twin.twin_rendered.code)
        assert twin.twin_key == f"{lo_v - 1} {lo_v} {hi_v} {hi_v + 1}"


def test_twin_changes_only_the_data_block_not_the_prompt():
    for fid in TEXT_IDS:
        fam = QA[fid]
        inst = fam.generate(3)
        twin = fam.twin(inst)
        if twin is not None:
            assert twin.twin_rendered.prompt == inst.rendered.prompt
            assert twin.changed_literal.block == "code" or twin.changed_literal.block.startswith("table:")
