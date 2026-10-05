"""«Двойник в буфере»: анализ литералов, генерация X′, гейты, покрытие реестра (spec-antiassist §2.1).

Часть тестов — быстрые (разбор литералов, подбор значений, гейты на крошечных заданиях). Часть — по настоящему
реестру из 69 семейств: двойники строятся исполнением (run_python / run_js / run_sql), поэтому один прогон
по реестру кешируется на весь pytest-процесс (anticheat.gates.registry_runs).
"""

from __future__ import annotations

import hashlib
import random
import sys

import pytest

from app.testing.anticheat import literals as lit
from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.executor import (
    find_projection,
    project_rows,
    run_code,
    run_query,
    setup_sql,
    tables_from_rendered,
)
from app.testing.anticheat.gates import pool_answer_hashes, registry_coverage, registry_runs, stats_of
from app.testing.anticheat.twins import (
    compose_text,
    honest_collision_estimate,
    make_twin,
    reason_text,
    try_make_twin,
)
from app.testing.bank.base import Family, Instance, Rendered, canonical
from app.testing.bank.execution import run_js, run_python, run_sql
from app.testing.bank.registry import all_families
from tests.test_anticheat_support import (
    ORDERS_QUERY,
    ORDERS_ROWS,
    PRICES_CODE,
    SPEC_CODE,
    TinyFamily,
    code_instance,
    orders_rendered,
)

CFG = load_anticheat_config()
REGISTRY_N = 20  # сидов на семейство в тестах реестра; в отчёте и гейте — gate.n (50)

# ----------------------------------------------------------------------------- разбор литералов


def _analysis(code: str, language: str = "python") -> lit.Analysis:
    toks = lit.tokenize_code(code, language)
    assert toks is not None
    return lit.analyze_code(toks, CFG.twins)


def test_literals_spec_example_roles():
    an = _analysis(SPEC_CODE)
    by_text = {}
    for n in an.numbers:
        by_text.setdefault(n.tok.text, []).append(n)
    # 0 — служебное; «% 3» и «== 0» — модуль и порог, они не кандидаты
    assert set(by_text) == {"2", "3", "9", "4"}
    assert by_text["2"][0].role == "multiplier" and by_text["2"][0].tier == 1
    assert {by_text["9"][0].role, by_text["4"][0].role} == {"element"}
    # элемент 3 равен модулю из кода («связь»): трогаем в последнюю очередь
    assert by_text["3"][0].tier == CFG.twins.dup_tier
    assert 0.0 in an.pivots and 3.0 not in an.pivots and an.mods == [3]  # модуль — не порог: сохраняется остаток


def test_literals_duplicate_value_goes_last():
    an = _analysis("xs = [5, 8, 5, 21]\nprint(sum(xs))")
    tiers = {n.tok.text: n.tier for n in an.numbers}
    assert tiers["8"] == 1 and tiers["21"] == 1
    assert tiers["5"] == CFG.twins.dup_tier  # значение встречается дважды: ничья или связь, трогаем в последнюю очередь


def test_literals_exclude_index_range_slice_compare_timer():
    py = _analysis(
        "a = [10, 20, 30, 40]\n"
        "for i in range(2, 4):\n"
        "    print(a[2], a[1:3], i * 7)\n"
        "if len(a) > 5:\n"
        "    print(99)"
    )
    assert {n.tok.text for n in py.numbers} == {"10", "20", "30", "40", "7", "99"}
    js = _analysis(
        'const prices = [100, 250, 400];\n'
        "const total = prices.reduce((s, p) => s + p * 2, 0);\n"
        'if (total > 500) console.log("big", total);\n'
        "setTimeout(() => console.log(1), 50);",
        "javascript",
    )
    assert {n.tok.text for n in js.numbers} == {"100", "250", "400", "2"}


def test_literals_sorted_list_bounds_and_signs():
    an = _analysis("data = [16, 22, 28, 33]\nprint(len(data))")
    bounds = {n.tok.text: (n.spec.lo, n.spec.hi) for n in an.numbers}
    assert bounds["22"] == (16.0, 28.0)
    assert bounds["16"] == (None, 22.0) and bounds["33"] == (28.0, None)
    neg = _analysis("vals = (-46, 243, 989)\nif min(vals) <= 0:\n    print(1)")
    sign = {n.tok.text: n.spec.sign for n in neg.numbers}
    assert sign["46"] == -1 and sign["243"] == 1


