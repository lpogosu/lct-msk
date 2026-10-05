"""Фаза 1 калибровки: MAP по b, якоря, pretest → боевое, карантин (spec-testing §3.9, §2.4)."""

import math
import random

import numpy as np
import pytest

from app.testing.calibration import (
    calibrate_family,
    calibration_run,
    map_b,
    next_status,
    point_biserial,
    promotion_decision,
)
from app.testing.cat import OPERATIONAL, PRETEST, QUARANTINE, RETIRED, FamilyMeta, ScaleParam
from app.testing.irt import a_eff, load_scale

SCALE = load_scale()
SPEC = "backend_python"


def simulate_responses(b_true, n, rng, c=0.0, theta_noise=0.25):
    """θ ~ N(0,1); ответ по Рашу A = 1.5 с шумом клонов σ_inst; движок видит LOO-θ̂ с ошибкой."""
    thetas, xs = [], []
    for _ in range(n):
        th = rng.gauss(0, 1)
        delta = rng.gauss(0, SCALE.sigma_inst)
        p = c + (1 - c) / (1 + math.exp(-SCALE.A * (th - b_true - delta)))
        xs.append(rng.random() < p)
        thetas.append(th + rng.gauss(0, theta_noise))
    return thetas, xs


def fam(fid, b_prior=0.0, sigma=0.5, **kw):
    return FamilyMeta(
        id=fid, module="python_core", specs=(SPEC,), target_grade="middle",
        params={SPEC: ScaleParam(b=b_prior, sigma_b=sigma)}, **kw,
    )


def test_map_converges_toward_true_b():
    rng = random.Random(1)
    th, xs = simulate_responses(0.8, 300, rng)
    res = map_b(th, xs, b_prior=0.0, sigma_b=0.5, a=a_eff(SCALE.A, 0, SCALE.sigma_inst))
    assert res.converged and res.iterations <= 25
    assert abs(res.b - 0.8) < 0.25
    assert abs(res.b - 0.8) < abs(0.0 - 0.8)
    assert res.se_b < 0.2


def test_map_with_guessing_converges():
    rng = random.Random(2)
    th, xs = simulate_responses(-0.4, 400, rng, c=0.25)
    res = map_b(th, xs, b_prior=0.3, sigma_b=0.8, c=0.25, a=a_eff(SCALE.A, 0, SCALE.sigma_inst))
    assert res.converged and abs(res.b + 0.4) < 0.35


def test_rmse_decreases_with_more_responses():
    """Эксперты ошибаются на σ = 0.5; после 30 → 300 ответов RMSE b падает (§6.4: 0.54 → 0.31 → 0.24)."""
    rng = random.Random(3)
    errs = {0: [], 30: [], 300: []}
    for k in range(40):
        b_true = rng.uniform(-2.0, 1.5)
        b_prior = b_true + rng.gauss(0, 0.5)
        meta = fam(f"f{k}", b_prior=b_prior, sigma=0.5)
        th, xs = simulate_responses(b_true, 300, rng)
        errs[0].append(b_prior - b_true)
        for n in (30, 300):
            res = calibrate_family(meta, SPEC, th[:n], xs[:n])
            assert res.updated
            errs[n].append(res.b - b_true)
    rmse = {n: float(np.sqrt(np.mean(np.square(e)))) for n, e in errs.items()}
    assert rmse[300] < rmse[30] < rmse[0]
    assert rmse[300] <= 0.30


def test_anchors_fixed_and_min_responses():
    rng = random.Random(4)
    th, xs = simulate_responses(1.0, 100, rng)
    anchor = fam("anchor", b_prior=0.0, is_anchor=True)
    res = calibrate_family(anchor, SPEC, th, xs)
    assert not res.updated and res.reason == "anchor" and res.b == 0.0
    res = calibrate_family(fam("young"), SPEC, th[:29], xs[:29])
    assert not res.updated and res.reason == "not_enough_responses"
    res = calibrate_family(fam("ok"), SPEC, th[:30], xs[:30])
    assert res.updated and res.new_param.sigma_b == pytest.approx(res.se_b)  # апостериор → новый приор


