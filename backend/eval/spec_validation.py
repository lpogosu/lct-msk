"""Test-retest и разделяющая способность теста — по специализациям (организаторы: «тест-ретест + разделяющая способность»).

Один и тот же латентный профиль проходит тест несколько раз на прод-движке (`app.testing.simulate`):
    A — основная сессия;
    B — независимая параллельная сессия (другой сид → другие клоны и другая траектория, семейства не исключаются);
    C — пересдача по реальным правилам (spec-testing §7.1): семейства сессии A исключены (другая экспозиция).
Дальше считаются: согласие грейдов A↔B и A↔C, корреляция θ, доля решений, «перевернувшихся» через порог;
точность против истинного грейда (матрица 4×5, точность решения у каждого порога, AUC по θ̂), дискриминация
заданий (r_pb, IRT-крутизна, Δp), информация теста у порогов и SEM, устойчивость к разбросу клонов σ_inst,
эффект «слитых ключей» (θ̂ у списывающего против честной сессии того же человека), покрытие двойниками.

Все случайности — `random.Random(f"{seed}:...")`: тот же сид → тот же отчёт, число процессов результат не меняет.
Контракт: `simulate_profiles(profiles, ...) -> rows`, `summarize(rows, specs) -> dict` (их использует eval/jury_check.py).
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from typing import Any

from app.testing.calibration import point_biserial
from app.testing.cat import load_blueprint
from app.testing.irt import Scale, information, load_scale
from app.testing.simulate import SimulatedExaminee, sample_claimed_grade
from eval import metrics as M
from eval.common import MASTER_SEED, grade_band, grade_index, real_bank
from eval.population import simulate_test

SPECS: tuple[str, ...] = ("backend_python", "frontend_js", "qa", "devops")
GRADES: tuple[str, ...] = ("intern", "junior", "middle", "senior")
CUTS: tuple[tuple[str, int], ...] = (("intern|junior", 1), ("junior|middle", 2), ("middle|senior", 3))
MODULE_SD = 0.35  # как в eval.population: уровень модуля = θ + N(0; 0,35²)
NEAR_CUT = 0.5  # «рядом с порогом»: |θ − порог| ≤ 0,5


@dataclass(frozen=True)
class SpecRunConfig:
    seed: int = MASTER_SEED
    n_per_spec: int = 400
    n_sigma: int = 120  # профилей на условие σ_inst
    sigmas: tuple[float, ...] = (0.0, 0.15, 0.30)
    n_leak: int = 100
    coalition: int = 20
    n_twin_seeds: int = 6
    n_collision: int = 40

    @classmethod
    def make(cls, quick: bool, seed: int = MASTER_SEED) -> SpecRunConfig:
        if quick:
            return cls(seed=seed, n_per_spec=24, n_sigma=12, n_leak=12, coalition=4, n_twin_seeds=1, n_collision=8)
        return cls(seed=seed)


# --------------------------------------------------------------------------- профили


def make_profiles(spec: str, n: int, seed: int) -> list[dict[str, Any]]:
    """n латентных профилей, поровну по четырём грейдам; заявка — как в популяции (60% верно, 30% выше, 10% ниже)."""
    scale = load_scale()
    out = []
    for i in range(n):
        rng = random.Random(f"{seed}:prof:{spec}:{i}")
        grade = GRADES[i % len(GRADES)]
        lo, hi = grade_band(grade)
        theta = rng.uniform(lo, hi)
        claimed = sample_claimed_grade(grade, rng, scale)
        out.append({"id": f"{spec}-{i + 1:04d}", "spec": spec, "true_grade": grade, "theta": theta, "claimed_grade": claimed})
    return out


def _module_theta(spec: str, theta: float, rng: random.Random) -> dict[str, float]:
    return {m: theta + rng.gauss(0.0, MODULE_SD) for m in sorted(load_blueprint(spec))}


def _true_idx(theta: float, scale: Scale) -> int:
    g = scale.grade_of(theta)
    return grade_index(g) if g in scale.grades else -1


def _admin_info(outcome: Any, spec: str, scale: Scale, by_id: Mapping[str, Any]) -> list[float]:
    """Суммарная информация Фишера выданных зачётных заданий у каждого порога (intern|junior, …)."""
    items = [by_id[r.family_id].item_params(spec, scale) for r in outcome.responses if not r.is_pretest]
    return [float(sum(information(scale.cut(GRADES[k]), ip) for ip in items)) for _, k in CUTS]


def _session(p: Mapping[str, Any], tag: str, seed: int, *, seen: Sequence[str] = (), sigma: float | None = None) -> Any:
    rng = random.Random(f"{seed}:mod:{p['id']}")
    mt = p.get("module_theta") or _module_theta(p["spec"], p["theta"], rng)
    ex = SimulatedExaminee(theta=p["theta"], module_theta=mt, sigma_inst=sigma)
    return simulate_test(theta=p["theta"], module_theta=mt, spec=p["spec"], claimed_grade=p["claimed_grade"],
                         rng=random.Random(f"{seed}:sess:{tag}:{p['id']}"), bank=real_bank(), examinee=ex,
                         seen_families=seen, session_id=f"{tag}-{p['id']}")


def _row(p: Mapping[str, Any], seed: int) -> dict[str, Any]:
    scale = load_scale()
    by_id = {m.id: m for m in real_bank()}
    a = _session(p, "A", seed)
    b = _session(p, "B", seed)
    c = _session(p, "C", seed, seen=a.families)

    def pack(o: Any) -> dict[str, Any]:
        return {"idx": grade_index(o.final_grade), "passed": bool(o.passed), "theta": o.theta, "se": o.se, "n": o.n_total}

    return {
        "id": p["id"], "spec": p["spec"], "true_grade": p["true_grade"], "theta_true": p["theta"],
        "true_idx": _true_idx(p["theta"], scale), "claimed_grade": p["claimed_grade"],
        "claim_ge_true": grade_index(p["claimed_grade"]) >= grade_index(p["true_grade"]),
        "a": pack(a), "b": pack(b), "c": pack(c),
        "c_shared_families": len(set(a.families) & set(c.families)),
        "b_shared_families": len(set(a.families) & set(b.families)),
        "info_cuts": _admin_info(a, p["spec"], scale, by_id),
        "resp": [(r.family_id, bool(r.correct), r.loo_mean) for r in a.responses if not r.is_pretest],
    }


def _rows_task(profiles: Sequence[Mapping[str, Any]], seed: int) -> list[dict[str, Any]]:
    return [_row(p, seed) for p in profiles]


def simulate_profiles(profiles: Sequence[Mapping[str, Any]], seed: int = MASTER_SEED, workers: int = 1) -> list[dict[str, Any]]:
    """Три сессии на профиль (A, B, C). Порядок результата = порядок профилей, число процессов на него не влияет."""
    if workers <= 1 or len(profiles) < 8:
        return _rows_task(profiles, seed)
    size = max(4, len(profiles) // (workers * 3))
    chunks = [list(profiles[i : i + size]) for i in range(0, len(profiles), size)]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(_rows_task, chunks, [seed] * len(chunks)))
    return [r for part in parts for r in part]


# --------------------------------------------------------------------------- метрики


def _agree(ga: Sequence[int], gb: Sequence[int]) -> dict[str, Any]:
    n = len(ga)
    if not n:
        return {"n": 0}
    flips = {}
    for name, k in CUTS:
        flips[name] = sum((x >= k) != (y >= k) for x, y in zip(ga, gb, strict=True)) / n
    return {
        "n": n,
        "grade_exact": M.agreement(ga, gb),
        "grade_within1": sum(abs(x - y) <= 1 for x, y in zip(ga, gb, strict=True)) / n,
        "grade_qwk": M.quadratic_weighted_kappa(ga, gb, -1, 3),
        "grade_kappa": M.cohen_kappa(ga, gb),
        "flip_across_cut": flips,
        "flip_any_cut": sum(x != y for x, y in zip(ga, gb, strict=True)) / n,
    }


def retest_block(rows: Sequence[Mapping[str, Any]], other: str) -> dict[str, Any]:
    ga = [r["a"]["idx"] for r in rows]
    gb = [r[other]["idx"] for r in rows]
    out = _agree(ga, gb)
    if not rows:
        return out
    out["decision_agreement"] = M.agreement([r["a"]["passed"] for r in rows], [r[other]["passed"] for r in rows])
    out["decision_kappa"] = M.cohen_kappa([r["a"]["passed"] for r in rows], [r[other]["passed"] for r in rows])
    out["theta_r"] = M.pearson([r["a"]["theta"] for r in rows], [r[other]["theta"] for r in rows])
    out["theta_sd_diff"] = M.rmse([r["a"]["theta"] - r[other]["theta"] for r in rows]) / math.sqrt(2)  # SEM по паре сессий
    near = [r for r in rows if any(abs(r["theta_true"] - load_scale().cut(GRADES[k])) <= NEAR_CUT for _, k in CUTS)]
    out["near_cut_n"] = len(near)
    out["near_cut_exact"] = M.agreement([r["a"]["idx"] for r in near], [r[other]["idx"] for r in near]) if near else None
    return out


def separation_block(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Точность против латентного грейда. Кандидаты, заявившие грейд ниже истинного (10%), исключены: тест на
    заявленный грейд не может выдать больше (повышение — отдельный тест, §7.1), это правило продукта, а не ошибка теста."""
    scale = load_scale()
    use = [r for r in rows if r["claim_ge_true"] and r["true_idx"] >= 0]
    out: dict[str, Any] = {"n": len(use), "n_excluded_under_claim": len(rows) - len(use)}
    if not use:
        return out
    t = [r["true_idx"] for r in use]
    f = [r["a"]["idx"] for r in use]
    conf: dict[str, dict[str, int]] = {}
    for tt, ff in zip(t, f, strict=True):
        conf.setdefault(GRADES[tt], {}).setdefault("none" if ff < 0 else GRADES[ff], 0)
        conf[GRADES[tt]]["none" if ff < 0 else GRADES[ff]] += 1
    out["confusion"] = conf  # строки — истина, столбцы — итог теста (none = не подтверждён)
    out["grade_exact"] = M.agreement(t, f)
    out["grade_within1"] = sum(abs(a - b) <= 1 for a, b in zip(t, f, strict=True)) / len(use)
    out["grade_qwk"] = M.quadratic_weighted_kappa(t, f, -1, 3)
    cuts = {}
    for name, k in CUTS:
        cut = scale.cut(GRADES[k])
        ok = [(r["a"]["idx"] >= k) == (r["true_idx"] >= k) for r in use]
        near = [(r["a"]["idx"] >= k) == (r["true_idx"] >= k) for r in use if abs(r["theta_true"] - cut) <= NEAR_CUT]
        hi = [r["a"]["theta"] for r in use if r["true_idx"] >= k]
        lo = [r["a"]["theta"] for r in use if r["true_idx"] < k]
        cuts[name] = {"cut_theta": cut, "decision_accuracy": M.mean([float(x) for x in ok]),
                      "near_cut_accuracy": M.mean([float(x) for x in near]) if near else None, "near_cut_n": len(near),
                      "auc_theta": M.auc(hi, lo) if hi and lo else None}
    out["per_cut"] = cuts
    # Информация и SEM у порогов: по выданным заданиям основной цепочки сессии A.
    info = {}
    for j, (name, _k) in enumerate(CUTS):
        vals = [r["info_cuts"][j] for r in use]
        mi = M.mean(vals)
        info[name] = {"mean_information": mi, "sem_from_information": (1 / math.sqrt(mi)) if mi else None}
    out["information"] = info
    out["sem_posterior_mean"] = M.mean([r["a"]["se"] for r in use])
    near_any = [r["a"]["se"] for r in use if any(abs(r["theta_true"] - scale.cut(GRADES[k])) <= NEAR_CUT for _, k in CUTS)]
    out["sem_posterior_near_cut"] = M.mean(near_any) if near_any else None
    out["length_mean"] = M.mean([r["a"]["n"] for r in use])
    out["length_p95"] = M.percentile([r["a"]["n"] for r in use], 95)
    th = [r["a"]["theta"] for r in use]
    var = M.mean([(x - M.mean(th)) ** 2 for x in th])
    out["reliability"] = (1.0 - M.mean([r["a"]["se"] ** 2 for r in use]) / var) if var else None
    return out


