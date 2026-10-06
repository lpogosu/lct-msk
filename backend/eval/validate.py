"""`uv run python -m eval.validate [--quick]` — полный прогон валидации (концепция §7, VALIDATION_PLAN.md).

Шаги: популяция с латентной истиной (тест и пересдача на прод-движке) → тестирование (S1–S5, бейзлайны,
калибровка, дискриминативность) → категоризация (V-CAT) → подбор (40 брифов, бейзлайны, устойчивость,
экспозиция, объяснения) → eval/out/validation.json (один файл для экрана «Лаборатория»), SVG-графики,
docs/VALIDATION.md и набор пар eval/pairs_test.jsonl.

Независимые разделы считаются в нескольких процессах; результат от этого не зависит: у каждого раздела
и каждого кандидата свой генератор с сидом (eval.common.stream).

Контракт для других модулей:
    load_result(path=None) -> dict           # прочитать eval/out/validation.json
    save_validation_runs(session, result)    # записать ValidationRun по видам (sim, leak, drift, vcat, matching, llm_baseline)

Образ бэкенда не содержит eval/, а «Лаборатория» читает таблицу validation_runs. Залить готовый результат
в базу без повторного прогона: `uv run python -m eval.validate --save-db --from-json eval/out/validation.json`
(VZ_DATABASE_URL — база compose).
"""

from __future__ import annotations

import argparse
import json
import os
import pickle
import subprocess
import sys
import time
from collections.abc import Callable, Mapping
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from eval import matching_eval as ME
from eval import metrics as M
from eval import testing_eval as T
from eval import vcat
from eval.common import MASTER_SEED, REF_DATE, bank_for, real_bank
from eval.population import (
    SPEC_MIX_WITH_QA_DEVOPS,
    Population,
    PopulationConfig,
    generate_chunk,
    vss_truth_correlation,
)

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_DIR = EVAL_DIR.parent
REPO_DIR = BACKEND_DIR.parent
DEFAULT_OUT = EVAL_DIR / "out"
RESULT_NAME = "validation.json"
QUICK_RESULT_NAME = "validation_quick.json"


@dataclass(frozen=True)
class RunConfig:
    """Размеры выборок. Полный режим — как в VALIDATION_PLAN.md; быстрый — те же процедуры на меньших n."""

    quick: bool
    seed: int = MASTER_SEED
    with_qa_devops: bool = False  # популяция с QA и DevOps по 15% (прежняя по умолчанию: Backend 50 / Frontend 40 / Fullstack 10)
    n_pop: int = 2000
    n_hyst: int = 400
    n_s2: int = 400
    s2_sigmas: tuple[float, ...] = (0.0, 0.3, 0.5)
    s2_a: tuple[float, ...] = (1.0, 2.0)
    n_cheaters: int = 300
    coalitions: tuple[int, ...] = (0, 5, 20, 100)
    method_deltas: tuple[float, ...] = (0.0, 0.25, 0.5)
    collision_instances: int = 200
    n_base: int = 400
    b3_sigma: float = 0.5
    b3_broken: float = 0.10
    cusum_reps: int = 20
    cusum_sessions: int = 3000
    cusum_h: tuple[float, ...] = (4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0)
    calib_reps: int = 5
    calib_ns: tuple[int, ...] = (0, 30, 100, 300)
    n_boot: int = 2000

    @classmethod
    def make(cls, quick: bool, seed: int = MASTER_SEED, with_qa_devops: bool = False) -> RunConfig:
        if not quick:
            return cls(quick=False, seed=seed, with_qa_devops=with_qa_devops)
        return cls(quick=True, seed=seed, with_qa_devops=with_qa_devops, n_pop=300, n_hyst=60, n_s2=60, n_cheaters=60, collision_instances=40,
                   n_base=60, cusum_reps=3, cusum_sessions=1000, calib_reps=2, n_boot=300)

    def b3_variants(self) -> list[tuple[str, float, float, bool]]:
        """(подпись, σ сложности, доля битых ключей, считать ли пересдачу)."""
        main = (f"B3 «LLM под каждого» (σ = {_ru(self.b3_sigma)}, битых ключей {_pct(self.b3_broken)})",
                self.b3_sigma, self.b3_broken, True)
        if self.quick:
            return [main]
        return [
            main,
            ("B3, битых ключей 0%", self.b3_sigma, 0.0, False),
            ("B3, битых ключей 5%", self.b3_sigma, 0.05, False),
            ("B3, битых ключей 20%", self.b3_sigma, 0.20, False),
            ("B3 «идеальный LLM» (σ = 0,3, 0% битых)", 0.3, 0.0, True),
        ]


