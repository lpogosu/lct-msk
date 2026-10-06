"""Рекомендация категорий, охват вилки, «Как мы проверим» и проверочный набор по вакансии.

Концепция §4.2 «Охват», §5.5; spec-contact §3.2; DECISIONS п.3 (проверочный набор):
- P(c = s×g) = P(s)·P(g); до 3 категорий с P ≥ 0,10; при N < 15 в основной — предложение смежной;
- гистограмма минимумов шагом 10 тыс. (считается заранее, ползунок — на клиенте);
  защита от вычисления минимума: ползунок при N ≥ 10, приросты — только ступенями ≥ 3 человек;
- «Как мы проверим»: навыки брифа → модули теста; серый список «тестом не проверяется»;
- проверочный набор: must-навыки → модули → 3–5 семейств типа A, ≤ 15 мин (чистый планировщик).
"""

from __future__ import annotations

import math
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.matching.scoring import GRADES, BriefSpec, CandidateFeatures, ScoringConfig
from app.matching.skills import SkillCatalog, get_catalog

# --------------------------------------------------------------------------- категории


@dataclass(frozen=True)
class CategoryRecommendation:
    spec: str
    grade: str
    p: float
    kind: str  # primary | suggested | adjacent
    name: str
    status: str  # active | survey_only | derived
    n: int | None = None
    note: str | None = None


def _adj_spec(cfg: ScoringConfig, a: str, b: str) -> float:
    if a == b:
        return 1.0
    for x, y, w in cfg.section("G")["spec_adjacency"]:
        if {x, y} == {a, b}:
            return float(w)
    return 0.0


def recommend_categories(
    spec_probs: Mapping[str, float],
    grade_probs: Mapping[str, float],
    *,
    counts: Mapping[tuple[str, str], int] | None = None,
    cfg: ScoringConfig | None = None,
) -> tuple[CategoryRecommendation, ...]:
    """1–3 категории с P(s)·P(g) ≥ 0,10; основная — argmax. При N основной < 15 — смежная категория."""
    cfg = cfg or ScoringConfig.load()
    cc = cfg.section("coverage")
    status = {s["code"]: s.get("status", "active") for s in cfg.taxonomy["specializations"]}
    joint = sorted(
        ((s, g, float(ps) * float(grade_probs.get(g, 0.0))) for s, ps in spec_probs.items() for g in GRADES),
        key=lambda t: (-t[2], list(spec_probs).index(t[0]), GRADES.index(t[1])),
    )
    chosen = [t for t in joint if t[2] >= float(cc["min_category_p"]) - 1e-12][: int(cc["max_categories"])]
    if not chosen:
        chosen = joint[:1]
    out: list[CategoryRecommendation] = []
    for i, (s, g, p) in enumerate(chosen):
        n = counts.get((s, g)) if counts is not None else None
        note = None
        if status.get(s) == "survey_only":
            if n is None:
                note = "Категория пока не тестируется — кандидаты в листе ожидания"
            elif n % 10 == 1 and n % 100 != 11:
                note = f"Категория пока не тестируется. {n} кандидат прошёл опрос и ждёт теста"
            else:
                word = "кандидата" if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else "кандидатов"
                note = f"Категория пока не тестируется. {n} {word} прошли опрос и ждут теста"
        out.append(
            CategoryRecommendation(
                s,
                g,
                p,
                "primary" if i == 0 else "suggested",
                cfg.category_name(s, g),
                status.get(s, "active"),
                n,
                note,
            )
        )
    if counts is not None and out:
        main = out[0]
        if (main.n or 0) < int(cc["adjacent_if_n_below"]):
            taken = {(r.spec, r.grade) for r in out}
            gc = cfg.section("G")
            options = []
            gi = GRADES.index(main.grade)
            for s in spec_probs:
                for g in GRADES:
                    if (s, g) in taken or status.get(s) == "survey_only":
                        continue
                    d = GRADES.index(g) - gi
                    if abs(d) > 1:
                        continue
                    w = _adj_spec(cfg, s, main.spec) * (
                        float(gc["grade_above"]) if d > 0 else float(gc["grade_below"]) if d < 0 else 1.0
                    )
                    if w >= 0.8 - 1e-9 and counts.get((s, g), 0) > 0:
                        options.append((w, counts.get((s, g), 0), s, g))
            if options:
                w, n, s, g = max(options)
                out.append(
                    CategoryRecommendation(
                        s,
                        g,
                        float(spec_probs.get(s, 0.0)) * float(grade_probs.get(g, 0.0)),
                        "adjacent",
                        cfg.category_name(s, g),
                        status.get(s, "active"),
                        n,
                        f"В основной категории мало кандидатов ({main.n or 0}) — добавьте смежную",
                    )
                )
    return tuple(out)