def discrimination_block(rows: Sequence[Mapping[str, Any]], spec_of: Mapping[str, str] | None = None, min_n: int = 25) -> dict[str, Any]:
    """Дискриминация зачётных заданий: r_pb (ответ против оценки уровня без этого ответа), IRT-крутизна A_eff и Δp
    между соседними грейдами (по оценке уровня без этого ответа)."""
    scale = load_scale()
    by_id = {m.id: m for m in real_bank()}
    data: dict[str, list[tuple[bool, float]]] = {}
    spec = rows[0]["spec"] if rows else None
    for r in rows:
        for fid, ok, loo in r["resp"]:
            data.setdefault(fid, []).append((ok, loo))
    fam_rows = []
    for fid, v in sorted(data.items()):
        if len(v) < min_n:
            continue
        rpb = point_biserial([x for x, _ in v], [t for _, t in v])
        a_eff = by_id[fid].item_params(spec, scale).a if spec else None
        by_g: dict[str, list[bool]] = {}
        for ok, t in v:
            by_g.setdefault(scale.grade_of(t), []).append(ok)
        dps = []
        for lo, hi in zip(GRADES, GRADES[1:], strict=False):
            if len(by_g.get(lo, ())) >= 8 and len(by_g.get(hi, ())) >= 8:
                dps.append(sum(by_g[hi]) / len(by_g[hi]) - sum(by_g[lo]) / len(by_g[lo]))
        fam_rows.append({"family_id": fid, "n": len(v), "r_pb": rpb, "a_eff": a_eff, "dp_adjacent": M.mean(dps) if dps else None})
    rpb = [x["r_pb"] for x in fam_rows if x["r_pb"] is not None]
    dp = [x["dp_adjacent"] for x in fam_rows if x["dp_adjacent"] is not None]
    return {
        "families": len(fam_rows),
        "r_pb_median": M.median(rpb), "r_pb_p10": M.percentile(rpb, 10),
        "share_r_pb_ge_020": M.mean([float(x >= 0.20) for x in rpb]) if rpb else None,
        "a_eff_median": M.median([x["a_eff"] for x in fam_rows if x["a_eff"] is not None]),
        "dp_adjacent_median": M.median(dp), "dp_families": len(dp),
        "weak_families": sorted(x["family_id"] for x in fam_rows if x["r_pb"] is not None and x["r_pb"] < 0.10),
    }


