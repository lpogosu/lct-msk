"""Админ-инструменты жюри (02-compliance G3, DECISIONS п. 5): импорт кандидатов и брифов, ранжирование, метрики по парам.

Формат импорта — строки набора пар `backend/eval/pairs_test.jsonl` (схема `eval/pairs_test.schema.json`): бриф, кандидат с
признаками подбора (CandidateFeatures как JSON) и разметка rel 0–3. Кандидат без теста (`features: null`) попадает в группу
«категория по опросу, не подтверждена»: он ранжируется по заявленным навыкам и всегда ниже подтверждённых (тот же
движок app.matching.*, что и в подборке работодателя, — формулы здесь не дублируются).

Метрики — P@5, P@10, nDCG@10 по брифам со средним и бутстреп-ДИ — берутся из eval/metrics.py; если пакет eval/ не входит
в образ (в Docker копируется только app/), используется встроенная копия тех же формул.
"""

from __future__ import annotations

import csv
import io
import math
import random
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import ApiError, bad_request, not_found
from app.matching.brief_parser import BriefEditError, parse_brief, set_salary, to_brief_spec
from app.matching.coverage import recommend_categories
from app.matching.scoring import (
    GRADES,
    BriefSpec,
    CandidateFeatures,
    ModuleScore,
    ScoringConfig,
    ScoringContext,
    TaskResult,
    build_category_stats,
    rank_results,
    score_candidate,
)
from app.matching.selection import SelectionItem, build_version
from app.matching.skills import get_catalog
from app.services.demo.cfg import section
from app.services.demo.models import JuryBatch, JuryBrief, JuryCandidate, JuryPair

# --------------------------------------------------------------------------- метрики (eval/metrics.py или копия)

try:  # pragma: no cover — ветка зависит от состава образа
    from eval import metrics as M
except ImportError:  # pragma: no cover

    def _percentile(xs: Sequence[float], q: float) -> float | None:
        if not xs:
            return None
        s = sorted(xs)
        pos = (len(s) - 1) * q / 100.0
        lo, hi = math.floor(pos), math.ceil(pos)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)

    def _precision(ranked_rel: Sequence[int], k: int, threshold: int = 2) -> float:
        return sum(1 for r in ranked_rel[:k] if r >= threshold) / k

    def _dcg(rels: Sequence[int]) -> float:
        return sum((2.0**r - 1.0) / math.log2(i + 2) for i, r in enumerate(rels))

    def _ndcg(ranked_rel: Sequence[int], all_rel: Sequence[int], k: int) -> float:
        ideal = _dcg(sorted(all_rel, reverse=True)[:k])
        return _dcg(list(ranked_rel[:k])) / ideal if ideal > 0 else 0.0

    def _bootstrap_ci(values: Sequence[float], rng: random.Random, *, n_boot: int = 2000, alpha: float = 0.05):
        if not values:
            return None, None
        n = len(values)
        reps = [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(n_boot)]
        return _percentile(reps, 100 * alpha / 2), _percentile(reps, 100 * (1 - alpha / 2))

    M = SimpleNamespace(
        precision_at_k=_precision, ndcg_at_k=_ndcg, bootstrap_ci=_bootstrap_ci, percentile=_percentile
    )


# --------------------------------------------------------------------------- проверка формата импорта

_GOLD_KEYS = ("spec", "grade", "must", "nice", "formats", "salary_from", "salary_to")
_FEATURE_KEYS = (
    "candidate_id", "spec", "grade", "theta", "se", "modules", "tasks", "vss_by_discipline", "declared_skills",
    "salary_min", "salary_net", "formats", "city", "relocation",
)
_TAGS = ("strong", "confirmed", "insufficient", "none")
FORMATS = ("office", "hybrid", "remote")
UNCONFIRMED_NOTE = "категория по опросу, тестом не подтверждена"


def _invalid(where: str, message: str) -> ApiError:
    return bad_request("import_invalid", f"{where}: {message}", {"where": where})