# --------------------------------------------------------------------------- охват вилки


@dataclass(frozen=True)
class Histogram:
    """Минимумы зарплат: bins = ((верхняя граница ступени U, сколько кандидатов с минимумом в (U − step, U]), …)."""

    step: int
    bins: tuple[tuple[int, int], ...]

    @property
    def n(self) -> int:
        return sum(c for _, c in self.bins)

    def reach(self, upper: float) -> int:
        return sum(c for u, c in self.bins if u <= upper + 1e-9)


def salary_histogram(mins: Iterable[float], step: int | None = None) -> Histogram:
    step = step or int(ScoringConfig.load().section("coverage")["histogram_step"])
    counts: dict[int, int] = {}
    for m in mins:
        u = int(math.ceil(float(m) / step - 1e-9)) * step
        counts[u] = counts.get(u, 0) + 1
    return Histogram(step, tuple(sorted(counts.items())))


def coarsen(h: Histogram, k: int) -> Histogram:
    """Склеить соседние ступени, пока в каждой не окажется ≥ k человек: прирост охвата виден только ступенями ≥ k,
    поэтому минимум отдельного кандидата не вычислить подбором верха вилки (концепция §4.2)."""
    out: list[list[int]] = []
    acc = 0
    for u, c in h.bins:
        acc += c
        if acc >= k:
            out.append([u, acc])
            acc = 0
    if acc:
        if out:
            out[-1] = [h.bins[-1][0], out[-1][1] + acc]
        else:
            out.append([h.bins[-1][0], acc])
    return Histogram(h.step, tuple((u, c) for u, c in out))


@dataclass(frozen=True)
class ReachPoint:
    upper: int
    reach: int


@dataclass(frozen=True)
class ReachView:
    n: int
    shown: bool  # ползунок показывается при N ≥ 10
    reach_at_to: int | None
    histogram: Histogram | None  # огрублённая гистограмма для клиента (None, если ползунок скрыт)
    points: tuple[ReachPoint, ...]
    hint: str | None
    p25: int | None
    p50: int | None
    p75: int | None
    note: str | None = None


def candidate_mins(
    pool: Iterable[CandidateFeatures], brief_net: bool, cfg: ScoringConfig | None = None
) -> list[float]:
    """Минимумы кандидатов в базисе брифа (на руки / до НДФЛ)."""
    cfg = cfg or ScoringConfig.load()
    ndfl = float(cfg.section("U")["ndfl_rate"])
    out = []
    for c in pool:
        if c.salary_min is None:
            continue
        m = float(c.salary_min)
        if c.salary_net != brief_net:
            m = m * (1 - ndfl) if brief_net else m / (1 - ndfl)
        out.append(m)
    return out