def summarize(rows: Sequence[Mapping[str, Any]], specs: Sequence[str]) -> dict[str, Any]:
    """Метрики по каждой специализации и сводно («all»). Для «all» дискриминация не считается (у семейств свои сложности)."""
    res: dict[str, Any] = {}
    for key, subset in [(s, [r for r in rows if r["spec"] == s]) for s in specs] + [("all", list(rows))]:
        if not subset:
            continue
        block = {
            "n": len(subset),
            "retest_parallel": retest_block(subset, "b"),
            "retest_real_rules": retest_block(subset, "c"),
            "separation": separation_block(subset),
            "mean_shared_families_real_rules": M.mean([r["c_shared_families"] for r in subset]),
            "mean_shared_families_parallel": M.mean([r["b_shared_families"] for r in subset]),
        }
        if key != "all":
            block["discrimination"] = discrimination_block(subset)
        res[key] = block
    return res


# --------------------------------------------------------------------------- устойчивость: клоны, утечка, двойники


def clone_sensitivity(spec: str, cfg: SpecRunConfig) -> dict[str, Any]:
    """Эквивалентность клонов: как точность и согласие при пересдаче зависят от разброса сложности клонов σ_inst.
    σ из config/testing.yaml — допущение (живых клонов у нас нет); показываем, что σ×2 даёт, а σ = 0 даёт как верхнюю границу."""
    profs = make_profiles(spec, cfg.n_sigma, cfg.seed + 7)
    out = []
    for s in cfg.sigmas:
        a_idx, b_idx, t_idx = [], [], []
        for p in profs:
            a = _session(p, f"S{s}A", cfg.seed, sigma=s)
            b = _session(p, f"S{s}B", cfg.seed, sigma=s)
            a_idx.append(grade_index(a.final_grade))
            b_idx.append(grade_index(b.final_grade))
            t_idx.append(_true_idx(p["theta"], load_scale()))
        use = [i for i, p in enumerate(profs) if grade_index(p["claimed_grade"]) >= grade_index(p["true_grade"])]
        out.append({"sigma_inst": s, "n": len(profs),
                    "grade_exact_vs_truth": M.agreement([t_idx[i] for i in use], [a_idx[i] for i in use]),
                    "retest_grade_exact": M.agreement(a_idx, b_idx)})
    return {"configured_sigma_inst": load_scale().sigma_inst, "rows": out}


