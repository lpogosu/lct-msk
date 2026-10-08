"""Валидация подбора (концепция §7.1–7.2, spec-contact §6, DECISIONS п.1, 5, 11).

Все решения принимает прод-код: разбор брифа `brief_parser.parse_brief`, рекомендация категорий
`coverage.recommend_categories`, версия подборки `selection.build_version` (Score, объяснения), скоринг
`scoring.score_candidate`. Здесь — оракул релевантности по скрытой истине генератора, бейзлайны
(B0 случайный, B1 BM25, B2 категория + точечная оценка, B3 TF-IDF), абляции, устойчивость, экспозиция
кандидатов без ФСП, честность объяснений и экспорт набора пар для жюри.
"""

from __future__ import annotations

import bisect
import json
import statistics
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from app.matching.brief_parser import (
    BriefEditError,
    ParsedBrief,
    answer_question,
    parse_brief,
    set_grade,
    set_salary,
    set_spec,
    to_brief_spec,
)
from app.matching.coverage import recommend_categories
from app.matching.scoring import (
    COMPONENTS,
    GRADES,
    BriefSpec,
    CandidateFeatures,
    ScoringConfig,
    ScoringContext,
    build_category_stats,
    rank_results,
    score_candidate,
    validate_weights,
)
from app.matching.selection import SelectionVersion, build_version
from app.matching.skills import SkillCatalog, get_catalog
from eval import metrics as M
from eval.common import grade_index, stream
from eval.population import Candidate, Population
from eval.textsearch import BM25, TfIdf, rank_positions

BRIEFS_DIR = Path(__file__).resolve().parent / "briefs"
RELEVANT = 2  # релевантен ⇔ rel ≥ 2 (концепция §7.1)
FORMAT_OK = 0.7  # посадка формата не ниже этой величины считается «согласен работать так» (офис↔гибрид 0,7)
SYSTEMS = ("ours", "ours_gold", "ours_noq", "b0", "b1", "b2", "b3")
SYSTEM_LABELS = {
    "ours": "Наш (сквозной: разбор брифа + 1 уточнение)",
    "ours_gold": "Наш (эталонный бриф)",
    "ours_noq": "Наш (только парсер, без уточнения)",
    "b0": "B0 случайный порядок в тех же категориях",
    "b1": "B1 BM25 по самоописанию (как поиск по резюме)",
    "b2": "B2 категория + точечная оценка уровня",
    "b3": "B3 TF-IDF «бриф ↔ самоописание» (заменитель эмбеддингов)",
}
ABLATIONS = ("no_lcb", "no_K", "no_F", "no_A", "no_U")
ABLATION_LABELS = {
    "no_lcb": "без нижней границы (точечная оценка, SE = 0)",
    "no_K": "без K (стек)",
    "no_F": "без F (ФСП)",
    "no_A": "без A (свежесть)",
    "no_U": "без U (условия)",
}
# Кандидаты на «добавленный nice-навык» (берём первый, которого нет в брифе).
EXTRA_NICE = {
    "backend_python": ("redis", "docker", "git", "linux", "celery"),
    "frontend_js": ("jest", "git", "sass", "webpack", "typescript"),
    "fullstack": ("docker", "git", "redis", "jest"),
    "qa": ("docker", "git", "linux", "jenkins", "bash"),
    "devops": ("python", "git", "redis", "aws", "grafana"),
}


# --------------------------------------------------------------------------- брифы


@dataclass(frozen=True)
class Brief:
    id: str
    split: str
    style: str
    author: str
    text: str
    form: Mapping[str, Any] | None
    gold: Mapping[str, Any]
    note: str | None = None


def load_briefs(path: Path = BRIEFS_DIR) -> list[Brief]:
    out = []
    for f in sorted(path.glob("b*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        out.append(Brief(d["id"], d["split"], d["style"], d.get("author", "team"), d["text"], d.get("form"),
                         d["gold"], d.get("note")))
    return out


def gold_brief_spec(b: Brief) -> BriefSpec:
    g = b.gold
    return BriefSpec(
        need_id=b.id,
        spec=g["spec"],
        grade=g["grade"],
        must=tuple(g["must"]),
        nice=tuple(g["nice"]),
        salary_from=g["salary_from"],
        salary_to=g["salary_to"],
        salary_net=True if g.get("salary_net") is None else bool(g["salary_net"]),
        formats=frozenset(g["formats"]),
        city=g.get("city"),
    )


# --------------------------------------------------------------------------- оракул


def spec_adjacency(cfg: ScoringConfig, a: str, b: str) -> float:
    if a == b:
        return 1.0
    for x, y, w in cfg.section("G")["spec_adjacency"]:
        if {x, y} == {a, b}:
            return float(w)
    return 0.0


def skill_covered(c: Candidate, skill: str, threshold: float, cat: SkillCatalog) -> bool:
    """Навык покрыт ⇔ он в истинном стеке И (не тестируется ИЛИ истинный уровень одного из модулей ≥ порога)."""
    if skill not in c.truth.skills:
        return False
    sk = cat.skills.get(skill)
    modules = cat.modules_of(skill) if sk is not None else ()
    if sk is None or sk.untestable or not modules:
        return True
    # Модули QA и DevOps (qa_testing, devops_core) есть в истине только у кандидатов этих специализаций. У остальных
    # уровень такого модуля не определён, и навык (Docker у Backend-разработчика) покрывается истинным стеком, как и
    # раньше, когда Docker не тестировался: иначе подключение теста DevOps сдвинуло бы релевантность по Backend/Frontend.
    levels = [c.truth.module_theta[m] for m in modules if m in c.truth.module_theta]
    return max(levels) >= threshold if levels else True


def oracle_rel(c: Candidate, gold: Mapping[str, Any], cfg: ScoringConfig, cat: SkillCatalog, *, conditions: bool = True) -> int:
    """rel ∈ {0..3} по скрытой истине (VALIDATION_PLAN §1.5). conditions=False — прежний оракул v1 без формата и города."""
    if spec_adjacency(cfg, c.truth.spec, gold["spec"]) < 0.8 - 1e-9:
        return 0
    rel = 3
    d = abs(grade_index(c.truth.grade) - GRADES.index(gold["grade"]))
    if d >= 2:
        return 0
    if d == 1:
        rel -= 1
    must = list(gold.get("must") or ())
    if must:
        thr = float(cfg.cuts[gold["grade"]]) - 0.5
        covered = sum(1 for s in must if skill_covered(c, s, thr, cat))
        if covered < 0.5 * len(must):
            rel -= 2
        elif covered < len(must):
            rel -= 1
    to = gold.get("salary_to")
    if to and c.salary_min is not None:
        ndfl = float(cfg.section("U")["ndfl_rate"])
        to_net = float(to) if gold.get("salary_net") in (True, None) else float(to) * (1.0 - ndfl)
        if c.salary_min > 1.1 * to_net:
            rel -= 1
    if conditions and not format_city_ok(c, gold, cfg):
        rel -= 1
    return max(0, rel)


