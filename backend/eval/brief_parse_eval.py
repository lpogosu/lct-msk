"""Точность разбора брифа на отложенной выборке (≈60 текстов, живой язык работодателя и сленг).

Выборка `brief_heldout.jsonl` написана вручную и НЕ использовалась при подборе словаря: словарь
расширялся по агрегатам открытых вакансий (data/market/), а не по ошибкам этой выборки.
Метрики: F1 навыков (все / must), точность специализации, грейда, вилки и её основания (на руки / до НДФЛ),
формата и города. Запуск: `uv run python -m eval.brief_parse_eval [--json out.json]`.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.matching.brief_parser import parse_brief
from eval import metrics as M

HELDOUT = Path(__file__).resolve().parent / "brief_heldout.jsonl"


def load_heldout(path: Path = HELDOUT) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def evaluate(items: Sequence[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = list(items) if items is not None else load_heldout()
    spec_ok, grade_ok, sal_ok, net_ok, fmt_ok, city_ok = [], [], [], [], [], []
    tp = fp = fn = tp_m = fp_m = fn_m = 0
    rows = []
    for it in items:
        g = it["gold"]
        p = parse_brief(it["text"])
        ps, gs = set(p.must) | set(p.nice), set(g["must"]) | set(g["nice"])
        a, b, c = M.set_prf(gs, ps)
        tp, fp, fn = tp + a, fp + b, fn + c
        a, b, c = M.set_prf(set(g["must"]), set(p.must))
        tp_m, fp_m, fn_m = tp_m + a, fp_m + b, fn_m + c
        spec_ok.append(float(p.top_spec == g["spec"]))
        grade_ok.append(float(p.top_grade == g["grade"]))
        s = p.salary.value if p.salary else None
        sal_ok.append(float(s is not None and s.from_ == g["salary_from"] and s.to == g["salary_to"]))
        if g.get("salary_net") is not None:
            net_ok.append(float(s is not None and s.net == g["salary_net"]))
        fmt_ok.append(float({f.value for f in p.formats} == set(g["formats"])))
        city_ok.append(float((p.city.value if p.city else None) == g.get("city")))
        rows.append({
            "id": it["id"], "spec": [p.top_spec, g["spec"]], "grade": [p.top_grade, g["grade"]],
            "missed": sorted(gs - ps), "extra": sorted(ps - gs),
        })
    return {
        "n": len(items),
        "skills": M.prf_from_counts(tp, fp, fn),
        "must": M.prf_from_counts(tp_m, fp_m, fn_m),
        "spec_top1": M.mean(spec_ok),
        "grade_exact": M.mean(grade_ok),
        "salary_exact": M.mean(sal_ok),
        "salary_basis_accuracy": M.mean(net_ok),
        "formats_exact": M.mean(fmt_ok),
        "city_accuracy": M.mean(city_ok),
        "rows": rows,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", type=Path, default=None)
    ap.add_argument("--errors", action="store_true", help="показать пропуски и лишние навыки")
    args = ap.parse_args()
    r = evaluate()
    print(f"n={r['n']}  skillsF1={r['skills']['f1']:.3f} (P={r['skills']['precision']:.3f} R={r['skills']['recall']:.3f})"
          f"  mustF1={r['must']['f1']:.3f}  spec={r['spec_top1']:.3f}  grade={r['grade_exact']:.3f}"
          f"  salary={r['salary_exact']:.3f}  basis={r['salary_basis_accuracy']:.3f}"
          f"  format={r['formats_exact']:.3f}  city={r['city_accuracy']:.3f}")
    if args.errors:
        for row in r["rows"]:
            if row["missed"] or row["extra"] or row["spec"][0] != row["spec"][1]:
                print(row["id"], "miss", row["missed"], "extra", row["extra"], "spec", row["spec"], "grade", row["grade"])
    if args.json:
        args.json.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
