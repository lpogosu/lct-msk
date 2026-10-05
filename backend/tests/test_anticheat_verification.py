"""«Подтверждение за 5 минут»: комплект по закончившейся сессии, ответы вычислены исполнением, итог и метка."""

from __future__ import annotations

import json
import re

import pytest

from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.executor import find_projection, project_rows, setup_sql, tables_from_rendered
from app.testing.anticheat.twins import compose_text
from app.testing.anticheat.verification import (
    SessionItem,
    build_kit,
    covered_modules,
    evaluate_verification,
    skill_is_covered,
)
from app.testing.bank.base import canonical
from app.testing.bank.execution import run_js, run_python, run_sql
from app.testing.bank.registry import all_families

CFG = load_anticheat_config()
REG = all_families()


def _items() -> list[SessionItem]:
    return [
        # ответ совпал с ключом двойника: «след двойника», верным для X не считается
        SessionItem("py.dict.basics", 5, "4 7 37 -1 14", False, 41000, 60000, 0.4, "twin_trace"),
        # трудное, верно и быстро
        SessionItem("sql.aggregate.null", 3, "x", True, 9000, 60000, 1.2),
        SessionItem("js.closure_loop", 2, "x", True, 8000, 60000, 1.0),
        # лёгкие и неверные: в комплект не годятся первыми
        SessionItem("py.format.spec", 1, "x", True, 50000, 60000, -1.0),
        SessionItem("algo.stack_queue.trace", 4, "x", False, 30000, 60000, 0.8),
    ]


def _independent(q) -> str:
    """Пересчитать ожидаемый ответ напрямую через bank.execution (минуя обёртки anticheat)."""
    fam = REG[q.family_id]
    r = q.variant_rendered
    assert r is not None
    if r.language == "python":
        return canonical(fam.normalize(run_python(r.code or "")))
    if r.language == "javascript":
        return canonical(fam.normalize(run_js(r.code or "")))
    base = fam.generate(q.seed)
    base_tables = tables_from_rendered(base.rendered.table)
    from app.testing.anticheat.executor import run_query

    ok, rows, _ = run_query(base_tables, base.rendered.code or "")
    assert ok
    proj = find_projection(fam, base, rows)
    assert proj is not None
    raw = run_sql(setup_sql(tables_from_rendered(r.table)), r.code or "")
    return canonical(fam.normalize(project_rows(raw, proj)))


@pytest.fixture(scope="module")
def kit():
    return build_kit(_items(), candidate_ref="cand-1", must_skills=["Kafka", "python", "Redis", "Docker"])


def test_kit_is_deterministic_and_independent_of_input_order(kit):
    again = build_kit(_items(), candidate_ref="cand-1", must_skills=["Kafka", "python", "Redis", "Docker"])
    assert again.kit_id == kit.kit_id
    assert again.to_dict("methodist") == kit.to_dict("methodist")
    shuffled = build_kit(list(reversed(_items())), candidate_ref="cand-1", must_skills=["Kafka", "python", "Redis", "Docker"])
    assert shuffled.kit_id == kit.kit_id
    other = build_kit(_items(), candidate_ref="cand-2", must_skills=["Kafka"])
    assert other.kit_id != kit.kit_id  # привязан к кандидату


def test_kit_picks_three_of_the_candidates_own_items_preferring_twin_trace_then_hard_fast(kit):
    trace = kit.trace_questions
    assert len(trace) == 3 and kit.shortfall == 0
    assert [q.id for q in trace] == ["q1", "q2", "q3"]
    assert len({q.family_id for q in trace}) == 3  # разные семейства
    assert trace[0].family_id == "py.dict.basics" and trace[0].selection_reason == "twin_trace"
    assert {q.selection_reason for q in trace[1:]} <= {"hard_fast", "hard"}
    assert {q.family_id for q in trace[1:]} == {"sql.aggregate.null", "js.closure_loop"}
    # неверные ответы (кроме следа двойника) в комплект не попадают; лёгкое верное — только если не хватило трудных
    assert "algo.stack_queue.trace" not in {q.family_id for q in trace}
    assert all(q.seed == dict((i.family_id, i.seed) for i in _items())[q.family_id] for q in trace if q.method == "literal_mutation")


