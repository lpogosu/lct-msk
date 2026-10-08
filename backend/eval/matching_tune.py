"""Подбор параметров Score на СИНТЕТИЧЕСКИХ брифах и ДРУГОЙ популяции (защита от подгонки под test-брифы).

Зачем: p@10 на 20 test-брифах шумит (±0,13), а менять веса, глядя на них, нельзя (VALIDATION_PLAN: веса на
test не подбираем). Поэтому настройка идёт на брифах `synth_briefs` (сид настройки) и популяции с другим
сидом, а b01–b40 и основная популяция остаются отложенной выборкой, которую смотрят один раз после выбора.
Скрипт воспроизводим: `uv run python -m eval.matching_tune --pop-seed 777 --n 120`.
Оракул — тот же `matching_eval.oracle_rel` (скрытая истина генератора), в бриф-генератор попадают только поля gold.
"""

from __future__ import annotations

import argparse
import statistics
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.matching.scoring import ScoreResult, score_candidate
from eval import matching_eval as ME
from eval.common import stream
from eval.population import SPEC_SKILLS

TUNE_SEED = 777
SPEC_MIX = (("backend_python", 0.5), ("frontend_js", 0.4), ("fullstack", 0.1))
GRADE_MIX = (("intern", 0.1), ("junior", 0.25), ("middle", 0.35), ("senior", 0.3))
CITIES = ("moscow", "spb", "kazan", "novosibirsk", "ekaterinburg", "nizhny_novgorod", "samara", "krasnodar")
FORMAT_SETS = (("remote",), ("office",), ("hybrid",), ("remote", "office"), ("office", "hybrid"), ("hybrid", "remote"))


def _pick(rng: Any, pairs: Sequence[tuple[Any, float]]) -> Any:
    x, acc = rng.random() * sum(w for _, w in pairs), 0.0
    for v, w in pairs:
        acc += w
        if x <= acc:
            return v
    return pairs[-1][0]


def synth_briefs(n: int, pop: Any, seed: int = TUNE_SEED) -> list[ME.Brief]:
    """Бриф-ярлыки как у человека: 2–5 must из типового стека специализации, 0–6 nice, вилка около медианы грейда."""
    rng = stream(f"synth:{seed}", seed)
    med: dict[str, float] = {}
    for g in ("intern", "junior", "middle", "senior"):
        v = sorted(c.salary_min for c in pop.candidates if c.truth.grade == g and c.salary_min)
        med[g] = float(v[len(v) // 2])
    out = []
    for i in range(n):
        spec, grade = _pick(rng, SPEC_MIX), _pick(rng, GRADE_MIX)
        src = SPEC_SKILLS["backend_python" if spec == "fullstack" else spec]
        names = [s for s, _ in src]
        core = names[:1] if spec != "fullstack" else ["python", "react"]
        rest = [s for s in names if s not in core]
        rng.shuffle(rest)
        k_must = rng.randint(2, 5)
        must = (core + rest)[:k_must]
        nice = rest[k_must : k_must + rng.randint(0, 5)]
        to = round(med[grade] * rng.uniform(0.9, 1.35) / 10_000) * 10_000
        frm = round(to * rng.uniform(0.75, 0.9) / 10_000) * 10_000
        fmts = list(rng.choice(FORMAT_SETS))
        city = None if fmts == ["remote"] else (rng.choice(CITIES[:2]) if rng.random() < 0.6 else rng.choice(CITIES))
        gold = {"spec": spec, "grade": grade, "must": must, "nice": nice, "salary_from": frm, "salary_to": to,
                "salary_net": rng.random() < 0.7, "formats": fmts, "city": city}
        out.append(ME.Brief(f"s{i + 1:03d}", "tune", "synthetic", "synth", "", None, gold))
    return out


@dataclass
class BriefData:
    brief: ME.Brief
    ids: list[str]
    x: np.ndarray  # n×5 (T,K,F,A,U)
    g: np.ndarray
    cov: np.ndarray
    sal: np.ndarray
    fmt: np.ndarray
    theta: np.ndarray
    rel: np.ndarray  # bool


def precompute(world: ME.World, briefs: Sequence[ME.Brief], *, e2e: bool = False, conditions: bool = True) -> list[BriefData]:
    ctx = ME._ctx(world)
    out = []
    for b in briefs:
        if e2e:
            r = ME.run_brief(b, world)
            spec, cats = r.spec, r.categories
        else:
            spec, cats = ME.gold_brief_spec(b), ME.gold_categories(b, world)
        rels = world.rels(b.gold, conditions=conditions)
        rs: list[ScoreResult] = [score_candidate(c, spec, ctx) for c in world.pool if (c.spec, c.grade) in set(cats)]
        # fullstack-кандидат приходит в нескольких категориях; в рейтинге он один раз — берём лучший Score
        seen: dict[str, ScoreResult] = {}
        for r in rs:
            if r.candidate_id not in seen or r.score > seen[r.candidate_id].score:
                seen[r.candidate_id] = r
        rs = sorted(seen.values(), key=lambda r: r.candidate_id)
        out.append(BriefData(
            b, [r.candidate_id for r in rs],
            np.array([[r.x[k] for k in "TKFAU"] for r in rs]), np.array([r.G for r in rs]), np.array([r.Cov for r in rs]),
            np.array([r.u.sal_fit for r in rs]), np.array([r.u.fmt_fit for r in rs]),
            np.array([r.features.theta or 0.0 for r in rs]),
            np.array([rels[r.candidate_id] >= ME.RELEVANT for r in rs]),
        ))
    return out


Formula = Callable[[BriefData], np.ndarray]


def p10(d: BriefData, score: np.ndarray, k: int = 10) -> float:
    order = np.lexsort((np.array(d.ids), -score))  # детерминированный тай-брейк по id
    return float(d.rel[order[:k]].mean()) if len(order) else 0.0


def mean_p10(data: Sequence[BriefData], f: Formula) -> float:
    return statistics.fmean(p10(d, f(d)) for d in data)


def formula(w: Mapping[str, float], *, gate: float = 0.0) -> Formula:
    """Score = G·Cov·Hгейт·Σ w·x. gate = 0 — прежняя формула; gate > 0 — доля потерь за жёсткое несоответствие условий."""
    wv = np.array([w[k] for k in "TKFAU"])

    def f(d: BriefData) -> np.ndarray:
        base = d.g * d.cov * (d.x @ wv)
        if gate > 0:
            base = base * (1 - gate * (1 - d.sal)) * (1 - gate * (1 - d.fmt))
        return base

    return f


def main() -> None:  # pragma: no cover
    import pickle

    ap = argparse.ArgumentParser()
    ap.add_argument("--pop", required=True)
    ap.add_argument("--n", type=int, default=120)
    a = ap.parse_args()
    pop = pickle.load(open(a.pop, "rb"))  # noqa: S301 — свой же кэш популяции
    world = ME.World.build(pop)
    data = precompute(world, synth_briefs(a.n, pop))
    base = world.cfg.weights()
    print("base", mean_p10(data, formula(base)))


if __name__ == "__main__":
    main()