def _need(d: Any, keys: Iterable[str], where: str) -> None:
    if not isinstance(d, Mapping):
        raise _invalid(where, "ожидается объект")
    missing = [k for k in keys if k not in d]
    if missing:
        raise _invalid(where, "нет обязательных полей: " + ", ".join(missing))


def validate_brief(brief: Any, where: str) -> None:
    _need(brief, ("text",), where)
    if not isinstance(brief["text"], str) or not brief["text"].strip():
        raise _invalid(where, "text — непустая строка")
    gold = brief.get("gold")
    if gold is not None:
        _need(gold, _GOLD_KEYS, f"{where}.gold")
        if gold["grade"] not in GRADES:
            raise _invalid(f"{where}.gold.grade", f"грейд из {', '.join(GRADES)}")
        if not isinstance(gold["salary_from"], int | float) or not isinstance(gold["salary_to"], int | float):
            raise _invalid(f"{where}.gold", "salary_from и salary_to — числа")


def validate_candidate(cand: Any, where: str) -> None:
    _need(cand, ("features", "self_description"), where)
    f = cand["features"]
    if f is None:
        return
    _need(f, _FEATURE_KEYS, f"{where}.features")
    if f["grade"] not in GRADES:
        raise _invalid(f"{where}.features.grade", f"грейд из {', '.join(GRADES)}")
    for m, s in (f["modules"] or {}).items():
        _need(s, ("theta", "se", "tag"), f"{where}.features.modules.{m}")
        if s["tag"] not in _TAGS:
            raise _invalid(f"{where}.features.modules.{m}.tag", f"тег из {', '.join(_TAGS)}")
    for i, t in enumerate(f["tasks"] or ()):
        _need(t, ("task_id", "kind", "q", "days_ago"), f"{where}.features.tasks[{i}]")
        if t["kind"] not in ("A", "B") or not 0 <= float(t["q"]) <= 1:
            raise _invalid(f"{where}.features.tasks[{i}]", "kind — A или B, q — от 0 до 1")
    bad = [x for x in f["formats"] or () if x not in FORMATS]
    if bad:
        raise _invalid(f"{where}.features.formats", f"формат из {', '.join(FORMATS)}")


def validate_pair(row: Any, i: int) -> None:
    where = f"pairs[{i}]"
    _need(row, ("brief_id", "brief", "candidate_id", "candidate", "rel"), where)
    for k in ("brief_id", "candidate_id"):
        if not isinstance(row[k], str) or not row[k] or len(row[k]) > 64:
            raise _invalid(f"{where}.{k}", "непустая строка до 64 символов")
    if not isinstance(row["rel"], int) or isinstance(row["rel"], bool) or not 0 <= row["rel"] <= 3:
        raise _invalid(f"{where}.rel", "целое от 0 до 3")
    validate_brief(row["brief"], f"{where}.brief")
    validate_candidate(row["candidate"], f"{where}.candidate")


# --------------------------------------------------------------------------- признаки подбора из JSON


def features_from_json(cid: str, f: Mapping[str, Any]) -> CandidateFeatures:
    """JSON признаков (формат eval/pairs_test.schema.json) → CandidateFeatures: с тестом, полный Score."""
    return CandidateFeatures(
        candidate_id=cid,
        spec=f["spec"],
        grade=f["grade"],
        theta=None if f.get("theta") is None else float(f["theta"]),
        se=float(f.get("se") if f.get("se") is not None else 0.5),
        theta_age_days=float(f.get("theta_age_days") or 0.0),
        test_days_ago=None if f.get("test_days_ago") is None else float(f["test_days_ago"]),
        test_session_id=f.get("test_session_id") or f"import-{cid}",
        borderline=bool(f.get("borderline")),
        trust=float(f["trust"]) if f.get("trust") is not None else 1.0,
        is_fullstack=bool(f.get("is_fullstack")),
        modules={m: ModuleScore(float(s["theta"]), float(s["se"]), str(s["tag"])) for m, s in (f.get("modules") or {}).items()},
        tasks=tuple(
            TaskResult(
                str(t["task_id"]), str(t["kind"]), float(t["q"]), float(t["days_ago"]), str(t.get("title") or ""),
                tuple(t.get("skills") or ()), t.get("module"),
            )
            for t in f.get("tasks") or ()
        ),
        vss_by_discipline={k: float(v) for k, v in (f.get("vss_by_discipline") or {}).items()},
        fsp_summary=f.get("fsp_summary"),
        fsp_ref=f"fsp-{cid}" if any(float(v) > 0 for v in (f.get("vss_by_discipline") or {}).values()) else None,
        declared_skills=frozenset(f.get("declared_skills") or ()),
        salary_min=f.get("salary_min"),
        salary_net=bool(f.get("salary_net", True)),
        salary_visibility=str(f.get("salary_visibility") or "signal"),
        formats=frozenset(f.get("formats") or ()),
        city=f.get("city"),
        relocation=bool(f.get("relocation")),
        industries=frozenset(f.get("industries") or ()),
    )