def format_city_ok(c: Candidate, gold: Mapping[str, Any], cfg: ScoringConfig) -> bool:
    """Формат и город: работодатель не назовёт релевантным того, кто не согласен работать так, как написано в брифе.

    Формат совместим, если для какого-то формата брифа и приемлемого формата кандидата посадка из
    `scoring.yaml → U.format_fit` не ниже FORMAT_OK (офис↔гибрид 0,7 — допустимо; удалёнка↔офис 0,3 и 0 — нет).
    Город важен только для офиса и гибрида: другой город без готовности к релокации — несовместимо.
    Ярлык не зависит от Score: берёт только скрытые настройки кандидата (VALIDATION_PLAN §1.5, правка v2).
    """
    bf = set(gold.get("formats") or ())
    if not bf:
        return True
    matrix = cfg.section("U")["format_fit"]
    city_bad = bool(gold.get("city") and c.city and gold["city"] != c.city and not c.relocation)
    for f in sorted(bf):
        if f == "remote":
            fit = max((float(matrix[f].get(cf, 0.0)) for cf in c.formats), default=1.0)
            if fit >= FORMAT_OK:
                return True
            continue
        if city_bad:
            continue
        fit = max((float(matrix[f].get(cf, 0.0)) for cf in c.formats), default=1.0)
        if fit >= FORMAT_OK:
            return True
    return False


IRRELEVANCE_LABELS = {
    "spec": "другая специализация",
    "grade_far": "грейд дальше чем на ступень",
    "grade_above": "грейд на ступень выше брифа, покрыты не все must или минимум выше вилки",
    "grade_below": "грейд на ступень ниже брифа, покрыты не все must или минимум выше вилки",
    "must_lt_half": "грейд совпал, покрыто меньше половины must",
    "partial_and_salary": "грейд совпал, покрыты не все must и минимум выше вилки",
    "conditions": "грейд и must подходят, но формат/город или зарплата не подходят",
}


def irrelevance_reason(c: Candidate, gold: Mapping[str, Any], cfg: ScoringConfig, cat: SkillCatalog) -> str | None:
    """Почему кандидат нерелевантен по оракулу (None — релевантен). Описательная диагностика, не порог."""
    if oracle_rel(c, gold, cfg, cat) >= RELEVANT:
        return None
    if spec_adjacency(cfg, c.truth.spec, gold["spec"]) < 0.8 - 1e-9:
        return "spec"
    d = grade_index(c.truth.grade) - GRADES.index(gold["grade"])
    if abs(d) >= 2:
        return "grade_far"
    if d == 1:
        return "grade_above"
    if d == -1:
        return "grade_below"
    must = list(gold.get("must") or ())
    thr = float(cfg.cuts[gold["grade"]]) - 0.5
    covered = sum(1 for s in must if skill_covered(c, s, thr, cat))
    if must and covered < 0.5 * len(must):
        return "must_lt_half"
    return "conditions" if covered == len(must) else "partial_and_salary"


# --------------------------------------------------------------------------- контекст


@dataclass
class World:
    """Популяция + всё, что строится один раз: признаки, статистика категорий, индексы текста."""

    pop: Population
    cfg: ScoringConfig
    cat: SkillCatalog
    pool: list[CandidateFeatures]
    stats: Mapping[tuple[str, str], Sequence[float]]
    counts: dict[tuple[str, str], int]
    by_id: dict[str, Candidate]
    feat_by_id: dict[str, CandidateFeatures]
    bm25: BM25
    tfidf: TfIdf

    @classmethod
    def build(cls, pop: Population) -> World:
        cfg = ScoringConfig.load()
        pool = pop.features()
        counts: dict[tuple[str, str], int] = {}
        for f in pool:
            counts[(f.spec, f.grade)] = counts.get((f.spec, f.grade), 0) + 1
        docs = {c.id: c.self_description for c in pop.candidates}
        return cls(
            pop=pop,
            cfg=cfg,
            cat=get_catalog(),
            pool=pool,
            stats=build_category_stats(pool, cfg),
            counts=counts,
            by_id=pop.by_id(),
            feat_by_id={f.candidate_id: f for f in pool},
            bm25=BM25(docs),
            tfidf=TfIdf(docs),
        )

    def rels(self, gold: Mapping[str, Any], *, conditions: bool = True) -> dict[str, int]:
        return {c.id: oracle_rel(c, gold, self.cfg, self.cat, conditions=conditions) for c in self.pop.candidates}


@dataclass
class BriefRun:
    """Разбор брифа и категории в сквозном режиме (с уточнением и без)."""

    parsed: ParsedBrief  # как разобрал парсер (до правок)
    final: ParsedBrief  # после ответа на уточняющий вопрос и обязательной формы вилки
    question: str | None
    salary_filled: bool
    spec: BriefSpec
    categories: tuple[tuple[str, str], ...]
    spec_noq: BriefSpec
    categories_noq: tuple[tuple[str, str], ...]


def _with_salary(p: ParsedBrief, gold: Mapping[str, Any]) -> tuple[ParsedBrief, bool]:
    """Вилка обязательна (форма): если парсер её не нашёл или она неполная — работодатель вводит её в форме."""
    s = p.salary.value if p.salary else None
    if s is None or s.from_ is None or s.to is None or p.salary_check.status in ("blocked", "invalid"):
        return set_salary(p, gold["salary_from"], gold["salary_to"], gold.get("salary_net")), True
    return p, False


def _categories(p: ParsedBrief, counts: Mapping[tuple[str, str], int], cfg: ScoringConfig) -> tuple[tuple[str, str], ...]:
    recs = recommend_categories(p.spec_probs.value, p.grade_probs.value, counts=counts, cfg=cfg)
    return tuple((r.spec, r.grade) for r in recs)


def run_brief(b: Brief, world: World) -> BriefRun:
    form = dict(b.form or {})
    parsed = parse_brief(b.text, **form)
    q = parsed.question
    final = parsed
    if q is not None and q.field in ("spec", "grade"):
        want = b.gold["spec"] if q.field == "spec" else b.gold["grade"]
        if want in [o[0] for o in q.options]:
            final = answer_question(parsed, want)
        else:  # нужного варианта нет среди двух — работодатель правит поле вручную
            final = set_spec(parsed, want) if q.field == "spec" else set_grade(parsed, want)
    final, filled = _with_salary(final, b.gold)
    noq, _ = _with_salary(parsed, b.gold)
    try:
        spec = to_brief_spec(final, b.id)
    except BriefEditError:
        final = set_salary(final, b.gold["salary_from"], b.gold["salary_to"], b.gold.get("salary_net"))
        spec, filled = to_brief_spec(final, b.id), True
    try:
        spec_noq = to_brief_spec(noq, b.id)
    except BriefEditError:
        noq = set_salary(noq, b.gold["salary_from"], b.gold["salary_to"], b.gold.get("salary_net"))
        spec_noq = to_brief_spec(noq, b.id)
    return BriefRun(
        parsed=parsed,
        final=final,
        question=q.field if q else None,
        salary_filled=filled,
        spec=spec,
        categories=_categories(final, world.counts, world.cfg),
        spec_noq=spec_noq,
        categories_noq=_categories(noq, world.counts, world.cfg),
    )