def leak_gain(spec: str, cfg: SpecRunConfig) -> dict[str, Any]:
    """«Слитые ключи»: коалиция из `coalition` человек выложила всё, что видела; слабый кандидат (intern/junior) вводит
    слитый ключ чужого экземпляра. Сравнение парное: та же сессия честно и со слитыми ключами (тот же сид).
    Прирост θ̂ ≈ 0 (или < 0) означает, что слив ответов не помогает. Доля коллизий ключей и «одинаковых по тексту»
    экземпляров берётся с реального банка (key_collision_rates)."""
    from eval.testing_eval import CopyExaminee, key_collision_rates

    bank_ids = [m.id for m in real_bank() if spec in m.specs]
    col = key_collision_rates(cfg.n_collision, bank_ids)
    members = make_profiles(spec, cfg.coalition, cfg.seed + 11)
    leaked: set[str] = set()
    for p in members:
        leaked |= set(_session(p, "M", cfg.seed).families)
    cheaters = [p for p in make_profiles(spec, cfg.n_leak * 2, cfg.seed + 13) if p["true_grade"] in ("intern", "junior")][: cfg.n_leak]
    gains, honest_pass, cheat_pass, flagged = [], [], [], 0
    for p in cheaters:
        p = {**p, "claimed_grade": GRADES[min(GRADES.index(p["true_grade"]) + 1, 3)]}  # заявка на ступень выше истины
        rng = random.Random(f"{cfg.seed}:mod:{p['id']}")
        mt = _module_theta(spec, p["theta"], rng)
        h = simulate_test(theta=p["theta"], module_theta=mt, spec=spec, claimed_grade=p["claimed_grade"],
                          rng=random.Random(f"{cfg.seed}:leak:{p['id']}"), bank=real_bank(),
                          examinee=SimulatedExaminee(theta=p["theta"], module_theta=mt), session_id="lh")
        ex = CopyExaminee(theta=p["theta"], module_theta=mt, leaked=frozenset(leaked), collision=col["rates"],
                          twin=col["identical_pair"])
        c = simulate_test(theta=p["theta"], module_theta=mt, spec=spec, claimed_grade=p["claimed_grade"],
                          rng=random.Random(f"{cfg.seed}:leak:{p['id']}"), bank=real_bank(), examinee=ex, session_id="lc")
        gains.append(c.theta - h.theta)
        honest_pass.append(float(h.passed))
        cheat_pass.append(float(c.passed))
        flagged += bool(ex.caught)
    ci = M.bootstrap_ci(gains, random.Random(f"{cfg.seed}:leakci:{spec}"), n_boot=500) if len(gains) > 2 else None
    return {
        "n": len(cheaters), "coalition": cfg.coalition,
        "leaked_share_of_spec_bank": len(leaked & set(bank_ids)) / len(bank_ids) if bank_ids else None,
        "theta_gain_mean": M.mean(gains), "theta_gain_ci95": ci,
        "false_pass_honest": M.mean(honest_pass), "false_pass_with_leak": M.mean(cheat_pass),
        "share_flagged_foreign_key": flagged / len(cheaters) if cheaters else None,
        "detector_active_families": sum(1 for f in bank_ids if col["rates"].get(f, 1.0) < 0.02),
        "bank_families": len(bank_ids),
        "median_key_collision": M.median([col["rates"][f] for f in bank_ids if f in col["rates"]]),
    }


