"""Дообучение параметров семейств по мере накопления ответов (фаза 1, MVP).

Источник: _internal/design/spec-testing.md §3.9, §2.4 (жизненный цикл семейства).
MAP по b при известных LOO-θ̂ кандидатов:
    ℓ(b) = Σ[x ln P + (1−x) ln(1−P)] − (b − b_prior)²/(2σ_b²),  Ньютон ≤ 25 итераций,  SE_b = (−ℓ'')^(−1/2).
Апостериор становится новым приором; якоря шкалы не трогаем. Выданные грейды не пересчитываются
(в сессии хранится params_version).
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.configs import load_config
from app.testing.cat import OPERATIONAL, PRETEST, QUARANTINE, RETIRED, FamilyMeta, ScaleParam
from app.testing.irt import Scale, a_eff, load_scale

_EPS = 1e-9


@dataclass(frozen=True)
class CalibrationConfig:
    min_new_responses: int = 30
    newton_max_iter: int = 25
    newton_tol: float = 1e-6
    newton_max_step: float = 1.0
    promote_min_n: int = 50
    promote_max_se_b: float = 0.35
    promote_min_r_pb: float = 0.15
    quarantine_recal_n: int = 100
    quarantine_max_delta_b: float = 0.3
    quarantine_prior_sd: float = 0.8

    @classmethod
    def from_config(cls, testing: dict[str, Any] | None = None) -> CalibrationConfig:
        c = (testing if testing is not None else load_config("testing"))["calibration"]
        p = c["promote"]
        return cls(
            min_new_responses=int(c["min_new_responses"]),
            newton_max_iter=int(c["newton_max_iter"]),
            newton_tol=float(c["newton_tol"]),
            newton_max_step=float(c.get("newton_max_step", 1.0)),
            promote_min_n=int(p["min_n"]),
            promote_max_se_b=float(p["max_se_b"]),
            promote_min_r_pb=float(p["min_r_pb"]),
            quarantine_recal_n=int(c["quarantine_recal_n"]),
            quarantine_max_delta_b=float(c["quarantine_max_delta_b"]),
            quarantine_prior_sd=float(c.get("quarantine_prior_sd", 0.8)),
        )


@lru_cache(maxsize=1)
def load_calibration_config() -> CalibrationConfig:
    return CalibrationConfig.from_config()


@dataclass(frozen=True)
class MapResult:
    b: float
    se_b: float
    iterations: int
    converged: bool


def map_b(
    thetas: Sequence[float],
    xs: Sequence[bool | int],
    *,
    b_prior: float,
    sigma_b: float,
    c: float = 0.0,
    a: float,
    max_iter: int = 25,
    tol: float = 1e-6,
    max_step: float = 1.0,
) -> MapResult:
    """MAP-оценка b методом Ньютона.

    Если наблюдаемая ℓ'' ≥ 0 (бывает при c > 0 вдали от решения), шаг делается по ожидаемой
    информации (скоринг Фишера) — она всегда отрицательно определена.
    """
    th = np.asarray(thetas, dtype=float)
    x = np.asarray(xs, dtype=float)
    inv_var = 1.0 / sigma_b**2
    b = float(b_prior)

    def derivs(b: float) -> tuple[float, float, float]:
        s = 1.0 / (1.0 + np.exp(-np.clip(a * (th - b), -35, 35)))
        p = np.clip(c + (1 - c) * s, _EPS, 1 - _EPS)
        dp = -(1 - c) * a * s * (1 - s)  # dP/db
        d2p = (1 - c) * a * a * s * (1 - s) * (1 - 2 * s)  # d²P/db²
        dl_dp = x / p - (1 - x) / (1 - p)
        d2l_dp2 = -x / p**2 - (1 - x) / (1 - p) ** 2
        g = float(np.sum(dl_dp * dp)) - (b - b_prior) * inv_var
        h_obs = float(np.sum(d2l_dp2 * dp**2 + dl_dp * d2p)) - inv_var
        h_exp = -float(np.sum(dp**2 / (p * (1 - p)))) - inv_var
        return g, h_obs, h_exp

    converged = False
    iterations = 0
    while iterations < max_iter:
        iterations += 1
        g, h_obs, h_exp = derivs(b)
        h = h_obs if h_obs < 0 else h_exp
        step = -g / h
        step = max(-max_step, min(max_step, step))
        b += step
        if abs(step) < tol:
            converged = True
            break
    _, h_obs, h_exp = derivs(b)
    h = h_obs if h_obs < 0 else h_exp
    return MapResult(b=b, se_b=1.0 / math.sqrt(-h), iterations=iterations, converged=converged)


@dataclass(frozen=True)
class CalibrationResult:
    family_id: str
    spec: str
    b_prior: float
    sigma_prior: float
    b: float
    se_b: float
    n: int
    updated: bool
    reason: str
    iterations: int = 0

    @property
    def delta_b(self) -> float:
        return self.b - self.b_prior

    @property
    def new_param(self) -> ScaleParam:
        """Апостериор становится новым приором (b, σ_b = SE_b)."""
        return ScaleParam(b=self.b, sigma_b=self.se_b)

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "spec": self.spec,
            "b_prior": round(self.b_prior, 4),
            "sigma_prior": round(self.sigma_prior, 4),
            "b": round(self.b, 4),
            "se_b": round(self.se_b, 4),
            "delta_b": round(self.delta_b, 4),
            "n": self.n,
            "updated": self.updated,
            "reason": self.reason,
            "iterations": self.iterations,
        }


def calibrate_family(
    meta: FamilyMeta,
    spec: str,
    thetas_loo: Sequence[float],
    xs: Sequence[bool | int],
    *,
    scale: Scale | None = None,
    cfg: CalibrationConfig | None = None,
    prior_sd: float | None = None,
) -> CalibrationResult:
    """Фаза 1 для одного семейства на шкале spec. Якоря и семейства с < 30 новыми ответами не обновляются.

    prior_sd — переопределить ширину приора (перекалибровка семейства в карантине, §2.4).
    """
    scale = scale or load_scale()
    cfg = cfg or load_calibration_config()
    prior = meta.scale_param(spec, scale)
    sigma = prior.sigma_b if prior_sd is None else prior_sd
    n = len(xs)
    if meta.is_anchor:
        return CalibrationResult(meta.id, spec, prior.b, prior.sigma_b, prior.b, prior.sigma_b, n, False, "anchor")
    if sigma <= 0:
        # σ_b = 0 — параметр зафиксирован вручную: MAP вырожден (деление на σ_b²)
        return CalibrationResult(meta.id, spec, prior.b, prior.sigma_b, prior.b, prior.sigma_b, n, False, "fixed")
    if n < cfg.min_new_responses:
        return CalibrationResult(
            meta.id, spec, prior.b, prior.sigma_b, prior.b, prior.sigma_b, n, False, "not_enough_responses"
        )
    # Клоны остаются разными: крутизна семейства с учётом σ_inst, без σ_b (его мы и оцениваем)
    a = a_eff(scale.A, 0.0, scale.sigma_inst)
    res = map_b(
        thetas_loo,
        xs,
        b_prior=prior.b,
        sigma_b=sigma,
        c=meta.c,
        a=a,
        max_iter=cfg.newton_max_iter,
        tol=cfg.newton_tol,
        max_step=cfg.newton_max_step,
    )
    return CalibrationResult(
        meta.id,
        spec,
        prior.b,
        sigma,
        res.b,
        res.se_b,
        n,
        True,
        "map" if res.converged else "map_not_converged",
        res.iterations,
    )


def point_biserial(xs: Sequence[bool | int], thetas: Sequence[float]) -> float:
    """r_pb между правильностью ответа и θ̂ (по pretest-слотам: задание не входит в θ̂)."""
    x = np.asarray(xs, dtype=float)
    t = np.asarray(thetas, dtype=float)
    if len(x) < 2 or x.std() == 0 or t.std() == 0:
        return 0.0
    return float(np.corrcoef(x, t)[0, 1])


def promotion_decision(
    n: int, se_b: float, r_pb: float, *, distractor_anomaly: bool = False, cfg: CalibrationConfig | None = None
) -> tuple[bool, str]:
    """PRETEST → OPERATIONAL: n ≥ 50, SE_b ≤ 0.35, r_pb ≥ 0.15 по pretest-слотам, нет аномалий дистракторов."""
    cfg = cfg or load_calibration_config()
    if n < cfg.promote_min_n:
        return False, f"Мало ответов: {n} из {cfg.promote_min_n}"
    if se_b > cfg.promote_max_se_b:
        return False, f"Сложность ещё неточна: SE {se_b:.2f} > {cfg.promote_max_se_b}"
    if r_pb < cfg.promote_min_r_pb:
        return False, f"Задание плохо различает уровни: r_pb {r_pb:.2f} < {cfg.promote_min_r_pb}"
    if distractor_anomaly:
        return False, "Аномалия дистракторов — нужна проверка методистом"
    return True, "Семейство переводится в боевой пул"


def next_status(
    status: str,
    *,
    n: int,
    se_b: float,
    r_pb: float,
    b_before: float | None = None,
    b_after: float | None = None,
    distractor_anomaly: bool = False,
    cfg: CalibrationConfig | None = None,
) -> str:
    """Переходы PRETEST → OPERATIONAL и QUARANTINE → OPERATIONAL | RETIRED (§2.4)."""
    cfg = cfg or load_calibration_config()
    if status == PRETEST:
        ok, _ = promotion_decision(n, se_b, r_pb, distractor_anomaly=distractor_anomaly, cfg=cfg)
        return OPERATIONAL if ok else PRETEST
    if status == QUARANTINE:
        if n < cfg.quarantine_recal_n or b_before is None or b_after is None:
            return QUARANTINE
        return OPERATIONAL if abs(b_after - b_before) < cfg.quarantine_max_delta_b else RETIRED
    return status


def calibration_run(
    families: Sequence[FamilyMeta],
    responses: Mapping[str, Sequence[tuple[float, bool | int]]],
    spec: str,
    *,
    scale: Scale | None = None,
    cfg: CalibrationConfig | None = None,
) -> dict[str, Any]:
    """Ночная задача / кнопка «Лаборатории»: MAP по всем семействам шкалы spec. Возвращает отчёт CalibrationRun."""
    scale = scale or load_scale()
    cfg = cfg or load_calibration_config()
    results = []
    for meta in families:
        if spec not in meta.specs:
            continue
        rows = responses.get(meta.id, ())
        th = [r[0] for r in rows]
        xs = [r[1] for r in rows]
        quarantined = meta.status == QUARANTINE
        res = calibrate_family(
            meta, spec, th, xs, scale=scale, cfg=cfg, prior_sd=cfg.quarantine_prior_sd if quarantined else None
        )
        entry = res.to_dict()
        if meta.status == PRETEST and res.updated:
            r_pb = point_biserial(xs, th)
            entry["r_pb"] = round(r_pb, 4)
            entry["next_status"] = next_status(PRETEST, n=res.n, se_b=res.se_b, r_pb=r_pb, cfg=cfg)
        elif quarantined and res.updated:
            # QUARANTINE → (перекалибровка по 100 ответам) → OPERATIONAL (|Δb| < 0.3) | RETIRED (§2.4)
            entry["next_status"] = next_status(
                QUARANTINE, n=res.n, se_b=res.se_b, r_pb=0.0, b_before=res.b_prior, b_after=res.b, cfg=cfg
            )
        results.append(entry)
    updated = [r for r in results if r["updated"]]
    return {
        "phase": 1,
        "spec": spec,
        "n_responses": sum(r["n"] for r in results),
        "families_updated": len(updated),
        "families": results,
    }
