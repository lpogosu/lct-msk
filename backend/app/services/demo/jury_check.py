"""«Проверка подбора на ваших данных» (/lab/check): сводка набора пар и топ по каждому брифу для ручной проверки жюри.

Источник — набор пар из репозитория (`eval/pairs_test.jsonl`) или загрузка жюри (jury_batches). Метрики — те же
`jury_admin.pairs_metrics`, что у POST /api/admin/eval/pairs и в `make validate`. Топ по брифу строится тем же
`build_version`, что подборка работодателя, на том же пуле и с той же статистикой категорий, что и метрики: порядок
в топе совпадает с порядком, по которому посчитан P@k, поэтому P@10 можно пересчитать руками по галочкам ✓/✗.

Бриф без разметки (только в загрузке) ранжируется как в продукте: все кандидаты загрузки в рекомендованных категориях.
"""

from __future__ import annotations

import csv
import io
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ApiError, not_found
from app.matching.coverage import recommend_categories
from app.matching.scoring import CandidateFeatures, ScoringConfig, build_category_stats
from app.matching.selection import SelectionItem, build_version
from app.matching.skills import get_catalog
from app.services.demo import jury_admin as svc
from app.services.demo.cfg import section
from app.services.demo.models import JuryBatch, JuryBrief, JuryCandidate

BACKEND_DIR = Path(__file__).resolve().parents[3]


@dataclass
class Dataset:
    """Набор для проверки: размеченные пары, все брифы и кандидаты (у загрузки могут быть и без разметки)."""

    source: str  # repo | batch
    title: str
    rows: list[dict[str, Any]]
    briefs: dict[str, dict[str, Any]]
    candidates: dict[str, dict[str, Any]]
    batch: JuryBatch | None = None
    meta: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- источники


def repo_path() -> Path:
    return BACKEND_DIR / str(section("admin")["check"]["repo_pairs"])


@lru_cache(maxsize=2)
def _repo_rows(path: str, mtime_ns: int) -> tuple[dict[str, Any], ...]:  # noqa: ARG001 — mtime в ключе кэша
    text = Path(path).read_text(encoding="utf-8")
    return tuple(json.loads(line) for line in text.splitlines() if line.strip())


def repo_dataset() -> Dataset:
    """Набор пар из репозитория (тот же файл, что в VALIDATION.md, раздел 5). Читается один раз на версию файла."""
    path = repo_path()
    if not path.exists():
        raise not_found("pairs_missing", f"Файла набора пар нет на сервере: {section('admin')['check']['repo_pairs']}")
    rows = [dict(r) for r in _repo_rows(str(path), path.stat().st_mtime_ns)]
    briefs: dict[str, dict[str, Any]] = {}
    cands: dict[str, dict[str, Any]] = {}
    for r in rows:
        briefs.setdefault(r["brief_id"], r["brief"])
        cands.setdefault(r["candidate_id"], r["candidate"])
    return Dataset("repo", "Наш набор пар (eval/pairs_test.jsonl)", rows, briefs, cands, meta={"file": path.name})


def batch_dataset(db: Session, batch: JuryBatch) -> Dataset:
    rows = svc.rows_from_batch(db, batch.id)
    briefs = {b.brief_id: dict(b.payload) for b in db.scalars(select(JuryBrief).where(JuryBrief.batch_id == batch.id))}
    cands = {
        c.candidate_id: dict(c.payload)
        for c in db.scalars(select(JuryCandidate).where(JuryCandidate.batch_id == batch.id))
    }
    return Dataset("batch", batch.name, rows, briefs, cands, batch=batch, meta=dict(batch.meta or {}))


# --------------------------------------------------------------------------- общее


def _natural(s: str) -> tuple[Any, ...]:
    return tuple(int(p) if p.isdigit() else p for p in re.split(r"(\d+)", s))


def _cfg() -> Mapping[str, Any]:
    return section("admin")


def _skills(ids: Sequence[str]) -> list[dict[str, str]]:
    cat = get_catalog()
    return [{"id": s, "name": cat.name(s) if s in cat.skills else s} for s in ids]


def _city_name(code: Any) -> str | None:
    if not code:
        return None
    c = get_catalog().cities.get(str(code))
    return c.name if c else str(code)


def _parse(brief_id: str, payload: Mapping[str, Any], use_gold: bool) -> tuple[Any, Any, Any, dict[str, Any]] | str:
    """(BriefSpec, P(spec), P(grade), как разобран) или текст ошибки (нет вилки и т. п.)."""
    try:
        return svc.brief_to_spec(brief_id, payload, use_gold=use_gold)
    except ApiError as e:
        return e.message


