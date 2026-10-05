"""IRT-ядро: модель ответа, A_eff, EAP на сетке, общий приор, балл и интервал (spec-testing §3.1–3.3)."""

import math

import numpy as np
import pytest

from app.testing.irt import (
    BELOW_INTERN,
    ItemParams,
    Posterior,
    a_eff,
    b_from_expert,
    eap,
    info_at,
    information,
    load_scale,
    loo_means,
    person_fit_lz,
    prob,
    se_with_age,
    sigma_b_from_ratings,
)

SCALE = load_scale()


def test_a_eff_formula_matches_spec():
    A, sb, si = 1.5, 0.5, 0.15
    expected = A / math.sqrt(1 + math.pi * A**2 * (sb**2 + si**2) / 8)
    assert a_eff(A, sb, si) == pytest.approx(expected)
    assert a_eff(A, 0.8, si) < a_eff(A, 0.5, si) < a_eff(A, 0.0, si) < A  # неуверенность → положе
    assert a_eff(A, 0.0, 0.0) == pytest.approx(A)


def test_response_model_and_spec_explanation_of_A():
    item = ItemParams(b=0.0, c=0.0, a=1.5)
    assert prob(0.0, item) == pytest.approx(0.5)
    # «типичный Junior (θ = −0.5) решает пороговое задание Middle ≈ 32%, типичный Middle (θ = +0.5) ≈ 68%»
    assert prob(-0.5, item) == pytest.approx(0.32, abs=0.005)
    assert prob(0.5, item) == pytest.approx(0.68, abs=0.005)
    guess = ItemParams(b=0.0, c=0.25, a=1.5)
    assert prob(0.0, guess) == pytest.approx(0.625)
    assert prob(-10.0, guess) == pytest.approx(0.25, abs=1e-4)


def test_information_peaks_at_b_and_guessing_reduces_it():
    item = ItemParams(b=0.3, a=1.4)
    grid = np.linspace(-3, 3, 601)
    assert grid[np.argmax(information(grid, item))] == pytest.approx(0.3, abs=0.01)
    assert info_at(0.3, item) == pytest.approx(1.4**2 / 4)
    assert info_at(0.3, ItemParams(b=0.3, c=0.25, a=1.4)) < info_at(0.3, item)


def test_grid_and_cuts_on_grid():
    g = SCALE.grid
    assert len(g) == 161
    assert g[0] == -4.0 and g[-1] == 4.0
    for _, cut in SCALE.cuts:
        assert cut in set(g.tolist())


def test_expert_prior_b_and_sigma():
    assert b_from_expert("junior", 1, SCALE) == pytest.approx(-0.65)
    assert b_from_expert("middle", 0, SCALE) == pytest.approx(0.0)
    assert b_from_expert("senior", -1, SCALE) == pytest.approx(0.65)
    assert sigma_b_from_ratings([("middle", 0), ("middle", 1)], SCALE) == 0.5
    assert sigma_b_from_ratings([("middle", -1), ("middle", 1)], SCALE) == 0.8
    assert sigma_b_from_ratings([("middle", 0), ("senior", 0)], SCALE) is None  # расхождение на грейд


def test_prior_is_common_N_minus025_12():
    p = Posterior.prior(SCALE)
    assert p.mean == pytest.approx(-0.25, abs=0.01)
    assert p.sd == pytest.approx(1.2, abs=0.03)  # сетка [−4, 4] чуть обрезает хвосты
    assert sum(p.class_probs().values()) == pytest.approx(1.0)


def test_eap_moves_with_answers_and_p_pass_is_monotone():
    items = [ItemParams(b=b, a=1.3) for b in (-1.0, -0.5, 0.0, 0.5, 1.0)]
    all_right = eap([(i, 1) for i in items], SCALE)
    all_wrong = eap([(i, 0) for i in items], SCALE)
    assert all_right.mean > -0.25 > all_wrong.mean
    assert all_right.sd < 1.2
    assert all_right.p_at_least(0.0) > all_wrong.p_at_least(0.0)
    ps = [all_right.p_at_least(t) for t in (-2.0, -1.0, 0.0, 1.0)]
    assert ps == sorted(ps, reverse=True)


def test_class_probs_bands_and_score_interval():
    post = eap([(ItemParams(b=0.0, a=1.3), 1)] * 6, SCALE)
    cp = post.class_probs()
    assert set(cp) == {BELOW_INTERN, "intern", "junior", "middle", "senior"}
    assert sum(cp.values()) == pytest.approx(1.0)
    assert cp["middle"] + cp["senior"] == pytest.approx(post.p_at_least(0.0))
    s = post.summary("middle")
    assert s["score"] == round(500 + 100 * post.mean)
    lo, hi = s["interval80"]
    assert (hi - lo) / 2 == pytest.approx(128 * post.sd, abs=1.0)  # S ± 128·SE


def test_loo_and_person_fit():
    items = [ItemParams(b=b, a=1.3) for b in np.linspace(-2, 2, 12)]
    consistent = [1 if it.b < 0 else 0 for it in items]  # лёгкие решены, трудные нет
    aberrant = [0 if it.b < 0 else 1 for it in items]  # наоборот
    th_c = eap(zip(items, consistent, strict=True), SCALE).mean
    th_a = eap(zip(items, aberrant, strict=True), SCALE).mean
    assert person_fit_lz(items, consistent, th_c) > -1.0
    assert person_fit_lz(items, aberrant, th_a) < -2.33
    loo = loo_means(items, consistent, SCALE)
    assert len(loo) == len(items)
    # без верного ответа на лёгкое задание оценка ниже полной
    assert loo[0] < eap(zip(items, consistent, strict=True), SCALE).mean


def test_freshness_se_growth():
    # SE 0.30 → ≈ 0.57 за 12 месяцев без активности (§3.11)
    assert se_with_age(0.30, 12, 0.02) == pytest.approx(0.574, abs=0.002)
    assert se_with_age(0.30, 0, 0.02) == pytest.approx(0.30)