def gold_categories(b: Brief, world: World) -> tuple[tuple[str, str], ...]:
    """Эталонный бриф: P(spec) = P(grade) = 1, смежная категория — по правилу N < 15 (как в продукте)."""
    sp = {s["code"]: (1.0 if s["code"] == b.gold["spec"] else 0.0) for s in world.cfg.taxonomy["specializations"]}
    gp = {g: (1.0 if g == b.gold["grade"] else 0.0) for g in GRADES}
    recs = recommend_categories(sp, gp, counts=world.counts, cfg=world.cfg)
    return tuple((r.spec, r.grade) for r in recs)


# --------------------------------------------------------------------------- ранжирование


def _ctx(world: World, weights: Mapping[str, float] | None = None, *, pool: Sequence[CandidateFeatures] | None = None,
         stats: Mapping[tuple[str, str], Sequence[float]] | None = None) -> ScoringContext:
    return ScoringContext.build(pool if pool is not None else world.pool, weights=weights, cfg=world.cfg,
                                catalog=world.cat, category_stats=stats if stats is not None else world.stats)


def rank_fast(spec: BriefSpec, categories: Sequence[tuple[str, str]], pool: Sequence[CandidateFeatures],
              ctx: ScoringContext) -> list[str]:
    """Тот же порядок, что «Общий топ» build_version без фильтров (проверяется тестом), но без объяснений."""
    cats = set(categories)
    res = rank_results((score_candidate(c, spec, ctx) for c in pool if (c.spec, c.grade) in cats), ctx.cfg)
    seen: set[str] = set()
    out = []
    for r in res:
        if r.candidate_id not in seen:
            seen.add(r.candidate_id)
            out.append(r.candidate_id)
    return out


def build_ours(spec: BriefSpec, categories: Sequence[tuple[str, str]], world: World) -> tuple[SelectionVersion, float]:
    t0 = time.perf_counter()
    v = build_version(brief=spec, pool=world.pool, categories=categories, category_stats=world.stats,
                      cfg=world.cfg, catalog=world.cat)
    return v, (time.perf_counter() - t0) * 1000.0


def in_scope(categories: Sequence[tuple[str, str]], pool: Sequence[CandidateFeatures]) -> list[CandidateFeatures]:
    cats = set(categories)
    return [c for c in pool if (c.spec, c.grade) in cats]


def rank_b0(b: Brief, categories: Sequence[tuple[str, str]], world: World, seed: int) -> list[str]:
    ids = sorted({c.candidate_id for c in in_scope(categories, world.pool)})
    stream(f"b0:{b.id}", seed).shuffle(ids)
    return ids


def b2_category(categories: Sequence[tuple[str, str]], world: World) -> tuple[str, str] | None:
    """Первая рекомендованная категория, в которой есть кандидаты («минимальное прочтение ТЗ»)."""
    return next((c for c in categories if world.counts.get(c, 0) > 0), None)


def rank_b2(categories: Sequence[tuple[str, str]], pool: Sequence[CandidateFeatures], world: World) -> list[str]:
    cat = b2_category(categories, world)
    if cat is None:
        return []
    rows = [c for c in pool if (c.spec, c.grade) == cat and c.theta is not None]
    return [c.candidate_id for c in sorted(rows, key=lambda c: (-float(c.theta or 0.0), c.candidate_id))]


# --------------------------------------------------------------------------- метрики по брифу


def brief_metrics(ranking: Sequence[str], rels: Mapping[str, int]) -> dict[str, float | None]:
    ranked_rel = [rels.get(cid, 0) for cid in ranking]
    total_rel = sum(1 for r in rels.values() if r >= RELEVANT)
    return {
        "p10": M.precision_at_k(ranked_rel, 10, RELEVANT),
        "p5": M.precision_at_k(ranked_rel, 5, RELEVANT),
        "ndcg10": M.ndcg_at_k(ranked_rel, list(rels.values()), 10),
        "recall50": M.recall_at_k(ranked_rel, total_rel, 50, RELEVANT),
        "mrr": M.reciprocal_rank(ranked_rel, RELEVANT),
    }


METRIC_KEYS = ("p10", "p5", "ndcg10", "recall50", "mrr")


def parser_metrics(briefs: Sequence[Brief], runs: Mapping[str, BriefRun]) -> dict[str, Any]:
    """Разбор брифа против эталона (до правок работодателя)."""
    spec_ok, g_exact, g_w1, kind_ok, cat3, sal_ok, net_ok, fmt_ok, city_ok, q = [], [], [], [], [], [], [], [], [], []
    tp = fp = fn = 0
    tp_m = fp_m = fn_m = 0
    rows = []
    for b in briefs:
        p, g = runs[b.id].parsed, b.gold
        spec_ok.append(float(p.top_spec == g["spec"]))
        d = abs(GRADES.index(p.top_grade) - GRADES.index(g["grade"]))
        g_exact.append(float(d == 0))
        g_w1.append(float(d <= 1))
        ps, gs = set(p.must) | set(p.nice), set(g["must"]) | set(g["nice"])
        a, b_, c_ = M.set_prf(gs, ps)
        tp, fp, fn = tp + a, fp + b_, fn + c_
        a, b_, c_ = M.set_prf(set(g["must"]), set(p.must))
        tp_m, fp_m, fn_m = tp_m + a, fp_m + b_, fn_m + c_
        both = gs & ps
        kind_ok += [float((s in g["must"]) == (s in p.must)) for s in sorted(both)]
        recs = recommend_categories(p.spec_probs.value, p.grade_probs.value)
        cat3.append(float((g["spec"], g["grade"]) in [(r.spec, r.grade) for r in recs[:3]]))
        s = p.salary.value if p.salary else None
        sal_ok.append(float(s is not None and s.from_ == g["salary_from"] and s.to == g["salary_to"]))
        if g.get("salary_net") is not None:
            net_ok.append(float(s is not None and s.net == g["salary_net"]))
        fmt_ok.append(float({f.value for f in p.formats} == set(g["formats"])))
        city_ok.append(float((p.city.value if p.city else None) == g.get("city")))
        q.append(float(p.question is not None))
        rows.append({
            "id": b.id, "style": b.style, "spec_ok": p.top_spec == g["spec"], "parsed_spec": p.top_spec,
            "grade": p.top_grade, "gold_grade": g["grade"], "missed": sorted(gs - ps), "extra": sorted(ps - gs),
            "kind_errors": sorted(s for s in both if (s in g["must"]) != (s in p.must)),
            "question": p.question.field if p.question else None,
        })
    return {
        "n": len(briefs),
        "spec_top1": M.mean(spec_ok),
        "grade_exact": M.mean(g_exact),
        "grade_within1": M.mean(g_w1),
        "skills": M.prf_from_counts(tp, fp, fn),
        "must": M.prf_from_counts(tp_m, fp_m, fn_m),
        "must_nice_accuracy": M.mean(kind_ok),
        "category_top3": M.mean(cat3),
        "salary_exact": M.mean(sal_ok),
        "salary_basis_accuracy": M.mean(net_ok),
        "formats_exact": M.mean(fmt_ok),
        "city_accuracy": M.mean(city_ok),
        "question_rate": M.mean(q),
        "rows": rows,
    }


