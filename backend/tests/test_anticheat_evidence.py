"""Улики двойника: классификация ответа и агрегирование по сессии (spec-antiassist §2.1, §3)."""

from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime, timedelta

import pytest

from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.evidence import (
    AFTER,
    BEFORE,
    AnswerClass,
    TwinObservation,
    aggregate_session,
    classify_answer,
    classify_answer_ex,
    combine_honest,
    format_probability,
    p_at_least,
)

CFG = load_anticheat_config()
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def _norm(a):
    return " ".join(str(a).split())


# ----------------------------------------------------------------------------- классификация


def test_classify_answer_four_classes():
    kw = {"key": "24", "twin_key": "30", "distractors": {"12": "forgot_multiplier"}, "normalize": _norm}
    assert classify_answer("24", **kw) == "correct"
    assert classify_answer("  24 ", **kw) == "correct"  # нормализация как у семейства
    assert classify_answer("30", **kw) == "twin_trace"
    assert classify_answer("12", **kw) == "misconception"
    assert classify_answer("99", **kw) == "other"
    ex = classify_answer_ex("12", **kw)
    assert ex.kind == "misconception" and ex.tag == "forgot_multiplier" and ex.matched == "distractor"
    tw = classify_answer_ex("30", **kw)
    assert (tw.kind, tw.matched, tw.strength) == ("twin_trace", "twin_key", "exact") and tw.is_exact_twin


def test_twin_answer_is_always_scored_wrong_for_the_level():
    kw = {"key": "24", "twin_key": "30", "distractors": {}, "normalize": _norm}
    assert classify_answer_ex("24", **kw).scored_correct is True
    for wrong in ("30", "12", "99"):
        assert classify_answer_ex(wrong, **kw).scored_correct is False


def test_twin_key_that_collides_with_a_misconception_is_not_an_accusation():
    # если ключ двойника случайно совпал с известным заблуждением, считаем это заблуждением, а не следом
    ex = classify_answer_ex("30", key="24", twin_key="30", distractors={"30": "tag"}, normalize=_norm)
    assert ex.kind == "misconception"


def test_twin_token_trace_is_weak_context():
    kw = {"key": "24 7", "twin_key": "30 7", "distractors": {}, "normalize": _norm, "known_text": "f([3, 9, 4]) 24 7"}
    weak = classify_answer_ex("[3,12,4] 5", twin_to="12", **kw)
    assert (weak.kind, weak.matched, weak.strength) == ("twin_trace", "twin_token", "token") and not weak.is_exact_twin
    assert classify_answer_ex("16 5", twin_to="6", **kw).kind == "other"  # «6» — не слово «16»
    assert classify_answer_ex("9 5", twin_to="9", **kw).kind == "other"  # значение есть на экране


def test_fractional_twin_key_matches_rounded_answer_but_integers_stay_exact():
    kw = {"key": "4 3 8780", "twin_key": "4 3 9586.666666666666", "normalize": _norm}
    assert classify_answer("4 3 9586.67", **kw) == "twin_trace"  # бот округлил среднее
    assert classify_answer("4 3 9586.666666666666", **kw) == "twin_trace"
    assert classify_answer("4 3 9586.7", **kw) == "other"
    assert classify_answer("4 4 9586.67", **kw) == "other"  # каркас ответа должен совпасть
    assert classify_answer("31", key="24", twin_key="30", normalize=_norm) == "other"  # целые сравниваются точно


# ----------------------------------------------------------------------------- вероятности


def test_p_at_least_matches_spec_binomial_numbers():
    four = [0.02] * 4
    assert p_at_least(2, four) == pytest.approx(2.33e-3, rel=0.02)
    assert p_at_least(3, four) == pytest.approx(3.16e-5, rel=0.02)
    assert p_at_least(4, [0.02] * 8) == pytest.approx(1.05e-5, rel=0.02)
    assert p_at_least(0, four) == 1.0 and p_at_least(5, four) == 0.0
    assert p_at_least(1, four) > p_at_least(2, four) > p_at_least(3, four)
    # разные вероятности: Пуассон-биномиальное считается точно
    assert p_at_least(2, [0.5, 0.5]) == pytest.approx(0.25)
    assert p_at_least(1, [0.1, 0.2]) == pytest.approx(1 - 0.9 * 0.8)


def test_format_probability_and_combine():
    assert format_probability(3.2e-5) == "3,2·10⁻⁵"
    assert format_probability(0.023) == "0,023"
    assert format_probability(0.0) == "0"
    assert combine_honest(0.02, 0.0) == pytest.approx(0.02)
    assert combine_honest(0.02, 0.01) == pytest.approx(1 - 0.98 * 0.99)


# ----------------------------------------------------------------------------- агрегирование по сессии


