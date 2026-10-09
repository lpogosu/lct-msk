"""Точность разбора брифа: правила vs правила + LLM (GigaChat) на отложенной выборке `brief_heldout.jsonl`.

LLM только ДОБАВЛЯЕТ «желательные» навыки с проверенной цитатой (app/services/matching/llm_brief.py), поэтому ожидаемый
эффект — рост полноты (recall) ценой точности. Скрипт считает те же метрики, что `eval.brief_parse_eval`, плюс:
задержку, токены (поле usage ответа API), долю сбоев/fallback, число отброшенных охранником цитаты полей и качество
добавленных навыков (сколько из добавленного есть в эталоне). Режимы:
  record — живой вызов провайдера и запись кассеты (нужен ключ в VZ_LLM_GIGACHAT_AUTH_KEY, читается из окружения/.env);
  replay — только кассета, без сети и ключей (детерминированно, для CI и стенда);
  live   — живой вызов без записи (для сравнения моделей).
Запуск: `uv run python -m eval.llm_brief_eval --mode replay [--json out.json] [--min-quote-ratio 90]`.
Ключ нигде не печатается и не записывается; в кассете — sha256-ключ и ответ модели (брифы синтетические, без ПДн).
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from app.llm.gateway import Cassette, GigaChatProvider, LLMGateway
from app.llm.settings import LLMSettings
from app.matching import brief_parser as bp
from app.matching.skills import get_catalog
from app.services.matching import llm_brief
from eval import brief_parse_eval as bpe
from eval import metrics as M

CASSETTE = Path(__file__).resolve().parent / "llm_cassette_brief.json"
ENV_FILE = Path(__file__).resolve().parents[2] / ".env"


def load_env(path: Path = ENV_FILE) -> None:
    """Подтянуть VZ_LLM_* из .env, не перезаписывая заданное в окружении (значения не печатаются)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            if k.startswith("VZ_LLM_"):
                os.environ.setdefault(k.strip(), v.strip())


class TimedProvider:
    """Обёртка провайдера: время каждого вызова и счётчик сбоев."""

    def __init__(self, inner: GigaChatProvider) -> None:
        self.inner, self.name = inner, inner.name
        self.times: list[float] = []
        self.errors = 0

    def complete(self, system: str, user: str, timeout_s: float) -> str:
        t = time.perf_counter()
        try:
            return self.inner.complete(system, user, timeout_s)
        except Exception:
            self.errors += 1
            raise
        finally:
            self.times.append(time.perf_counter() - t)


def build_gateway(
    mode: str, model: str | None, min_ratio: float | None, cassette: Path
) -> tuple[LLMGateway, TimedProvider | None]:
    kw: dict[str, Any] = {"mode": mode, "cassette_path": str(cassette), "provider": "gigachat", "cache_size": 0}
    if model:
        kw["gigachat_model"] = model
    if min_ratio is not None:
        kw["min_quote_ratio"] = min_ratio
    settings = LLMSettings(**kw)
    if mode == "replay":
        return LLMGateway(settings, providers=[]), None
    prov = TimedProvider(GigaChatProvider(settings))
    return LLMGateway(settings, providers=[prov], cassette=Cassette(cassette) if mode == "record" else None), prov