def test_trace_variants_change_exactly_one_literal_and_answers_are_execution_verified(kit):
    for q in kit.trace_questions:
        assert q.method == "literal_mutation" and q.verified_by == "execution"
        fam = REG[q.family_id]
        base = fam.generate(q.seed)
        ch = q.changed
        assert ch and ch["from"] != ch["to"]
        base_text = compose_text(base.rendered).text
        off = ch["offset"]
        # ровно один токен: остальное посимвольно, а токен именно тот, что назван в вопросе
        assert base_text[off : off + len(ch["from"])] == ch["from"]
        assert q.variant_text[off : off + len(ch["to"])] == ch["to"]
        assert base_text[:off] == q.variant_text[:off]
        assert base_text[off + len(ch["from"]) :] == q.variant_text[off + len(ch["to"]) :]
        assert ch["line_before"] != ch["line_after"] and ch["from"] in ch["line_before"] and ch["to"] in ch["line_after"]
        # ожидаемый ответ получен исполнением и не равен ключу исходного варианта
        assert q.expected_answer == _independent(q), q.family_id
        assert q.expected_answer != canonical(base.key)
        assert ch["from"] in q.prompt_ru and ch["to"] in q.prompt_ru
        assert q.variant_code == base.variant_code and f"#{q.variant_code}" in q.prompt_ru


def test_trace_variant_avoids_the_literal_used_by_the_clipboard_twin(kit):
    from app.testing.anticheat.twins import make_twin

    q = kit.trace_questions[0]
    fam = REG[q.family_id]
    twin = make_twin(fam, fam.generate(q.seed))
    assert twin is not None and twin.changed_literal.ident != q.changed["ident"]


def test_rubric_and_listen_for_are_templated_russian(kit):
    for q in kit.questions:
        assert [r.score for r in q.rubric] == [0, 1, 2]
        assert all(re.search(r"[А-Яа-я]", r.text) for r in q.rubric)
        assert q.listen_for_ru and "{" not in q.listen_for_ru and re.search(r"[А-Яа-я]", q.listen_for_ru)
        assert "{" not in q.prompt_ru and "θ" not in q.prompt_ru
    q = kit.trace_questions[0]
    assert f"{q.changed['from']} → {q.changed['to']}" in q.listen_for_ru


def test_skill_questions_are_templates_marked_uncalibrated(kit):
    skills = kit.skill_questions
    # python покрыт тестом (в сессии есть задания Python), поэтому спрашиваем про Kafka и Redis; не больше двух
    assert [q.id for q in skills] == ["s1", "s2"] and len(skills) == CFG.verification.max_skill_questions
    assert all(not q.calibrated and q.label_ru == "не откалибровано" for q in skills)
    assert "Kafka" in skills[0].prompt_ru and "Redis" in skills[1].prompt_ru
    assert all(q.method == "template" and q.verified_by == "none" for q in skills)
    assert kit.trace_questions[0].calibrated is True
    # навык вне шаблонов получает общий вопрос
    generic = build_kit(_items(), candidate_ref="c", must_skills=["Rust"], covered_skills=[])
    assert "Rust" in generic.skill_questions[0].prompt_ru and not generic.skill_questions[0].calibrated


def test_covered_skills_follow_session_families():
    covered = covered_modules(_items(), REG)
    assert skill_is_covered("SQL", covered, CFG) and skill_is_covered("python", covered, CFG)
    assert skill_is_covered("javascript", covered, CFG)
    assert not skill_is_covered("Kafka", covered, CFG)
    assert skill_is_covered("kafka", ["Kafka"], CFG)


def test_employer_view_contains_no_evidence_or_selection_reason(kit):
    d = kit.to_dict("employer")
    blob = json.dumps(d, ensure_ascii=False).lower()
    for q in d["questions"]:
        assert "selection_reason" not in q and "family_id" not in q and "seed" not in q
    for word in ("двойник", "twin", "улик", "флаг", "буфер", "копир", "подмен"):
        assert word not in blob, word
    first = d["questions"][0]
    assert first["candidate_answer"] is None  # ответ следа двойника не цитируется
    quoted = [q for q in d["questions"] if q["kind"] == "trace" and q["candidate_answer"]]
    assert quoted and all(f"«{q['candidate_answer']}»" in q["prompt_ru"] for q in quoted)
    internal = kit.to_dict("methodist")["questions"][0]
    assert internal["selection_reason"] == "twin_trace" and internal["family_id"] == "py.dict.basics"