def _cls(kind: str, strength: str = "exact") -> AnswerClass:
    if kind == "twin_trace":
        return AnswerClass("twin_trace", "twin_key" if strength == "exact" else "twin_token", strength)
    if kind == "correct":
        return AnswerClass("correct", "key")
    if kind == "misconception":
        return AnswerClass("misconception", "distractor", tag="t")
    return AnswerClass("other", "none")


def obs(seq, kind="twin_trace", *, sub="data_literal:element", p=0.02, strength="exact", family=None, **kw):
    return TwinObservation(
        seq=seq,
        family_id=family or f"fam.{seq}",
        variant_code=f"V{seq:03d}",
        mutation_class=sub.split(":")[0],
        mutation_subclass=sub,
        answer="30",
        answer_class=_cls(kind, strength),
        honest_p=p,
        **kw,
    )


def four(kinds, subs=("data_literal:element", "data_literal:value", "table_value:cell", "data_literal:multiplier")):
    return [obs(i + 1, k, sub=subs[i]) for i, k in enumerate(kinds)]


def calibrated_cfg(weight=0.4):
    return dataclasses.replace(CFG, evidence=dataclasses.replace(CFG.evidence, calibrated=True, trust_weight=weight))


def test_no_hits_means_no_state():
    s = aggregate_session(four(["correct", "other", "misconception", "correct"]))
    assert s.state == "none" and s.n_counted_hits == 0 and s.n_twin_items == 4
    assert s.suggested_level == "confirmed" and s.proposed_level == "confirmed"
    assert s.p_honest_session == 1.0 and not s.methodist_attention
    assert aggregate_session([]).state == "none"


def test_first_occurrence_is_warning_state_not_evidence():
    s = aggregate_session(four(["correct", "twin_trace", "correct", "correct"]))
    assert s.state == "warning" and s.n_counted_hits == 1
    assert s.hits[0].phase == BEFORE  # первое попадание само запускает предупреждение
    assert s.suggested_level == "note"  # до предупреждения — не выше «С оговоркой»
    assert not s.methodist_attention


def test_repeat_after_warning_is_evidence_for_methodist():
    s = aggregate_session(four(["twin_trace", "correct", "twin_trace", "correct"]))
    assert s.state == "evidence" and s.methodist_attention
    assert [h.phase for h in s.hits] == [BEFORE, AFTER]
    assert s.n_counted_hits == 2
    # 2 попадания из 4 при ε=0,02: 2,3·10⁻³ > 10⁻³ — на «Требует подтверждения» не хватает (spec §3, примечание к порогу)
    assert s.p_honest_session == pytest.approx(2.33e-3, rel=0.02)
    assert s.suggested_level == "note"


def test_three_hits_in_distinct_classes_propose_review_but_only_as_a_proposal():
    s = aggregate_session(four(["twin_trace", "twin_trace", "twin_trace", "correct"]), cfg=calibrated_cfg())
    assert s.state == "review_candidate"
    assert s.p_honest_session == pytest.approx(3.16e-5, rel=0.02)
    assert s.expected_false_per_10k == pytest.approx(0.316, rel=0.02)  # «ожидаемо N ложных на 10 000 сессий»
    assert s.proposed_level == "under_review"  # предложение; решает методист
    assert s.auto_ban is False
    d = s.to_dict("methodist", calibrated_cfg())
    assert d["auto_ban"] is False and d["decision"] == "только методист"


def test_weight_is_zero_until_calibrated():
    items = four(["twin_trace", "twin_trace", "twin_trace", "correct"])
    s = aggregate_session(items)  # конфиг по умолчанию: calibrated = false, trust_weight = 0
    assert s.context_only and s.trust_weight == 0.0 and s.trust_signal == 0.0
    assert s.proposed_level == "confirmed"  # уровень доверия не меняется: улика — контекст для методиста
    assert s.suggested_level == "under_review"  # что предложили бы правила при откалиброванной ε
    cal = aggregate_session(items, cfg=calibrated_cfg(0.4))
    assert not cal.context_only and cal.trust_weight == 0.4 and cal.trust_signal == 0.4


def test_one_hit_per_mutation_class():
    same = [obs(i, sub="data_literal:element") for i in (1, 2, 3)] + [obs(4, "correct")]
    s = aggregate_session(same, cfg=calibrated_cfg())
    assert s.n_counted_hits == 1  # коррелированные заблуждения не накапливаются
    assert s.hits[0].phase == AFTER  # из одного класса берём попадание после предупреждения
    assert s.state == "evidence" and s.suggested_level == "note"  # одного класса мало для review
    assert len([e for e in s.items if e.answer_class.is_exact_twin]) == 3  # остальные видны в записях
    assert sum(1 for e in s.items if e.counted) == 1


