"""Рыночные ориентиры зарплат по открытым данным (config/market.yaml, data/market/README.md).

Только подсказка работодателю и сигнал доверия: в Score не входит и на подбор не влияет
(концепция §3.1.2 — вилка обязательна и задаётся самим работодателем). Данные региональные
и заниженные относительно Москвы — это сказано в `note` ответа и в интерфейсе.
"""

from __future__ import annotations

from typing import Any

from app.core.configs import load_config

GRADES = ("intern", "junior", "middle", "senior")


def market_cfg() -> dict[str, Any]:
    return load_config("market")


def _net(v: float, ndfl: float) -> int:
    return int(round(v * (1 - ndfl), -2))


def benchmark(direction: str | None, grade: str | None) -> dict[str, Any] | None:
    """Квантили для направления × грейда; если ячейка тонкая (n < min_n) — по направлению в целом.

    Все величины в ₽/мес. `gross` — до НДФЛ (так указана зарплата в источнике), `net` — на руки.
    """
    if not direction:
        return None
    cfg = market_cfg()
    meta = cfg["meta"]
    ndfl = float(meta["ndfl_rate"])
    cell, scope = None, "direction_grade"
    if grade:
        cell = next((c for c in cfg["direction_grade"] if c["direction"] == direction and c["grade"] == grade), None)
    if cell is None:
        cell = next((c for c in cfg["direction"] if c["direction"] == direction), None)
        scope = "direction"
    if cell is None or int(cell["n"]) < int(cfg["min_n"]):
        return None
    keys = ("p10", "p25", "p50", "p75", "p90")
    return {
        "direction": direction,
        "grade": cell.get("grade") if scope == "direction_grade" else None,
        "scope": scope,
        "n": int(cell["n"]),
        "gross": {k: int(cell[k]) for k in keys},
        "net": {k: _net(cell[k], ndfl) for k in keys},
        "source": meta["source"],
        "fetched": meta["fetched"],
        "note": meta["bias_note"],
    }


def salary_vs_market(direction: str | None, grade: str | None, salary_to: int, net: bool, ratio: float) -> dict[str, Any] | None:
    """Верхняя граница вилки против нижнего квартиля рынка (с учётом основания gross/net).

    `far_below` ⇔ to < ratio · p25 (ratio — config/trust.yaml → flags.salary_far_below_market).
    """
    b = benchmark(direction, grade)
    if b is None:
        return None
    ndfl = float(market_cfg()["meta"]["ndfl_rate"])
    to_gross = salary_to / (1 - ndfl) if net else float(salary_to)
    p25 = b["gross"]["p25"]
    return {"benchmark": b, "to_gross": int(round(to_gross, -2)), "ratio_to_p25": round(to_gross / p25, 2),
            "far_below": to_gross < ratio * p25}
