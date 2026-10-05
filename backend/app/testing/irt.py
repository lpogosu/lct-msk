"""IRT-ядро: модель ответа, информация, EAP на сетке, person-fit.

Источник: _internal/design/spec-testing.md §3.1 (модель), §3.2 (экспертные приоры), §3.3 (EAP).
Чистый Python + numpy, без БД и HTTP. Все параметры — из config/testing.yaml и config/taxonomy.yaml.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import numpy as np

from app.core.configs import load_config

BELOW_INTERN = "below_intern"
BELOW_INTERN_NAME = "Ниже Стажёра"
_EPS = 1e-12


# ---------------------------------------------------------------------------
# Шкала
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Scale:
    """Параметры одной шкалы уровня (одинаковы для всех специализаций в MVP)."""

    A: float
    sigma_inst: float
    prior_mu: float
    prior_sd: float
    grid_min: float
    grid_max: float
    grid_step: float
    cuts: tuple[tuple[str, float], ...]  # (код грейда, нижний порог) по возрастанию
    grade_names: tuple[tuple[str, str], ...]
    score_base: float = 500.0
    score_per_theta: float = 100.0
    interval_z: float = 1.2816
    pos_step: float = 0.35
    sigma_b_agree: float = 0.5
    sigma_b_disagree: float = 0.8
    max_pos_spread: int = 1
    params_version: str = "prior-v1"
    _grid: np.ndarray = field(init=False, repr=False, compare=False)
    _log_prior: np.ndarray = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        n = int(round((self.grid_max - self.grid_min) / self.grid_step)) + 1
        # округление убирает хвосты float (0.0 должен быть ровно 0.0 — пороги лежат на сетке)
        grid = np.round(np.linspace(self.grid_min, self.grid_max, n), 10)
        object.__setattr__(self, "_grid", grid)
        object.__setattr__(self, "_log_prior", _log_normal(grid, self.prior_mu, self.prior_sd))

    # --- грейды ---
    @property
    def grid(self) -> np.ndarray:
        return self._grid

    @property
    def grades(self) -> tuple[str, ...]:
        return tuple(g for g, _ in self.cuts)

    def cut(self, grade: str) -> float:
        for g, c in self.cuts:
            if g == grade:
                return c
        raise KeyError(f"Неизвестный грейд: {grade}")

    def grade_index(self, grade: str) -> int:
        return self.grades.index(grade)

    def next_grade(self, grade: str) -> str | None:
        i = self.grade_index(grade)
        return self.grades[i + 1] if i + 1 < len(self.grades) else None

    def prev_grade(self, grade: str) -> str | None:
        i = self.grade_index(grade)
        return self.grades[i - 1] if i > 0 else None

    def grade_name(self, grade: str | None) -> str:
        if grade is None or grade == BELOW_INTERN:
            return BELOW_INTERN_NAME
        return dict(self.grade_names).get(grade, grade)

    def grade_of(self, theta: float) -> str:
        """Грейд, в полосу которого попадает θ (или below_intern)."""
        best = BELOW_INTERN
        for g, c in self.cuts:
            if theta >= c - 1e-9:
                best = g
        return best

    def band_width(self, grade: str) -> float:
        """Ширина полосы грейда; у верхнего грейда — как у предыдущего (§3.12)."""
        nxt = self.next_grade(grade)
        if nxt is not None:
            return self.cut(nxt) - self.cut(grade)
        prv = self.prev_grade(grade)
        return self.cut(grade) - self.cut(prv) if prv else 1.0

    # --- балл ---
    def score(self, theta: float) -> float:
        return self.score_base + self.score_per_theta * theta

    def score_interval(self, theta: float, se: float) -> tuple[float, float]:
        half = self.interval_z * self.score_per_theta * se
        s = self.score(theta)
        return s - half, s + half

    @classmethod
    def from_config(cls, testing: dict[str, Any] | None = None, taxonomy: dict[str, Any] | None = None) -> Scale:
        testing = testing if testing is not None else load_config("testing")
        taxonomy = taxonomy if taxonomy is not None else load_config("taxonomy")
        sc = testing["scale"]
        ep = testing.get("expert_prior", {})
        grades = sorted(taxonomy["grades"], key=lambda g: g["cut"])
        ss = taxonomy.get("score_scale", {})
        return cls(
            A=float(sc["A"]),
            sigma_inst=float(sc["sigma_inst"]),
            prior_mu=float(sc["prior_mu"]),
            prior_sd=float(sc["prior_sd"]),
            grid_min=float(sc["grid"]["min"]),
            grid_max=float(sc["grid"]["max"]),
            grid_step=float(sc["grid"]["step"]),
            cuts=tuple((g["code"], float(g["cut"])) for g in grades),
            grade_names=tuple((g["code"], g["name"]) for g in grades),
            score_base=float(ss.get("base", 500)),
            score_per_theta=float(ss.get("per_theta", 100)),
            interval_z=float(sc.get("interval_z", 1.2816)),
            pos_step=float(ep.get("pos_step", 0.35)),
            sigma_b_agree=float(ep.get("sigma_b_agree", 0.5)),
            sigma_b_disagree=float(ep.get("sigma_b_disagree", 0.8)),
            max_pos_spread=int(ep.get("max_pos_spread", 1)),
            params_version=str(sc.get("params_version", "prior-v1")),
        )


@lru_cache(maxsize=1)
def load_scale() -> Scale:
    return Scale.from_config()


def _log_normal(x: np.ndarray, mu: float, sd: float) -> np.ndarray:
    return -0.5 * ((x - mu) / sd) ** 2


# ---------------------------------------------------------------------------
# Модель ответа (§3.1)
# ---------------------------------------------------------------------------


def a_eff(A: float, sigma_b: float, sigma_inst: float) -> float:
    """A_eff = A / sqrt(1 + π·A²·(σ_b² + σ_inst²)/8).

    Пробит-аппроксимация свёртки логисты с N(0, σ²): честно учитывает неуверенность в b
    и разброс клонов — тест при неточных параметрах просто длиннее, а SE шире (§3.1).
    """
    return A / math.sqrt(1.0 + math.pi * A * A * (sigma_b**2 + sigma_inst**2) / 8.0)


@dataclass(frozen=True)
class ItemParams:
    b: float
    c: float = 0.0
    a: float = 1.5  # эффективная крутизна A_eff

    @classmethod
    def from_family(cls, b: float, sigma_b: float, c: float, scale: Scale) -> ItemParams:
        return cls(b=float(b), c=float(c), a=a_eff(scale.A, sigma_b, scale.sigma_inst))


def _sigmoid(z: np.ndarray | float) -> np.ndarray | float:
    z = np.clip(z, -35.0, 35.0)
    return 1.0 / (1.0 + np.exp(-z))


def prob(theta: np.ndarray | float, item: ItemParams) -> np.ndarray | float:
    """P = c + (1 − c)·σ(A_eff·(θ − b))."""
    return item.c + (1.0 - item.c) * _sigmoid(item.a * (np.asarray(theta, dtype=float) - item.b))


def information(theta: np.ndarray | float, item: ItemParams) -> np.ndarray | float:
    """I = A_eff² · ((P − c)/(1 − c))² · (1 − P)/P (§3.1)."""
    p = np.clip(prob(theta, item), _EPS, 1 - _EPS)
    return item.a**2 * ((p - item.c) / (1.0 - item.c)) ** 2 * (1.0 - p) / p


def info_at(t: float, item: ItemParams) -> float:
    """Информация Фишера у порога t — критерий выбора задания для классификации (§3.4)."""
    return float(information(t, item))


def loglik(theta: np.ndarray, item: ItemParams, x: int | bool) -> np.ndarray:
    p = np.clip(prob(theta, item), _EPS, 1 - _EPS)
    return np.log(p) if x else np.log1p(-p)


# ---------------------------------------------------------------------------
# Экспертные приоры (§3.2)
# ---------------------------------------------------------------------------


def b_from_expert(target_grade: str, position: float, scale: Scale) -> float:
    """b_prior = cut(g) + 0.35·pos (модифицированный Ангофф)."""
    return scale.cut(target_grade) + scale.pos_step * float(position)


def sigma_b_from_ratings(ratings: Sequence[tuple[str, float]], scale: Scale) -> float | None:
    """σ_b по оценкам экспертов [(грейд, pos), ...].

    0.5 при расхождении ≤ 1 шага pos в одном грейде, иначе 0.8. Расхождение на целый грейд → None
    (семейство уходит на обсуждение, в банк не попадает).
    """
    if not ratings:
        return None
    grades = {g for g, _ in ratings}
    if len(grades) > 1:
        return None
    positions = [p for _, p in ratings]
    spread = max(positions) - min(positions)
    return scale.sigma_b_agree if spread <= scale.max_pos_spread + 1e-9 else scale.sigma_b_disagree


# ---------------------------------------------------------------------------
# Апостериор на сетке (§3.3)
# ---------------------------------------------------------------------------


class Posterior:
    """Ненормированные лог-веса на сетке θ. Приор N(μ, sd²) — по умолчанию общий для всех."""

    __slots__ = ("scale", "logw", "n")

    def __init__(self, scale: Scale, logw: np.ndarray, n: int = 0) -> None:
        self.scale = scale
        self.logw = logw
        self.n = n

    @classmethod
    def prior(cls, scale: Scale, mu: float | None = None, sd: float | None = None) -> Posterior:
        if mu is None and sd is None:
            return cls(scale, scale._log_prior.copy())
        mu = scale.prior_mu if mu is None else mu
        sd = scale.prior_sd if sd is None else sd
        return cls(scale, _log_normal(scale.grid, mu, max(sd, 1e-6)))

    def copy(self) -> Posterior:
        return Posterior(self.scale, self.logw.copy(), self.n)

    def add(self, item: ItemParams, x: int | bool) -> Posterior:
        """Учесть ответ (на месте) и вернуть self."""
        self.logw = self.logw + loglik(self.scale.grid, item, x)
        self.n += 1
        return self

    def remove(self, item: ItemParams, x: int | bool) -> Posterior:
        """Исключить ответ (на месте) — для leave-one-out оценок детекторов."""
        self.logw = self.logw - loglik(self.scale.grid, item, x)
        self.n -= 1
        return self

    @property
    def weights(self) -> np.ndarray:
        w = np.exp(self.logw - self.logw.max())
        return w / w.sum()

    @property
    def mean(self) -> float:
        return float(np.dot(self.scale.grid, self.weights))

    @property
    def sd(self) -> float:
        w = self.weights
        m = float(np.dot(self.scale.grid, w))
        return float(math.sqrt(max(np.dot((self.scale.grid - m) ** 2, w), 0.0)))

    def p_at_least(self, t: float) -> float:
        """P(θ ≥ t) = Σ_{θ_q ≥ t} w_q."""
        w = self.weights
        return float(w[self.scale.grid >= t - 1e-9].sum())

    def class_probs(self) -> dict[str, float]:
        """Вероятности полос грейдов («облако» на экране результата), включая «ниже Стажёра»."""
        w = self.weights
        grid = self.scale.grid
        edges = [c for _, c in self.scale.cuts]
        out: dict[str, float] = {BELOW_INTERN: float(w[grid < edges[0] - 1e-9].sum())}
        for i, (g, c) in enumerate(self.scale.cuts):
            hi = edges[i + 1] if i + 1 < len(edges) else math.inf
            mask = (grid >= c - 1e-9) & (grid < hi - 1e-9)
            out[g] = float(w[mask].sum())
        return out

    def expected(self, item: ItemParams) -> float:
        """Апостериорная вероятность верного ответа (для CUSUM)."""
        return float(np.dot(self.weights, prob(self.scale.grid, item)))

    def summary(self, target_grade: str | None = None) -> dict[str, Any]:
        theta, se = self.mean, self.sd
        lo, hi = self.scale.score_interval(theta, se)
        out: dict[str, Any] = {
            "theta": round(theta, 4),
            "se": round(se, 4),
            "score": round(self.scale.score(theta)),
            "interval80": [round(lo), round(hi)],
            "class_probs": {k: round(v, 4) for k, v in self.class_probs().items()},
        }
        if target_grade is not None:
            out["target_grade"] = target_grade
            out["p_pass"] = round(self.p_at_least(self.scale.cut(target_grade)), 4)
        return out


def eap(
    responses: Iterable[tuple[ItemParams, int | bool]],
    scale: Scale | None = None,
    mu: float | None = None,
    sd: float | None = None,
) -> Posterior:
    """EAP по ответам. Без mu/sd — общий приор N(−0.25, 1.2²) для всех (§3.3)."""
    scale = scale or load_scale()
    post = Posterior.prior(scale, mu, sd)
    for item, x in responses:
        post.add(item, x)
    return post


def loo_means(items: Sequence[ItemParams], xs: Sequence[int | bool], scale: Scale | None = None) -> list[float]:
    """θ̂_{−j}: EAP без j-го ответа, для каждого j (детекторы и калибровка по LOO-θ̂)."""
    scale = scale or load_scale()
    full = eap(zip(items, xs, strict=True), scale)
    out = []
    for item, x in zip(items, xs, strict=True):
        out.append(full.copy().remove(item, x).mean)
    return out


# ---------------------------------------------------------------------------
# Person-fit l_z (§3.10)
# ---------------------------------------------------------------------------


def person_fit_lz(items: Sequence[ItemParams], xs: Sequence[int | bool], theta: float) -> float | None:
    """Стандартизованная лог-правдоподобность l_z = (l0 − E)/√V; None, если V = 0."""
    l0 = e = v = 0.0
    for item, x in zip(items, xs, strict=True):
        p = float(np.clip(prob(theta, item), _EPS, 1 - _EPS))
        q = 1.0 - p
        l0 += math.log(p) if x else math.log(q)
        e += p * math.log(p) + q * math.log(q)
        v += p * q * math.log(p / q) ** 2
    if v <= 0:
        return None
    return (l0 - e) / math.sqrt(v)


# ---------------------------------------------------------------------------
# Свежесть (§3.11)
# ---------------------------------------------------------------------------


def se_with_age(se_last: float, months: float, growth_per_month: float) -> float:
    """SE(t) = sqrt(SE²_last + 0.02·Δмес): без активности уверенность честно падает."""
    return math.sqrt(se_last**2 + growth_per_month * max(months, 0.0))