def test_denominator_is_all_twin_items_answered():
    items = four(["twin_trace", "correct", "correct", "correct"])
    s = aggregate_session(items + [obs(5, "correct", has_twin=False)])
    assert s.n_twin_items == 4  # задания без двойника в знаменатель не входят


def test_warning_boundary_uses_issue_time_when_known():
    warned = T0
    items = [
        obs(1, issued_at=T0 - timedelta(seconds=30)),  # показано до тоста: кандидат мог не прочитать
        obs(2, sub="data_literal:value", issued_at=T0 + timedelta(seconds=40)),
        obs(3, "correct", issued_at=T0 + timedelta(seconds=90)),
    ]
    s = aggregate_session(items, warned_at=warned)
    phases = {h.seq: h.phase for h in s.hits}
    assert phases == {1: BEFORE, 2: AFTER}
    assert s.state == "evidence"


def test_accessibility_mode_caps_level_and_never_reaches_review():
    items = four(["twin_trace", "twin_trace", "twin_trace", "correct"])
    s = aggregate_session(items, a11y_mode=True, cfg=calibrated_cfg())
    assert s.state == "evidence" and s.suggested_level == "note"  # попадания в режиме доступности не выше «С оговоркой»
    assert s.a11y_mode and s.proposed_level == "note"


def test_self_paste_and_stale_render_are_excluded_from_evidence():
    items = [
        obs(1, self_paste=True),
        obs(2, sub="data_literal:value", render_id="r-new", twin_render_id="r-old"),
        obs(3, sub="table_value:cell", render_id="r3", twin_render_id="r3"),
        obs(4, "correct"),
    ]
    s = aggregate_session(items)
    assert s.n_counted_hits == 1 and s.hits[0].seq == 3
    reasons = {e.seq: e.excluded for e in s.items}
    assert reasons[1] == "self_paste" and reasons[2] == "stale_clip" and reasons[3] is None
    assert {e.seq for e in s.context} == {1, 2}
    assert s.n_twin_items == 4  # знаменатель — все ответы на задания с двойником


def test_token_trace_is_context_not_a_hit():
    items = [obs(1, strength="token"), obs(2, "correct"), obs(3, "correct"), obs(4, "correct")]
    s = aggregate_session(items)
    assert s.state == "none" and s.n_counted_hits == 0
    assert [e.seq for e in s.context] == [1] and not s.context[0].counted


def test_twin_answers_are_wrong_for_theta_and_dismissal_excludes_them():
    items = four(["twin_trace", "correct", "twin_trace", "other"])
    s = aggregate_session(items)
    assert s.theta_correct == {1: False, 2: True, 3: False, 4: False}  # ответ двойника — это неверный ответ на X
    assert s.theta_items(dismissed=False) == s.theta_correct
    # методист снял флаг (dismissed): спорные задания исключаются из оценки, остальные остаются
    assert s.theta_items(dismissed=True) == {2: True, 4: False}
    assert s.theta_exclusions_if_dismissed == frozenset(h.seq for h in s.hits)


def test_family_collision_rate_raises_honest_probability():
    items = four(["twin_trace", "twin_trace", "correct", "correct"])
    base = aggregate_session(items)
    worse = aggregate_session(items, family_collision_rates={e.family_id: 0.05 for e in base.items})
    assert worse.p_honest_session > base.p_honest_session


def test_auto_ban_is_impossible():
    bad = dataclasses.replace(CFG, evidence=dataclasses.replace(CFG.evidence, auto_ban=True))
    with pytest.raises(ValueError, match="бана нет"):
        aggregate_session(four(["twin_trace", "correct", "correct", "correct"]), cfg=bad)
    assert CFG.evidence.auto_ban is False
    assert not hasattr(aggregate_session([]), "ban")


def test_candidate_view_is_minimal_and_texts_have_no_theta():
    s = aggregate_session(four(["twin_trace", "correct", "twin_trace", "correct"]))
    cand = s.to_dict("candidate")
    assert set(cand) == {"observations"} and len(cand["observations"]) == 2
    first = cand["observations"][0]
    assert set(first) == {"variant_code", "message_ru", "actions"}
    assert "не соответствует показанному условию" in first["message_ru"]
    assert first["actions"] == ["Оспорить", "Пройти подтверждение"]
    blob = json.dumps([cand, s.to_dict("methodist")], ensure_ascii=False)
    assert "θ" not in blob and "ИИ" not in blob
    assert aggregate_session([]).to_dict("candidate") == {"observations": []}


def test_employer_never_gets_an_evidence_view():
    s = aggregate_session(four(["twin_trace", "correct", "twin_trace", "correct"]))
    with pytest.raises(ValueError, match="только методисту и кандидату"):
        s.to_dict("employer")  # улики работодателю не отдаются никогда (spec §2, M5)