def _ru(x: float) -> str:
    return f"{x:g}".replace(".", ",")


def _pct(x: float) -> str:
    return f"{x * 100:g}%"


# --------------------------------------------------------------------------- задачи для процессов

_POP: Population | None = None


def _init_worker(blob: bytes) -> None:
    global _POP
    _POP = pickle.loads(blob)


def _leak_task(pop: Population, *, n_cheaters: int, coalitions: tuple[int, ...], deltas: tuple[float, ...],
               b3_broken: float, b3_sigma: float, n_instances: int) -> dict[str, Any]:
    fams = [f.id for f in bank_for(T.SPEC_LEAK, pop.bank)]
    col = T.key_collision_rates(n_instances, fams)
    res = T.leak_section(pop, n_cheaters=n_cheaters, coalitions=coalitions, collision=col["rates"], deltas=deltas,
                         b3_broken=b3_broken, b3_sigma=b3_sigma, twin=col["identical_pair"])
    rates = list(col["rates"].values())
    ident = col["identical_pair"]
    worst = max(ident, key=lambda f: (ident[f], f)) if ident else None
    res["collisions"] = {
        "n_instances": n_instances,
        "families": len(rates),
        "errors": col["errors"],
        "median_rate": M.median(rates),
        "share_below_2pct": M.mean([float(r < 0.02) for r in rates]),
        # Вероятность, что двум кандидатам с одним семейством выпадет одинаковый по содержанию экземпляр.
        "identical_pair_mean": M.mean(list(ident.values())),
        "identical_pair_max": ident[worst] if worst is not None else None,
        "identical_pair_max_family": worst,
        "families_with_identical": sum(1 for v in ident.values() if v > 0),
    }
    return res


def _match_task(pop: Population, *, seed: int, n_boot: int) -> dict[str, Any]:
    return ME.evaluate(pop, seed=seed, n_boot=n_boot)


TASKS: dict[str, Callable[..., Any]] = {
    "hysteresis": lambda pop, **kw: T.hysteresis_section(pop, **kw),
    "s2": lambda pop, **kw: T.expert_error_section(pop, **kw),
    "leak": _leak_task,
    "baselines": lambda pop, **kw: T.baselines_section(pop, **kw),
    "cusum": lambda pop, **kw: T.cusum_section(pop, **kw),
    "calibration": lambda pop, **kw: T.calibration_section(pop, **kw),
    "matching": _match_task,
    "vcat": lambda pop, **kw: vcat.evaluate(**kw),
}


def _run_task(key: str, name: str, kwargs: Mapping[str, Any]) -> tuple[str, Any, float]:
    t0 = time.perf_counter()
    res = TASKS[name](_POP, **kwargs)
    return key, res, time.perf_counter() - t0


def _tasks(rc: RunConfig) -> list[tuple[str, str, dict[str, Any]]]:
    out: list[tuple[str, str, dict[str, Any]]] = [
        ("matching", "matching", {"seed": rc.seed, "n_boot": rc.n_boot}),
        ("leak", "leak", {"n_cheaters": rc.n_cheaters, "coalitions": rc.coalitions, "deltas": rc.method_deltas,
                          "b3_broken": rc.b3_broken, "b3_sigma": rc.b3_sigma, "n_instances": rc.collision_instances}),
        ("hysteresis", "hysteresis", {"limit": rc.n_hyst}),
        ("cusum", "cusum", {"replicates": rc.cusum_reps, "h_grid": rc.cusum_h, "sessions_per_phase": rc.cusum_sessions}),
        ("calibration", "calibration", {"replicates": rc.calib_reps, "ns": rc.calib_ns}),
        ("vcat", "vcat", {"seed": rc.seed}),
    ]
    # Тяжёлые разделы режем на независимые части (одно условие / один вариант B3 на задачу).
    for s in rc.s2_sigmas:
        out.append((f"s2:sigma:{s}", "s2", {"n": rc.n_s2, "sigmas": (s,), "a_values": ()}))
    for a in rc.s2_a:
        out.append((f"s2:a:{a}", "s2", {"n": rc.n_s2, "sigmas": (), "a_values": (a,)}))
    for i, v in enumerate(rc.b3_variants()):
        out.append((f"baselines:{i}", "baselines", {"n": rc.n_base, "b3_variants": [v]}))
    return out


def _merge_s2(parts: Mapping[str, Any], rc: RunConfig) -> dict[str, Any]:
    keys = [f"s2:sigma:{s}" for s in rc.s2_sigmas] + [f"s2:a:{a}" for a in rc.s2_a]
    conds = [c for k in keys for c in parts[k]["conditions"]]
    return {"n": parts[keys[0]]["n"], "conditions": conds, "common_random_numbers": True}


