"""Отчёт о покрытии двойниками по всему реестру семейств.

    cd backend && uv run python -m app.testing.anticheat.report [--n 50] [--family ID ...] [--json] [--only-eligible]

Печатает по каждому семейству: формат, язык, режим ответа, пригодность по форме, долю экземпляров с валидным
двойником, среднее число исполнений, долю коллизий с ключами пула, отклонённых кандидатов и причины отказов;
в конце — итог по порогам config/anticheat.yaml → gate. Код возврата 1, если порог покрытия семейств не выполнен.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from collections.abc import Sequence

from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.gates import FamilyTwinStats, RegistryCoverage, compute_stats, stats_to_dict
from app.testing.anticheat.twins import reason_text
from app.testing.bank.registry import all_families


def _row(s: FamilyTwinStats) -> str:
    if not s.eligible:
        why = reason_text(s.ineligible_reason or "")
        return f"{s.family_id:<30} {s.format:<14} {s.language:<10} {s.answer_mode:<7} нет    -        -     -     -   {why}"
    top = ", ".join(f"{k}:{v}" for k, v in sorted(s.reasons.items(), key=lambda kv: -kv[1])[:2])
    rej = ", ".join(f"{k}:{v}" for k, v in sorted(s.rejects.items(), key=lambda kv: -kv[1])[:3])
    return (
        f"{s.family_id:<30} {s.format:<14} {s.language:<10} {s.answer_mode:<7} да     "
        f"{s.valid:>3}/{s.n:<3} {s.share:>4.0%}  {s.mean_attempts:>4.1f} {s.natural_collision:>5.1%} {s.k_err_mean:>5.1f}  "
        f"{('нет: ' + top) if top else 'ok'}{('  | отклонено: ' + rej) if rej else ''}"
    )


def format_report(cov: RegistryCoverage) -> str:
    cfg = load_anticheat_config()
    lines = [
        f"Двойники в буфере: покрытие по реестру (экземпляров на семейство: {cov.n})",
        "",
        f"{'семейство':<30} {'формат':<14} {'язык':<10} {'режим':<7} {'форма':<6} "
        f"{'двойники':<8} {'доля':<5} {'исп.':<5} {'колл.':<5} {'Kerr':<5} причины",
    ]
    lines += [_row(s) for s in cov.stats]
    fmt = Counter(s.format for s in cov.stats)
    pass_ids = {s.family_id for s in cov.passing}
    bad = [s.family_id for s in cov.eligible if s.family_id not in pass_ids]
    ineligible = Counter(s.ineligible_reason for s in cov.applicable if not s.eligible)
    lines += [
        "",
        f"Семейств в реестре: {cov.total} ({', '.join(f'{k}: {v}' for k, v in sorted(fmt.items()))})",
        f"predict_output / sql_result: {len(cov.applicable)}; пригодных по форме "
        f"(ответ текстом, исполняемый язык): {len(cov.eligible)}",
        "Непригодны по форме: "
        + (", ".join(f"{reason_text(k or '')} — {v}" for k, v in ineligible.most_common()) or "нет"),
        f"Двойник на ≥ {cfg.gate.min_twin_share:.0%} сидов: {len(cov.passing)} из {len(cov.eligible)} "
        f"пригодных семейств = {cov.family_share:.1%} (порог {cfg.gate.min_family_share:.0%}): "
        + ("ОК" if cov.meets_threshold else "НЕ ВЫПОЛНЕНО"),
        f"Экземпляров с двойником среди пригодных семейств: {cov.instance_share:.1%}",
    ]
    if bad:
        lines.append("Ниже порога по сидам: " + ", ".join(bad))
    codes = sorted({k for s in cov.stats for k in (*s.reasons, *s.rejects)} | {k for k in ineligible if k})
    legend: dict[str, str] = cfg.raw_text("report_legend")
    shown = [f"{c} — {legend.get(c) or reason_text(c)}" for c in codes]
    if shown:
        lines += ["", "Коды причин и отклонений:", *(f"  {x}" for x in shown)]
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Покрытие двойниками по реестру семейств")
    ap.add_argument("--n", type=int, default=None, help="сидов на семейство (по умолчанию gate.n из конфига)")
    ap.add_argument("--family", action="append", default=[], help="id семейства (можно несколько)")
    ap.add_argument("--json", action="store_true", help="вывести JSON")
    ap.add_argument("--only-eligible", action="store_true", help="показывать только пригодные по форме семейства")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass
    cfg = load_anticheat_config()
    n = cfg.gate.n if args.n is None else args.n
    reg = all_families()
    ids = sorted(args.family or reg)
    missing = [i for i in ids if i not in reg]
    if missing:
        print("Неизвестные семейства: " + ", ".join(missing), file=sys.stderr)
        return 2
    stats = compute_stats([reg[i] for i in ids], n, cfg)
    cov = RegistryCoverage(n, tuple(stats), cfg.gate.min_twin_share, cfg.gate.min_family_share)
    if args.only_eligible:
        cov = RegistryCoverage(n, tuple(s for s in stats if s.eligible), cfg.gate.min_twin_share, cfg.gate.min_family_share)
    if args.json:
        print(
            json.dumps(
                {
                    "n": n,
                    "families": [stats_to_dict(s) for s in cov.stats],
                    "eligible": len(cov.eligible),
                    "passing": len(cov.passing),
                    "family_share": round(cov.family_share, 4),
                    "meets_threshold": cov.meets_threshold,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(format_report(cov))
    return 0 if cov.meets_threshold or args.family else 1


if __name__ == "__main__":
    raise SystemExit(main())