def test_literals_threshold_variable_is_not_data():
    # limit сравнивается с переменной → это граница; count сравнивается с константой → это данные
    code = (
        "def check(amount, limit=850):\n"
        "    return amount > limit\n"
        "count = 28\n"
        "if count > 26:\n"
        "    print(check(900))"
    )
    texts = {n.tok.text for n in _analysis(code).numbers}
    assert "850" not in texts
    assert "28" in texts


def test_literals_strings_digits_and_labels():
    an = _analysis('track = "BY-49758-OMS-25"\nprint(track[7:11])\nlabel = "A"\nprint(label, "Привет 12")')
    kinds = {(s.role, s.text) for s in an.strings}
    assert ("string_digits", "49758") in kinds and ("string_digits", "25") in kinds
    assert ("string_label", "A") in kinds
    assert not any(s.text == "12" for s in an.strings)  # предложение на русском может тронуть переводчик
    js = _analysis('console.log("A");\nconsole.log("B");', "javascript")
    assert {s.text for s in js.strings} == {"A", "B"}
    assert all(not s.swap for s in js.strings)


@pytest.mark.parametrize(
    ("old", "new", "ok"),
    [("17", "11", False), ("38", "33", False), ("56", "55", False), ("98", "99", False), ("12", "13", True),
     ("98", "108", False), ("40", "25", True), ("l", "1", False)],
)
def test_glyph_gate(old, new, ok):
    assert lit.glyph_ok(old, new, CFG.twins.confusable_pairs) is ok


def test_propose_number_constraints():
    rng = random.Random("t")
    spec = lit.NumSpec("9", 9.0, True, 0, 1)
    out = lit.propose_number(
        spec, pivots=[3.0], mods=[3], existing_abs={3.0, 9.0, 4.0}, rng=rng, params=CFG.twins
    )
    assert out
    assert all(int(t) > 3 and int(t) not in (3, 4, 9) for t in out)  # порог не пересечён, нет «ничьих»
    assert all(abs(len(t) - 1) <= 1 for t in out)
    assert all(lit.glyph_ok("9", t, CFG.twins.confusable_pairs) for t in out)
    # «круглое» остаётся круглым: 1500 → кратное 100, а не 1517
    round_spec = lit.NumSpec("1500", 1500.0, True, 0, 1)
    rr = lit.propose_number(round_spec, pivots=[], mods=[], existing_abs={1500.0}, rng=random.Random("r"), params=CFG.twins)
    assert rr and all(int(t) % 100 == 0 for t in rr)
    # дробь: число знаков сохраняется
    dec = lit.NumSpec("0.25", 0.25, False, 2, 1)
    dd = lit.propose_number(dec, pivots=[], mods=[], existing_abs={0.25}, rng=random.Random("d"), params=CFG.twins)
    assert dd and all(len(t.split(".")[1]) == 2 for t in dd)


def test_propose_number_sorted_gap_keeps_order():
    spec = lit.NumSpec("46", 46.0, True, 0, 1, lo=41.0, hi=51.0)
    out = lit.propose_number(
        spec, pivots=[], mods=[], existing_abs={41.0, 46.0, 51.0}, rng=random.Random("g"), params=CFG.twins
    )
    assert out and all(41 < int(t) < 51 for t in out)


# ----------------------------------------------------------------------------- генерация двойника на крошечных заданиях


def _independent_key(fam: Family, twin_rendered: Rendered) -> str:
    """Пересчёт ключа двойника напрямую через bank.execution, минуя обёртку anticheat.executor."""
    if twin_rendered.language == "python":
        return canonical(fam.normalize(run_python(twin_rendered.code or "")))
    if twin_rendered.language == "javascript":
        return canonical(fam.normalize(run_js(twin_rendered.code or "")))
    raise AssertionError(twin_rendered.language)