def _merge_baselines(parts: Mapping[str, Any], rc: RunConfig) -> dict[str, Any]:
    keys = [f"baselines:{i}" for i in range(len(rc.b3_variants()))]
    first = parts[keys[0]]
    rows = list(first["rows"])
    for k in keys[1:]:
        rows += [r for r in parts[k]["rows"] if "sigma" in r]
    return {"n": first["n"], "rows": rows, "b3_assumptions": {"sigma": rc.b3_sigma, "broken_keys": rc.b3_broken,
                                                              "measured": False}}


# --------------------------------------------------------------------------- прогон


def default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


def _log(msg: str, t0: float) -> None:
    print(f"[{time.perf_counter() - t0:6.1f} с] {msg}", file=sys.stderr, flush=True)


def build_population(rc: RunConfig, workers: int, say: Callable[[str], None] = lambda _m: None) -> Population:
    """Популяция с тестом и пересдачей; при workers > 1 — по кускам в процессах (результат тот же)."""
    cfg = PopulationConfig(n=rc.n_pop, seed=rc.seed)
    if rc.with_qa_devops:
        cfg = replace(cfg, spec_mix=SPEC_MIX_WITH_QA_DEVOPS)
    idx = list(range(cfg.n))
    if workers <= 1:
        cands = generate_chunk(cfg, idx, True)
    else:
        size = max(10, cfg.n // (workers * 4))
        chunks = [idx[i : i + size] for i in range(0, len(idx), size)]
        with ProcessPoolExecutor(max_workers=workers) as ex:
            parts = list(ex.map(generate_chunk, [cfg] * len(chunks), chunks, [True] * len(chunks)))
        cands = [c for p in parts for c in p]
    say(f"популяция: {len(cands)} кандидатов, тест и пересдача на прод-движке")
    return Population(cfg, cands, real_bank())


def run(rc: RunConfig, *, workers: int | None = None, log: bool = True) -> dict[str, Any]:
    """Посчитать всё и вернуть результат (JSON-совместимый, без записи файлов)."""
    global _POP
    t0 = time.perf_counter()
    workers = default_workers() if workers is None else workers
    say: Callable[[str], None] = (lambda m: _log(m, t0)) if log else (lambda _m: None)
    pop = build_population(rc, workers, say)
    t_pop = time.perf_counter() - t0
    durations: dict[str, float] = {"population": t_pop}
    parts: dict[str, Any] = {}
    tasks = _tasks(rc)
    if workers <= 1:
        _POP = pop
        for key, name, kw in tasks:
            k, res, dt = _run_task(key, name, kw)
            parts[k], durations[k] = res, dt
            say(f"раздел {k}: {dt:.1f} с")
    else:
        blob = pickle.dumps(pop, protocol=pickle.HIGHEST_PROTOCOL)
        with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker, initargs=(blob,)) as ex:
            futs = [ex.submit(_run_task, key, name, kw) for key, name, kw in tasks]
            for f in futs:
                k, res, dt = f.result()
                parts[k], durations[k] = res, dt
                say(f"раздел {k}: {dt:.1f} с")
    # Быстрые разделы — в главном процессе.
    col = parts["leak"].get("collisions") or {}
    testing = {
        "accuracy": T.accuracy_section(pop),
        "instance_overlap": {
            **T.instance_overlap_section(pop),
            "by_construction": True,  # id уникальны по построению выдачи; содержание — ниже
            "content_identical_pair_mean": col.get("identical_pair_mean"),
            "content_identical_pair_max": col.get("identical_pair_max"),
            "content_identical_pair_max_family": col.get("identical_pair_max_family"),
            "content_families": col.get("families"),
            "content_families_with_identical": col.get("families_with_identical"),
            "content_n_instances": col.get("n_instances"),
        },
        "retest": T.retest_section(pop),
        "hysteresis": parts["hysteresis"],
        "expert_error": _merge_s2(parts, rc),
        "baselines": _merge_baselines(parts, rc),
        "leak": parts["leak"],
        "cusum": parts["cusum"],
        "calibration": parts["calibration"],
        "discrimination": T.discrimination_section(pop),
        "tags": T.tags_section(pop),
        "no_fsp": _no_fsp_section(pop),
    }
    matching = parts["matching"]
    result: dict[str, Any] = {
        "meta": {},
        "population": {**pop.summary(), "vss_truth_correlation": vss_truth_correlation(pop),
                       "config": _pop_config(pop.cfg)},
        "testing": testing,
        "categorization": parts["vcat"],
        "matching": ME.strip_private(matching),
    }
    result["_matching_private"] = {"_rankings": matching["_rankings"], "_rels": matching["_rels"]}
    result["_population"] = pop
    result["meta"] = _meta(rc, workers, durations, t0)
    say("все разделы посчитаны")
    return result


