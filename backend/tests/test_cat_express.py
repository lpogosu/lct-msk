"""Экспресс-CAT жюри: тот же движок, короче правило остановки; честность решения (DECISIONS п. 29)."""

import random

from app.testing.cat import FAILED, PASSED, express_cat_config, load_cat_config, load_express_config
from app.testing.irt import load_scale
from app.testing.simulate import run_simulated_session, synthetic_families

SCALE = load_scale()
SPEC = "backend_python"
BANK = synthetic_families((SPEC,), seed=11)


def test_express_config_is_shorter_but_keeps_confidence_thresholds():
    full, ex = load_cat_config(), express_cat_config()
    assert ex.n_max == 8 < full.n_max and ex.n_min < full.n_min and ex.pretest_count == 0
    # честность: пороги уверенности те же, что в полном тесте, — экспресс не «подгоняет» грейд
    assert (ex.p_stop_high, ex.p_stop_low) == (full.p_stop_high, full.p_stop_low)
    assert load_express_config().time_budget_s == 240


def test_express_never_exceeds_cap_and_confident_calls_are_accurate():
    rng = random.Random(77)
    ex = express_cat_config()
    confident = right = 0
    for i in range(120):
        theta = rng.uniform(-2.4, 1.8)
        claimed = SCALE.grade_of(theta) if SCALE.grade_of(theta) in SCALE.grades else "intern"
        r = run_simulated_session(theta, SPEC, claimed, BANK, rng, session_id=f"x{i}", cfg=ex, follow_extensions=False)
        assert ex.n_min <= r["n"] <= ex.n_max
        if r["decision"] in (PASSED, FAILED):
            confident += 1
            right += (theta >= SCALE.cut(claimed)) == (r["decision"] == PASSED)
    assert 0 < confident < 120  # часть исходов честно «не определён» — иначе точности не хватало бы на всех
    assert right / confident >= 0.85