def cold_start_features(cid: str, cand: Mapping[str, Any]) -> CandidateFeatures | None:
    """Кандидат без теста: категория — заявленная (claimed_spec × claimed_grade), θ нет, навыки — заявленные.

    Навыки берутся из candidate.declared_skills / candidate.survey.declared_skills, иначе — из самоописания по справочнику.
    Такой кандидат попадает в группу «категория по опросу, не подтверждена» и ранжируется ниже подтверждённых.
    """
    spec, grade = cand.get("claimed_spec"), cand.get("claimed_grade")
    if not spec or grade not in GRADES:
        return None
    survey = cand.get("survey") or {}
    declared = list(cand.get("declared_skills") or survey.get("declared_skills") or ())
    if not declared and cand.get("self_description"):
        declared = [m.id for m in get_catalog().find_skills(str(cand["self_description"]))]
    # История ФСП (если жюри её задало): из реестра, а не из самоописания, поэтому F считается как у всех;
    # нет истории — F = 0, это не штраф (концепция §4.2).
    vss = {str(k): float(v) for k, v in (cand.get("vss_by_discipline") or {}).items()}
    return CandidateFeatures(
        candidate_id=cid,
        spec=str(spec),
        grade=str(grade),
        theta=None,
        se=0.5,
        vss_by_discipline=vss,
        fsp_ref=f"fsp-{cid}" if any(v > 0 for v in vss.values()) else None,
        declared_skills=frozenset(declared),
        salary_min=survey.get("salary_min", cand.get("salary_min")),
        salary_net=bool(survey.get("salary_net", cand.get("salary_net", True))),
        formats=frozenset(survey.get("formats", cand.get("formats")) or ()),
        city=survey.get("city", cand.get("city")),
        relocation=bool(survey.get("relocation", cand.get("relocation", False))),
        industries=frozenset(survey.get("industries", cand.get("industries")) or ()),
    )


def candidate_features(cid: str, cand: Mapping[str, Any]) -> CandidateFeatures | None:
    f = cand.get("features")
    return features_from_json(cid, f) if f is not None else cold_start_features(cid, cand)


# --------------------------------------------------------------------------- импорт


def _brief_payload(brief: Mapping[str, Any]) -> dict[str, Any]:
    return {k: brief[k] for k in ("text", "form", "gold", "style") if k in brief}


