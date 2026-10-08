"""`uv run python -m eval.fairness [--quick]` — мониторы качества заданий на синтетике: DIF, дрейф, экспозиция.

Что показывает (DECISIONS п.31.2, docs/TESTING_MECHANISM.md «Качество заданий»):
1. DIF по Мантелю–Хензелю с классами ETS A/B/C. Группы заданы НАМИ (ФСП, регион, режим доступности, экспресс) и
   независимы от уровня, кроме ФСП (там есть реальное различие в уровне — «impact», его MH не принимает за DIF).
   В часть заданий внедрён сдвиг сложности для фокальной группы — отчёт обязан их поймать (проверка метода).
2. Монитор дрейфа: эмпирическая сложность по ответам против калиброванной b, 95% ДИ; внедрена «утечка»
   (часть кандидатов знает ответы 12 семейств) — монитор обязан поднять флаг, на чистой популяции флагов быть не должно.
3. Экспозиция семейств и Симпсон–Хеттер: максимум экспозиции, число семейств выше r_max, потеря точности.

Это проверка метода, а не вывод о реальных людях. Результат — eval/out/quality.json; раздел для docs/VALIDATION.md —
`render_markdown()` (его подставляет eval.report). Все случайные потоки — random.Random(f"{seed}:…").

    uv run python -m eval.fairness                # полный прогон, пишет eval/out/quality.json и раздел 8 в docs/VALIDATION.md
    uv run python -m eval.fairness --quick        # дымовой прогон
    uv run python -m eval.fairness --no-docs
"""

from __future__ import annotations

import argparse
import json
import random
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from app.testing.cat import CatConfig, FamilyMeta, load_cat_config
from app.testing.irt import ItemParams, load_scale
from app.testing.monitoring import (
    CLASS_A,
    CLASS_B,
    CLASS_C,
    NO_DATA,
    QualityConfig,
    estimate_drift,
    exposure_rates,
    load_quality_config,
    mh_from_records,
    sympson_hetter_update,
)
from app.testing.simulate import SimulatedExaminee, run_simulated_session
from eval.common import MASTER_SEED, bank_for, real_bank
from eval.population import Candidate, PopulationConfig, generate_candidate, outcome_from_result

EVAL_DIR = Path(__file__).resolve().parent
DEFAULT_OUT = EVAL_DIR / "out" / "quality.json"
DOCS_PATH = EVAL_DIR.parent.parent / "docs" / "VALIDATION.md"
MARK_BEGIN, MARK_END = "<!-- quality:begin -->", "<!-- quality:end -->"
SPECS = ("backend_python", "frontend_js")
PLANT_SPEC = "backend_python"
GROUP_LABEL = {"fsp": "без истории ФСП", "region": "регионы", "a11y": "режим доступности", "express": "экспресс-версия"}


# --------------------------------------------------------------------------- сессии


@dataclass
class SessRec:
    index: int
    spec: str
    groups: Mapping[str, bool]  # атрибут → фокальная ли группа
    theta_true: float
    theta_hat: float
    se: float
    n_total: int
    correct_decision: bool
    items: list[tuple[str, bool]]  # (семейство, верно) — только зачётные
    shown: set[str]
    selected: set[str]  # предъявленные + отклонённые фильтром Симпсона–Хеттера
    loo: list[tuple[str, float, bool]] = field(default_factory=list)  # (семейство, θ без ответа, верно)


def group_flags(cand: Candidate, seed: int, rates: Mapping[str, float]) -> dict[str, bool]:
    """Атрибуты кандидата. Свой поток на кандидата: результат не зависит от порядка и размера популяции."""
    r = random.Random(f"{seed}:grp:{cand.index}")
    return {
        "fsp": not cand.has_fsp,
        "region": r.random() < rates["region"],
        "a11y": r.random() < rates["a11y"],
        "express": r.random() < rates["express"],
    }


def make_candidates(n: int, seed: int, rates: Mapping[str, float]) -> list[tuple[Candidate, dict[str, bool]]]:
    pc = PopulationConfig(n=n, seed=seed, run_tests=False)
    out = []
    for i in range(n):
        c = generate_candidate(i, pc, bank=real_bank())
        if c.claimed_spec in SPECS:
            out.append((c, group_flags(c, seed, rates)))
    return out