# --------------------------------------------------------------------------- устойчивость


def shifted_weights(base: Mapping[str, float], comp: str, delta: float, cfg: ScoringConfig) -> dict[str, float] | None:
    """w_comp += δ, остальные компенсируют пропорционально.

    Если пропорциональная компенсация выводит вес за ограничение (wF > 0,15 или wT < 0,30), этот вес
    остаётся на прежнем значении, а компенсацию делят остальные. None — сдвиг недопустим и при этом.
    """
    cons = cfg.raw["weight_constraints"]
    frozen: set[str] = set()
    for _ in range(len(base)):
        w = dict(base)
        w[comp] = base[comp] + delta
        free = [k for k in base if k != comp and k not in frozen]
        rest = sum(base[k] for k in free)
        if rest <= 0:
            return None
        for k in free:
            w[k] = base[k] - delta * base[k] / rest
        bad = {k for k in free if (k == "F" and w[k] > float(cons["F_max"]) + 1e-12)
               or (k == "T" and w[k] < float(cons["T_min"]) - 1e-12) or w[k] < 0}
        if not bad:
            break
        frozen |= bad
    try:
        validate_weights(w, cons)
    except ValueError:
        return None
    return w


def ablation_weights(base: Mapping[str, float], comp: str) -> dict[str, float]:
    """Снятый компонент отдаёт вес тесту (ограничения wT ≥ 0,30 и wF ≤ 0,15 соблюдаются)."""
    w = dict(base)
    w["T"] += w[comp]
    w[comp] = 0.0
    return w


def _stuff(f: CandidateFeatures, skills: Sequence[str]) -> CandidateFeatures:
    return replace(f, declared_skills=frozenset(f.declared_skills | set(skills)))


def _exposure_rows(items: Sequence[tuple[str, float, bool, int | None]], rels: Mapping[str, int],
                   k_values: Sequence[int]) -> list[dict[str, Any]]:
    """items: (candidate_id, компонент T, есть ли ФСП, место в общем топе)."""
    if not items:
        return []
    ts = sorted(t for _, t, _, _ in items)
    n = len(ts)

    def decile(t: float) -> int:
        below = bisect.bisect_left(ts, t)
        return min(9, int(10 * below / n))

    return [{
        "decile": decile(t),
        "has_fsp": has_fsp,
        "relevant": rels.get(cid, 0) >= RELEVANT,
        "rank": rank,
        **{f"top{k}": bool(rank is not None and rank <= k) for k in k_values},
    } for cid, t, has_fsp, rank in items]


def exposure_rows(version: SelectionVersion, rels: Mapping[str, int], k_values: Sequence[int]) -> list[dict[str, Any]]:
    """Строки для условной экспозиции по строкам версии (≤ 500): дециль T, есть ли ФСП, релевантен ли, в топ-k ли."""
    return _exposure_rows([(i.candidate_id, float(i.components["T"]), i.has_fsp, i.rank) for i in version.visible()],
                          rels, k_values)


def exposure_rows_full(spec: BriefSpec, categories: Sequence[tuple[str, str]], world: World, rels: Mapping[str, int],
                       k_values: Sequence[int], ctx: ScoringContext | None = None) -> list[dict[str, Any]]:
    """То же по ВСЕМ кандидатам категорий брифа: версия хранит ≤ 500 строк, и при большей категории обрезка
    по Score сдвигала бы децили T (ниже границы чаще оказываются кандидаты без ФСП). Порядок — как у версии
    (rank_results, тот же ключ сортировки; проверяется тестом rank_fast)."""
    ctx = ctx or _ctx(world)
    res = rank_results((score_candidate(c, spec, ctx) for c in in_scope(categories, world.pool)), ctx.cfg)
    items: list[tuple[str, float, bool, int | None]] = []
    seen: set[str] = set()
    for r in res:
        if r.candidate_id in seen:
            continue
        seen.add(r.candidate_id)
        items.append((r.candidate_id, float(r.x["T"]), r.features.has_fsp, len(items) + 1))
    return _exposure_rows(items, rels, k_values)


def exposure_ratio(rows: Sequence[Mapping[str, Any]], k: int) -> dict[str, float | None]:
    """Условное отношение экспозиции (DECISIONS п.11): наблюдаемое число релевантных без ФСП в топ-k к
    ожидаемому при той же доле попадания в топ-k, что у всех релевантных того же дециля T."""
    rel = [r for r in rows if r["relevant"]]
    by_d: dict[int, list[Mapping[str, Any]]] = {}
    for r in rel:
        by_d.setdefault(int(r["decile"]), []).append(r)
    expected = observed = 0.0
    for rs in by_d.values():
        rate = sum(1 for r in rs if r[f"top{k}"]) / len(rs)
        nf = [r for r in rs if not r["has_fsp"]]
        expected += rate * len(nf)
        observed += sum(1 for r in nf if r[f"top{k}"])
    cond = observed / expected if expected > 0 else None
    top = [r for r in rel if r[f"top{k}"]]
    share_top = (sum(1 for r in top if not r["has_fsp"]) / len(top)) if top else None
    share_all = (sum(1 for r in rel if not r["has_fsp"]) / len(rel)) if rel else None
    uncond = (share_top / share_all) if share_top is not None and share_all else None
    return {"conditional": cond, "unconditional": uncond, "observed": observed, "expected": expected,
            "relevant_rows": float(len(rel))}


# --------------------------------------------------------------------------- честность объяснений