def store_import(
    db: Session,
    *,
    name: str | None,
    created_by: uuid.UUID | None,
    pairs: Sequence[Mapping[str, Any]],
    briefs: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    extra_warnings: Sequence[str] = (),
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Проверить и сохранить загрузку жюри одним пакетом (batch). Любая ошибка формата — 400 без частичной записи.

    extra_warnings — предупреждения адаптера простого формата (идут первыми), meta — что сохранить о загрузке."""
    cfg = section("admin")["import"]
    if len(pairs) > int(cfg["max_pairs"]):
        raise bad_request("import_too_large", f"Не больше {cfg['max_pairs']} строк за один импорт")
    for i, row in enumerate(pairs):
        validate_pair(row, i)
    brief_rows: dict[str, dict[str, Any]] = {}
    cand_rows: dict[str, dict[str, Any]] = {}
    pair_rows: dict[tuple[str, str], int] = {}
    for row in pairs:
        brief_rows[row["brief_id"]] = _brief_payload(row["brief"])
        cand_rows[row["candidate_id"]] = dict(row["candidate"])
        pair_rows[(row["brief_id"], row["candidate_id"])] = int(row["rel"])
    for i, b in enumerate(briefs):
        _need(b, ("brief_id",), f"briefs[{i}]")
        body = b.get("brief") if isinstance(b.get("brief"), Mapping) else b
        validate_brief(body, f"briefs[{i}]")
        brief_rows[str(b["brief_id"])] = _brief_payload(body)
    for i, c in enumerate(candidates):
        _need(c, ("candidate_id",), f"candidates[{i}]")
        body = c.get("candidate") if isinstance(c.get("candidate"), Mapping) else {k: v for k, v in c.items() if k != "candidate_id"}
        body = dict(body)
        body.setdefault("features", None)
        body.setdefault("self_description", "")
        validate_candidate(body, f"candidates[{i}]")
        cand_rows[str(c["candidate_id"])] = body
    if not (brief_rows or cand_rows):
        raise bad_request("import_empty", "В импорте нет ни брифов, ни кандидатов")
    if len(brief_rows) > int(cfg["max_briefs"]):
        raise bad_request("import_too_large", f"Не больше {cfg['max_briefs']} брифов за один импорт (в файле {len(brief_rows)})")
    if len(cand_rows) > int(cfg["max_candidates"]):
        raise bad_request(
            "import_too_large", f"Не больше {cfg['max_candidates']} кандидатов за один импорт (в файле {len(cand_rows)})"
        )
    warnings: list[str] = list(extra_warnings)
    stored: dict[str, bool] = {}
    for cid, cand in cand_rows.items():
        feats = candidate_features(cid, cand)
        if feats is None:
            warnings.append(f"Кандидат {cid}: нет теста и не заданы claimed_spec/claimed_grade — пропущен")
            continue
        stored[cid] = feats.confirmed
    batch = JuryBatch(
        name=(name or "Импорт жюри")[:200], created_by=created_by, created_at=clock.now(),
        n_briefs=len(brief_rows), n_candidates=len(stored), n_unconfirmed=sum(1 for v in stored.values() if not v),
        n_pairs=len(pair_rows), meta={**(meta or {}), "warnings": warnings[:50]},
    )
    db.add(batch)
    db.flush()
    db.add_all(JuryBrief(batch_id=batch.id, brief_id=bid, payload=p) for bid, p in brief_rows.items())
    db.add_all(
        JuryCandidate(batch_id=batch.id, candidate_id=cid, confirmed=conf, payload=cand_rows[cid]) for cid, conf in stored.items()
    )
    db.add_all(
        JuryPair(batch_id=batch.id, brief_id=b, candidate_id=c, rel=r) for (b, c), r in pair_rows.items() if c in stored
    )
    db.flush()
    return {
        "batch_id": batch.id,
        "name": batch.name,
        "briefs": batch.n_briefs,
        "candidates": batch.n_candidates,
        "confirmed": batch.n_candidates - batch.n_unconfirmed,
        "unconfirmed": batch.n_unconfirmed,
        "pairs": batch.n_pairs,
        "warnings": warnings[:50],
        "note": (
            f"Кандидаты без теста ({batch.n_unconfirmed}) собраны в группу «{section('admin')['unconfirmed_group_label']}»: "
            "они ранжируются по заявленным навыкам и всегда ниже подтверждённых тестом."
            if batch.n_unconfirmed
            else "Все кандидаты с тестом: полный Score."
        ),
    }


def batch_or_404(db: Session, batch_id: uuid.UUID | None, owner: uuid.UUID | None = None) -> JuryBatch:
    """Импорт по id (или последний). owner — только загрузки этого пользователя (методист, вход жюри); чужая
    загрузка неотличима от несуществующей — 404, как в app.core.access (защита от перебора id)."""
    if batch_id is not None:
        b = db.get(JuryBatch, batch_id)
        if b is not None and owner is not None and b.created_by != owner:
            b = None
    else:
        q = select(JuryBatch)
        if owner is not None:
            q = q.where(JuryBatch.created_by == owner)
        b = db.scalar(q.order_by(JuryBatch.created_at.desc()).limit(1))
    if b is None:
        raise not_found("batch_not_found", "Импорт не найден — сначала загрузите данные: POST /api/admin/import")
    return b


def list_batches(db: Session, owner: uuid.UUID | None = None) -> list[JuryBatch]:
    q = select(JuryBatch)
    if owner is not None:
        q = q.where(JuryBatch.created_by == owner)
    return list(db.scalars(q.order_by(JuryBatch.created_at.desc())))


def delete_batch(db: Session, batch_id: uuid.UUID, owner: uuid.UUID | None = None) -> None:
    b = db.get(JuryBatch, batch_id)
    if b is None or (owner is not None and b.created_by != owner):
        raise not_found("batch_not_found", "Импорт не найден")
    db.execute(delete(JuryBatch).where(JuryBatch.id == batch_id))


def batch_pool(db: Session, batch_id: uuid.UUID) -> list[CandidateFeatures]:
    pool: list[CandidateFeatures] = []
    for c in db.scalars(select(JuryCandidate).where(JuryCandidate.batch_id == batch_id).order_by(JuryCandidate.candidate_id)):
        f = candidate_features(c.candidate_id, c.payload)
        if f is not None:
            pool.append(f)
    return pool


# --------------------------------------------------------------------------- бриф → скоринг


def _gold_spec(brief_id: str, g: Mapping[str, Any]) -> BriefSpec:
    return BriefSpec(
        need_id=brief_id,
        spec=g["spec"],
        grade=g["grade"],
        must=tuple(g["must"]),
        nice=tuple(g["nice"]),
        salary_from=int(g["salary_from"]),
        salary_to=int(g["salary_to"]),
        salary_net=True if g.get("salary_net") is None else bool(g["salary_net"]),
        formats=frozenset(g["formats"]),
        city=g.get("city"),
    )


def brief_to_spec(
    brief_id: str, payload: Mapping[str, Any], *, use_gold: bool = False
) -> tuple[BriefSpec, Mapping[str, float], Mapping[str, float], dict[str, Any]]:
    """Бриф → (BriefSpec, P(spec), P(grade), как разобран). По умолчанию — разбор текста правилами (как в продукте);
    use_gold — эталонный разбор жюри (если он есть в импорте). Вилка обязательна: из текста, формы или эталона."""
    gold = payload.get("gold")
    if use_gold and gold:
        sp = {s["code"]: (1.0 if s["code"] == gold["spec"] else 0.0) for s in ScoringConfig.load().taxonomy["specializations"]}
        gp = {g: (1.0 if g == gold["grade"] else 0.0) for g in GRADES}
        return _gold_spec(brief_id, gold), sp, gp, {"source": "gold"}
    form = {k: v for k, v in (payload.get("form") or {}).items() if k in ("salary_from", "salary_to", "salary_net")}
    parsed = parse_brief(str(payload["text"]), **form)
    try:
        spec = to_brief_spec(parsed, brief_id)
    except BriefEditError as e:
        if gold:
            parsed = set_salary(parsed, int(gold["salary_from"]), int(gold["salary_to"]), gold.get("salary_net"))
            try:
                spec = to_brief_spec(parsed, brief_id)
            except BriefEditError as e2:
                raise ApiError(422, "brief_invalid", str(e2)) from e2
        else:
            raise ApiError(422, "brief_invalid", f"{e}. Укажите salary_from и salary_to") from e
    return spec, parsed.spec_probs.value, parsed.grade_probs.value, {
        "source": "parser", "question": parsed.question.field if parsed.question else None
    }


def _counts(pool: Sequence[CandidateFeatures]) -> dict[tuple[str, str], int]:
    out: dict[tuple[str, str], int] = defaultdict(int)
    for f in pool:
        out[(f.spec, f.grade)] += 1
    return dict(out)


def rank_pool(
    brief_id: str,
    payload: Mapping[str, Any],
    pool: Sequence[CandidateFeatures],
    *,
    top: int,
    use_gold: bool = False,
    all_categories: bool = False,
    labels: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Топ-N подборки по брифу: Score, компоненты, «за»/«против». Тот же build_version, что у работодателя."""
    cfg = ScoringConfig.load()
    cat = get_catalog()
    spec, sp, gp, how = brief_to_spec(brief_id, payload, use_gold=use_gold)
    counts = _counts(pool)
    if all_categories:
        cats: list[tuple[str, str]] = sorted(counts)
    else:
        cats = [(r.spec, r.grade) for r in recommend_categories(sp, gp, counts=counts, cfg=cfg)]
    stats = build_category_stats(pool, cfg)
    version = build_version(brief=spec, pool=pool, categories=cats, category_stats=stats, cfg=cfg, catalog=cat)
    items = sorted((i for i in version.items if i.rank is not None), key=lambda i: i.rank or 0)
    label = str(section("admin")["unconfirmed_group_label"])
    rows = [rank_row(i, label, labels) for i in items[:top]]
    return {
        "brief_id": brief_id,
        "brief": {
            "spec": spec.spec, "grade": spec.grade, "must": list(spec.must), "nice": list(spec.nice),
            "salary_from": spec.salary_from, "salary_to": spec.salary_to, "salary_net": spec.salary_net,
            "formats": sorted(spec.formats), "city": spec.city, "parsed": how,
            "category": cfg.category_name(spec.spec, spec.grade),
            "must_names": [cat.name(s) for s in spec.must], "nice_names": [cat.name(s) for s in spec.nice],
        },
        "categories": [{"spec": s, "grade": g, "n": counts.get((s, g), 0), "name": cfg.category_name(s, g)} for s, g in cats],
        "pool_size": len(pool),
        "total": len(items),
        "confirmed": sum(1 for i in items if i.confirmed),
        "unconfirmed": sum(1 for i in items if not i.confirmed),
        "items": rows,
        "scoring_config_version": cfg.version,
    }


def rank_row(i: SelectionItem, unconfirmed_label: str, labels: Mapping[str, str] | None) -> dict[str, Any]:
    return {
        "rank": i.rank,
        "candidate_id": i.candidate_id,
        "source": (labels or {}).get(i.candidate_id, "import"),
        "group": "confirmed" if i.confirmed else "unconfirmed",
        "group_label": "Подтверждена тестом" if i.confirmed else unconfirmed_label,
        "category": i.category,
        "score": i.score_display,
        "components": {k: round(float(v), 4) for k, v in i.components.items()},
        "contributions": {k: round(float(v), 2) for k, v in i.contributions.items()},
        "parts": {k: float(v) for k, v in i.parts.items()},
        "reasons": [{"kind": r.kind, "code": r.code, "component": r.component, "text": r.text} for r in i.reasons],
        "flags": sorted(i.flags),
        "has_fsp": bool(i.has_fsp),
        "chips": [{"name": c.name, "kind": c.kind, "state": c.state, "label": c.label} for c in i.chips],
    }


def rank_csv(result: Mapping[str, Any]) -> str:
    """Тот же топ в CSV (UTF-8): по строке на кандидата, «за» и «против» одной ячейкой."""
    items = result["items"]
    comps = sorted({k for r in items for k in r["components"]}) or ["T", "K", "F", "A", "U"]
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["rank", "candidate_id", "group", "category", "score", *[f"x_{c}" for c in comps], "pros", "cons", "flags"])
    for r in items:
        pros = "; ".join(x["text"] for x in r["reasons"] if x["kind"] == "pro")
        cons = "; ".join(x["text"] for x in r["reasons"] if x["kind"] == "con")
        w.writerow([r["rank"], r["candidate_id"], r["group"], r["category"], r["score"],
                    *[r["components"].get(c, "") for c in comps], pros, cons, " ".join(r["flags"])])
    return buf.getvalue()