def twin_coverage(spec: str, cfg: SpecRunConfig) -> dict[str, Any]:
    """«Двойник в буфере»: у какой доли экземпляров семейств специализации он строится и отличается от ключа
    (т.е. ответ по скопированному тексту засчитывается неверным). Детерминированно, без людей и без LLM."""
    from app.testing.anticheat.dispatch import family_twin_result
    from app.testing.bank.registry import all_families

    fams = all_families()
    ids = [m.id for m in real_bank() if spec in m.specs]
    made = diff = total = 0
    fam_with = 0
    reasons: dict[str, int] = {}
    for fid in ids:
        ok_any = False
        for s in range(cfg.n_twin_seeds):
            try:
                inst = fams[fid].generate(s)
                res = family_twin_result(fams[fid], inst)  # как в проде: Family.twin() первым (QA, DevOps), затем общий мутатор
            except Exception:  # noqa: BLE001 — семейство без исполнителя: двойника нет
                reasons["error"] = reasons.get("error", 0) + 1
                total += 1
                continue
            total += 1
            if res.twin is not None:
                made += 1
                diff += res.twin.twin_key_hash != res.twin.key_hash
                ok_any = True
            else:
                reasons[res.reason] = reasons.get(res.reason, 0) + 1
        fam_with += ok_any
    return {"families": len(ids), "families_with_twin": fam_with, "instances": total,
            "twin_built_share": made / total if total else None,
            "twin_key_differs_share_of_built": diff / made if made else None, "no_twin_reasons": reasons}


