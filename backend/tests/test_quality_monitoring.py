"""Мониторы качества заданий (app/testing/monitoring.py, DECISIONS п.31.2): MH на разобранном примере, внедрённый DIF,
флаг дрейфа, Симпсон–Хеттер выключен по умолчанию и не меняет выбор."""

from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from app.testing.cat import load_cat_config
from app.testing.irt import ItemParams, load_scale
from app.testing.monitoring import (
    CLASS_A,
    CLASS_B,
    CLASS_C,
    classify_ets,
    estimate_drift,
    exposure_rates,
    load_quality_config,
    mh_dif,
    mh_from_records,
    sympson_hetter_update,
)
from app.testing.simulate import run_simulated_session
from eval.common import real_bank

# --- MH: разобранный пример (считается руками) -------------------------------------------------------------
# Страты (опорная верно, неверно, фокальная верно, неверно):
#   k1: 30 10 20 20, N = 80  → A·D/N = 600/80 = 7,5   B·C/N = 200/80 = 2,5
#   k2: 40 10 30 20, N = 100 → 800/100 = 8            300/100 = 3
#   k3: 20 20 10 30, N = 80  → 600/80 = 7,5           200/80 = 2,5
# α_MH = (7,5 + 8 + 7,5)/(2,5 + 3 + 2,5) = 23/8 = 2,875;  Δ = −2,35·ln 2,875 ≈ −2,4818
TABLES = [(30, 10, 20, 20), (40, 10, 30, 20), (20, 20, 10, 30)]


def test_mh_worked_example():
    r = mh_dif(TABLES)
    assert r.alpha == pytest.approx(23 / 8)
    assert r.delta == pytest.approx(-2.35 * math.log(23 / 8))
    assert r.delta == pytest.approx(-2.4818, abs=1e-3)
    # дисперсия ln α (Робинс–Бреслоу–Гринланд) по определению, независимой записью
    P = [(a + d) / (a + b + c + d) for a, b, c, d in TABLES]
    Q = [(b + c) / (a + b + c + d) for a, b, c, d in TABLES]
    R = [a * d / (a + b + c + d) for a, b, c, d in TABLES]
    S = [b * c / (a + b + c + d) for a, b, c, d in TABLES]
    var = (
        sum(p * r_ for p, r_ in zip(P, R, strict=True)) / (2 * sum(R) ** 2)
        + sum(p * s + q * r_ for p, q, r_, s in zip(P, Q, R, S, strict=True)) / (2 * sum(R) * sum(S))
        + sum(q * s for q, s in zip(Q, S, strict=True)) / (2 * sum(S) ** 2)
    )
    assert r.se_delta == pytest.approx(2.35 * math.sqrt(var))
    assert r.chi2 is not None and r.chi2 > 3.84
    assert r.dif_class == CLASS_C and r.n_ref == 130 and r.n_focal == 130


def test_mh_no_difference_gives_alpha_one():
    r = mh_dif([(20, 20, 20, 20), (30, 10, 30, 10)])
    assert r.alpha == pytest.approx(1.0) and r.delta == pytest.approx(0.0) and r.dif_class == CLASS_A


def test_mh_too_few_answers_is_no_data():
    assert mh_dif([(5, 5, 5, 5)]).dif_class == "n/a"


def test_ets_classes_thresholds():
    cfg = load_quality_config().dif
    assert (cfg.a_max, cfg.c_min, cfg.delta_scale) == (1.0, 1.5, 2.35)
    assert classify_ets(-0.8, 0.2, 20.0, cfg) == CLASS_A  # |Δ| < 1
    assert classify_ets(-1.8, 0.5, 2.0, cfg) == CLASS_A  # незначимо по χ²
    assert classify_ets(-1.2, 0.2, 9.0, cfg) == CLASS_B  # значимо, но < 1,5
    assert classify_ets(-1.6, 0.5, 9.0, cfg) == CLASS_B  # ≥ 1,5, но не значимо больше 1: (0,6/0,5) = 1,2 < 1,645
    assert classify_ets(1.6, 0.2, 9.0, cfg) == CLASS_C  # (0,6/0,2) = 3 > 1,645; знак не важен


# --- внедрённый DIF на модели Раша --------------------------------------------------------------------------


def _rasch_records(n: int, shift: float, focal_mean: float, seed: int):
    rng = random.Random(seed)
    out = []
    for _ in range(n):
        focal = rng.random() < 0.4
        th = rng.gauss(focal_mean if focal else 0.0, 1.0)
        b = 0.2 + (shift if focal else 0.0)
        p = 1 / (1 + math.exp(-1.5 * (th - b)))
        out.append((th, focal, rng.random() < p))
    return out