def run_sessions(
    cands: Sequence[tuple[Candidate, Mapping[str, bool]]],
    *,
    seed: int,
    tag: str,
    spec: str | None = None,
    examinee_for: Callable[[Candidate, Mapping[str, bool], FamilyMeta | None], SimulatedExaminee] | None = None,
    cfg: CatConfig | None = None,
    with_loo: bool = False,
) -> list[SessRec]:
    bank = real_bank()
    by_id = {f.id: f for f in bank}
    out: list[SessRec] = []
    for cand, groups in cands:
        sp = cand.claimed_spec
        if sp not in SPECS or (spec is not None and sp != spec):
            continue
        ex = (
            examinee_for(cand, groups, None)
            if examinee_for
            else SimulatedExaminee(theta=cand.truth.theta, module_theta=cand.truth.module_theta)
        )
        res = run_simulated_session(
            cand.truth.theta, sp, cand.claimed_grade, bank, random.Random(f"{seed}:{tag}:{cand.index}"),
            module_theta=cand.truth.module_theta, examinee=ex, cfg=cfg, session_id=f"{tag}-{cand.index}",
        )
        items = [(it["family_id"], bool(it["correct"])) for it in res["items"] if not it["is_pretest"]]
        shown = {it["family_id"] for it in res["items"]}
        rejected = {f for t in res["trace"] for f in (t.get("reason") or {}).get("sh_rejected", ())}
        loo: list[tuple[str, float, bool]] = []
        if with_loo:
            oc = outcome_from_result(res, sp, by_id)
            loo = [(r.family_id, r.loo_mean, r.correct) for r in oc.responses if not r.is_pretest]
        out.append(
            SessRec(
                cand.index, sp, groups, cand.truth.theta, float(res["theta_hat_final"]), float(res["se_final"]),
                int(res["n_total"]), bool(res["correct_decision"]), items, shown, shown | rejected, loo,
            )
        )
    return out


def _bias_examinee(shifts: Mapping[tuple[str, str], float], spec: str) -> Callable[..., SimulatedExaminee]:
    """Кандидат фокальной группы g получает сдвинутую сложность b + shift для внедрённого семейства (g, family)."""
    scale = load_scale()
    by_id = {f.id: f for f in real_bank()}

    def make(cand: Candidate, groups: Mapping[str, bool], _meta: FamilyMeta | None) -> SimulatedExaminee:
        tb = {
            fam: by_id[fam].scale_param(spec, scale).b + sh
            for (g, fam), sh in shifts.items()
            if groups.get(g) and cand.claimed_spec == spec
        }
        return SimulatedExaminee(theta=cand.truth.theta, module_theta=cand.truth.module_theta, true_b=tb or None)

    return make


# --------------------------------------------------------------------------- 1. DIF


def _exposure_rank(sessions: Sequence[SessRec]) -> list[str]:
    rates = exposure_rates(s.shown for s in sessions)
    return [f for f, _ in sorted(rates.items(), key=lambda kv: (-kv[1], kv[0]))]