def test_family_reseed_fallback_is_marked_as_generator_verified():
    fam = REG["http.status.scenario"]  # текстовый сценарий без исполняемого кода: литерал подменить нельзя
    items = [SessionItem(fam.id, 2, "x", True, 5000, 20000, 1.0)]
    k = build_kit(items, candidate_ref="r")
    q = k.trace_questions[0]
    assert q.method == "family_reseed" and q.verified_by == "generator" and q.changed is None
    assert q.expected_answer and q.variant_text and q.seed != 2
    assert build_kit(items, candidate_ref="r").to_dict("methodist") == k.to_dict("methodist")


def test_shortfall_when_session_has_few_suitable_items():
    k = build_kit([SessionItem("py.dict.basics", 2, "x", True, 1000, 5000, 1.0)], candidate_ref="x")
    assert len(k.trace_questions) == 1 and k.shortfall == 2
    empty = build_kit([SessionItem("py.dict.basics", 2, "x", False, 1000, 5000, 1.0)], candidate_ref="x")
    assert empty.trace_questions == () and empty.shortfall == 3


def test_verification_result_matches_above_below(kit):
    q = [x.id for x in kit.trace_questions]
    res = evaluate_verification(kit, {q[0]: 2, q[1]: 2, q[2]: 2})
    assert res.verdict == "matches" and res.verdict_ru == "соответствует" and res.badge_ru == "Подтверждено вживую"
    assert res.ratio == pytest.approx(1.0)
    assert evaluate_verification(kit, {q[0]: 2, q[1]: 2, q[2]: 1}).verdict == "matches"  # 5/6 ≥ 0,67
    low = evaluate_verification(kit, {q[0]: 2, q[1]: 1, q[2]: 0})
    assert low.verdict == "below" and low.verdict_ru == "ниже" and low.badge_ru == "Не подтверждено вживую"
    top = evaluate_verification(kit, {q[0]: 2, q[1]: 2, q[2]: 2}, exceeded_expectations=True)
    assert top.verdict == "above" and top.verdict_ru == "выше"
    # «выше» только при полном балле: тест уже заявил, что кандидат справляется с этими заданиями
    assert evaluate_verification(kit, {q[0]: 2, q[1]: 2, q[2]: 1}, exceeded_expectations=True).verdict == "matches"


def test_verification_result_feeds_expert_label_and_ignores_uncalibrated_skills(kit):
    q = [x.id for x in kit.trace_questions]
    s = [x.id for x in kit.skill_questions]
    res = evaluate_verification(kit, {q[0]: 2, q[1]: 2, q[2]: 2, s[0]: 0, s[1]: 0})
    assert res.verdict == "matches"  # шаблонные вопросы по навыкам вес 0
    assert res.skill_scores == {s[0]: 0, s[1]: 0}
    label = res.to_expert_label()
    assert label["source"] == "verification_5min" and label["label"] == "matches" and label["label_ru"] == "соответствует"
    assert label["kit_id"] == kit.kit_id and label["n_trace"] == 3 and label["skill_scores_calibrated"] is False


def test_verification_result_validates_scores(kit):
    q = [x.id for x in kit.trace_questions]
    with pytest.raises(ValueError, match="вне 0"):
        evaluate_verification(kit, {q[0]: 3})
    with pytest.raises(ValueError, match="неизвестных вопросов"):
        evaluate_verification(kit, {"zzz": 1})
    with pytest.raises(ValueError, match="хотя бы один"):
        evaluate_verification(kit, {})


def test_kit_view_rejects_unknown_audience(kit):
    with pytest.raises(ValueError, match="работодателю или методисту"):
        kit.to_dict("candidate")  # кандидат комплект до встречи не видит