def run(
    mode: str = "replay", model: str | None = None, min_ratio: float | None = None, cassette: Path = CASSETTE
) -> dict[str, Any]:
    items = bpe.load_heldout()
    gw, prov = build_gateway(mode, model, min_ratio, cassette)
    gold = {it["text"]: set(it["gold"]["must"]) | set(it["gold"]["nice"]) for it in items}
    stats: dict[str, Any] = {"asked": 0, "used": 0, "no_llm": 0, "errors": [], "rejected": 0, "proposed": 0,
                             "added": 0, "added_in_gold": 0, "added_items": 0}
    added_rows: list[dict[str, Any]] = []
    orig_extract = gw.extract
    solo = [0, 0, 0]  # tp, fp, fn навыков, которые предложила одна LLM (без правил): автономное качество модели

    def spy(*a: Any, **k: Any):  # учёт результата шлюза: сколько полей принято / отброшено охранником цитаты
        res = orig_extract(*a, **k)
        stats["asked"] += 1
        stats["used"] += int(res.used)
        stats["no_llm"] += int(not res.used)
        if res.error:
            stats["errors"].append(res.error)
        stats["rejected"] += res.rejected
        stats["proposed"] += len(res.fields) + res.rejected
        cat = get_catalog()
        ids = {f[0].id for f in (cat.find_skills(x.value, fuzzy=False) for x in res.fields) if f}
        tp, fp, fn = M.set_prf(gold[a[1]], ids)
        solo[0], solo[1], solo[2] = solo[0] + tp, solo[1] + fp, solo[2] + fn
        return res

    gw.extract = spy  # type: ignore[method-assign]

    def parse_llm(text: str) -> bp.ParsedBrief:
        rules = bp.parse_brief(text)
        out = llm_brief.augment(rules, gw)
        new = [c for c in out.skills if c.method == "llm"]
        stats["added_items"] += int(bool(new))
        for c in new:
            ok = c.skill_id in gold[text]
            stats["added"] += 1
            stats["added_in_gold"] += int(ok)
            added_rows.append({"skill": c.skill_id, "in_gold": ok, "quote": c.quote.text if c.quote else None})
        return out

    base = bpe.evaluate(items)
    orig = bpe.parse_brief
    bpe.parse_brief = parse_llm  # type: ignore[assignment]
    try:
        llm = bpe.evaluate(items)
    finally:
        bpe.parse_brief = orig  # type: ignore[assignment]
    times = prov.times if prov else []
    return {
        "mode": mode, "model": model or LLMSettings().gigachat_model, "min_quote_ratio": gw.settings.min_quote_ratio,
        "n": len(items), "llm_only_skills": M.prf_from_counts(*solo), "rules": base, "llm": llm, "gateway": {**stats, "errors": sorted(set(stats["errors"]))},
        "failure_rate": (stats["no_llm"] / stats["asked"]) if stats["asked"] else None,
        "added_precision": (stats["added_in_gold"] / stats["added"]) if stats["added"] else None,
        "latency_s": {"calls": len(times), "mean": M.mean(times), "p50": M.median(times),
                      "p95": M.percentile(times, 95), "max": max(times) if times else None,
                      "provider_errors": prov.errors if prov else 0},
        "usage": dict(prov.inner.usage) if prov else {}, "added": added_rows,
    }


def fmt(r: dict[str, Any]) -> str:
    a, b = r["rules"], r["llm"]
    lines = [f"режим={r['mode']} модель={r['model']} n={r['n']} min_quote_ratio={r['min_quote_ratio']}"]
    for k, name in (("skills", "F1 навыков"), ("must", "F1 must")):
        lines.append(f"{name}: правила {a[k]['f1']:.3f} (P {a[k]['precision']:.3f} R {a[k]['recall']:.3f})"
                     f" -> +LLM {b[k]['f1']:.3f} (P {b[k]['precision']:.3f} R {b[k]['recall']:.3f})")
    for k in ("spec_top1", "grade_exact", "salary_exact", "salary_basis_accuracy", "formats_exact", "city_accuracy"):
        lines.append(f"{k}: {a[k]:.3f} -> {b[k]:.3f}")
    o = r["llm_only_skills"]
    lines.append(f"одна LLM без правил (навыки): F1 {o['f1']:.3f} (P {o['precision']:.3f} R {o['recall']:.3f})")
    g = r["gateway"]
    fr = r["failure_rate"]
    lines.append(f"шлюз: запросов {g['asked']}, ответов {g['used']}, без LLM {g['no_llm']} (доля {fr:.3f}), "
                 f"предложено полей {g['proposed']}, отброшено цитатой {g['rejected']}, добавлено навыков {g['added']} "
                 f"в {g['added_items']} брифах, из них в эталоне {g['added_in_gold']}; ошибки: {g['errors']}")
    lt = r["latency_s"]
    if lt["calls"]:
        lines.append(f"задержка, с: mean {lt['mean']:.2f} p50 {lt['p50']:.2f} p95 {lt['p95']:.2f} max {lt['max']:.2f}; "
                     f"usage {r['usage']}")
    return "\n".join(lines)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["replay", "record", "live"], default="replay")
    ap.add_argument("--model", default=None)
    ap.add_argument("--min-quote-ratio", type=float, default=None)
    ap.add_argument("--cassette", type=Path, default=CASSETTE)
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args()
    if args.mode != "replay":
        load_env()
    r = run(args.mode, args.model, args.min_quote_ratio, args.cassette)
    print(fmt(r))
    if args.json:
        args.json.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