def _no_fsp_section(pop: Population) -> dict[str, Any]:
    """Кандидат без истории ФСП получает категорию по тем же правилам: доля подтверждённых с ФСП и без."""
    tested = [c for c in pop.candidates if c.test is not None]
    with_fsp = [c for c in tested if c.has_fsp]
    without = [c for c in tested if not c.has_fsp]

    def share(xs: list[Any]) -> float | None:
        return M.mean([float(c.test.final_grade is not None) for c in xs]) if xs else None

    return {
        "confirmed_share_with_fsp": share(with_fsp),
        "confirmed_share_without_fsp": share(without),
        "n_with_fsp": len(with_fsp),
        "n_without_fsp": len(without),
        "survey_and_test_take_no_fsp_input": vcat._no_fsp_inputs(),
    }


def _pop_config(cfg: PopulationConfig) -> dict[str, Any]:
    d = asdict(cfg)
    d["spec_mix"] = {k: v for k, v in cfg.spec_mix}
    d["grade_mix"] = {k: v for k, v in cfg.grade_mix}
    return d


def _git_sha() -> str | None:
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_DIR, capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _meta(rc: RunConfig, workers: int, durations: Mapping[str, float], t0: float) -> dict[str, Any]:
    from app.matching.scoring import ScoringConfig

    return {
        "generator": "eval.validate",
        "mode": "quick" if rc.quick else "full",
        "seed": rc.seed,
        "ref_date": REF_DATE.isoformat(),
        "git_sha": _git_sha(),
        "workers": workers,
        "bank_families": len(real_bank()),
        "scoring_config_version": ScoringConfig.load().version,
        "run_config": {k: (list(v) if isinstance(v, tuple) else v) for k, v in asdict(rc).items()},
        "durations_s": {k: round(v, 2) for k, v in durations.items()},
        "total_s": round(time.perf_counter() - t0, 1),
    }


# --------------------------------------------------------------------------- запись


def public_result(result: Mapping[str, Any]) -> dict[str, Any]:
    from eval.report import tiles

    out = {k: v for k, v in result.items() if not k.startswith("_")}
    out["tiles"] = tiles(out)
    return M.round_tree(out, 4)