# --------------------------------------------------------------------------- метрики по размеченным парам


def _ranked_by_us(spec: BriefSpec, pool: Sequence[CandidateFeatures], ctx: ScoringContext) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for r in rank_results((score_candidate(c, spec, ctx) for c in pool), ctx.cfg):
        if r.candidate_id not in seen:
            seen.add(r.candidate_id)
            out.append(r.candidate_id)
    return out


def _summary(values: Sequence[float], rng: random.Random, n_boot: int, alpha: float, min_briefs: int) -> dict[str, Any]:
    if not values:
        return {"mean": None, "ci_low": None, "ci_high": None, "n_briefs": 0}
    mean = sum(values) / len(values)
    lo = hi = None
    if len(values) >= min_briefs:
        lo, hi = M.bootstrap_ci(list(values), rng, n_boot=n_boot, alpha=alpha)
    return {
        "mean": round(mean, 4),
        "ci_low": None if lo is None else round(lo, 4),
        "ci_high": None if hi is None else round(hi, 4),
        "n_briefs": len(values),
    }


def pairs_metrics(
    rows: Sequence[Mapping[str, Any]], *, use_gold: bool = False, systems: Sequence[str] | None = None
) -> dict[str, Any]:
    """P@5, P@10, nDCG@10 нашей системы по размеченным парам (среднее по брифам и бутстреп-ДИ).

    Для каждого брифа ранжируются кандидаты, размеченные в файле (стандартный пулинг: неразмеченные считаются
    нерелевантными). Если в строках есть `ranks_top20` (наш набор пар: ours, b0–b3), метрики по этим местам считаются так же —
    для сравнения. rows — строки формата eval/pairs_test.schema.json."""
    cfg_m = section("admin")["pairs"]
    ks = [int(k) for k in cfg_m["ks"]]
    nk = int(cfg_m["ndcg_k"])
    thr = int(cfg_m["relevant_from"])
    boot = cfg_m["bootstrap"]
    for i, row in enumerate(rows):
        validate_pair(row, i)
    cfg = ScoringConfig.load()
    by_brief: dict[str, dict[str, Any]] = {}
    feats: dict[str, CandidateFeatures] = {}
    for row in rows:
        b = by_brief.setdefault(row["brief_id"], {"brief": row["brief"], "rel": {}, "pos": defaultdict(dict)})
        cid = row["candidate_id"]
        b["rel"][cid] = int(row["rel"])
        f = candidate_features(cid, row["candidate"])
        if f is None:
            continue
        feats[cid] = f
        for s, p in (row.get("ranks_top20") or {}).items():
            if p is not None:
                b["pos"][s][cid] = int(p)
    ctx_pool = list(feats.values())
    stats = build_category_stats(ctx_pool, cfg)
    ctx = ScoringContext.build(ctx_pool, cfg=cfg, catalog=get_catalog(), category_stats=stats)
    metric_names = [f"p@{k}" for k in ks] + [f"ndcg@{nk}"]
    ours: dict[str, list[float]] = {m: [] for m in metric_names}
    reported: dict[str, dict[str, list[float]]] = {}
    per_brief: list[dict[str, Any]] = []
    for bid in sorted(by_brief):
        b = by_brief[bid]
        rel: dict[str, int] = b["rel"]
        spec, _, _, _ = brief_to_spec(bid, b["brief"], use_gold=use_gold)
        pool = [feats[c] for c in rel if c in feats]
        order = _ranked_by_us(spec, pool, ctx)
        ranked_rel = [rel[c] for c in order]
        all_rel = list(rel.values())
        vals = {f"p@{k}": M.precision_at_k(ranked_rel, k, thr) for k in ks}
        vals[f"ndcg@{nk}"] = M.ndcg_at_k(ranked_rel, all_rel, nk)
        for m, v in vals.items():
            ours[m].append(v)
        per_brief.append({"brief_id": bid, "n_pairs": len(rel), "n_relevant": sum(1 for r in all_rel if r >= thr),
                          **{m: round(v, 4) for m, v in vals.items()}})
        for s, pos in b["pos"].items():
            if systems and s not in systems:
                continue
            order_s = [c for c, _ in sorted(pos.items(), key=lambda x: x[1])]
            rr = [rel.get(c, 0) for c in order_s]
            d = reported.setdefault(s, {m: [] for m in metric_names})
            for k in ks:
                d[f"p@{k}"].append(M.precision_at_k(rr, k, thr))
            d[f"ndcg@{nk}"].append(M.ndcg_at_k(rr, all_rel, nk))
    rng = random.Random(int(boot["seed"]))
    n_boot, alpha, min_briefs = int(boot["n"]), float(boot["alpha"]), int(cfg_m["min_briefs_for_ci"])
    notes = [
        "Ранжируются только кандидаты, размеченные в файле для брифа (пулинг); остальные считаются нерелевантными.",
        f"Релевантен ⇔ rel ≥ {thr}. ДИ — бутстреп по брифам ({n_boot} повторов, {int(100 * (1 - alpha))}%).",
    ]
    if len(by_brief) < min_briefs:
        notes.append("Брифов меньше двух — доверительный интервал не считается.")
    return {
        "n_briefs": len(by_brief),
        "n_pairs": len(rows),
        "n_candidates": len(feats),
        "n_unconfirmed": sum(1 for f in feats.values() if not f.confirmed),
        "ours": {m: _summary(v, rng, n_boot, alpha, min_briefs) for m, v in ours.items()},
        "reported_systems": {
            s: {m: _summary(v, rng, n_boot, alpha, min_briefs) for m, v in d.items()} for s, d in sorted(reported.items())
        },
        "per_brief": per_brief,
        "notes": notes,
        "scoring_config_version": cfg.version,
    }


