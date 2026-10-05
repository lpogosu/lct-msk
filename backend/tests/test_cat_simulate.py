"""Симуляция на прод-движке: точность у порога, длина, детерминизм, «два кандидата — два теста» (spec §6)."""

import random
import statistics

import pytest

from app.testing.cat import BORDERLINE_FAIL, BORDERLINE_PASS, FAILED, PASSED, instance_overlap
from app.testing.irt import load_scale
from app.testing.simulate import (
    SimulatedExaminee,
    as_random,
    decision_accuracy,
    run_simulated_session,
    sample_claimed_grade,
    synthetic_families,
)

SCALE = load_scale()
SPEC = "backend_python"
BANK = synthetic_families((SPEC,), seed=11)


@pytest.fixture(scope="module")
def population():
    """300 симулянтов, θ равномерно через все полосы; заявка 60% верно / 30% выше / 10% ниже (S1)."""
    rng = random.Random(2026)
    out = []
    for i in range(300):
        theta = rng.uniform(-2.6, 1.9)
        true = SCALE.grade_of(theta)
        claimed = sample_claimed_grade(true if true in SCALE.grades else "intern", rng)
        out.append(run_simulated_session(theta, SPEC, claimed, BANK, rng, session_id=f"p{i}"))
    return out


def test_accuracy_at_cut_over_300_candidates(population):
    acc = decision_accuracy(population)
    assert acc >= 0.80, acc


def test_lengths_within_rules(population):
    ns = [r["n"] for r in population]
    assert min(ns) >= 8 and max(ns) <= 20
    assert statistics.mean(ns) <= 15  # «обычно 12–15»
    assert all(r["n_total"] - r["n"] <= 8 * len(r["extensions"]) for r in population)
    assert all(r["n_pretest"] == 0 for r in population)  # в синтетическом банке нет pretest-семейств
    codes = {r["decision"] for r in population}
    assert codes <= {PASSED, FAILED, BORDERLINE_PASS, BORDERLINE_FAIL}


def test_final_grade_mostly_within_one(population):
    order = {g: i for i, g in enumerate(SCALE.grades)}
    close = 0
    for r in population:
        true = r["true_grade"]
        if r["final_grade"] is None:
            close += true in ("below_intern", "intern")
        elif true == "below_intern":
            close += r["final_grade"] == "intern"
        else:
            close += abs(order[r["final_grade"]] - order[true]) <= 1
    assert close / len(population) >= 0.95


def test_result_shape_and_trace():
    r = run_simulated_session(0.3, SPEC, "middle", BANK, 5)
    for key in ("decision", "theta_hat", "se", "n", "items", "trace", "final_grade", "class_probs", "extensions"):
        assert key in r
    assert len(r["trace"]) == len(r["items"]) == r["n_total"] + r["n_pretest"]
    assert all("text_ru" in t["reason"] for t in r["trace"])
    assert r["score"] == round(500 + 100 * r["theta_hat_final"])  # S = 500 + 100·θ̂
    assert r["interval80"][0] < r["score"] < r["interval80"][1]


def test_deterministic_with_seed():
    a = run_simulated_session(-0.4, SPEC, "middle", BANK, 123)
    b = run_simulated_session(-0.4, SPEC, "middle", BANK, 123)
    assert a["items"] == b["items"] and a["decision"] == b["decision"] and a["theta_hat"] == b["theta_hat"]
    c = run_simulated_session(-0.4, SPEC, "middle", BANK, 124)
    assert [i["family_id"] for i in a["items"]] != [i["family_id"] for i in c["items"]]


def test_two_candidates_two_tests_zero_instance_overlap():
    """Превью для работодателя: θ = −0.5 / 0.3 / 1.2 — разные экземпляры, пересечение 0 (§2.6)."""
    rng = random.Random(77)
    runs = [run_simulated_session(t, SPEC, "middle", BANK, rng, session_id=f"v{i}") for i, t in
            enumerate((-0.5, 0.3, 1.2))]
    for i in range(3):
        for j in range(i + 1, 3):
            assert instance_overlap(runs[i]["instances"], runs[j]["instances"]) == 0
    assert runs[0]["theta_hat"] < runs[2]["theta_hat"]


def test_module_theta_affects_subscores():
    ex = SimulatedExaminee(theta=0.0, module_theta={"python_core": 2.5, "sql": -2.5})
    r = run_simulated_session(0.0, SPEC, "middle", BANK, 31, examinee=ex, follow_extensions=False)
    py = [i for i in r["items"] if i["module"] == "python_core"]
    sql = [i for i in r["items"] if i["module"] == "sql"]
    assert sum(i["correct"] for i in py) / len(py) > sum(i["correct"] for i in sql) / len(sql)


def test_numpy_generator_accepted():
    import numpy as np

    r = run_simulated_session(1.0, SPEC, "middle", BANK, np.random.default_rng(5))
    assert r["decision"] in (PASSED, FAILED, BORDERLINE_PASS, BORDERLINE_FAIL)
    with pytest.raises(TypeError):
        as_random("seed")


def test_frontend_shared_modules_bank():
    bank = synthetic_families(("backend_python", "frontend_js"), seed=2)
    shared = [f for f in bank if f.module == "http_api"]
    assert shared and all(set(f.specs) == {"backend_python", "frontend_js"} for f in shared)
    r = run_simulated_session(0.5, "frontend_js", "middle", bank, 3)
    assert {i["module"] for i in r["items"]} >= {"js_core", "react", "html_css", "http_api"}
