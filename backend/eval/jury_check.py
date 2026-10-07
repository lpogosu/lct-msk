"""Набор жюри: test-retest и разделяющая способность на ВАШИХ синтетических профилях.

    cd backend
    uv run python -m eval.jury_check eval/jury_profiles_example.jsonl            # или .csv
    uv run python -m eval.jury_check profiles.csv --seed 1 --out result.json --workers 4

Формат строки (JSONL — объект на строку; CSV — те же имена столбцов, заголовок обязателен):

    id             строка, уникальна                                    обязательно
    spec           backend_python | frontend_js | qa | devops           обязательно
    true_grade     intern | junior | middle | senior — ваш эталон       обязательно
    theta          число, латентный уровень (шкала config/testing.yaml)  необязательно; нет — берётся из полосы грейда по сиду
    claimed_grade  грейд, на который профиль проходит тест              необязательно; нет — равен true_grade

Каждый профиль проходит тест три раза на прод-движке (app/testing): A — основная сессия, B — независимая параллельная
(другие клоны), C — пересдача по реальным правилам (семейства A исключены). Считается: согласие грейдов (точное, ±1,
взвешенный κ), корреляция θ, доля «переворотов» через порог, матрица ошибок против true_grade, точность решения у каждого
порога, AUC, информация и SEM у порогов, дискриминация заданий. Сид фиксирован — результат воспроизводим побитно.
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

from eval import metrics as M
from eval import spec_validation as SV
from eval.common import MASTER_SEED, grade_band

REQUIRED = ("id", "spec", "true_grade")


class ProfileError(ValueError):
    """Строка набора не соответствует формату (в тексте — номер строки и поле)."""


def load_profiles(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        raw = list(csv.DictReader(text.splitlines()))
    else:
        raw = []
        for i, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    raw.append(json.loads(line))
                except json.JSONDecodeError as e:
                    raise ProfileError(f"строка {i}: не JSON ({e.msg})") from e
    return [parse_profile(r, i) for i, r in enumerate(raw, 1)]


def parse_profile(r: dict[str, Any], line: int = 0) -> dict[str, Any]:
    """Проверить строку и привести к виду, который ждёт spec_validation (theta без значения подставляет вызывающий)."""
    for k in REQUIRED:
        if not str(r.get(k) or "").strip():
            raise ProfileError(f"строка {line}: нет поля «{k}»")
    spec = str(r["spec"]).strip()
    if spec not in SV.SPECS:
        raise ProfileError(f"строка {line}: spec «{spec}» не из {', '.join(SV.SPECS)}")
    grade = str(r["true_grade"]).strip()
    if grade not in SV.GRADES:
        raise ProfileError(f"строка {line}: true_grade «{grade}» не из {', '.join(SV.GRADES)}")
    claimed = str(r.get("claimed_grade") or grade).strip()
    if claimed not in SV.GRADES:
        raise ProfileError(f"строка {line}: claimed_grade «{claimed}» не из {', '.join(SV.GRADES)}")
    theta_raw = r.get("theta")
    theta: float | None
    if theta_raw is None or str(theta_raw).strip() == "":
        theta = None
    else:
        try:
            theta = float(str(theta_raw).replace(",", "."))
        except ValueError as e:
            raise ProfileError(f"строка {line}: theta «{theta_raw}» не число") from e
    return {"id": str(r["id"]).strip(), "spec": spec, "true_grade": grade, "claimed_grade": claimed, "theta": theta}


def fill_theta(profiles: list[dict[str, Any]], seed: int) -> list[dict[str, Any]]:
    ids = [p["id"] for p in profiles]
    if len(set(ids)) != len(ids):
        raise ProfileError("id профилей должны быть уникальны")
    out = []
    for p in profiles:
        if p["theta"] is None:
            lo, hi = grade_band(p["true_grade"])
            p = {**p, "theta": random.Random(f"{seed}:jury:{p['id']}").uniform(lo, hi)}
        out.append(p)
    return out


def check(profiles: list[dict[str, Any]], seed: int = MASTER_SEED, workers: int = 1) -> dict[str, Any]:
    profiles = fill_theta(profiles, seed)
    rows = SV.simulate_profiles(profiles, seed, workers)
    specs = [s for s in SV.SPECS if any(r["spec"] == s for r in rows)]
    summ = SV.summarize(rows, specs)
    per_profile = [{"id": r["id"], "spec": r["spec"], "true_grade": r["true_grade"], "theta_true": r["theta_true"],
                    "grade_A": _g(r["a"]["idx"]), "grade_B": _g(r["b"]["idx"]), "grade_C_real_rules": _g(r["c"]["idx"]),
                    "theta_A": r["a"]["theta"], "theta_B": r["b"]["theta"], "theta_C": r["c"]["theta"]} for r in rows]
    return {"seed": seed, "n": len(rows), "summary": summ, "profiles": per_profile}


def _g(idx: int) -> str:
    return "none" if idx < 0 else SV.GRADES[idx]


def render_text(res: dict[str, Any]) -> str:
    def f(x: Any, nd: int = 2) -> str:
        return "—" if x is None else f"{x:.{nd}f}"

    L = [f"Профилей: {res['n']}, сид {res['seed']}", ""]
    hdr = f"{'спец.':<16}{'n':>5} | {'ретест A-B: точно / ±1 / QWK / r(θ)':<38} | {'реальные правила A-C: точно / QWK':<34} | {'против истины: точно / QWK':<27}"
    L += [hdr, "-" * len(hdr)]
    for s, b in res["summary"].items():
        p, c, sep = b["retest_parallel"], b["retest_real_rules"], b["separation"]
        L.append(f"{s:<16}{b['n']:>5} | {f(p.get('grade_exact')):>8} {f(p.get('grade_within1')):>6} {f(p.get('grade_qwk')):>6} "
                 f"{f(p.get('theta_r')):>6}{'':<9}| {f(c.get('grade_exact')):>8} {f(c.get('grade_qwk')):>6}{'':<19}| "
                 f"{f(sep.get('grade_exact')):>8} {f(sep.get('grade_qwk')):>6}")
    L += ["", "Точность решения у порогов (весь набор):"]
    sep = res["summary"]["all"]["separation"]
    for name, v in sep.get("per_cut", {}).items():
        L.append(f"  {name:<14} точность {f(v['decision_accuracy'])}, у порога (±{SV.NEAR_CUT}) {f(v['near_cut_accuracy'])}, AUC {f(v['auc_theta'])}")
    return "\n".join(L)


def main(argv: list[str] | None = None) -> int:
    for s in (sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="python -m eval.jury_check", description=__doc__.split("\n")[0])
    ap.add_argument("profiles", type=Path, help="JSONL или CSV с профилями (формат — в начале eval/jury_check.py)")
    ap.add_argument("--seed", type=int, default=MASTER_SEED)
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--out", type=Path, default=None, help="куда записать JSON (по умолчанию eval/out/jury_check.json)")
    args = ap.parse_args(argv)
    try:
        profiles = load_profiles(args.profiles)
        res = check(profiles, args.seed, args.workers)
    except ProfileError as e:
        print(f"Ошибка формата: {e}", file=sys.stderr)
        return 2
    out = args.out or (Path(__file__).parent / "out" / "jury_check.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(M.round_tree(res, 4), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(render_text(res))
    print(f"\nJSON: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