def _evidence_exists(ev_type: str, ev_id: str, f: CandidateFeatures, spec: BriefSpec) -> bool:
    if ev_type == "test_session":
        return f.test_session_id == ev_id and f.confirmed
    if ev_type == "module":
        return ev_id in f.modules
    if ev_type == "task":
        return any(t.task_id == ev_id for t in f.tasks)
    if ev_type == "fsp_result":
        return f.has_fsp and (f.fsp_ref == ev_id or ev_id.startswith("fsp:"))
    if ev_type == "prefs":
        return ev_id == f.candidate_id
    if ev_type == "brief_skill":
        return ev_id in spec.skills
    if ev_type == "declared":
        return ev_id in f.declared_skills
    if ev_type == "category":
        return ev_id == f"{f.spec}:{f.grade}"
    return False


def _remove_evidence(f: CandidateFeatures, component: str | None, evidence: Sequence[Any]) -> CandidateFeatures | None:
    """Контрфакт «доказательства довода нет»: T — нет теста; F — нет ФСП; A/K — убраны указанные задачи,
    модули (тег «нет») и записи ФСП. U — не доказательство, а настройки: не проверяется (None)."""
    if component == "T":
        return replace(f, theta=None, test_session_id=None)
    if component == "F":
        return replace(f, vss_by_discipline={}, fsp_ref=None, fsp_summary=None)
    if component in ("A", "K"):
        tasks = {e.id for e in evidence if e.type == "task"}
        mods = {e.id for e in evidence if e.type == "module"}
        drop_fsp = any(e.type == "fsp_result" for e in evidence)
        modules = {m: (replace(s, tag="none") if m in mods else s) for m, s in f.modules.items()}
        return replace(
            f,
            tasks=tuple(t for t in f.tasks if t.task_id not in tasks),
            modules=modules,
            vss_by_discipline={} if drop_fsp else f.vss_by_discipline,
        )
    return None


def explanation_fidelity(version: SelectionVersion, spec: BriefSpec, world: World, top_n: int = 50) -> dict[str, Any]:
    ctx = _ctx(world)
    sum_ok, ev_ok, fsp_never_con, pro_above_median, cf_ok, cf_checked = [], [], [], [], [], 0
    cf_by: dict[str, list[int]] = {}  # компонент довода №1 → [выполнено, проверено]
    # Медианы вкладов пересчитываем независимо по всем кандидатам категорий (версия хранит ≤ 500 строк).
    scope = [score_candidate(c, spec, ctx) for c in in_scope(version.categories, world.pool)]
    medians = {k: float(statistics.median(r.c[k] for r in scope)) for k in COMPONENTS} if scope else {}
    margin = float(world.cfg.section("explain")["pro_margin"])
    for it in version.visible()[:top_n]:
        f = world.feat_by_id[it.candidate_id]
        sum_ok.append(abs(sum(it.parts.values()) - it.score_display) <= 0.1 + 1e-9
                      and abs(sum(it.contributions.values()) - it.score) <= 1e-6)
        ok = all(r.evidence and all(_evidence_exists(e.type, e.id, f, spec) for e in r.evidence) for r in it.reasons)
        ev_ok.append(ok)
        fsp_never_con.append(not any(r.kind == "con" and (r.component == "F" or r.code == "fsp") for r in it.reasons))
        for r in it.pros:
            if r.component in COMPONENTS:
                pro_above_median.append(float(it.contributions[r.component]) - medians.get(r.component, 0.0) > margin - 1e-9)
        top = next((r for r in it.pros if r.component in COMPONENTS), None)
        if top is not None:
            cf = _remove_evidence(f, top.component, top.evidence)
            if cf is not None:
                cf_checked += 1
                new = score_candidate(cf, spec, ctx).score
                ok_cf = (it.score - new) >= float(top.delta or 0.0) - 1e-6
                cf_ok.append(ok_cf)
                by = cf_by.setdefault(str(top.component), [0, 0])
                by[0] += int(ok_cf)
                by[1] += 1
    return {
        "counterfactual_by_component": cf_by,
        "items": len(sum_ok),
        "sum_equals_score": M.mean([float(x) for x in sum_ok]),
        "evidence_exists": M.mean([float(x) for x in ev_ok]),
        "fsp_never_con": M.mean([float(x) for x in fsp_never_con]),
        "pros_above_median": M.mean([float(x) for x in pro_above_median]),
        "counterfactual_checked": cf_checked,
        "counterfactual_ok": M.mean([float(x) for x in cf_ok]),
    }


# --------------------------------------------------------------------------- основной прогон