def dif_section(n: int, seed: int, qc: QualityConfig) -> dict[str, Any]:
    q = qc.raw
    rates = q["dif"]["synthetic_rates"]
    cands = make_candidates(n, seed, rates)
    # Ранг по экспозиции в пробном прогоне выбирает внедряемые семейства: смещённое задание должно часто попадаться
    pre = run_sessions(cands[: max(300, n // 10)], seed=seed, tag="dif-pre", spec=PLANT_SPEC)
    rank = _exposure_rank(pre)
    planted = [
        {"group": p["group"], "family_id": rank[int(p["rank"]) - 1], "shift": float(p["shift"])} for p in q["dif"]["planted"]
    ]
    shifts = {(p["group"], p["family_id"]): p["shift"] for p in planted}
    sessions = run_sessions(cands, seed=seed, tag="dif", examinee_for=_bias_examinee(shifts, PLANT_SPEC))
    cfg = qc.dif

    rows: list[dict[str, Any]] = []
    for spec in SPECS:
        sp = [s for s in sessions if s.spec == spec]
        for g in q["dif"]["groups"]:
            per_fam: dict[str, list[tuple[float, bool, bool]]] = {}
            for s in sp:
                for fam, ok in s.items:
                    per_fam.setdefault(fam, []).append((s.theta_hat, s.groups[g], ok))
            for fam in sorted(per_fam):
                res = mh_from_records(per_fam[fam], cfg).to_dict()
                rec = per_fam[fam]
                ref = [ok for _, f, ok in rec if not f]
                foc = [ok for _, f, ok in rec if f]
                res.update(
                    spec=spec, group=g, family_id=fam,
                    p_ref=round(sum(ref) / len(ref), 3) if ref else None,
                    p_focal=round(sum(foc) / len(foc), 3) if foc else None,
                    planted_shift=shifts.get((g, fam)) if spec == PLANT_SPEC else None,
                )
                rows.append(res)

    def cls_counts(rs: Sequence[Mapping[str, Any]]) -> dict[str, int]:
        c = Counter(r["dif_class"] for r in rs)
        return {k: c.get(k, 0) for k in (CLASS_A, CLASS_B, CLASS_C, NO_DATA)}

    real = [r for r in rows if r["planted_shift"] is None]
    planted_rows = [r for r in rows if r["planted_shift"] is not None]
    by_group = {
        g: cls_counts([r for r in real if r["group"] == g]) | {"tested": sum(1 for r in real if r["group"] == g and r["dif_class"] != NO_DATA)}
        for g in q["dif"]["groups"]
    }
    # Влияние (impact): разница в среднем уровне между группами при отсутствии DIF — MH её не должен принимать за DIF
    impact = {}
    for g in q["dif"]["groups"]:
        f = [s.theta_true for s in sessions if s.groups[g]]
        r = [s.theta_true for s in sessions if not s.groups[g]]
        impact[g] = {
            "mean_theta_focal": round(sum(f) / len(f), 3), "mean_theta_ref": round(sum(r) / len(r), 3),
            "n_focal": len(f), "n_ref": len(r),
        }
    sig_real = [r for r in real if r["dif_class"] != NO_DATA]
    return {
        "n_sessions": len(sessions),
        "n_candidates": len(cands),
        "class_note": "A: |Δ| < 1 или незначимо; B: промежуточно; C: |Δ| ≥ 1,5 и значимо больше 1 (ETS)",
        "planted": [
            next(
                (
                    {**p, "dif_class": r["dif_class"], "delta": r["delta"], "se_delta": r["se_delta"], "n_focal": r["n_focal"],
                     "p_ref": r["p_ref"], "p_focal": r["p_focal"]}
                    for r in planted_rows if r["group"] == p["group"] and r["family_id"] == p["family_id"]
                ),
                p,
            )
            for p in planted
        ],
        "real_by_group": by_group,
        "real_total": cls_counts(real) | {"tested": len(sig_real)},
        "real_share_A": round(sum(1 for r in sig_real if r["dif_class"] == CLASS_A) / len(sig_real), 4) if sig_real else None,
        "real_flagged": sorted(
            (r for r in real if r["dif_class"] in (CLASS_B, CLASS_C)), key=lambda r: -abs(r["delta"] or 0)
        )[:10],
        "impact": impact,
        "rows": rows,
    }


# --------------------------------------------------------------------------- 2. дрейф


def drift_section(n: int, seed: int, qc: QualityConfig) -> dict[str, Any]:
    demo = qc.raw["drift"]["demo"]
    leak_shift, share, n_leaked = float(demo["shift_b"]), float(demo["share_knowers"]), int(demo["n_leaked"])
    cands = make_candidates(n, seed + 1, qc.raw["dif"]["synthetic_rates"])
    scale = load_scale()
    by_id = {f.id: f for f in real_bank()}
    spec = PLANT_SPEC
    clean = run_sessions(cands, seed=seed, tag="drift-clean", spec=spec, with_loo=True)
    pre = clean
    rank = _exposure_rank(pre)
    leaked = rank[:n_leaked]
    rl = random.Random(f"{seed}:drift:knowers")
    knowers = {c.index for c, _ in cands if rl.random() < share}

    def make(cand: Candidate, groups: Mapping[str, bool], _m: FamilyMeta | None) -> SimulatedExaminee:
        tb = {f: by_id[f].scale_param(spec, scale).b + leak_shift for f in leaked} if cand.index in knowers else None
        return SimulatedExaminee(theta=cand.truth.theta, module_theta=cand.truth.module_theta, true_b=tb)

    dirty = run_sessions(cands, seed=seed, tag="drift-leak", spec=spec, examinee_for=make, with_loo=True)

    def scan(sessions: Sequence[SessRec]) -> list[dict[str, Any]]:
        per: dict[str, tuple[list[float], list[bool]]] = {}
        for s in sessions:
            for fam, th, ok in s.loo:
                t, c = per.setdefault(fam, ([], []))
                t.append(th)
                c.append(ok)
        out = []
        for fam in sorted(per):
            item: ItemParams = by_id[fam].item_params(spec, scale)
            out.append(estimate_drift(fam, per[fam][0], per[fam][1], item, qc.drift).to_dict())
        return out

    est_clean, est_dirty = scan(clean), scan(dirty)
    flagged = lambda es: {e["family_id"] for e in es if e["flag"] == "drift"}  # noqa: E731
    fl_dirty, fl_clean = flagged(est_dirty), flagged(est_clean)
    tested_clean = sum(1 for e in est_clean if e["flag"] != NO_DATA)
    clean_ids = [e["family_id"] for e in est_dirty if e["family_id"] not in leaked and e["flag"] != NO_DATA]
    return {
        "spec": spec,
        "n_sessions": len(dirty),
        "leak": {"shift_b": leak_shift, "share_knowers": share, "leaked_families": leaked},
        "threshold_b": qc.drift.threshold_b,
        "min_n": qc.drift.min_n,
        "recall": round(len(fl_dirty & set(leaked)) / len(leaked), 3),
        "detected": sorted(fl_dirty & set(leaked)),
        "false_flags_in_leak_run": sorted(fl_dirty - set(leaked)),
        "specificity_leak_run": round(1 - len(fl_dirty - set(leaked)) / max(1, len(clean_ids)), 3),
        "control_false_flags": sorted(fl_clean),
        "control_tested": tested_clean,
        "mean_d_b_leaked": round(sum(e["d_b"] for e in est_dirty if e["family_id"] in leaked and e["d_b"] is not None) / len(leaked), 3),
        "expected_d_b_leaked": round(leak_shift * share, 3),
        "rows_leak_run": [e for e in est_dirty if e["family_id"] in leaked],
        "rows_control_top": sorted((e for e in est_clean if e["d_b"] is not None), key=lambda e: -abs(e["d_b"]))[:5],
    }


# --------------------------------------------------------------------------- 3. экспозиция и Симпсон–Хеттер


def _metrics(sessions: Sequence[SessRec], r_max: float, bank_ids: Sequence[str]) -> dict[str, Any]:
    ex = exposure_rates(s.shown for s in sessions)
    rates = [ex.get(f, 0.0) for f in bank_ids]
    top = sorted(((v, f) for f, v in ex.items()), reverse=True)[:5]
    n = max(1, len(sessions))
    return {
        "sessions": len(sessions),
        "max_exposure": round(max(rates), 3),
        "top5": [{"family_id": f, "exposure": round(v, 3)} for v, f in top],
        "families_over_rmax": sum(1 for v in rates if v > r_max + 1e-9),
        "unused_share": round(sum(1 for v in rates if v < 0.01) / len(rates), 3),
        "mean_exposure": round(sum(rates) / len(rates), 3),
        "mean_se": round(sum(s.se for s in sessions) / n, 4),
        "mean_items": round(sum(s.n_total for s in sessions) / n, 2),
        "decision_accuracy": round(sum(s.correct_decision for s in sessions) / n, 4),
    }


def exposure_section(n: int, seed: int, qc: QualityConfig, spec: str = PLANT_SPEC, r_max: float | None = None) -> dict[str, Any]:
    ex_cfg = qc.exposure
    r_max = r_max if r_max is not None else ex_cfg.r_max
    cands = make_candidates(int(n * 2.4), seed + 2, qc.raw["dif"]["synthetic_rates"])
    cands = [c for c in cands if c[0].claimed_spec == spec]
    half = len(cands) // 2
    train, verify = cands[:half], cands[half:]
    bank_ids = [f.id for f in bank_for(spec)]
    base_cfg = replace(load_cat_config(), sh_enabled=False, sh_k={})
    base_train = run_sessions(train, seed=seed, tag="exp-base", cfg=base_cfg)
    base_verify = run_sessions(verify, seed=seed, tag="exp-verify-base", cfg=base_cfg)

    k: dict[str, float] = {}
    history = []
    for it in range(ex_cfg.sim_iterations):
        cfg_it = replace(load_cat_config(), sh_enabled=True, sh_k=dict(k))
        sess = run_sessions(train, seed=seed, tag=f"exp-sh{it}", cfg=cfg_it)
        p_sel = exposure_rates(s.selected for s in sess)
        shown = exposure_rates(s.shown for s in sess)
        history.append({
            "iteration": it + 1, "max_selected": round(max(p_sel.values()), 3), "max_exposure": round(max(shown.values()), 3),
            "n_k_lt_1": len(k),
        })
        k = sympson_hetter_update(k, p_sel, r_max, ex_cfg.k_min)
    cfg_final = replace(load_cat_config(), sh_enabled=True, sh_k=dict(k))
    sh_verify = run_sessions(verify, seed=seed, tag="exp-verify-sh", cfg=cfg_final)
    return {
        "spec": spec,
        "r_max": r_max,
        "bank_families": len(bank_ids),
        "mean_exposure_floor": round(base_train and sum(s.n_total for s in base_train) / len(base_train) / len(bank_ids), 3),
        "baseline_train": _metrics(base_train, r_max, bank_ids),
        "baseline_verify": _metrics(base_verify, r_max, bank_ids),
        "sh_verify": _metrics(sh_verify, r_max, bank_ids),
        "iterations": history,
        "k": dict(sorted(k.items())),
        "k_count": len(k),
        "histogram_baseline": _hist(exposure_rates(s.shown for s in base_verify), bank_ids),
        "histogram_sh": _hist(exposure_rates(s.shown for s in sh_verify), bank_ids),
        "per_family": [
            {
                "family_id": f,
                "baseline": round(exposure_rates(s.shown for s in base_verify).get(f, 0.0), 3),
                "sh": round(exposure_rates(s.shown for s in sh_verify).get(f, 0.0), 3),
            }
            for f in bank_ids
        ],
    }


def _hist(rates: Mapping[str, float], bank_ids: Sequence[str], width: float = 0.1) -> list[dict[str, Any]]:
    bins = Counter(min(int(rates.get(f, 0.0) / width), int(1 / width) - 1) for f in bank_ids)
    return [{"lo": round(i * width, 2), "hi": round((i + 1) * width, 2), "families": bins.get(i, 0)} for i in range(int(1 / width))]


# --------------------------------------------------------------------------- прогон и отчёт


def run(quick: bool = False, seed: int = MASTER_SEED) -> dict[str, Any]:
    qc = load_quality_config()
    t0 = time.time()
    n_dif, n_drift, n_exp = (700, 600, 250) if quick else (4000, 3500, 700)
    out: dict[str, Any] = {"meta": {"seed": seed, "quick": quick, "synthetic": True}}
    out["dif"] = dif_section(n_dif, seed, qc)
    t1 = time.time()
    out["drift"] = drift_section(n_drift, seed, qc)
    t2 = time.time()
    out["exposure"] = exposure_section(n_exp, seed, qc)
    out["meta"]["durations_s"] = {"dif": round(t1 - t0, 1), "drift": round(t2 - t1, 1), "exposure": round(time.time() - t2, 1)}
    return out


def _fmt(x: Any, nd: int = 2) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return f"{x:.{nd}f}".replace(".", ",")
    return str(x)


def _pct(x: Any) -> str:
    return "—" if x is None else f"{100 * x:.0f}%".replace(".", ",")


def render_markdown(r: Mapping[str, Any]) -> str:
    """Раздел «8. Качество заданий» для docs/VALIDATION.md. Числа — из eval/out/quality.json."""
    d, dr, ex = r["dif"], r["drift"], r["exposure"]
    L: list[str] = [
        "## 8. Качество заданий: DIF, дрейф, экспозиция", "",
        "Монитор справедливости и стабильности заданий (`app/testing/monitoring.py`, прогон — `uv run python -m eval.fairness`, "
        f"seed {r['meta']['seed']}). **Это проверка метода на синтетике:** группы задаём мы, в одну группу кладём заведомо "
        "смещённые задания, и отчёт обязан их найти. Выводов о реальных людях здесь нет; пол и возраст платформа не собирает, "
        "поэтому группы — ФСП (есть/нет), регион (Москва и Санкт-Петербург / остальные), режим доступности, экспресс-версия.", "",
        "### 8.1. DIF: Мантель–Хензель, классы ETS", "",
        f"Сессий в анализе: {d['n_sessions']}. Критерий сопоставления — θ̂ сессии в стратах по 0,5 логита; MH D-DIF = −2,35·ln α_MH; "
        "A — |Δ| < 1 или различие незначимо, C — |Δ| ≥ 1,5 и значимо больше 1, иначе B. Δ < 0 — задание тяжелее для фокальной группы.", "",
        "**Внедрённые смещённые задания** (сдвиг сложности для фокальной группы в логитах):", "",
        "| Группа | Семейство | Сдвиг | Доля верных: опорная / фокальная | MH D-DIF (±SE) | Класс |", "|---|---|---|---|---|---|",
    ]
    for p in d["planted"]:
        se = f" ± {_fmt(p.get('se_delta'))}" if p.get("se_delta") is not None else ""
        L.append(
            f"| {GROUP_LABEL.get(p['group'], p['group'])} | `{p['family_id']}` | {_fmt(p['shift'])} | "
            f"{_fmt(p.get('p_ref'))} / {_fmt(p.get('p_focal'))} | {_fmt(p.get('delta'))}{se} | **{p.get('dif_class', '—')}** |"
        )
    t = d["real_total"]
    L += [
        "", "**Настоящие семейства банка** (без внедрения), все пары «семейство × группа» с достаточным числом ответов "
        f"(не меньше {load_quality_config().dif.min_group} в каждой группе):", "",
        "| Группа (фокальная) | Проверено пар | A | B | C |", "|---|---|---|---|---|",
    ]
    for g, c in d["real_by_group"].items():
        L.append(f"| {GROUP_LABEL.get(g, g)} | {c['tested']} | {c[CLASS_A]} | {c[CLASS_B]} | {c[CLASS_C]} |")
    L += [
        f"| **Всего** | **{t['tested']}** | **{t[CLASS_A]}** | **{t[CLASS_B]}** | **{t[CLASS_C]}** |", "",
        f"Доля класса A среди проверенных: **{_pct(d['real_share_A'])}**. При ~{t['tested']} проверках отдельные B ожидаемы "
        "(5% ложных срабатываний на уровне значимости), поэтому в лаборатории B — повод посмотреть, C — повод вывести семейство на ревью.", "",
        "Различие в уровне между группами при этом не принимается за DIF (impact): средний истинный уровень у кандидатов без ФСП "
        f"{_fmt(d['impact']['fsp']['mean_theta_focal'])} против {_fmt(d['impact']['fsp']['mean_theta_ref'])} с ФСП — MH сравнивает людей одного уровня.", "",
        "### 8.2. Монитор дрейфа сложности", "",
        f"По ответам после калибровки оценивается сдвиг сложности Δb (метод моментов при апостериорном уровне отвечавшего без этого ответа, "
        f"95% ДИ по информации Фишера). Флаг «дрейф» — Δb ≤ −{_fmt(dr['threshold_b'])} (стало легче) и весь ДИ ниже 0 (не меньше {dr['min_n']} ответов). "
        f"Эксперимент: {len(dr['leak']['leaked_families'])} самых частых семейств «утекли» — {_pct(dr['leak']['share_knowers'])} кандидатов "
        f"знают ответы (сложность для них ниже на {_fmt(-dr['leak']['shift_b'])}); {dr['n_sessions']} сессий.", "",
        f"- Найдено утёкших семейств: **{len(dr['detected'])} из {len(dr['leak']['leaked_families'])}** (полнота {_pct(dr['recall'])}); "
        f"средний Δb по утёкшим {_fmt(dr['mean_d_b_leaked'])} при «наивном» ожидании {_fmt(dr['expected_d_b_leaked'])} (оценка сжата: знающие завышают собственный уровень, и это съедает часть сдвига).",
        f"- Ложные флаги среди остальных семейств в прогоне с утечкой: {len(dr['false_flags_in_leak_run'])} (специфичность {_pct(dr['specificity_leak_run'])}).",
        f"- Контроль (без утечки): флагов **{len(dr['control_false_flags'])}** из {dr['control_tested']} семейств.", "",
        "Это дополняет CUSUM (раздел 2.7): CUSUM быстро реагирует на поток, монитор даёт величину сдвига с доверительным интервалом "
        "для решения методиста. Ночная задача калибровки пишет число флагов в журнал аудита (`scheduler.calibration`).", "",
        "### 8.3. Экспозиция семейств и Симпсон–Хеттер", "",
        f"Специализация {ex['spec']}: {ex['bank_families']} семейств, целевой максимум r_max = {_fmt(ex['r_max'])}. "
        "Экспозиция — доля сессий, в которых семейство показано (одноразовые клоны делают экспозицию конкретного экземпляра ≈ 0, "
        "поэтому контроль нужен на уровне семейства). Контроль Симпсона–Хеттера в движке выключен по умолчанию (`quality.sympson_hetter.enabled`); "
        f"таблица — симуляция с K_i, подобранными за {len(ex['iterations'])} итераций на одной половине кандидатов и проверенными на другой.", "",
        "| Показатель | Без контроля | Симпсон–Хеттер |", "|---|---|---|",
    ]
    b, s = ex["baseline_verify"], ex["sh_verify"]
    for name, key in (
        ("Сессий", "sessions"), ("Максимум экспозиции", "max_exposure"), ("Семейств выше r_max", "families_over_rmax"),
        ("Доля почти неиспользуемых (<1%)", "unused_share"), ("Средняя экспозиция", "mean_exposure"),
        ("Средняя погрешность (SE) итогового уровня", "mean_se"), ("Среднее число заданий", "mean_items"),
        ("Верных решений о пороге", "decision_accuracy"),
    ):
        L.append(f"| {name} | {_fmt(b[key], 3 if key in ('mean_se', 'decision_accuracy') else 2)} | {_fmt(s[key], 3 if key in ('mean_se', 'decision_accuracy') else 2)} |")
    L += [
        "", f"Семейств с K_i < 1: {ex['k_count']}. Нижняя граница экспозиции — средняя длина теста / размер банка = {_fmt(ex['mean_exposure_floor'])}: "
        "r_max ниже этого числа недостижим, поэтому его выбирают выше. Формула итерации: K_i ← min(1, r_max / P(S_i)), "
        "P(S_i) — доля сессий, где задание было выбрано до фильтра K (Sympson & Hetter 1985).", "",
        "**Честно про результат.** За несколько итераций на ~450 сессиях контроль сходится не полностью: K_i считаются по P(S_i), "
        "а P(S_i) растёт, когда соседние задания «отклоняют» (их место занимают остальные); классическая процедура требует "
        "десятков итераций на тысячах сессий. Максимум задают семейства самого большого модуля: квота модуля обязывает показывать "
        "их почти каждому. Поэтому контроль в продукте выключен, а `eval.fairness` — способ посчитать K_i под реальный банк перед включением.", "",
    ]
    return "\n".join(L).rstrip() + "\n"


def splice_docs(md_section: str, path: Path = DOCS_PATH) -> bool:
    """Подставить раздел в docs/VALIDATION.md между маркерами (или перед «Как воспроизвести»)."""
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8")
    block = f"{MARK_BEGIN}\n{md_section}{MARK_END}\n\n"
    if MARK_BEGIN in text and MARK_END in text:
        head, rest = text.split(MARK_BEGIN, 1)
        _, tail = rest.split(MARK_END, 1)
        new = head + block.rstrip("\n") + tail.lstrip("\n").join(["\n\n", ""]) if False else head + block + tail.lstrip("\n")
    else:
        marker = "## Как воспроизвести"
        if marker not in text:
            return False
        head, tail = text.split(marker, 1)
        new = head + block + marker + tail
    path.write_text(new, encoding="utf-8", newline="\n")
    return True


def load_result(path: Path | None = None) -> dict[str, Any] | None:
    p = path or DEFAULT_OUT
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--no-docs", action="store_true")
    ap.add_argument("--seed", type=int, default=MASTER_SEED)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    a = ap.parse_args(argv)
    res = run(a.quick, a.seed)
    a.out.parent.mkdir(parents=True, exist_ok=True)
    slim = {**res, "dif": {k: v for k, v in res["dif"].items() if k != "rows"} | {"rows": [r for r in res["dif"]["rows"] if r["dif_class"] in (CLASS_B, CLASS_C) or r["planted_shift"] is not None]}}
    a.out.write_text(json.dumps(slim, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    md = render_markdown(res)
    if not a.no_docs and not a.quick:
        print("docs/VALIDATION.md:", "обновлён" if splice_docs(md) else "не найден")
    print(md)
    print(f"Готово: {a.out} ({res['meta']['durations_s']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