def test_spec_example_twin_is_execution_verified():
    fam, inst = code_instance(SPEC_CODE)
    assert inst.key == "24"  # ключ X — результат исполнения, не введён руками
    res = try_make_twin(fam, inst)
    assert res.ok, res
    t = res.twin
    assert t is not None
    assert t.key == "24" and t.twin_key != "24"
    assert t.mutation_class == "data_literal" and t.changed_literal.block == "code"
    assert _independent_key(fam, t.twin_rendered) == t.twin_key
    # ровно один токен отличается; остальное совпадает посимвольно
    c = t.changed_literal
    assert t.shown_text[: c.offset] == t.clipboard_text[: c.offset]
    assert t.shown_text[c.offset + len(c.from_) :] == t.clipboard_text[c.offset + len(c.to) :]
    assert t.shown_text[c.offset : c.offset + len(c.from_)] == c.from_
    assert t.clipboard_text[c.offset : c.offset + len(c.to)] == c.to
    # координаты line/col указывают на тот же токен
    assert t.shown_text.split("\n")[c.line - 1][c.col : c.col + len(c.from_)] == c.from_
    assert t.twin_key_hash == hashlib.sha256(f"{fam.id}|{t.twin_key}".encode()).hexdigest()
    assert t.key_hash == inst.key_hash
    # условие (prompt) не меняется
    assert t.shown_text.startswith(inst.rendered.prompt) and t.clipboard_text.startswith(inst.rendered.prompt)
    assert c.offset > len(inst.rendered.prompt)
    # граница %3 остаётся: изменён элемент списка или множитель, но не модуль/порог
    assert c.from_ in {"2", "9", "4", "3"}


def test_twin_is_deterministic_and_seed_dependent():
    fam, inst = code_instance(PRICES_CODE)
    a, b = try_make_twin(fam, inst).twin, try_make_twin(fam, inst).twin
    assert a is not None and a == b
    literals = {
        (t.changed_literal.offset, t.changed_literal.to)
        for t in (make_twin(fam, inst, seed=s) for s in range(12))
        if t is not None
    }
    assert len({off for off, _ in literals}) >= 3  # выбор литерала зависит от seed экземпляра, а не фиксирован


def test_twin_key_never_equals_known_distractor():
    fam, inst = code_instance(SPEC_CODE)
    first = make_twin(fam, inst)
    assert first is not None
    # объявим ключ найденного двойника «известным заблуждением»: генератор обязан выбрать другой
    inst2 = Instance(inst.family_id, inst.seed, inst.rendered, inst.key, {first.twin_key: "known_error"})
    second = make_twin(fam, inst2)
    assert second is not None and second.twin_key != first.twin_key
    assert second.twin_key not in inst2.distractors


def test_avoid_literal_for_verification_variant():
    fam, inst = code_instance(PRICES_CODE)
    t1 = make_twin(fam, inst)
    assert t1 is not None
    res = try_make_twin(fam, inst, strict=False, avoid=[t1.changed_literal.ident], salt="v")
    assert res.twin is not None and res.twin.changed_literal.ident != t1.changed_literal.ident


def test_no_safe_twin_reasons():
    # выбор из вариантов: ключ двойника совпал бы с дистрактором
    fam, inst = code_instance(SPEC_CODE)
    choice = Instance(inst.family_id, 0, Rendered(prompt="x", code=SPEC_CODE, language="python", answer_mode="choice"), "a", {})
    assert try_make_twin(fam, choice).reason == "answer_mode_not_text"
    css = Instance(inst.family_id, 0, Rendered(prompt="x", code="a{}", language="css", answer_mode="text"), "1", {})
    assert try_make_twin(fam, css).reason == "language_not_executable"
    other = TinyFamily()
    other.format = "bug_line"
    assert try_make_twin(other, inst).reason == "format_unsupported"
    nolit = code_instance('print("hello")')
    assert try_make_twin(*nolit).reason == "no_candidates"
    bad = Instance(inst.family_id, 0, inst.rendered, "999", {})  # ключ не воспроизводится исполнением
    assert try_make_twin(fam, bad).reason == "key_not_reproducible"
    assert all(reason_text(r) != r for r in ("format_unsupported", "answer_mode_not_text", "no_candidates"))


def test_twin_does_not_cross_threshold():
    code = "prices = [150, 300, 420]\nbig = [p for p in prices if p > 100]\nprint(sum(big), len(big))"
    fam, inst = code_instance(code)
    for seed in range(10):
        t = make_twin(fam, inst, seed=seed)
        if t is None:
            continue
        # порог 100 — часть условия, он не меняется, и ни один элемент не опускается ниже него
        assert "> 100" in t.clipboard_text
        assert t.twin_key.split()[1] == "3"