def _spec_extras(spec: str, cfg: SpecRunConfig) -> dict[str, Any]:
    return {"clone_sensitivity": clone_sensitivity(spec, cfg), "leak": leak_gain(spec, cfg), "twin": twin_coverage(spec, cfg)}


# Пилот анти-ассиста (документ spec-antiassist §0, _internal/ в git не входит): без персональных данных, только счётчики.
PILOT_SUMMARY: dict[str, Any] = {
    "source": "пилот анти-ассиста: прогон трёх чат-моделей одного вендора (слабая, средняя, сильная)",
    "pilot1": {"models": 3, "conditions": ["clipboard", "displayed"], "clean_items": 2, "answers": 12, "correct": 12,
               "note": "обычные middle-задания решаются без ошибок: ИИ-ответ по одному ответу от сильного человека не отличить"},
    "pilot2": {"items_built": 14, "formats": {"predict_output": 10, "sql_result": 4}, "mutation_classes": ["data_literal", "table_value"],
               "twin_key_differs_from_key": 14, "gates_passed": 14,
               "model_run_with_traps": "не выполнен: recall двойника на моделях не измерен"},
    "honest_status": ("Измерен только факт построения двойников (14/14, ключ двойника ≠ ключу) и покрытие банка (ниже, twin). "
                      "Доля пойманных копипастеров среди живых людей и моделей не измерена; обход скриншотом двойник не ловит."),
}


# --------------------------------------------------------------------------- прогон


def _extras_task(spec: str, cfg: SpecRunConfig) -> tuple[str, dict[str, Any]]:
    return spec, _spec_extras(spec, cfg)


def run(cfg: SpecRunConfig, *, specs: Sequence[str] = SPECS, workers: int = 1) -> dict[str, Any]:
    profiles = [p for s in specs for p in make_profiles(s, cfg.n_per_spec, cfg.seed)]
    rows = simulate_profiles(profiles, cfg.seed, workers)
    summary = summarize(rows, specs)
    if workers <= 1 or len(specs) < 2:
        extras = dict(_extras_task(s, cfg) for s in specs)
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(specs))) as ex:
            extras = dict(ex.map(_extras_task, specs, [cfg] * len(specs)))
    for s in specs:
        summary[s].update(extras[s])
    return {
        "config": {"seed": cfg.seed, "n_per_spec": cfg.n_per_spec, "specs": list(specs), "grades": list(GRADES),
                   "procedure": ("A — основная сессия; B — независимая параллельная (другие клоны); C — пересдача по реальным "
                                 "правилам: семейства A исключены. Профили поровну по 4 грейдам, заявка 60/30/10."),
                   "sigma_inst": load_scale().sigma_inst, "near_cut": NEAR_CUT},
        "by_spec": {s: summary[s] for s in specs if s in summary},
        "overall": summary.get("all"),
        "pilot_antiassist": PILOT_SUMMARY,
    }


def main(argv: list[str] | None = None) -> int:
    import argparse
    import json
    import sys
    from pathlib import Path

    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m eval.spec_validation")
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--seed", type=int, default=MASTER_SEED)
    ap.add_argument("--out", type=Path, default=Path(__file__).parent / "out" / "spec_validation.json")
    args = ap.parse_args(argv)
    res = run(SpecRunConfig.make(args.quick, args.seed), workers=args.workers)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(M.round_tree(res, 4), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"written: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