def _brief_view(brief_id: str, payload: Mapping[str, Any], parsed: Any, cfg: ScoringConfig) -> dict[str, Any]:
    gold = payload.get("gold")
    out: dict[str, Any] = {
        "brief_id": brief_id,
        "text": str(payload.get("text") or ""),
        "form": payload.get("form") or None,
        "gold": None,
        "parsed": None,
        "parse_error": None,
    }
    if gold:
        out["gold"] = {
            "spec": gold.get("spec"), "grade": gold.get("grade"),
            "category": cfg.category_name(str(gold.get("spec")), str(gold.get("grade"))),
            "must": _skills(gold.get("must") or ()), "nice": _skills(gold.get("nice") or ()),
            "salary_from": gold.get("salary_from"), "salary_to": gold.get("salary_to"), "salary_net": gold.get("salary_net"),
            "formats": list(gold.get("formats") or ()), "city": _city_name(gold.get("city")),
        }
    if isinstance(parsed, str):
        out["parse_error"] = parsed
        return out
    spec, _, _, how = parsed
    out["parsed"] = {
        "spec": spec.spec, "grade": spec.grade, "category": cfg.category_name(spec.spec, spec.grade),
        "must": _skills(spec.must), "nice": _skills(spec.nice),
        "salary_from": spec.salary_from, "salary_to": spec.salary_to, "salary_net": spec.salary_net,
        "formats": sorted(spec.formats), "city": _city_name(spec.city), "source": how.get("source"),
    }
    return out


def _profile(cand: Mapping[str, Any]) -> dict[str, Any]:
    """Что жюри видит о кандидате своего набора для ручной проверки (это синтетические данные набора, не ПДн платформы).
    Самоописание и заявленные навыки показываются, но в Score не входят."""
    f = cand.get("features")
    src: Mapping[str, Any] = f if isinstance(f, Mapping) else cand
    survey = cand.get("survey") or {}
    declared = list(src.get("declared_skills") or cand.get("declared_skills") or survey.get("declared_skills") or ())
    return {
        "confirmed": f is not None,
        "claimed_spec": cand.get("claimed_spec"),
        "claimed_grade": cand.get("claimed_grade"),
        "experience_months": cand.get("experience_months"),
        "self_description": str(cand.get("self_description") or ""),
        "declared_skills": [s["name"] for s in _skills(declared)],
        "salary_min": src.get("salary_min", survey.get("salary_min")),
        "salary_net": src.get("salary_net", survey.get("salary_net")),
        "formats": list(src.get("formats") or survey.get("formats") or ()),
        "city": _city_name(src.get("city") or survey.get("city")),
        "fsp_line": cand.get("fsp_line"),
    }


def _item(
    i: SelectionItem, unconfirmed_label: str, rel: Mapping[str, int] | None, thr: int, profile: Mapping[str, Any] | None
) -> dict[str, Any]:
    row = svc.rank_row(i, unconfirmed_label, None)  # тот же формат строки, что у /api/admin/eval/rank
    r = None if rel is None else rel.get(i.candidate_id)
    row["rel"] = r
    row["relevant"] = None if rel is None else (r is not None and r >= thr)
    row["profile"] = dict(profile) if profile is not None else None
    return row


# --------------------------------------------------------------------------- сводка