def test_sql_twin_mutates_table_cell_and_rebuilds_setup_from_rendered_table():
    rendered = orders_rendered()
    rows, query = ORDERS_ROWS, ORDERS_QUERY
    fam = TinyFamily()
    fam.format = "sql_result"
    tables = tables_from_rendered(rendered.table)
    ok, got, _ = run_query(tables, query)
    assert ok
    key = fam.normalize(project_rows(got, "all_cells"))
    assert key == "Борис 2400"  # результат исполнения запроса
    inst = Instance(fam.id, 0, rendered, key, {})
    assert find_projection(fam, inst, got) == "all_cells"
    res = try_make_twin(fam, inst)
    assert res.ok, res
    t = res.twin
    assert t is not None and t.mutation_class == "table_value" and t.changed_literal.role == "cell"
    c = t.changed_literal
    assert c.block == "table:orders" and c.column == "amount"  # не id и не текстовые столбцы
    assert t.twin_key != key
    # независимый пересчёт: схема из показанной таблицы + изменённая ячейка, прямой вызов run_sql
    new_rows = t.twin_rendered.table["rows"]
    changed = [(i, j) for i, (a, b) in enumerate(zip(rows, new_rows, strict=True)) for j in range(4) if a[j] != b[j]]
    assert changed == [(c.row, 3)]
    raw = run_sql(setup_sql(tables_from_rendered(t.twin_rendered.table)), query)
    assert fam.normalize(project_rows(raw, "all_cells")) == t.twin_key
    # clipboard отличается от показанного только в ячейке; запрос и остальные ячейки те же
    assert t.shown_text.split("\n\n")[0] == t.clipboard_text.split("\n\n")[0]
    assert t.shown_text.endswith(query) and t.clipboard_text.endswith(query)
    assert t.clipboard_text[c.offset : c.offset + len(c.to)] == c.to
    assert str(rows[c.row][3]) == c.from_


def test_compose_text_matches_table_cells():
    rendered = Rendered(
        prompt="Таблица.", code="SELECT 1;", language="sql", answer_mode="text",
        table={"name": "t", "columns": ["a", "b"], "rows": [[1, None], [22, 3.5]]},
    )
    comp = compose_text(rendered)
    for (name, i, j), (s, e) in comp.cells.items():
        assert name == "t"
        expect = [[1, None], [22, 3.5]][i][j]
        assert comp.text[s:e] == ("NULL" if expect is None else str(expect))


def test_family_twin_hook_delegates_to_generic_mutators():
    fam = all_families()["py.defaults.mutable"]
    inst = fam.generate(3)
    via_hook = fam.twin(inst)
    assert via_hook is not None and via_hook == make_twin(fam, inst)
    assert fam.twin(inst, seed=99) is not None
    # хук необязателен и обратно совместим: семейство без переопределения продолжает работать как раньше
    assert fam.check(inst, inst.key) == (True, None)


def test_client_clip_has_no_key_and_carries_map():
    fam, inst = code_instance(SPEC_CODE)
    t = make_twin(fam, inst)
    assert t is not None
    clip = t.client_clip(render_id="r-1")
    assert clip["render_id"] == "r-1" and clip["text"] == t.clipboard_text
    assert not any("key" in k for k in clip)
    assert clip["map"][0]["shown"][0] == clip["map"][0]["clip"][0] == t.changed_literal.offset
    assert "twin_key" not in t.to_dict(include_keys=False)
    assert "twin_key" in t.to_dict()
    assert "key" not in t.mutation_json() and "twin_key" not in t.mutation_json()


def test_twin_exposes_spec_names_and_dict_like_literal():
    fam, inst = code_instance(PRICES_CODE)
    t = make_twin(fam, inst)
    assert t is not None
    assert t.clip_text == t.clipboard_text and t.clip_map == t.client_clip()["map"]
    assert len(t.twin_id) == 12 and t.twin_id == make_twin(fam, inst).twin_id
    c = t.changed_literal
    assert (c["line"], c["col"], c["from"], c["to"]) == (c.line, c.col, c.from_, c.to)  # changed_literal{line, col, from, to}
    assert c.get("role") == c.role and set(c.to_dict()) >= {"line", "col", "from", "to"}