def rows_from_batch(db: Session, batch_id: uuid.UUID) -> list[dict[str, Any]]:
    """Размеченные пары сохранённого импорта → строки того же формата, что и загрузка."""
    briefs = {b.brief_id: b.payload for b in db.scalars(select(JuryBrief).where(JuryBrief.batch_id == batch_id))}
    cands = {c.candidate_id: c.payload for c in db.scalars(select(JuryCandidate).where(JuryCandidate.batch_id == batch_id))}
    out = []
    for p in db.scalars(select(JuryPair).where(JuryPair.batch_id == batch_id).order_by(JuryPair.brief_id, JuryPair.candidate_id)):
        if p.brief_id in briefs and p.candidate_id in cands:
            out.append({"brief_id": p.brief_id, "brief": briefs[p.brief_id], "candidate_id": p.candidate_id,
                        "candidate": cands[p.candidate_id], "rel": p.rel})
    return out


# --------------------------------------------------------------------------- пул платформы и валидация


def platform_pool(db: Session) -> list[CandidateFeatures]:
    """Видимые работодателям кандидаты платформы (слепой банк) — признаки из кэша подбора."""
    from app.services.matching import features as mf

    mf.ensure_fresh(db)
    return [r.features for r in mf.load_pool(db)]


def blind_platform_ids(result: dict[str, Any]) -> dict[str, Any]:
    """Слепая выдача: id кандидата банка платформы (это id пользователя) заменяется псевдонимом «К-1234» — как у
    работодателя до раскрытия (концепция §4.5): по ответу нельзя сопоставить кандидата с аккаунтом. Псевдоним
    стабилен для кандидата (HMAC с секретом окружения), совпадения внутри одного ответа разрешаются по порядку мест."""
    from app.contact.pseudonym import unique_pseudonym
    from app.core.settings import get_settings

    secret = get_settings().pseudonym_secret.get_secret_value()
    scope = str(section("admin")["check"]["pseudonym_scope"])
    taken: set[str] = set()
    for row in result["items"]:
        if row.get("source") != "platform":
            continue
        alias = unique_pseudonym(secret, scope, str(row["candidate_id"]), taken)
        taken.add(alias)
        row["candidate_id"] = alias
    return result


def validation_result() -> tuple[dict[str, Any], str, str] | None:
    """Последний полный прогон `make validate`: (результат, путь, время файла). None — прогона нет."""
    import json
    from datetime import UTC, datetime
    from pathlib import Path

    rel = str(section("admin")["validation_json"])
    path = Path(__file__).resolve().parents[3] / rel
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    stamp = datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat()
    return data, rel, stamp