def write_outputs(result: dict[str, Any], *, out_dir: Path, docs_path: Path | None, charts_dir: Path,
                  pairs_path: Path | None, quick: bool) -> dict[str, str]:
    from eval import report

    out_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, str] = {}
    pop: Population = result["_population"]
    if pairs_path is not None:
        private = {**result["matching"], **result["_matching_private"]}
        info = ME.export_pairs(private, pop, pairs_path, seed=result["meta"]["seed"])
        schema_path = pairs_path.with_name("pairs_test.schema.json")
        schema_path.write_text(json.dumps(ME.pairs_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result["pairs"] = {"file": _rel(pairs_path), "schema": _rel(schema_path), "lines": info["lines"],
                           "briefs": info["briefs"]}
        paths["pairs"] = str(pairs_path)
    pub = public_result(result)
    json_path = out_dir / (QUICK_RESULT_NAME if quick else RESULT_NAME)
    json_path.write_text(json.dumps(pub, ensure_ascii=False, indent=1, sort_keys=False) + "\n", encoding="utf-8")
    paths["json"] = str(json_path)
    charts_dir.mkdir(parents=True, exist_ok=True)
    charts = report.write_charts(pub, charts_dir)
    if docs_path is not None:
        docs_path.parent.mkdir(parents=True, exist_ok=True)
        md = report.render_markdown(pub, charts=charts, doc_dir=docs_path.parent, json_path=json_path)
        docs_path.write_text(md, encoding="utf-8", newline="\n")
        paths["docs"] = str(docs_path)
    return paths


def _rel(p: Path) -> str:
    try:
        return p.resolve().relative_to(REPO_DIR).as_posix()
    except ValueError:
        return p.as_posix()


def load_result(path: Path | None = None) -> dict[str, Any]:
    """Прочитать последний полный прогон (для экрана «Лаборатория»)."""
    p = path or (DEFAULT_OUT / RESULT_NAME)
    return json.loads(p.read_text(encoding="utf-8"))


VALIDATION_KINDS = {
    "sim": lambda r: {k: r["testing"][k] for k in ("accuracy", "retest", "hysteresis", "expert_error", "baselines",
                                                    "calibration", "discrimination", "instance_overlap", "no_fsp",
                                                    "tags") if k in r["testing"]},
    "leak": lambda r: r["testing"]["leak"],
    "drift": lambda r: r["testing"]["cusum"],
    "vcat": lambda r: r["categorization"],
    "matching": lambda r: r["matching"],
    "llm_baseline": lambda r: {"rows": [x for x in r["testing"]["baselines"]["rows"] if "sigma" in x],
                               "assumptions": r["testing"]["baselines"].get("b3_assumptions")},
}


def save_validation_runs(session: Any, result: Mapping[str, Any]) -> int:
    """Записать прогон в таблицу validation_runs: по строке на вид (ValidationKind). Возвращает число строк."""
    from app.models import ValidationRun

    meta = result["meta"]
    n = 0
    for kind, pick in VALIDATION_KINDS.items():
        metrics = M.round_tree(dict(pick(result)), 4)
        metrics["tiles"] = [t for t in result.get("tiles", []) if t.get("kind") == kind]
        session.add(ValidationRun(kind=kind, config={"mode": meta["mode"], "run_config": meta["run_config"],
                                                     "scoring_config_version": meta["scoring_config_version"]},
                                  seeds=[meta["seed"]], metrics=metrics, git_sha=meta.get("git_sha")))
        n += 1
    session.flush()
    return n


def _save_db(public: Mapping[str, Any]) -> int:
    """Записать публичный результат в базу из окружения (VZ_DATABASE_URL) одной транзакцией."""
    from app import db as app_db

    with app_db.get_sessionmaker()() as session:
        n = save_validation_runs(session, public)
        session.commit()
    return n


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m eval.validate", description="Валидация «Взаимно» на прод-движках")
    ap.add_argument("--quick", action="store_true", help="дымовой прогон: те же процедуры на меньших выборках")
    ap.add_argument("--workers", type=int, default=None, help="число процессов (1 — без параллельности)")
    ap.add_argument("--seed", type=int, default=MASTER_SEED)
    ap.add_argument("--with-qa-devops", action="store_true",
                    help="популяция с QA и DevOps (по 15%%): точность и длина теста по специализациям; "
                         "числа основного отчёта при этом меняются, поэтому для публикации — без флага")
    ap.add_argument("--out", type=Path, default=None, help="каталог для всех файлов прогона (по умолчанию eval/out)")
    ap.add_argument("--docs", type=Path, default=None, help="куда писать отчёт Markdown")
    ap.add_argument("--no-pairs", action="store_true", help="не выгружать набор пар для жюри")
    ap.add_argument("--save-db", action="store_true", help="записать ValidationRun в базу (VZ_DATABASE_URL)")
    ap.add_argument("--from-json", type=Path, default=None,
                    help="не считать заново, а записать в базу готовый результат (eval/out/validation.json); "
                         "только вместе с --save-db — так «Лаборатория» получает данные без повторного прогона")
    args = ap.parse_args(argv)
    if args.from_json is not None:
        if not args.save_db:
            ap.error("--from-json используется только вместе с --save-db")
        data = load_result(args.from_json)
        if not data.get("tiles") or not data.get("meta"):
            ap.error(f"{args.from_json}: это не результат eval.validate (нет meta/tiles)")
        print(f"db_rows: {_save_db(data)}")
        return 0
    rc = RunConfig.make(args.quick, args.seed, args.with_qa_devops)
    custom = args.out is not None
    out_dir = args.out or DEFAULT_OUT
    if custom or rc.quick:
        charts_dir = out_dir / ("charts_quick" if rc.quick else "charts")
        docs = args.docs or (out_dir / ("VALIDATION_quick.md" if rc.quick else "VALIDATION.md"))
        pairs = None if (args.no_pairs or rc.quick) else out_dir / "pairs_test.jsonl"
    else:
        charts_dir = EVAL_DIR / "report"
        docs = args.docs or (REPO_DIR / "docs" / "VALIDATION.md")
        pairs = None if args.no_pairs else EVAL_DIR / "pairs_test.jsonl"
    result = run(rc, workers=args.workers)
    paths = write_outputs(result, out_dir=out_dir, docs_path=docs, charts_dir=charts_dir, pairs_path=pairs,
                          quick=rc.quick)
    if args.save_db:
        paths["db_rows"] = str(_save_db(public_result(result)))
    for k, v in paths.items():
        print(f"{k}: {v}")
    print(f"готово за {result['meta']['total_s']} с")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
