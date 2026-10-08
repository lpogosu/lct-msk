"""Мониторы психометрического качества заданий: DIF (Мантель–Хензель), дрейф сложности, экспозиция (Симпсон–Хеттер).

Чистые функции без БД и без случайности: их вызывают лаборатория (app/services/testing/quality.py), ночная задача
калибровки и демонстрация на синтетике (eval/fairness.py). Все пороги — config/testing.yaml → quality.

Источники (описание — docs/TESTING_MECHANISM.md, «Качество заданий»):
- Holland & Thayer (1988), «Differential item performance and the Mantel–Haenszel procedure»: α_MH и шкала
  дельта ETS, MH D-DIF = −2,35·ln α_MH. Отрицательная дельта — задание тяжелее для фокальной группы.
- Классы ETS A/B/C: A — |Δ| < 1 или различие незначимо; C — |Δ| ≥ 1,5 и значимо больше 1; остальное — B
  (Zwick, Donoghue & Grima 1993; Paek & Holland 2015 уточняют проверку значимости для C).
- Дисперсия ln α_MH — Robins, Breslow & Greenland (1986); χ² MH с поправкой на непрерывность — Mantel & Haenszel (1959).
- Sympson & Hetter (1985): задание, выбранное CAT, предъявляется с вероятностью K_i; K_i подбирают итерациями
  симуляции: K_i ← min(1, r_max / P(S_i)), где P(S_i) — доля сессий, в которых задание было выбрано (до фильтра K).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from functools import lru_cache
from typing import Any

from app.core.configs import load_config
from app.testing.irt import ItemParams, prob

CLASS_A, CLASS_B, CLASS_C = "A", "B", "C"
NO_DATA = "n/a"


# --------------------------------------------------------------------------- конфиг


@dataclass(frozen=True)
class DifConfig:
    delta_scale: float
    a_max: float
    c_min: float
    z_c: float
    chi2_crit: float
    stratum_width: float
    stratum_min: float
    stratum_max: float
    min_group: int
    min_total: int


@dataclass(frozen=True)
class DriftConfig:
    min_n: int
    threshold_b: float
    z: float


@dataclass(frozen=True)
class ExposureConfig:
    r_max: float
    sim_sessions: int
    sim_iterations: int
    verify_sessions: int
    k_min: float


@dataclass(frozen=True)
class QualityConfig:
    dif: DifConfig
    drift: DriftConfig
    exposure: ExposureConfig
    raw: Mapping[str, Any]

    @classmethod
    def from_config(cls, testing: Mapping[str, Any] | None = None) -> QualityConfig:
        q = (testing if testing is not None else load_config("testing"))["quality"]
        d, dr, ex = q["dif"], q["drift"], q["exposure"]
        return cls(
            dif=DifConfig(
                delta_scale=float(d["delta_scale"]),
                a_max=float(d["a_max"]),
                c_min=float(d["c_min"]),
                z_c=float(d["z_c"]),
                chi2_crit=float(d["chi2_crit"]),
                stratum_width=float(d["stratum_width"]),
                stratum_min=float(d["stratum_min"]),
                stratum_max=float(d["stratum_max"]),
                min_group=int(d["min_group"]),
                min_total=int(d["min_total"]),
            ),
            drift=DriftConfig(min_n=int(dr["min_n"]), threshold_b=float(dr["threshold_b"]), z=float(dr["z"])),
            exposure=ExposureConfig(
                r_max=float(ex["r_max"]),
                sim_sessions=int(ex["sim_sessions"]),
                sim_iterations=int(ex["sim_iterations"]),
                verify_sessions=int(ex["verify_sessions"]),
                k_min=float(ex["k_min"]),
            ),
            raw=q,
        )


@lru_cache(maxsize=1)
def load_quality_config() -> QualityConfig:
    return QualityConfig.from_config()


# --------------------------------------------------------------------------- DIF: Мантель–Хензель


def _r(x: float | None, nd: int = 3) -> float | None:
    return None if x is None else round(x, nd)


@dataclass(frozen=True)
class MHResult:
    """Итог MH по одному заданию и одной паре групп. Δ < 0 — тяжелее для фокальной группы."""

    n_ref: int
    n_focal: int
    n_strata: int
    alpha: float | None
    delta: float | None
    se_delta: float | None
    chi2: float | None
    dif_class: str  # A | B | C | n/a (мало данных)

    def to_dict(self) -> dict[str, Any]:
        if self.delta is None or self.dif_class in (CLASS_A, NO_DATA):
            direction = None
        else:
            direction = "тяжелее для фокальной группы" if self.delta < 0 else "легче для фокальной группы"
        return {
            "n_ref": self.n_ref,
            "n_focal": self.n_focal,
            "n_strata": self.n_strata,
            "alpha": _r(self.alpha),
            "delta": _r(self.delta),
            "se_delta": _r(self.se_delta),
            "chi2": _r(self.chi2, 2),
            "dif_class": self.dif_class,
            "direction": direction,
        }


def mh_dif(tables: Iterable[tuple[int, int, int, int]], cfg: DifConfig | None = None) -> MHResult:
    """MH по таблицам 2×2 в стратах. Таблица k: (A, B, C, D) = (опорная верно, опорная неверно, фокальная верно,
    фокальная неверно). Страты с N < 2 или пустой группой в оценку не входят."""
    cfg = cfg or load_quality_config().dif
    sum_r = sum_s = 0.0  # R = Σ A·D/N, S = Σ B·C/N
    p_r = ps_qr = q_s = 0.0  # слагаемые дисперсии Робинса–Бреслоу–Гринланда
    sum_a = sum_e = sum_v = 0.0  # χ² MH: Σ A, Σ E(A), Σ Var(A)
    n_ref = n_foc = used = 0
    for a, b, c, d in tables:
        n_ref += a + b
        n_foc += c + d
        n = a + b + c + d
        if n < 2 or (a + b) == 0 or (c + d) == 0:
            continue
        used += 1
        r_k, s_k = a * d / n, b * c / n
        p_k, q_k = (a + d) / n, (b + c) / n
        sum_r += r_k
        sum_s += s_k
        p_r += p_k * r_k
        ps_qr += p_k * s_k + q_k * r_k
        q_s += q_k * s_k
        m1, m0 = a + c, b + d
        sum_a += a
        sum_e += (a + b) * m1 / n
        sum_v += (a + b) * (c + d) * m1 * m0 / (n * n * (n - 1))
    if n_ref < cfg.min_group or n_foc < cfg.min_group or n_ref + n_foc < cfg.min_total:
        return MHResult(n_ref, n_foc, used, None, None, None, None, NO_DATA)
    if sum_r <= 0 or sum_s <= 0:
        # Нулевая клетка во всех стратах: α не определена — честно «нет данных», а не бесконечная дельта
        return MHResult(n_ref, n_foc, used, None, None, None, None, NO_DATA)
    alpha = sum_r / sum_s
    delta = -cfg.delta_scale * math.log(alpha)
    var_ln = p_r / (2 * sum_r**2) + ps_qr / (2 * sum_r * sum_s) + q_s / (2 * sum_s**2)
    se = cfg.delta_scale * math.sqrt(var_ln)
    chi2 = (max(0.0, abs(sum_a - sum_e) - 0.5) ** 2 / sum_v) if sum_v > 0 else 0.0
    return MHResult(n_ref, n_foc, used, alpha, delta, se, chi2, classify_ets(delta, se, chi2, cfg))


def classify_ets(delta: float, se: float, chi2: float, cfg: DifConfig | None = None) -> str:
    """A: |Δ| < 1 или χ² незначим. C: |Δ| ≥ 1,5 и (|Δ| − 1)/SE > 1,645 (значимо больше 1). Иначе B."""
    cfg = cfg or load_quality_config().dif
    ad = abs(delta)
    if ad < cfg.a_max or chi2 < cfg.chi2_crit:
        return CLASS_A
    if ad >= cfg.c_min and se > 0 and (ad - cfg.a_max) / se > cfg.z_c:
        return CLASS_C
    return CLASS_B


def stratum_of(theta: float, cfg: DifConfig | None = None) -> int:
    """Номер страты по уровню (критерий сопоставления — апостериорный θ̂ сессии: в адаптивном тесте суммарного
    балла нет, у разных людей разные задания)."""
    cfg = cfg or load_quality_config().dif
    t = min(max(theta, cfg.stratum_min), cfg.stratum_max - 1e-9)
    return int((t - cfg.stratum_min) // cfg.stratum_width)


def mh_from_records(records: Iterable[tuple[float, bool, bool]], cfg: DifConfig | None = None) -> MHResult:
    """records: (θ̂ сессии, фокальная ли группа, верно ли)."""
    cfg = cfg or load_quality_config().dif
    cells: dict[int, list[int]] = {}
    for theta, focal, ok in records:
        c = cells.setdefault(stratum_of(theta, cfg), [0, 0, 0, 0])
        c[(2 if focal else 0) + (0 if ok else 1)] += 1
    return mh_dif(((v[0], v[1], v[2], v[3]) for _, v in sorted(cells.items())), cfg)


# --------------------------------------------------------------------------- дрейф сложности


@dataclass(frozen=True)
class DriftEstimate:
    family_id: str
    n: int
    observed_p: float | None
    expected_p: float | None
    d_b: float | None  # эмпирическая сложность − калиброванная b, логиты (< 0 — стало легче: возможная утечка)
    se: float | None
    lo: float | None
    hi: float | None
    flag: str  # drift | watch | ok | n/a

    def to_dict(self) -> dict[str, Any]:
        return {
            "family_id": self.family_id,
            "n": self.n,
            "observed_p": _r(self.observed_p),
            "expected_p": _r(self.expected_p),
            "d_b": _r(self.d_b),
            "se": _r(self.se),
            "ci_lo": _r(self.lo),
            "ci_hi": _r(self.hi),
            "flag": self.flag,
        }


def estimate_drift(
    family_id: str,
    thetas: Sequence[float],
    correct: Sequence[bool],
    item: ItemParams,
    cfg: DriftConfig | None = None,
) -> DriftEstimate:
    """Сдвиг сложности d: решение Σ P(θⱼ; b + d) = Σ yⱼ (метод моментов при известных θⱼ, бисекция) и 95% ДИ
    по информации Фишера по d. θⱼ — апостериор отвечавшего без этого ответа (LOO), как в калибровке (§3.9):
    его шум слегка сжимает оценку к нулю, т. е. флаг консервативен."""
    cfg = cfg or load_quality_config().drift
    n = len(thetas)
    if n < cfg.min_n:
        return DriftEstimate(family_id, n, None, None, None, None, None, None, "n/a")
    y = sum(1 for c in correct if c)

    def total(d: float) -> float:
        it = replace(item, b=item.b + d)
        return float(sum(prob(t, it) for t in thetas))

    lo_d, hi_d = -4.0, 4.0
    for _ in range(60):  # total(d) убывает по d
        mid = (lo_d + hi_d) / 2
        if total(mid) > y:
            lo_d = mid
        else:
            hi_d = mid
    d = (lo_d + hi_d) / 2
    it = replace(item, b=item.b + d)
    info = 0.0
    for t in thetas:
        p = float(prob(t, it))
        s = (p - item.c) / (1 - item.c) if item.c < 1 else p
        dp = (1 - item.c) * item.a * s * (1 - s)
        if 0 < p < 1:
            info += dp * dp / (p * (1 - p))
    se = 1 / math.sqrt(info) if info > 0 else float("inf")
    lo, hi = d - cfg.z * se, d + cfg.z * se
    exp_p = total(0.0) / n
    # «drift» — только в сторону «стало легче» (признак утечки). Рост сложности — «watch»: при массовой утечке он
    # возникает у остальных заданий как артефакт (знающие завышают свой уровень), это не повод карантина.
    if d <= -cfg.threshold_b and hi < 0:
        flag = "drift"
    elif abs(d) >= cfg.threshold_b:
        flag = "watch"  # большой сдвиг без подтверждения: ДИ накрывает 0 или сдвиг вверх
    else:
        flag = "ok"
    return DriftEstimate(family_id, n, y / n, exp_p, d, se, lo, hi, flag)


# --------------------------------------------------------------------------- экспозиция и Симпсон–Хеттер


def exposure_rates(sessions: Iterable[Iterable[str]]) -> dict[str, float]:
    """Экспозиция семейства = доля сессий, в которых оно предъявлено (в сессии считается один раз)."""
    counts: dict[str, int] = {}
    n = 0
    for fams in sessions:
        n += 1
        for f in set(fams):
            counts[f] = counts.get(f, 0) + 1
    return {f: c / n for f, c in counts.items()} if n else {}


def sympson_hetter_update(
    k: Mapping[str, float], p_selected: Mapping[str, float], r_max: float, k_min: float = 0.0
) -> dict[str, float]:
    """Один шаг итерации: K_i ← min(1, r_max / P(S_i)), если P(S_i) > r_max, иначе 1.
    P(S_i) — вероятность, что задание было ВЫБРАНО (до фильтра K) при действующих K на этом шаге; предъявляется
    оно с вероятностью K_i, значит P(A_i) = K_i·P(S_i), цель P(A_i) ≤ r_max. k_min не даёт «выключить» задание.
    В результат попадают только K < 1 (отсутствие записи = 1)."""
    out: dict[str, float] = {}
    for f, p in p_selected.items():
        new = r_max / p if p > r_max else 1.0
        out[f] = round(min(1.0, max(k_min, new)), 4)
    for f, v in k.items():
        out.setdefault(f, v)
    return {f: v for f, v in out.items() if v < 1.0}