def test_honest_collision_estimate_model():
    base = honest_collision_estimate("data_literal", 1, 0, 1)
    assert base == CFG.eps.default["data_literal"]  # консервативные 0,02, не измерено
    assert honest_collision_estimate("data_literal", 3, 0, 1) > base  # ярус «прочая роль» дороже
    assert honest_collision_estimate("data_literal", 1, 5, 6) > base  # много отклонённых кандидатов
    assert honest_collision_estimate("data_literal", 4, 100, 101) <= CFG.eps.cap


# ----------------------------------------------------------------------------- реестр (69 семейств)


@pytest.fixture(scope="module")
def runs():
    return registry_runs(REGISTRY_N)


# Семейства, для которых безопасного двойника сейчас нет, и причина. Список честный: если семейство начнёт давать
# двойники, тест ниже упадёт, и строку нужно удалить (так покрытие не «залипает» молча).
KNOWN_NO_TWIN = {
    "py.mro.super": "no_candidates",  # в коде только слова-идентификаторы, литерала данных нет (гейт 7: переводимые строки)
    "react.batched_updates": "executor_failed",  # JSX: Node без React его не исполнит
    "react.controlled_input": "executor_failed",
    "react.stale_closure": "executor_failed",
}


@pytest.mark.parametrize("fid", sorted(all_families()))
def test_each_family_twin_property(fid, runs):
    """Property-тест по каждому семейству (spec §7): доля двойников, гейты, причина отказа."""
    run = next(r for r in runs if r.family.id == fid)
    st = stats_of(run)
    if not run.eligible:
        assert not st.eligible and st.ineligible_reason and reason_text(st.ineligible_reason) != st.ineligible_reason
        return
    assert st.n == REGISTRY_N and st.key_equals_twin == 0 and st.pool_collision <= CFG.gate.max_honest_collision
    if fid in KNOWN_NO_TWIN:
        assert st.share < CFG.gate.min_twin_share and KNOWN_NO_TWIN[fid] in st.reasons, st.reasons
    else:
        assert st.share >= CFG.gate.min_twin_share, f"{fid}: {st.valid}/{st.n} {st.reasons}"
        # у каждого валидного двойника есть запись о том, как он получен (метрика качества каталога)
        for res in run.results:
            if res.twin is not None:
                assert res.twin.executor in ("run_python", "run_js", "run_sql", "exec_py")
                assert res.attempts >= 1 and res.reason == "ok"


def test_registry_report_covers_all_families(runs):
    cov = registry_coverage(REGISTRY_N)
    assert cov.total == len(all_families()) >= 69
    assert len(cov.applicable) >= 55  # predict_output + sql_result
    assert {s.family_id for s in cov.stats} == set(all_families())
    # у каждого непригодного по форме семейства есть код причины на русском
    for s in cov.stats:
        if s.format in ("predict_output", "sql_result") and not s.eligible:
            assert s.ineligible_reason and reason_text(s.ineligible_reason) != s.ineligible_reason


def test_registry_coverage_threshold(runs):
    cov = registry_coverage(REGISTRY_N)
    assert len(cov.eligible) >= 30
    lines = [f"{s.family_id}: {s.valid}/{s.n} {dict(s.reasons)}" for s in cov.eligible if s.share < CFG.gate.min_twin_share]
    assert cov.family_share >= CFG.gate.min_family_share, (
        f"двойники на ≥{CFG.gate.min_twin_share:.0%} сидов дают {cov.family_share:.1%} пригодных семейств "
        f"(нужно ≥{CFG.gate.min_family_share:.0%}); ниже порога: {lines}"
    )
    assert cov.instance_share >= 0.85


