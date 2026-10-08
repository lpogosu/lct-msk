"""Проверочный набор по вакансии: P@10 до и после (docs/MATCHING.md «Навыки, наблюдаемые через проверочный набор»).

    cd backend
    uv run python -m eval.checkset_eval                 # полная популяция (2000), все 40 брифов
    uv run python -m eval.checkset_eval --slope 1.2     # чувствительность к крутизне задачи набора

Популяция и тест — как в eval.validate; кандидатам добавлен скрытый уровень «наблюдаемых набором» навыков
(population.Truth.skill_theta). «До» — выдача без набора, «после» — после набора у верха выдачи (30 человек).
Веса Score не трогаются; результат — JSON в stdout и eval/out/checkset.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from eval import matching_eval as ME
from eval.common import MASTER_SEED
from eval.validate import RunConfig, build_population, default_workers


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=MASTER_SEED)
    ap.add_argument("--slope", type=float, default=ME.CHECKSET_SLOPE)
    ap.add_argument("--n-pop", type=int, default=2000)
    ap.add_argument("--workers", type=int, default=None)
    args = ap.parse_args(argv)
    rc = RunConfig(quick=False, seed=args.seed, n_pop=args.n_pop)
    pop = build_population(rc, args.workers or default_workers())
    world = ME.World.build(pop)
    briefs = ME.load_briefs()
    runs = {b.id: ME.run_brief(b, world) for b in briefs}
    base = {}
    for b in briefs:
        r = runs[b.id]
        v, _ = ME.build_ours(r.spec, r.categories, world)
        base[b.id] = [i.candidate_id for i in v.visible()]
    out = ME.checkset_arm(briefs, runs, world, base, args.seed, slope=args.slope)
    path = Path(__file__).resolve().parent / "out" / "checkset.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    slim = {k: ({kk: vv for kk, vv in v.items() if kk != "per_brief"} if isinstance(v, dict) else v) for k, v in out.items()}
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(slim, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