def summary(ds: Dataset, *, use_gold: bool = False) -> dict[str, Any]:
    """Метрики набора (если есть разметка) и список брифов с P@k по каждому. Брифы, которые не разбираются (нет вилки),
    не валят сводку: они помечаются ошибкой и в метрики не входят."""
    cfg_a = _cfg()
    ks = [int(k) for k in cfg_a["pairs"]["ks"]]
    nk = int(cfg_a["pairs"]["ndcg_k"])
    thr = int(cfg_a["pairs"]["relevant_from"])
    preview = int(cfg_a["check"]["text_preview_chars"])
    scfg = ScoringConfig.load()
    parsed = {bid: _parse(bid, p, use_gold) for bid, p in ds.briefs.items()}
    bad = {bid for bid, p in parsed.items() if isinstance(p, str)}
    rows = [r for r in ds.rows if r["brief_id"] not in bad]
    metrics = svc.pairs_metrics(rows, use_gold=use_gold) if rows else None
    per = {b["brief_id"]: b for b in (metrics or {}).get("per_brief", [])}
    labeled: dict[str, int] = {}
    for r in ds.rows:
        labeled[r["brief_id"]] = labeled.get(r["brief_id"], 0) + 1
    briefs = []
    for bid in sorted(ds.briefs, key=_natural):
        view = _brief_view(bid, ds.briefs[bid], parsed[bid], scfg)
        text = view["text"]
        m = per.get(bid)
        briefs.append({
            "brief_id": bid,
            "preview": text if len(text) <= preview else text[: preview - 1].rstrip() + "…",
            "category": (view["parsed"] or {}).get("category"),
            "gold_category": (view["gold"] or {}).get("category"),
            "salary_from": (view["parsed"] or {}).get("salary_from"),
            "salary_to": (view["parsed"] or {}).get("salary_to"),
            "salary_net": (view["parsed"] or {}).get("salary_net"),
            "parse_error": view["parse_error"],
            "n_pairs": labeled.get(bid, 0),
            "n_relevant": m["n_relevant"] if m else None,
            "metrics": {k: v for k, v in m.items() if k not in ("brief_id", "n_pairs", "n_relevant")} if m else None,
        })
    notes = list((metrics or {}).get("notes", []))
    small = [b["brief_id"] for b in briefs if b["metrics"] and 0 < b["n_pairs"] < max(ks)]
    if small:
        notes.append(
            f"У {len(small)} брифов размечено меньше {max(ks)} кандидатов: пустые места топа считаются нерелевантными, поэтому "
            f"P@{max(ks)} у них не выше «релевантных / {max(ks)}»."
        )
    if bad:
        notes.append(f"Брифы без вилки или с ошибкой разбора ({', '.join(sorted(bad, key=_natural))}) в метрики не вошли.")
    unlabeled = [b["brief_id"] for b in briefs if not b["n_pairs"]]
    if unlabeled:
        notes.append(
            f"Брифов без разметки: {len(unlabeled)} — для них показывается топ по всем кандидатам загрузки "
            "в рекомендованных категориях, без метрик."
        )
    n_unconf = sum(1 for c in ds.candidates.values() if c.get("features") is None)
    systems = cfg_a["check"]["systems"]
    return {
        "source": ds.source,
        "title": ds.title,
        "batch_id": ds.batch.id if ds.batch else None,
        "created_at": ds.batch.created_at if ds.batch else None,
        "n_briefs": len(ds.briefs),
        "n_labeled_briefs": sum(1 for b in briefs if b["n_pairs"]),
        "n_pairs": len(ds.rows),
        "n_candidates": len(ds.candidates),
        "n_unconfirmed": n_unconf,
        "relevant_from": thr,
        "ks": ks,
        "ndcg_k": nk,
        "metrics": None if metrics is None else {
            "ours": metrics["ours"],
            "reported_systems": metrics["reported_systems"],
            "n_briefs": metrics["n_briefs"],
        },
        "system_labels": {k: str(v) for k, v in systems.items()},
        "briefs": briefs,
        "warnings": list(ds.meta.get("warnings") or [])[:50],
        "notes": notes,
        "scoring_config_version": scfg.version,
    }


@lru_cache(maxsize=4)
def _repo_summary(mtime_ns: int, use_gold: bool) -> dict[str, Any]:  # noqa: ARG001 — mtime в ключе кэша
    return summary(repo_dataset(), use_gold=use_gold)


def repo_summary(*, use_gold: bool = False) -> dict[str, Any]:
    """Сводка по набору из репозитория: файл не меняется между запросами — считаем один раз на версию файла."""
    path = repo_path()
    if not path.exists():
        return summary(repo_dataset(), use_gold=use_gold)  # бросит понятную 404
    return _repo_summary(path.stat().st_mtime_ns, use_gold)


# --------------------------------------------------------------------------- бриф


