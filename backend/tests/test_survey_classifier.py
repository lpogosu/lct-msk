"""Классификатор специализации по опросу (concept §2.4, spec-testing §3.7)."""

import math

import pytest

from app.core.configs import load_config
from app.testing.survey import (
    SurveyAnswers,
    SurveyConfig,
    classify,
    load_survey_config,
    senior_warning,
    softmax,
    suggest_grade,
    survey_form,
)

R, S, N = "regularly", "sometimes", "never"


def test_form_has_16_actions_with_russian_labels():
    form = survey_form()
    assert len(form["actions"]) == 16
    assert [f["label"] for f in form["frequency_levels"]] == ["Не делал", "Иногда", "Регулярно"]
    for a in form["actions"]:
        assert any("а" <= ch.lower() <= "я" for ch in a["label"])
    assert {s["code"] for s in form["specializations"]} >= {"backend_python", "frontend_js", "fullstack"}
    cfg = load_survey_config()
    assert cfg.frequency == {"never": 0.0, "sometimes": 0.5, "regularly": 1.0}


def test_backend_profile_no_hint():
    r = classify(
        SurveyAnswers(
            "backend_python",
            actions={"server_api": R, "sql_db": R, "queues_cache": S},
            technologies={"python": "now", "django_fastapi": "now", "postgresql": "now"},
        )
    )
    assert r.top == "backend_python" and not r.hint and r.hint_text_ru is None
    assert r.final_spec == "backend_python"
    assert sum(r.probs.values()) == pytest.approx(1.0)
    assert set(r.declared_modules) == {"python_core", "http_api", "sql"}


def test_hint_when_answers_point_elsewhere_but_candidate_decides():
    r = classify(
        SurveyAnswers(
            "backend_python",
            actions={"ui_layout": R, "spa_components": R, "browser_debug": R},
            technologies={"javascript": "now", "react": "now"},
        )
    )
    assert r.top == "frontend_js" and r.hint
    assert r.probs["frontend_js"] - r.probs["backend_python"] >= 0.2
    assert "Frontend (JS/TS)" in r.hint_text_ru
    assert r.final_spec == "backend_python"  # итог = выбор кандидата


def test_fullstack_and_language_specific_backends():
    fs = classify(
        SurveyAnswers(
            "fullstack",
            actions={"server_api": R, "sql_db": S, "ui_layout": R, "spa_components": R},
            technologies={"javascript": "now", "react": "now", "nodejs": "now", "postgresql": "now"},
        )
    )
    assert fs.top == "fullstack"
    assert fs.chosen_status == "derived" and fs.waitlist_text_ru is None
    assert "«Backend (Python)» и «Frontend (JS/TS)»" in fs.derived_text_ru
    go = classify(SurveyAnswers("backend_go", actions={"server_api": R, "sql_db": R}, technologies={"go": "now"}))
    assert go.top == "backend_go"
    devops = classify(SurveyAnswers("devops", actions={"ci_cd": R, "containers_infra": R, "monitoring": R}))
    assert devops.top == "devops" and devops.chosen_status == "survey_only"
    assert devops.waitlist_text_ru is not None


def test_formula_exact_on_custom_config():
    """s_k = Σ W·freq + 0.3·Σ V·recency + 0.5·[выбор = k];  p = softmax(s/0.5); подсказка при разрыве ≥ 0.2."""
    cfg = SurveyConfig(
        temperature=0.5, tech_weight=0.3, choice_bonus=0.5, hint_margin=0.2,
        frequency={"never": 0.0, "sometimes": 0.5, "regularly": 1.0}, frequency_labels={},
        recency={"now": 1.0, "older": 0.1}, recency_labels={},
        actions={"act": {"label": "x", "weights": {"a": 1.0}}},
        technologies={"tech": {"label": "t", "weights": {"b": 1.0}, "modules": []}},
        grade_min_months={}, senior_warning_months=24,
    )
    tax = {"specializations": [{"code": "a", "name": "A"}, {"code": "b", "name": "B"}]}
    r = classify(SurveyAnswers("b", actions={"act": S}, technologies={"tech": "older"}), cfg, tax)
    s_a, s_b = 0.5, 0.3 * 0.1 + 0.5
    assert r.scores == pytest.approx({"a": s_a, "b": s_b})
    pa = math.exp(s_a / 0.5) / (math.exp(s_a / 0.5) + math.exp(s_b / 0.5))
    assert r.probs["a"] == pytest.approx(pa)
    assert r.top == "b" and not r.hint  # выбор перевесил
    r2 = classify(SurveyAnswers("b", actions={"act": R}), cfg, tax)
    # s_a = 1.0, s_b = 0.5 → p_a = σ(1) ≈ 0.731, разрыв ≈ 0.46 ≥ 0.2 → подсказка
    assert r2.top == "a" and r2.hint and r2.probs["a"] == pytest.approx(1 / (1 + math.exp(-1)))
    r3 = classify(SurveyAnswers("b", actions={"act": S}, technologies={"tech": 0.0}), cfg, tax)
    # s_a = 0.5, s_b = 0.5 → ничья, подсказки нет
    assert not r3.hint


def test_softmax_temperature():
    p = softmax({"x": 1.0, "y": 0.0}, 0.5)
    assert p["x"] == pytest.approx(1 / (1 + math.exp(-2)))


def test_validation_errors():
    with pytest.raises(ValueError):
        classify(SurveyAnswers("backend_python", actions={"nope": R}))
    with pytest.raises(ValueError):
        classify(SurveyAnswers("backend_python", actions={"server_api": "often"}))
    with pytest.raises(ValueError):
        classify(SurveyAnswers("unknown_spec"))
    with pytest.raises(ValueError):
        classify(SurveyAnswers("backend_python", technologies={"python": "yesterday"}))


def test_grade_hint_and_senior_warning():
    assert suggest_grade(3)[0] == "intern"
    assert suggest_grade(12)[0] == "junior"
    assert suggest_grade(30)[0] == "middle"
    g, text = suggest_grade(60)
    assert g == "senior" and "Senior" in text
    assert suggest_grade(None) == (None, None)
    assert senior_warning("senior", 12) is not None
    assert senior_warning("senior", 30) is None
    assert senior_warning("middle", 6) is None


def test_weights_reference_known_specs_only():
    specs = {s["code"] for s in load_config("taxonomy")["specializations"]}
    cfg = load_survey_config()
    for a in cfg.actions.values():
        assert set(a["weights"]) <= specs
    for t in cfg.technologies.values():
        assert set(t["weights"]) <= specs
        assert set(t["modules"]) <= set(load_config("taxonomy")["modules"])