def evaluate(pop: Population, *, seed: int, n_boot: int = 2000, briefs: Sequence[Brief] | None = None,
             progress: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Метрики подбора по test-брифам (dev — справочно) и материалы для набора пар."""
    log = progress or (lambda _m: None)
    world = World.build(pop)
    briefs = list(briefs) if briefs is not None else load_briefs()
    base_w = world.cfg.weights()
    runs = {b.id: run_brief(b, world) for b in briefs}
    rels = {b.id: world.rels(b.gold) for b in briefs}
    point_pool = pop.features(point_estimate=True)
    point_stats = build_category_stats(point_pool, world.cfg)
    ctx_base = _ctx(world)

    per_brief: dict[str, dict[str, Any]] = {}
    rankings: dict[str, dict[str, list[str]]] = {}
    versions: dict[str, SelectionVersion] = {}
    build_ms: list[float] = []
    for b in briefs:
        r = runs[b.id]
        v, ms = build_ours(r.spec, r.categories, world)
        versions[b.id] = v
        build_ms.append(ms)
        gspec = gold_brief_spec(b)
        gcats = gold_categories(b, world)
        rk = {
            "ours": [i.candidate_id for i in v.visible()],
            "ours_gold": rank_fast(gspec, gcats, world.pool, ctx_base),
            "ours_noq": rank_fast(r.spec_noq, r.categories_noq, world.pool, ctx_base),
            "b0": rank_b0(b, r.categories, world, seed),
            "b1": world.bm25.rank(b.text),
            "b2": rank_b2(r.categories, world.pool, world),
            "b3": world.tfidf.rank(b.text),
        }
        for a in ABLATIONS:
            if a == "no_lcb":
                ctx = _ctx(world, pool=point_pool, stats=point_stats)
                rk[a] = rank_fast(r.spec, r.categories, point_pool, ctx)
            else:
                rk[a] = rank_fast(r.spec, r.categories, world.pool, _ctx(world, ablation_weights(base_w, a[3:])))
        rankings[b.id] = rk
        rl = rels[b.id]
        n_rel = sum(1 for x in rl.values() if x >= RELEVANT)
        per_brief[b.id] = {
            "split": b.split,
            "style": b.style,
            "spec": b.gold["spec"],
            "grade": b.gold["grade"],
            "n_relevant": n_rel,
            "p10_ceiling": min(10, n_rel) / 10,
            "in_scope": len(rk["b0"]),
            "categories": [list(c) for c in r.categories],
            "question": r.question,
            "salary_filled": r.salary_filled,
            "metrics": {s: brief_metrics(rk[s], rl) for s in (*SYSTEMS, *ABLATIONS)},
        }
    log("подбор: версии и бейзлайны готовы")

    def agg(split: str) -> dict[str, Any]:
        # "all" = test + dev вместе: ни те ни другие не участвовали в настройке (она на matching_tune), мощность выше в два раза
        ids = [b.id for b in briefs if split == "all" or b.split == split]
        out: dict[str, Any] = {"n_briefs": len(ids), "systems": {}}
        for s in (*SYSTEMS, *ABLATIONS):
            out["systems"][s] = {
                m: M.summarize([float(per_brief[i]["metrics"][s][m] or 0.0) for i in ids],
                               stream(f"bootstrap:{split}:{s}:{m}", seed), n_boot=n_boot)
                for m in METRIC_KEYS
            }
        p10 = {s: [float(per_brief[i]["metrics"][s]["p10"]) for i in ids] for s in (*SYSTEMS, *ABLATIONS)}
        out["paired_vs_b2"] = {
            s: M.paired_bootstrap_diff(p10[s], p10["b2"], stream(f"paired:{split}:{s}", seed), n_boot=n_boot)
            for s in (*SYSTEMS, *ABLATIONS) if s != "b2"
        }
        out["paired_vs_ours"] = {
            s: M.paired_bootstrap_diff(p10["ours"], p10[s], stream(f"paired_ours:{split}:{s}", seed), n_boot=n_boot)
            for s in (*ABLATIONS, "b0", "b1", "b3")
        }
        out["p10_ceiling"] = M.mean([per_brief[i]["p10_ceiling"] for i in ids])
        return out

    test = [b for b in briefs if b.split == "test"]
    result: dict[str, Any] = {
        "n_population": len(pop.candidates),
        "n_pool_confirmed": len(world.pool),
        "systems": SYSTEM_LABELS,
        "ablations": ABLATION_LABELS,
        "test": agg("test"),
        "dev": agg("dev"),
        "all": agg("all"),
        "parser": {"test": parser_metrics(test, runs),
                   "dev": parser_metrics([b for b in briefs if b.split == "dev"], runs)},
        "per_brief": per_brief,
        "build_ms": {"p50": M.median(build_ms), "p95": M.percentile(build_ms, 95), "max": max(build_ms) if build_ms else None,
                     "n": len(build_ms), "pool": len(world.pool)},
        "weights_balance": base_w,
    }
    log("подбор: метрики и бутстреп готовы")
    result["robustness"] = robustness(test, runs, rels, versions, rankings, world, pop, point_pool, seed)
    log("подбор: устойчивость готова")
    result["top10_errors"] = _top10_errors(test, rankings, world)
    result["exposure"] = _exposure(test, runs, rels, world)
    result["fidelity"] = _fidelity(test, versions, runs, world)
    result["_rankings"] = {b.id: {s: rankings[b.id][s][:50] for s in SYSTEMS} for b in test}
    result["_rels"] = {b.id: rels[b.id] for b in test}
    return result


def _top10_errors(briefs: Sequence[Brief], rankings: Mapping[str, Mapping[str, list[str]]], world: World) -> dict[str, Any]:
    """Из-за чего нерелевантны места топ-10 (наш подбор и B2) — описательная диагностика после прогона."""
    out: dict[str, Any] = {"labels": IRRELEVANCE_LABELS, "slots": 10 * len(briefs)}
    for s in ("ours", "b2"):
        counts: dict[str, int] = {}
        empty = 0
        for b in briefs:
            top = rankings[b.id][s][:10]
            empty += 10 - len(top)
            for cid in top:
                why = irrelevance_reason(world.by_id[cid], b.gold, world.cfg, world.cat)
                if why is not None:
                    counts[why] = counts.get(why, 0) + 1
        out[s] = {"reasons": dict(sorted(counts.items(), key=lambda kv: -kv[1])), "irrelevant": sum(counts.values()),
                  "empty_slots": empty}
    return out


def _exposure(briefs: Sequence[Brief], runs: Mapping[str, BriefRun], rels: Mapping[str, Mapping[str, int]],
              world: World) -> dict[str, Any]:
    ctx = _ctx(world)
    rows: list[dict[str, Any]] = []
    for b in briefs:
        r = runs[b.id]
        rows += exposure_rows_full(r.spec, r.categories, world, rels[b.id], (10, 50), ctx)
    return {
        "k10": exposure_ratio(rows, 10),
        "k50": exposure_ratio(rows, 50),
        "share_no_fsp_relevant": M.mean([float(not r["has_fsp"]) for r in rows if r["relevant"]]),
        "rows": len(rows),
        "scope": "все кандидаты рекомендованных категорий брифа (без обрезки версии до 500 строк)",
    }


def _fidelity(briefs: Sequence[Brief], versions: Mapping[str, SelectionVersion], runs: Mapping[str, BriefRun],
              world: World) -> dict[str, Any]:
    per = [explanation_fidelity(versions[b.id], runs[b.id].spec, world) for b in briefs]
    keys = ("sum_equals_score", "evidence_exists", "fsp_never_con", "pros_above_median", "counterfactual_ok")
    total = sum(p["items"] for p in per)
    by: dict[str, list[int]] = {}
    for p in per:
        for comp, (ok, n) in p["counterfactual_by_component"].items():
            by.setdefault(comp, [0, 0])
            by[comp][0] += ok
            by[comp][1] += n
    out: dict[str, Any] = {"items": total, "counterfactual_checked": sum(p["counterfactual_checked"] for p in per),
                           "counterfactual_by_component": {k: {"ok": v[0], "n": v[1], "share": v[0] / v[1] if v[1] else None}
                                                           for k, v in sorted(by.items())}}
    for k in keys:
        vals = [(p[k], p["items"] if k != "counterfactual_ok" else p["counterfactual_checked"]) for p in per if p[k] is not None]
        w = sum(n for _, n in vals)
        out[k] = sum(v * n for v, n in vals) / w if w else None
    return out


def robustness(briefs: Sequence[Brief], runs: Mapping[str, BriefRun], rels: Mapping[str, Mapping[str, int]],
               versions: Mapping[str, SelectionVersion], rankings: Mapping[str, Mapping[str, list[str]]],
               world: World, pop: Population, point_pool: Sequence[CandidateFeatures], seed: int) -> dict[str, Any]:
    cfg = world.cfg
    base_w = cfg.weights()
    ctx = _ctx(world)
    out: dict[str, Any] = {}

    # 1. Накрутка: 50 самым слабым в выдаче добавлены 5 навыков брифа в самоописание.
    shifts_ours, shifts_bm25, top10_ours, top10_bm25 = [], [], [], []
    for b in briefs:
        r = runs[b.id]
        order = rankings[b.id]["ours"]
        stuffers = order[-50:]
        skills = list(r.spec.skills)[:5]
        if not stuffers or not skills:
            continue
        sset = set(stuffers)
        pool2 = [_stuff(f, skills) if f.candidate_id in sset else f for f in world.pool]
        after = rank_fast(r.spec, r.categories, pool2, ctx)
        pos0, pos1 = rank_positions(order), rank_positions(after)
        shifts_ours += [pos0[c] - pos1[c] for c in stuffers]
        top10_ours += [float(pos1[c] <= 10) for c in stuffers]
        names = " ".join(world.cat.name(s) for s in skills)
        docs = {c.id: (c.self_description + " " + names if c.id in sset else c.self_description) for c in pop.candidates}
        bm_before = rank_positions(rankings[b.id]["b1"])
        bm_after = rank_positions(BM25(docs).rank(b.text))
        shifts_bm25 += [bm_before[c] - bm_after[c] for c in stuffers]
        top10_bm25 += [float(bm_after[c] <= 10) for c in stuffers]
    out["stuffing"] = {
        "ours_mean_shift_up": M.mean(shifts_ours),
        "ours_max_shift_up": max(shifts_ours) if shifts_ours else None,
        "ours_share_into_top10": M.mean(top10_ours),
        "bm25_mean_shift_up": M.mean(shifts_bm25),
        "bm25_share_into_top10": M.mean(top10_bm25),
        "stuffers": len(shifts_ours),
    }

    # 2. Стабильность при пересимуляции теста (пересдача на непересекающихся семействах).
    retest_pool = pop.features(which="retest")
    if retest_pool:
        retest_stats = build_category_stats(retest_pool, cfg)
        ctx_rt = _ctx(world, pool=retest_pool, stats=retest_stats)
        point_rt = pop.features(which="retest", point_estimate=True)
        ctx_pt = _ctx(world, pool=point_pool, stats=build_category_stats(point_pool, cfg))
        ctx_pt_rt = _ctx(world, pool=point_rt, stats=build_category_stats(point_rt, cfg))
        ov_lcb, ov_point, ov_b2 = [], [], []
        for b in briefs:
            r = runs[b.id]
            a = rankings[b.id]["ours"]
            ov_lcb.append(M.overlap_at_k(a, rank_fast(r.spec, r.categories, retest_pool, ctx_rt), 10))
            ov_point.append(M.overlap_at_k(rank_fast(r.spec, r.categories, point_pool, ctx_pt),
                                           rank_fast(r.spec, r.categories, point_rt, ctx_pt_rt), 10))
            ov_b2.append(M.overlap_at_k(rankings[b.id]["b2"], rank_b2(r.categories, retest_pool, world), 10))
        out["stability"] = {"ours_overlap10": M.mean(ov_lcb), "point_overlap10": M.mean(ov_point),
                            "b2_overlap10": M.mean(ov_b2), "n_briefs": len(ov_lcb)}

    # 3. Жаккар топ-10 до и после «добавлен nice-навык».
    jac = []
    for b in briefs:
        r = runs[b.id]
        extra = next((s for s in EXTRA_NICE.get(r.spec.spec, ("git",)) if s not in r.spec.skills), None)
        if extra is None:
            continue
        s2 = replace(r.spec, nice=r.spec.nice + (extra,))
        jac.append(M.jaccard(rankings[b.id]["ours"][:10], rank_fast(s2, r.categories, world.pool, ctx)[:10]))
    out["nice_skill_jaccard10"] = {"mean": M.mean(jac), "min": min(jac) if jac else None, "n": len(jac)}

    # 4. Чувствительность к весам ±0,05 (компенсация пропорционально остальным).
    def mean_p10(weights: Mapping[str, float]) -> float:
        c = _ctx(world, weights)
        vals = [M.precision_at_k([rels[b.id].get(x, 0) for x in rank_fast(runs[b.id].spec, runs[b.id].categories,
                                                                        world.pool, c)], 10, RELEVANT) for b in briefs]
        return float(M.mean(vals) or 0.0)

    base_p10 = mean_p10(base_w)
    rows = []
    for comp in COMPONENTS:
        for delta in (0.05, -0.05):
            w = shifted_weights(base_w, comp, delta, cfg)
            if w is None:
                rows.append({"component": comp, "delta": delta, "skipped": True})
                continue
            p = mean_p10(w)
            rows.append({"component": comp, "delta": delta, "skipped": False, "p10": p, "drop": base_p10 - p,
                         "weights": {k: round(v, 4) for k, v in w.items()}})
    drops = [r["drop"] for r in rows if not r["skipped"]]
    out["weights"] = {"base_p10": base_p10, "rows": rows, "max_drop": max(drops) if drops else None}
    return out


# --------------------------------------------------------------------------- набор пар для жюри


def features_to_dict(f: CandidateFeatures | None) -> dict[str, Any] | None:
    """CandidateFeatures → JSON (поля как в app.matching.scoring.CandidateFeatures)."""
    if f is None:
        return None
    return {
        "candidate_id": f.candidate_id,
        "spec": f.spec,
        "grade": f.grade,
        "theta": None if f.theta is None else round(f.theta, 4),
        "se": round(f.se, 4),
        "theta_age_days": f.theta_age_days,
        "test_days_ago": f.test_days_ago,
        "borderline": f.borderline,
        "trust": f.trust,
        "modules": {m: {"theta": round(s.theta, 4), "se": round(s.se, 4), "tag": s.tag} for m, s in sorted(f.modules.items())},
        "tasks": [{"task_id": t.task_id, "kind": t.kind, "q": t.q, "days_ago": t.days_ago, "title": t.title,
                   "skills": list(t.skills), "module": t.module} for t in f.tasks],
        "vss_by_discipline": {k: round(v, 2) for k, v in sorted(f.vss_by_discipline.items())},
        "fsp_summary": f.fsp_summary,
        "declared_skills": sorted(f.declared_skills),
        "salary_min": f.salary_min,
        "salary_net": f.salary_net,
        "formats": sorted(f.formats),
        "city": f.city,
        "relocation": f.relocation,
        "industries": sorted(f.industries),
    }


POOL_SYSTEMS = ("ours", "b0", "b1", "b2", "b3")
POOL_DEPTH = 20


def export_pairs(result: Mapping[str, Any], pop: Population, path: Path, *, seed: int, n_random: int = 10,
                 depth: int = POOL_DEPTH) -> dict[str, Any]:
    """eval/pairs_test.jsonl: test-брифы × пул (объединение топ-20 систем ours, B0–B3 + 10 случайных), rel по оракулу.

    Пул глубины 20, а не 50, как в плане: иначе файл весит ~8 МБ (у каждой строки свой текст брифа и признаки).
    Для P@10 и P@5 глубины 20 хватает; кандидаты вне пула при оценке считаются нерелевантными (стандартный пулинг).
    """
    briefs = {b.id: b for b in load_briefs()}
    feats = {f.candidate_id: f for f in pop.features()}
    cands = pop.by_id()
    lines = 0
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as fh:
        for bid in sorted(result["_rankings"]):
            b = briefs[bid]
            ranks = result["_rankings"][bid]
            ids: dict[str, None] = {}
            for s in POOL_SYSTEMS:
                for cid in ranks[s][:depth]:
                    ids[cid] = None
            rng = stream(f"pairs:{bid}", seed)
            rest = sorted(c.id for c in pop.candidates if c.id not in ids)
            for cid in rng.sample(rest, min(n_random, len(rest))):
                ids[cid] = None
            pos = {s: rank_positions(ranks[s][:depth]) for s in POOL_SYSTEMS}
            for cid in sorted(ids):
                c = cands[cid]
                pub = c.to_public_dict()
                row = {
                    "brief_id": bid,
                    "brief": {"text": b.text, "form": b.form, "gold": dict(b.gold), "style": b.style},
                    "candidate_id": cid,
                    "candidate": {
                        "features": features_to_dict(feats.get(cid)),
                        "self_description": pub["self_description"],
                        "claimed_spec": pub["claimed_spec"],
                        "claimed_grade": pub["claimed_grade"],
                        "experience_months": pub["experience_months"],
                        "fsp_line": pub["fsp"]["employer_line"] if pub["fsp"] else None,
                    },
                    "rel": int(result["_rels"][bid][cid]),
                    "ranks_top20": {s: pos[s].get(cid) for s in POOL_SYSTEMS},
                }
                fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
                lines += 1
    return {"path": str(path), "lines": lines, "briefs": len(result["_rankings"])}


def pairs_schema() -> dict[str, Any]:
    """JSON Schema строки eval/pairs_test.jsonl (draft 2020-12)."""
    feat = {
        "type": ["object", "null"],
        "description": "Признаки подбора (app.matching.scoring.CandidateFeatures); null — категория тестом не подтверждена",
        "required": ["candidate_id", "spec", "grade", "theta", "se", "modules", "tasks", "vss_by_discipline",
                     "declared_skills", "salary_min", "salary_net", "formats", "city", "relocation"],
        "properties": {
            "candidate_id": {"type": "string"},
            "spec": {"type": "string", "enum": ["backend_python", "frontend_js", "fullstack", "qa", "devops"]},
            "grade": {"type": "string", "enum": list(GRADES)},
            "theta": {"type": ["number", "null"], "description": "Оценка уровня по шкале теста (порог Middle = 0)"},
            "se": {"type": "number"},
            "theta_age_days": {"type": "number"},
            "test_days_ago": {"type": ["number", "null"]},
            "borderline": {"type": "boolean"},
            "trust": {"type": "number"},
            "modules": {"type": "object", "additionalProperties": {
                "type": "object", "required": ["theta", "se", "tag"],
                "properties": {"theta": {"type": "number"}, "se": {"type": "number"},
                               "tag": {"type": "string", "enum": ["strong", "confirmed", "insufficient", "none"]}}}},
            "tasks": {"type": "array", "items": {"type": "object", "required": ["task_id", "kind", "q", "days_ago"],
                                                 "properties": {"task_id": {"type": "string"},
                                                                "kind": {"type": "string", "enum": ["A", "B"]},
                                                                "q": {"type": "number", "minimum": 0, "maximum": 1},
                                                                "days_ago": {"type": "number"},
                                                                "title": {"type": "string"},
                                                                "skills": {"type": "array", "items": {"type": "string"}},
                                                                "module": {"type": ["string", "null"]}}}},
            "vss_by_discipline": {"type": "object", "additionalProperties": {"type": "number", "minimum": 0, "maximum": 100}},
            "fsp_summary": {"type": ["string", "null"]},
            "declared_skills": {"type": "array", "items": {"type": "string"}},
            "salary_min": {"type": ["integer", "null"]},
            "salary_net": {"type": "boolean"},
            "formats": {"type": "array", "items": {"type": "string", "enum": ["office", "hybrid", "remote"]}},
            "city": {"type": ["string", "null"]},
            "relocation": {"type": "boolean"},
            "industries": {"type": "array", "items": {"type": "string"}},
        },
    }
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://vzaimno.example/schemas/pairs_test.schema.json",
        "title": "Набор пар «бриф × кандидат» с релевантностью по оракулу",
        "type": "object",
        "required": ["brief_id", "brief", "candidate_id", "candidate", "rel", "ranks_top20"],
        "properties": {
            "brief_id": {"type": "string", "pattern": "^b[0-9]{2}$"},
            "brief": {
                "type": "object",
                "required": ["text", "gold"],
                "properties": {
                    "text": {"type": "string"},
                    "style": {"type": "string"},
                    "form": {"type": ["object", "null"], "description": "Поля обязательной формы вилки, если вилки нет в тексте"},
                    "gold": {"type": "object", "required": ["spec", "grade", "must", "nice", "formats", "salary_from", "salary_to"],
                             "properties": {"spec": {"type": "string"}, "grade": {"type": "string", "enum": list(GRADES)},
                                            "must": {"type": "array", "items": {"type": "string"}},
                                            "nice": {"type": "array", "items": {"type": "string"}},
                                            "formats": {"type": "array", "items": {"type": "string"}},
                                            "city": {"type": ["string", "null"]},
                                            "salary_from": {"type": "integer"}, "salary_to": {"type": "integer"},
                                            "salary_net": {"type": ["boolean", "null"]}}},
                },
            },
            "candidate_id": {"type": "string"},
            "candidate": {
                "type": "object",
                "required": ["features", "self_description"],
                "properties": {
                    "features": feat,
                    "self_description": {"type": "string", "description": "Самоописание (в Score не входит — только бейзлайны B1/B3)"},
                    "claimed_spec": {"type": "string"},
                    "claimed_grade": {"type": "string"},
                    "experience_months": {"type": "integer"},
                    "fsp_line": {"type": ["string", "null"]},
                },
            },
            "rel": {"type": "integer", "minimum": 0, "maximum": 3, "description": "Оракул по скрытой истине; релевантен ⇔ rel ≥ 2"},
            "ranks_top20": {"type": "object",
                            "description": "Место в топ-20 систем ours, b0, b1, b2, b3 (null — вне топ-20)",
                            "additionalProperties": {"type": ["integer", "null"]}},
        },
    }


def strip_private(result: dict[str, Any]) -> dict[str, Any]:
    """Убрать служебные поля (_rankings, _rels) перед записью в JSON для Лаборатории."""
    return {k: v for k, v in result.items() if not k.startswith("_")}