def quantile(sorted_vals: Sequence[float], q: float) -> float:
    """Квантиль q ∈ [0, 1] отсортированного списка (линейная интерполяция)."""
    if not sorted_vals:
        return 0.0
    pos = (len(sorted_vals) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


_quantile = quantile  # прежнее имя (используется внутри модуля и в тестах)


def _thousands(x: int) -> str:
    return f"{x // 1000} тыс."


def reach_view(
    mins: Sequence[float],
    salary_from: int,
    salary_to: int,
    *,
    n_total: int | None = None,
    cfg: ScoringConfig | None = None,
) -> ReachView:
    """Ползунок «верх вилки → охват» с защитой приватности. n_total — N категории (по умолчанию len(mins))."""
    cfg = cfg or ScoringConfig.load()
    cc = cfg.section("coverage")
    sal = cfg.section("brief_parser")["salary"]
    step = int(cc["histogram_step"])
    n = n_total if n_total is not None else len(mins)
    if n < int(cc["privacy_min_n"]):
        return ReachView(
            n, False, None, None, (), None, None, None, None, "Мало кандидатов для ползунка охвата"
        )
    hist = coarsen(salary_histogram(mins, step), int(cc["privacy_min_delta"]))
    # Кандидаты без указанного минимума считаются в охвате при любой вилке.
    unknown = n - hist.n

    def reach(u: float) -> int:
        return hist.reach(u) + unknown

    at_to = reach(salary_to)
    hi = int(math.floor(salary_from * float(sal["ratio_block"]) / step)) * step
    lo = int(math.ceil(salary_from / step)) * step
    points = tuple(ReachPoint(u, reach(u)) for u in range(lo, max(lo, hi) + 1, step))
    hint = None
    if n and at_to / n < float(cc["reach_hint_share"]):
        target = float(cc["hint_target_share"]) * n
        better = next((p for p in points if p.upper > salary_to and p.reach >= target), None)
        if better is None:
            cands = [p for p in points if p.upper > salary_to and p.reach > at_to]
            better = cands[-1] if cands else None
        hint = f"Ваша вилка покрывает {at_to} из {n}."
        if better is not None:
            hint += f" При верхе {_thousands(better.upper)} — {better.reach} из {n}."
    p25 = p50 = p75 = None
    if len(mins) >= int(cc["quantiles_min_n"]):
        s = sorted(mins)
        # Округляем до ступени гистограммы — точные значения отдельных людей не показываем.
        p25, p50, p75 = (int(round(_quantile(s, q) / step)) * step for q in (0.25, 0.5, 0.75))
    return ReachView(n, True, at_to, hist, points, hint, p25, p50, p75)


@dataclass(frozen=True)
class CategoryCard:
    rec: CategoryRecommendation
    n: int
    reach: ReachView
    share_fsp: float | None
    share_active: float | None


def category_cards(
    recs: Sequence[CategoryRecommendation],
    pool: Sequence[CandidateFeatures],
    brief: BriefSpec,
    cfg: ScoringConfig | None = None,
) -> tuple[CategoryCard, ...]:
    """Карточки Р4: N, ползунок охвата, P25–P75 ожиданий, доля с ФСП, доля активных (задача ≤ 30 дней)."""
    cfg = cfg or ScoringConfig.load()
    active_days = float(cfg.section("A")["active_days"])
    out = []
    for r in recs:
        members = [c for c in pool if c.spec == r.spec and c.grade == r.grade]
        n = len(members)
        rv = reach_view(
            candidate_mins(members, brief.salary_net, cfg),
            brief.salary_from or 0,
            brief.salary_to or 0,
            n_total=n,
            cfg=cfg,
        )
        share_fsp = sum(c.has_fsp for c in members) / n if n else None
        share_active = (
            sum(any(t.days_ago <= active_days for t in c.tasks) for c in members) / n if n else None
        )
        out.append(CategoryCard(r, n, rv, share_fsp, share_active))
    return tuple(out)


# --------------------------------------------------------------------------- «Как мы проверим»


@dataclass(frozen=True)
class SkillCheck:
    skill_id: str
    name: str
    kind: str | None  # must | nice
    covered: bool
    modules: tuple[tuple[str, str], ...]  # (код, название)
    text: str  # «PostgreSQL ✓ через модуль „SQL и базы данных“» / «Docker — тестом не проверяется …»
    reason: str | None


@dataclass(frozen=True)
class CheckCoverage:
    items: tuple[SkillCheck, ...]
    module_weights: Mapping[str, float]

    @property
    def covered(self) -> tuple[SkillCheck, ...]:
        return tuple(i for i in self.items if i.covered)

    @property
    def untestable(self) -> tuple[SkillCheck, ...]:
        """Серый список «тестом не проверяется — проверьте на интервью»."""
        return tuple(i for i in self.items if not i.covered)


def module_weights(
    must: Sequence[str],
    nice: Sequence[str],
    catalog: SkillCatalog | None = None,
    cfg: ScoringConfig | None = None,
) -> dict[str, float]:
    """Веса модулей теста по брифу (концепция §5.5 п.1): must 1,0 / nice 0,4, делится между модулями навыка, Σ = 1."""
    cat = catalog or get_catalog()
    kc = (cfg or ScoringConfig.load()).section("K")
    acc: dict[str, float] = {}
    for ids, w in ((must, float(kc["must_weight"])), (nice, float(kc["nice_weight"]))):
        for sid in ids:
            mods = cat.modules_of(sid)
            for m in mods:
                acc[m] = acc.get(m, 0.0) + w / len(mods)
    tot = sum(acc.values())
    return {m: v / tot for m, v in sorted(acc.items(), key=lambda kv: -kv[1])} if tot else {}


def how_we_check(
    must: Sequence[str], nice: Sequence[str] = (), catalog: SkillCatalog | None = None
) -> CheckCoverage:
    cat = catalog or get_catalog()
    items = []
    for kind, ids in (("must", must), ("nice", nice)):
        for sid in ids:
            sk = cat.skills.get(sid)
            if sk is None:
                continue
            mods = tuple((m, cat.module_names.get(m, m)) for m in sk.modules)
            if mods:
                names = ", ".join(f"«{nm}»" for _, nm in mods)
                text = f"{sk.name} ✓ через {'модуль' if len(mods) == 1 else 'модули'} {names}"
                items.append(SkillCheck(sid, sk.name, kind, True, mods, text, None))
            else:
                reason = sk.untestable_reason or "Тестом не проверяется — проверьте на интервью"
                # «Docker ✗ инфраструктура … тестом не проверяются — проверьте на интервью» (концепция §5.5 п.2)
                text = f"{sk.name} ✗ {reason[:1].lower()}{reason[1:]}"
                items.append(SkillCheck(sid, sk.name, kind, False, (), text, reason))
    return CheckCoverage(tuple(items), module_weights(must, nice, cat))


# --------------------------------------------------------------------------- проверочный набор по вакансии


@dataclass(frozen=True)
class CheckSetSlot:
    module: str
    module_name: str
    skills: tuple[str, ...]
    family_id: str | None
    minutes: float


@dataclass(frozen=True)
class CheckSetPlan:
    slots: tuple[CheckSetSlot, ...]
    untestable: tuple[str, ...]
    total_minutes: float
    note: str | None = None

    @property
    def modules(self) -> tuple[str, ...]:
        return tuple(s.module for s in self.slots)


FamilyCatalog = Mapping[str, Sequence[tuple[str, float]]]  # модуль → [(family_id, минуты)]


def family_catalog_from_registry() -> dict[str, list[tuple[str, float]]]:
    """Каталог семейств из банка заданий (модуль → [(id, минуты)]). Пустой, если банк недоступен."""
    try:
        from app.testing.bank.registry import all_families
    except Exception:  # noqa: BLE001 — банк собирается параллельно; планировщик работает и без него
        return {}
    out: dict[str, list[tuple[str, float]]] = {}
    for fid, fam in sorted(all_families().items()):
        out.setdefault(fam.module, []).append((fid, float(fam.time_limit_s) / 60.0))
    return out


def plan_check_set(
    must: Sequence[str],
    *,
    family_catalog: FamilyCatalog | None = None,
    seed: int = 0,
    catalog: SkillCatalog | None = None,
    cfg: ScoringConfig | None = None,
) -> CheckSetPlan:
    """must-навыки брифа → модули по графу → 3–5 слотов семейств типа A, ≤ 15 минут (DECISIONS п.3).

    Стандартный тест и грейд не меняются: результат идёт в K через test_k. Выбор семейства внутри модуля —
    детерминированно по seed (каждому из шортлиста потом выдаются свои одноразовые экземпляры)."""
    cat = catalog or get_catalog()
    cs = (cfg or ScoringConfig.load()).section("check_set")
    weight: dict[str, float] = {}
    first: dict[str, int] = {}
    skills_of: dict[str, list[str]] = {}
    untestable: list[str] = []
    for i, sid in enumerate(must):
        mods = cat.modules_of(sid)
        if not mods:
            untestable.append(sid)
            continue
        for m in mods:
            weight[m] = weight.get(m, 0.0) + 1.0 / len(mods)
            first.setdefault(m, i)
            skills_of.setdefault(m, []).append(sid)
    if not weight:
        return CheckSetPlan(
            (),
            tuple(untestable),
            0.0,
            "Обязательные навыки брифа тестом не проверяются — проверьте на интервью",
        )
    order = sorted(weight, key=lambda m: (-weight[m], first[m]))
    default_min = float(cs["default_family_minutes"])
    n_slots = max(int(cs["min_families"]), min(int(cs["max_families"]), len(order)))
    rng = random.Random(seed)
    used: set[str] = set()
    slots: list[CheckSetSlot] = []
    total = 0.0
    i = 0
    while len(slots) < n_slots and i < n_slots * 4:
        m = order[i % len(order)]
        i += 1
        fam_id, minutes = None, default_min
        if family_catalog is not None:
            options = sorted((f, t) for f, t in family_catalog.get(m, ()) if f not in used)
            if not options:
                continue
            fam_id, minutes = options[rng.randrange(len(options))]
            used.add(fam_id)
        if total + minutes > float(cs["max_minutes"]) + 1e-9:
            break
        total += minutes
        slots.append(CheckSetSlot(m, cat.module_names.get(m, m), tuple(skills_of[m]), fam_id, minutes))
    note = None
    if len(slots) < int(cs["min_families"]):
        note = "В банке не хватает откалиброванных семейств для полного набора"
    return CheckSetPlan(tuple(slots), tuple(untestable), total, note)


def plan_summary(plan: CheckSetPlan) -> dict[str, Any]:
    return {
        "modules": list(plan.modules),
        "families": [s.family_id for s in plan.slots],
        "untestable": list(plan.untestable),
        "total_minutes": round(plan.total_minutes, 1),
        "note": plan.note,
    }