def test_planted_dif_is_flagged_c_and_clean_item_is_a():
    biased = mh_from_records(_rasch_records(6000, shift=0.8, focal_mean=0.0, seed=1))
    clean = mh_from_records(_rasch_records(6000, shift=0.0, focal_mean=0.0, seed=2))
    assert biased.dif_class == CLASS_C and biased.delta is not None and biased.delta < -1.5
    assert clean.dif_class == CLASS_A


def test_impact_without_dif_is_not_dif():
    """Фокальная группа слабее на 0,5 логита, но задание одинаково для людей одного уровня → A."""
    r = mh_from_records(_rasch_records(8000, shift=0.0, focal_mean=-0.5, seed=3))
    assert r.dif_class == CLASS_A


# --- дрейф ------------------------------------------------------------------------------------------------


def _drift_data(true_shift: float, n: int, seed: int):
    scale = load_scale()
    item = ItemParams.from_family(0.0, 0.5, 0.0, scale)
    rng = random.Random(seed)
    th = [rng.gauss(0.0, 1.0) for _ in range(n)]
    ys = [rng.random() < 1 / (1 + math.exp(-item.a * (t - (item.b + true_shift)))) for t in th]
    return item, th, ys


def test_drift_flags_easier_item_and_not_stable_one():
    item, th, ys = _drift_data(-0.9, 600, 5)
    e = estimate_drift("f", th, ys, item)
    assert e.flag == "drift" and e.d_b is not None and -1.3 < e.d_b < -0.5 and e.hi is not None and e.hi < 0
    item, th, ys = _drift_data(0.0, 600, 6)
    assert estimate_drift("f", th, ys, item).flag == "ok"


def test_drift_needs_min_answers_and_harder_is_only_watch():
    item, th, ys = _drift_data(-0.9, 20, 7)
    assert estimate_drift("f", th, ys, item).flag == "n/a"
    item, th, ys = _drift_data(0.9, 600, 8)
    assert estimate_drift("f", th, ys, item).flag == "watch"


# --- экспозиция и Симпсон–Хеттер ---------------------------------------------------------------------------


def test_exposure_rates_count_family_once_per_session():
    assert exposure_rates([["a", "a", "b"], ["a"], []]) == {"a": pytest.approx(2 / 3), "b": pytest.approx(1 / 3)}


def test_sh_update_formula():
    k = sympson_hetter_update({}, {"a": 0.8, "b": 0.2, "c": 0.4}, r_max=0.4, k_min=0.05)
    assert k == {"a": 0.5}  # 0,4/0,8; b и c (=r_max) не превышают → K = 1 (в словаре не хранится)
    assert sympson_hetter_update({}, {"a": 0.99}, r_max=0.01, k_min=0.05) == {"a": 0.05}


def _run(cfg, seed=11, theta=0.3):
    res = run_simulated_session(theta, "backend_python", "middle", real_bank(), random.Random(seed), cfg=cfg)
    return [(i["family_id"], i["correct"]) for i in res["items"]], res["trace"]


def test_sh_default_off_is_identical():
    base = load_cat_config()
    assert base.sh_enabled is False and dict(base.sh_k) == {}  # выключено в config/testing.yaml
    for seed in range(5):
        ref, trace_ref = _run(base, seed)
        # K заданы, но флаг выключен → выбор тот же и в трассе нет следов контроля
        with_k = replace(base, sh_k={f: 0.05 for f, _ in ref})
        got, trace = _run(with_k, seed)
        assert got == ref and trace == trace_ref
        assert all("sh_rejected" not in (t.get("reason") or {}) for t in trace)
        # включён, но все K = 1 → генератор расходуется так же: тот же тест
        on = replace(base, sh_enabled=True, sh_k={})
        assert _run(on, seed)[0] == ref


def test_sh_enabled_reduces_exposure_of_limited_family():
    base = load_cat_config()
    fam = "http.idempotency.retry_cart"

    def share(cfg):
        n = 0
        for seed in range(120):
            items, _ = _run(cfg, seed, theta=random.Random(seed).gauss(0, 1))
            n += any(f == fam for f, _ in items)
        return n / 120

    off = share(base)
    on = share(replace(base, sh_enabled=True, sh_k={fam: 0.2}))
    assert off > 0.1 and on < off * 0.6