def brief_detail(ds: Dataset, brief_id: str, *, top: int, use_gold: bool = False) -> dict[str, Any]:
    """Бриф: текст, как разобран, топ-N с Score, категорией, «за»/«против» и релевантностью по разметке (0–3, ✓/✗)."""
    if brief_id not in ds.briefs:
        raise not_found("brief_not_found", f"Бриф «{brief_id}» не найден в наборе")
    cfg_a = _cfg()
    thr = int(cfg_a["pairs"]["relevant_from"])
    ks = [int(k) for k in cfg_a["pairs"]["ks"]]
    nk = int(cfg_a["pairs"]["ndcg_k"])
    label = str(cfg_a["unconfirmed_group_label"])
    scfg = ScoringConfig.load()
    cat = get_catalog()
    payload = ds.briefs[brief_id]
    parsed = _parse(brief_id, payload, use_gold)
    view = _brief_view(brief_id, payload, parsed, scfg)
    if isinstance(parsed, str):
        raise ApiError(422, "brief_invalid", f"Бриф «{brief_id}» не разобран: {parsed}")
    spec, sp, gp, _ = parsed
    rel = {r["candidate_id"]: int(r["rel"]) for r in ds.rows if r["brief_id"] == brief_id}
    notes: list[str] = []
    if rel:
        # Тот же пул и та же статистика категорий, что в pairs_metrics: все размеченные кандидаты набора
        feats: dict[str, CandidateFeatures] = {}
        for r in ds.rows:
            f = svc.candidate_features(r["candidate_id"], r["candidate"])
            if f is not None:
                feats[r["candidate_id"]] = f
        stats = build_category_stats(list(feats.values()), scfg)
        pool = [feats[c] for c in rel if c in feats]
        cats = sorted({(f.spec, f.grade) for f in pool})
        mode = "labeled"
        notes.append("Ранжируются кандидаты, размеченные для этого брифа, — тот же порядок, по которому посчитаны P@k.")
    else:
        pool = [f for cid, c in sorted(ds.candidates.items()) if (f := svc.candidate_features(cid, c)) is not None]
        stats = build_category_stats(pool, scfg)
        counts: dict[tuple[str, str], int] = {}
        for f in pool:
            counts[(f.spec, f.grade)] = counts.get((f.spec, f.grade), 0) + 1
        cats = [(r.spec, r.grade) for r in recommend_categories(sp, gp, counts=counts, cfg=scfg)]
        mode = "pool"
        notes.append("Разметки для брифа нет: показан топ по всем кандидатам загрузки в рекомендованных категориях, как в подборке.")
    version = build_version(brief=spec, pool=pool, categories=cats, category_stats=stats, cfg=scfg, catalog=cat)
    ranked = sorted((i for i in version.items if i.rank is not None), key=lambda i: i.rank or 0)
    rel_or_none = rel if rel else None
    items = [_item(i, label, rel_or_none, thr, _profile(ds.candidates.get(i.candidate_id, {}))) for i in ranked[:top]]
    metrics = None
    below: list[dict[str, Any]] = []
    if rel:
        order = [rel.get(i.candidate_id, 0) for i in ranked]
        metrics = {f"p@{k}": round(svc.M.precision_at_k(order, k, thr), 4) for k in ks}
        metrics[f"ndcg@{nk}"] = round(svc.M.ndcg_at_k(order, list(rel.values()), nk), 4)
        below = [
            {"rank": i.rank, "candidate_id": i.candidate_id, "rel": rel[i.candidate_id], "score": i.score_display,
             "category": i.category, "group": "confirmed" if i.confirmed else "unconfirmed"}
            for i in ranked[top:] if rel.get(i.candidate_id, 0) >= thr
        ][: int(cfg_a["check"]["relevant_below_max"])]
    return {
        "source": ds.source,
        "batch_id": ds.batch.id if ds.batch else None,
        "brief": view,
        "mode": mode,
        "categories": [{"spec": s, "grade": g, "name": scfg.category_name(s, g)} for s, g in cats],
        "pool_size": len(pool),
        "ranked": len(ranked),
        "n_labeled": len(rel),
        "n_relevant": sum(1 for v in rel.values() if v >= thr),
        "relevant_from": thr,
        "metrics": metrics,
        "items": items,
        "relevant_below": below,
        "notes": notes,
        "scoring_config_version": scfg.version,
    }


def tops_csv(ds: Dataset, *, top: int, use_gold: bool = False) -> str:
    """Топ-N по каждому брифу набора одной таблицей (UTF-8): место, кандидат, Score, компоненты, rel, «за»/«против»."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    comps = ["T", "K", "F", "A", "U"]
    w.writerow(["brief_id", "rank", "candidate_id", "group", "category", "score", *[f"x_{c}" for c in comps], "rel",
                "relevant", "pros", "cons", "flags"])
    for bid in sorted(ds.briefs, key=_natural):
        try:
            d = brief_detail(ds, bid, top=top, use_gold=use_gold)
        except ApiError as e:
            w.writerow([bid, "", "", "", "", "", *[""] * len(comps), "", "", "", e.message, ""])
            continue
        for r in d["items"]:
            pros = "; ".join(x["text"] for x in r["reasons"] if x["kind"] == "pro")
            cons = "; ".join(x["text"] for x in r["reasons"] if x["kind"] == "con")
            relevant = "" if r["relevant"] is None else ("1" if r["relevant"] else "0")
            w.writerow([bid, r["rank"], r["candidate_id"], r["group"], r["category"], r["score"],
                        *[r["components"].get(c, "") for c in comps], "" if r["rel"] is None else r["rel"], relevant,
                        pros, cons, " ".join(r["flags"])])
    return buf.getvalue()
