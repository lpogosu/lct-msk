"""Чувствительность точности и длины теста к правилу остановки CAT (VALIDATION_PLAN §7, пункты про «Точный грейд» и корреляцию).

Не часть `eval.validate`: правило остановки живёт в конфиге, а конфиг кэшируется при импорте, поэтому каждый вариант
считается отдельным процессом с каталогом конфигов из `VZ_CONFIG_DIR` (копия `config/` с изменённым `cat:` в testing.yaml):

    VZ_CONFIG_DIR=/tmp/cfg_p92 uv run python -m eval.stop_rule_sweep --seed 101

Печатает одну строку JSON: точный грейд, верное решение, длина основной части и с доборами, p95, корреляция оценок
уровня при пересдаче, QWK, κ. Сиды 101–105 — НЕ основной (20261005): варианты сравниваются на других популяциях.
"""

from __future__ import annotations

import argparse
import json

from eval import testing_eval as T
from eval import validate as V


def main() -> int:
    ap = argparse.ArgumentParser(prog="python -m eval.stop_rule_sweep")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    pop = V.build_population(V.RunConfig.make(False, args.seed), args.workers)
    a, r = T.accuracy_section(pop), T.retest_section(pop)
    print(json.dumps({
        "seed": args.seed,
        "exact": round(a["grade"]["exact"], 4),
        "decision": round(a["decision_accuracy"], 4),
        "main_mean": round(a["length"]["main_mean"], 2),
        "total_mean": round(a["length"]["total_mean"], 2),
        "total_p95": a["length"]["total_p95"],
        "theta_r": round(r["theta_r"], 4),
        "qwk": round(r["grade_qwk"], 3),
        "kappa": round(r["decision_kappa"], 3),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