def test_point_biserial_and_promotion_rule():
    rng = random.Random(5)
    th, xs = simulate_responses(0.0, 200, rng)
    assert point_biserial(xs, th) > 0.3
    assert point_biserial([1] * 10, list(range(10))) == 0.0
    assert promotion_decision(50, 0.35, 0.15)[0]
    assert not promotion_decision(49, 0.30, 0.30)[0]
    assert not promotion_decision(80, 0.36, 0.30)[0]
    ok, why = promotion_decision(80, 0.30, 0.14)
    assert not ok and "r_pb" in why
    assert not promotion_decision(80, 0.30, 0.30, distractor_anomaly=True)[0]
    assert next_status(PRETEST, n=60, se_b=0.3, r_pb=0.2) == OPERATIONAL
    assert next_status(PRETEST, n=20, se_b=0.3, r_pb=0.2) == PRETEST


def test_quarantine_resolution():
    assert next_status(QUARANTINE, n=100, se_b=0.2, r_pb=0.3, b_before=0.0, b_after=0.2) == OPERATIONAL
    assert next_status(QUARANTINE, n=100, se_b=0.2, r_pb=0.3, b_before=0.0, b_after=-0.45) == RETIRED
    assert next_status(QUARANTINE, n=60, se_b=0.2, r_pb=0.3, b_before=0.0, b_after=0.0) == QUARANTINE


def test_quarantine_recalibration_uses_wide_prior_and_resolves_status():
    """Слив метода: семейство стало легче на 0.6. Узкий приор прошлой калибровки (σ_b = SE_b ≈ 0.1)
    стянул бы оценку к старой b и спрятал сдвиг (|Δb| < 0.3 → назад в бой); перекалибровка в карантине
    идёт с широким приором (§2.4: 100 ответов → OPERATIONAL при |Δb| < 0.3, иначе RETIRED)."""
    n_runs, masked, retired, kept = 40, 0, 0, 0
    for seed in range(n_runs):
        rng = random.Random(seed)
        leaked = fam("leaked", b_prior=0.5, sigma=0.1, status=QUARANTINE)
        stable = fam("stable", b_prior=0.0, sigma=0.1, status=QUARANTINE)
        responses = {}
        for f, b_true in (("leaked", -0.1), ("stable", 0.05)):
            th, xs = simulate_responses(b_true, 100, rng)
            responses[f] = list(zip(th, xs, strict=True))
        tight = calibrate_family(leaked, SPEC, *zip(*responses["leaked"], strict=True))
        masked += abs(tight.delta_b) < 0.3  # именно это маскировало бы утечку
        rep = calibration_run([leaked, stable], responses, SPEC)
        by = {r["family_id"]: r for r in rep["families"]}
        assert by["leaked"]["sigma_prior"] == 0.8
        retired += by["leaked"]["next_status"] == RETIRED
        kept += by["stable"]["next_status"] == OPERATIONAL
    assert masked / n_runs >= 0.95
    assert retired / n_runs >= 0.85
    assert kept / n_runs >= 0.75


def test_zero_sigma_prior_is_fixed_not_crash():
    rng = random.Random(9)
    th, xs = simulate_responses(0.3, 60, rng)
    res = calibrate_family(fam("fixed", sigma=0.0), SPEC, th, xs)
    assert not res.updated and res.reason == "fixed"


def test_calibration_run_report():
    rng = random.Random(6)
    fams = [fam("a", b_prior=0.0), fam("b", b_prior=0.5, is_anchor=True), fam("p", b_prior=-0.5, status=PRETEST)]
    responses = {}
    for f, b_true in (("a", 0.6), ("b", 0.5), ("p", -0.2)):
        th, xs = simulate_responses(b_true, 120, rng)
        responses[f] = list(zip(th, xs, strict=True))
    rep = calibration_run(fams, responses, SPEC)
    assert rep["families_updated"] == 2 and rep["n_responses"] == 360
    by = {r["family_id"]: r for r in rep["families"]}
    assert by["b"]["reason"] == "anchor"
    assert by["a"]["b"] > 0.0
    assert by["p"]["next_status"] in (OPERATIONAL, PRETEST) and "r_pb" in by["p"]