def test_registry_twin_invariants(runs):
    checked = 0
    for run in runs:
        for inst, res in zip(run.instances, run.results, strict=False):
            t = res.twin
            if t is None:
                continue
            checked += 1
            fid = run.family.id
            c = t.changed_literal
            key = canonical(inst.key)
            assert t.key == key and t.twin_key != key, fid  # twin_key != key всегда
            assert t.twin_key and t.twin_key not in inst.distractors, fid  # не известное заблуждение
            # ровно один токен; всё остальное посимвольно
            assert t.shown_text[: c.offset] == t.clipboard_text[: c.offset], fid
            assert t.shown_text[c.offset + len(c.from_) :] == t.clipboard_text[c.offset + len(c.to) :], fid
            assert t.shown_text[c.offset : c.offset + len(c.from_)] == c.from_, fid
            assert t.clipboard_text[c.offset : c.offset + len(c.to)] == c.to, fid
            assert c.from_ != c.to and abs(len(c.to) - len(c.from_)) <= CFG.twins.max_length_delta, fid
            assert lit.glyph_ok(c.from_, c.to, CFG.twins.confusable_pairs), fid
            assert c.offset > len(inst.rendered.prompt), fid  # не литерал из текста вопроса
            assert t.shown_text.split("\n")[c.line - 1][c.col : c.col + len(c.from_)] == c.from_, fid
            assert t.mutation_class in CFG.twins.classes_mvp, fid
            assert 0 < t.honest_collision_estimate <= CFG.eps.cap, fid
            assert t.twin_key_hash != t.key_hash and t.key_hash == inst.key_hash, fid
    assert checked >= 500


def test_registry_twin_keys_are_recomputed_by_execution(runs):
    """Выборка двойников пересчитывается напрямую через bank.execution: ключ получен исполнением, а не руками."""
    sample = []
    for run in runs:
        pairs = [(i, r.twin) for i, r in zip(run.instances, run.results, strict=False) if r.twin is not None]
        sample += [(run.family, i, t) for i, t in pairs[:2]]
    assert len(sample) >= 40
    for fam, inst, t in sample:
        rendered = t.twin_rendered
        if rendered.language == "python":
            if "asyncio" in (rendered.code or "") and sys.platform == "win32":
                continue  # run_python под Windows не запускает asyncio (урезанное окружение), см. executor.py
            raw = run_python(rendered.code or "")
        elif rendered.language == "javascript":
            raw = run_js(rendered.code or "")
        else:
            tables = tables_from_rendered(rendered.table)
            rows = run_sql(setup_sql(tables), rendered.code or "")
            base_tables = tables_from_rendered(inst.rendered.table)
            proj = find_projection(fam, inst, run_sql(setup_sql(base_tables), inst.rendered.code or ""))
            assert proj is not None
            raw = project_rows(rows, proj)
        assert canonical(fam.normalize(raw)) == t.twin_key, fam.id


def test_registry_twins_are_deterministic(runs):
    for run in runs:
        pools = pool_answer_hashes(run.family, run.instances) if run.eligible else []
        found = [(k, i, r.twin) for k, (i, r) in enumerate(zip(run.instances, run.results, strict=False)) if r.twin]
        if not found:
            continue
        k, inst, t = found[0]
        again = try_make_twin(run.family, inst, pool_key_hashes=pools[k]).twin
        assert again == t, run.family.id


def test_registry_pool_gate_removes_collisions_with_neighbour_instances(runs):
    """Гейт 4 (spec §2.1): ключ двойника не совпадает с ключом или заблуждением другого экземпляра пула."""
    cov = registry_coverage(REGISTRY_N)
    for s in cov.eligible:
        assert s.pool_collision <= CFG.gate.max_honest_collision, s.family_id
        assert s.natural_collision < 0.2, s.family_id  # плотность ключей: до отбрасывания коллизий
    assert sum(s.key_equals_twin for s in cov.eligible) == 0
    for run in runs:
        if not run.eligible:
            continue
        pools = pool_answer_hashes(run.family, run.instances)
        for k, res in enumerate(run.results):
            if res.twin is not None:
                assert res.twin.twin_key_hash not in pools[k], run.family.id


def test_run_code_wrapper_is_cached_and_reports_errors():
    a = run_code("python", "print(2 + 3)")
    assert a.ok and a.output == "5" and a.via in ("run_python", "exec_py")
    assert run_code("python", "print(2 + 3)") is a  # кеш по тексту
    bad = run_code("python", "raise ValueError('x')")
    assert not bad.ok and "ValueError" in bad.error
    assert not run_code("ruby", "puts 1").ok
