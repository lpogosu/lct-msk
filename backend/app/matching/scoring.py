"""Скоринг кандидата под бриф — чистые функции без БД.

Score(c | бриф) = 100 · G · Cov · (wT·T + wK·K + wF·F + wA·A + wU·U)          — концепция §4.2

С поправками DECISIONS.md: self_k удалён из v_k (п.1) — заявленные навыки не влияют на Score,
только на серые чипы и предпоследний ключ сортировки; Q_min для ФСП считается в модуле ФСП (п.2);
кандидат без теста («категория по опросу») идёт ниже подтверждённых (п.5).
"""

from __future__ import annotations

import hashlib
import json
import math
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from functools import cached_property, lru_cache
from types import MappingProxyType
from typing import Any

from app.core.configs import load_config
from app.matching.skills import SkillCatalog, get_catalog

COMPONENTS: tuple[str, ...] = ("T", "K", "F", "A", "U")
GRADES: tuple[str, ...] = ("intern", "junior", "middle", "senior")
FORMATS: tuple[str, ...] = ("office", "hybrid", "remote")

# --------------------------------------------------------------------------- утилиты


def phi(x: float) -> float:
    """Функция распределения стандартного нормального закона."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def round1(x: float) -> float:
    """Округление до 0,1 «как в школе» (78,25 → 78,3), без банковского округления Python."""
    return math.floor(x * 10.0 + 0.5 + 1e-9) / 10.0


def fmt1(x: float, *, sign: bool = False, digits: int = 1) -> str:
    """78.3 → «78,3»; со знаком: «+4,0» / «−1,9» (типографский минус). digits=2 — для множителей (×0,84)."""
    v = round1(abs(x)) if digits == 1 else round(abs(x), digits)
    s = f"{v:.{digits}f}".replace(".", ",")
    if sign:
        return ("−" if x < 0 and v > 0 else "+") + s
    return ("−" if x < 0 and v > 0 else "") + s


def display_parts(contrib: Mapping[str, float], total: float) -> dict[str, float]:
    """Вклады с шагом 0,1, сумма которых ровно равна округлённому Score (метод наибольших остатков)."""
    target = int(round1(total) * 10 + (0.5 if total >= 0 else -0.5))
    floors = {k: math.floor(v * 10 + 1e-9) for k, v in contrib.items()}
    rest = target - sum(floors.values())
    order = sorted(
        contrib,
        key=lambda k: (-(contrib[k] * 10 - floors[k]), COMPONENTS.index(k) if k in COMPONENTS else 99),
    )
    for k in order[: max(0, rest)]:
        floors[k] += 1
    return {k: floors[k] / 10.0 for k in contrib}


def _freeze(m: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType(dict(m))


# --------------------------------------------------------------------------- конфиг


@dataclass(frozen=True)
class ScoringConfig:
    """scoring.yaml + матрицы ФСП (fsp.yaml) + пороги грейдов (taxonomy.yaml)."""

    raw: Mapping[str, Any]
    fsp: Mapping[str, Any]
    taxonomy: Mapping[str, Any]

    @classmethod
    def load(cls) -> ScoringConfig:
        return _default_config()

    # cached_property пишет прямо в __dict__, поэтому работает и у frozen-датакласса.
    @cached_property
    def version(self) -> str:
        """scoring_config_version: хеш ВСЕХ входов формулы — scoring.yaml, матриц ФСП (F и K), порогов грейдов
        и blueprint (taxonomy.yaml), графа «навык → модули» (skills.yaml). Иначе правка матрицы m(d,s)
        поменяла бы ранги при той же записанной версии (концепция §4.2 «Детерминизм»)."""
        graph = load_config("skills").get("skills", {}) or {}
        payload = {
            "scoring": self.raw,
            "fsp": {
                k: self.fsp.get(k) for k in ("spec_relevance", "module_relevance", "skill_evidence_factor")
            },
            "taxonomy": {k: self.taxonomy.get(k) for k in ("grades", "specializations")},
            "skills": {sid: list((s or {}).get("modules") or ()) for sid, s in sorted(graph.items())},
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()
        return f"{self.raw.get('version', 0)}-{hashlib.sha256(blob).hexdigest()[:10]}"

    @cached_property
    def cuts(self) -> Mapping[str, float]:
        return _freeze({g["code"]: float(g["cut"]) for g in self.taxonomy["grades"]})

    @cached_property
    def grade_names(self) -> Mapping[str, str]:
        return _freeze({g["code"]: g["name"] for g in self.taxonomy["grades"]})

    @cached_property
    def spec_names(self) -> Mapping[str, str]:
        return _freeze({s["code"]: s["name"] for s in self.taxonomy["specializations"]})

    def blueprint(self, spec: str) -> dict[str, float]:
        for s in self.taxonomy["specializations"]:
            if s["code"] == spec:
                return {k: float(v) for k, v in (s.get("blueprint") or {}).items()}
        return {}

    def category_name(self, spec: str, grade: str) -> str:
        return f"{self.spec_names.get(spec, spec)} · {self.grade_names.get(grade, grade)}"

    def weights(self, preset: str | None = None) -> dict[str, float]:
        name = preset or self.raw["default_preset"]
        presets = self.raw["presets"]
        if name not in presets:
            raise ValueError(f"Неизвестный пресет весов: {name}")
        w = {k: float(v) for k, v in presets[name]["weights"].items()}
        validate_weights(w, self.raw["weight_constraints"])
        return w

    def section(self, name: str) -> Mapping[str, Any]:
        section: Mapping[str, Any] = self.raw[name]
        return section


def validate_weights(w: Mapping[str, float], constraints: Mapping[str, float]) -> None:
    """wT ≥ 0,30, wF ≤ 0,15, Σw = 1 (концепция §4.2): ФСП не перевешивает большой разрыв в тесте."""
    missing = [k for k in COMPONENTS if k not in w]
    if missing:
        raise ValueError(f"Не заданы веса: {', '.join(missing)}")
    if w["T"] < constraints["T_min"] - 1e-12:
        raise ValueError(f"Вес теста {w['T']} меньше минимума {constraints['T_min']}")
    if w["F"] > constraints["F_max"] + 1e-12:
        raise ValueError(f"Вес ФСП {w['F']} больше максимума {constraints['F_max']}")
    if any(v < 0 for v in w.values()):
        raise ValueError("Веса не могут быть отрицательными")
    if abs(sum(w[k] for k in COMPONENTS) - constraints["sum"]) > constraints.get("sum_tolerance", 1e-6):
        raise ValueError("Сумма весов должна быть равна 1")


@lru_cache(maxsize=1)
def _default_config() -> ScoringConfig:
    return ScoringConfig(load_config("scoring"), load_config("fsp"), load_config("taxonomy"))


# --------------------------------------------------------------------------- входные данные


@dataclass(frozen=True)
class ModuleScore:
    """Подбалл модуля теста. tag — как в app/testing/cat.py: strong (✓✓) | confirmed (✓) | insufficient (~) | none.
    Доказательством навыка служат только strong/confirmed (scoring.yaml K.confirmed_module_tags)."""

    theta: float
    se: float
    tag: str = "confirmed"


@dataclass(frozen=True)
class TaskResult:
    """Регулярная задача. kind A — автопроверка (θ, K, A); kind B — «предложи подход» (только A)."""

    task_id: str
    kind: str
    q: float
    days_ago: float
    title: str = ""
    skills: tuple[str, ...] = ()
    module: str | None = None


@dataclass(frozen=True)
class CandidateFeatures:
    """Признаки кандидата В ОДНОЙ категории (spec × grade). Fullstack-кандидат приходит в каждой из своих категорий."""

    candidate_id: str
    spec: str
    grade: str
    theta: float | None = None  # None — категория по опросу, тестом не подтверждена (DECISIONS п.5)
    se: float = 0.5
    theta_age_days: float = 0.0  # дни с последнего обновления θ (тест или задача типа A) → SE(t)
    test_days_ago: float | None = None  # дни с теста (подпись «тест N дней назад», «старше 12 мес.»)
    test_session_id: str | None = None
    borderline: bool = False
    trust: float = 1.0
    is_fullstack: bool = False
    modules: Mapping[str, ModuleScore] = field(default_factory=dict)
    tasks: tuple[TaskResult, ...] = ()
    vss_by_discipline: Mapping[str, float] = field(default_factory=dict)
    fsp_summary: str | None = None  # обобщённая строка ФСП для работодателя («Призёр всероссийских · …»)
    fsp_ref: str | None = None  # id записи ФСП для ссылки-доказательства
    declared_skills: frozenset[str] = frozenset()  # резюме/опрос: только серые чипы, в Score не входят
    salary_min: int | None = None
    salary_net: bool = True
    salary_visibility: str = "signal"  # signal | exact
    formats: frozenset[str] = frozenset()
    city: str | None = None
    relocation: bool = False
    industries: frozenset[str] = frozenset()

    @property
    def confirmed(self) -> bool:
        return self.theta is not None

    @property
    def has_fsp(self) -> bool:
        return any(v > 0 for v in self.vss_by_discipline.values())


@dataclass(frozen=True)
class BriefSpec:
    """Бриф в форме, нужной скорингу (после разбора и правок работодателя)."""

    need_id: str
    spec: str
    grade: str
    must: tuple[str, ...] = ()
    nice: tuple[str, ...] = ()
    salary_from: int | None = None
    salary_to: int | None = None
    salary_net: bool = True
    formats: frozenset[str] = frozenset()
    city: str | None = None

    @property
    def skills(self) -> tuple[str, ...]:
        return self.must + tuple(k for k in self.nice if k not in self.must)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["must"], d["nice"] = list(self.must), list(self.nice)
        d["formats"] = sorted(self.formats)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> BriefSpec:
        return cls(
            need_id=str(d["need_id"]),
            spec=d["spec"],
            grade=d["grade"],
            must=tuple(d.get("must", ())),
            nice=tuple(d.get("nice", ())),
            salary_from=d.get("salary_from"),
            salary_to=d.get("salary_to"),
            salary_net=bool(d.get("salary_net", True)),
            formats=frozenset(d.get("formats", ())),
            city=d.get("city"),
        )


# --------------------------------------------------------------------------- результат


@dataclass(frozen=True)
class SkillEvidence:
    """Чип навыка брифа в карточке: v_k, источник и ссылка на доказательство."""

    skill_id: str
    name: str
    kind: str  # must | nice
    v: float
    source: str | None  # test | task | fsp | None
    ref: str | None  # код модуля / id задачи / дисциплина ФСП
    label: str  # «Тест: модуль SQL ✓✓», «Задача „…“ 4/5», «ФСП: алгоритмическое», «заявлено», «нет данных»
    state: str  # confirmed | weak | declared | no_data
    declared: bool
    untestable: bool


@dataclass(frozen=True)
class TDetail:
    value: float
    theta_lcb: float | None
    se_t: float | None
    pct: float | None
    method: str  # percentile | normal | none
    trust: float


@dataclass(frozen=True)
class FDetail:
    value: float
    discipline: str | None
    vss: float | None
    relevance: float | None


@dataclass(frozen=True)
class ADetail:
    value: float
    task_id: str | None
    title: str | None
    days_ago: float | None
    q: float | None
    tagged: bool


@dataclass(frozen=True)
class UDetail:
    value: float
    sal_fit: float
    fmt_fit: float
    salary_above_pct: float | None  # на сколько % минимум выше верха вилки (None — в вилке или неизвестно)
    salary_known: bool
    format_match: bool
    city_ok: bool


@dataclass(frozen=True)
class ScoreResult:
    need_id: str
    candidate_id: str
    spec: str
    grade: str
    score: float
    G: float
    Cov: float
    x: Mapping[str, float]
    c: Mapping[str, float]
    weights: Mapping[str, float]
    skills: tuple[SkillEvidence, ...]
    t: TDetail
    f: FDetail
    a: ADetail
    u: UDetail
    must_total: int
    must_confirmed: int
    flags: frozenset[str]  # borderline | low_trust | stale_test | unconfirmed
    declared_overlap: int
    features: CandidateFeatures
    cond: float = 1.0  # множитель жёстких условий (зарплата, формат/город); 1,0 — условия выполнены
    cond_parts: Mapping[str, float] = field(default_factory=lambda: {"salary": 1.0, "format": 1.0})  # Cond = salary·format

    @property
    def confirmed(self) -> bool:
        return self.features.confirmed

    @property
    def score_display(self) -> float:
        return round1(self.score)

    @property
    def parts_display(self) -> dict[str, float]:
        return display_parts(self.c, self.score)

    @property
    def tie_hash(self) -> int:
        return tie_hash(self.need_id, self.candidate_id)


def tie_hash(need_id: str, candidate_id: str) -> int:
    """Последний ключ сортировки: детерминированный hash(need_id, candidate_id)."""
    return int(hashlib.sha256(f"{need_id}|{candidate_id}".encode()).hexdigest()[:16], 16)


# --------------------------------------------------------------------------- компоненты


def theta_lcb(
    theta: float, se: float, age_days: float, cfg: ScoringConfig | None = None
) -> tuple[float, float]:
    """θ_lcb = θ̂ − SE(t), SE(t)² = SE² + 0,02·Δмес: без активности неопределённость растёт (spec-testing §3.11)."""
    c = (cfg or _default_config()).section("T")
    months = max(0.0, age_days) / float(c["days_per_month"])
    se_t = math.sqrt(se * se + float(c["se_growth_per_month"]) * months)
    return theta - se_t, se_t


def percentile_rank(sorted_values: Sequence[float], x: float) -> float:
    """Доля категории ниже x (совпадения — наполовину)."""
    n = len(sorted_values)
    if n == 0:
        return 0.0
    lo, hi = bisect_left(sorted_values, x), bisect_right(sorted_values, x)
    return (lo + 0.5 * (hi - lo)) / n


CategoryStats = Mapping[tuple[str, str], Sequence[float]]


def build_category_stats(
    pool: Iterable[CandidateFeatures], cfg: ScoringConfig | None = None
) -> dict[tuple[str, str], tuple[float, ...]]:
    """Отсортированные θ_lcb подтверждённых кандидатов по категориям — база для перцентиля T."""
    cfg = cfg or _default_config()
    out: dict[tuple[str, str], list[float]] = {}
    seen: set[tuple[str, str, str]] = set()
    for c in pool:
        key = (c.candidate_id, c.spec, c.grade)
        if c.theta is None or key in seen:
            continue
        seen.add(key)
        out.setdefault((c.spec, c.grade), []).append(theta_lcb(c.theta, c.se, c.theta_age_days, cfg)[0])
    return {k: tuple(sorted(v)) for k, v in out.items()}


def component_T(c: CandidateFeatures, stats: CategoryStats | None, cfg: ScoringConfig) -> TDetail:
    """T = trust · Pct_категории(θ_lcb); при n < 30 — Φ((θ_lcb − μ_g)/σ_g) по калибровке."""
    if c.theta is None:
        return TDetail(0.0, None, None, None, "none", c.trust)
    tc = cfg.section("T")
    lcb, se_t = theta_lcb(c.theta, c.se, c.theta_age_days, cfg)
    pop = (stats or {}).get((c.spec, c.grade), ())
    if len(pop) >= int(tc["min_category_n"]):
        pct, method = percentile_rank(pop, lcb), "percentile"
    else:
        fb = tc["fallback"][c.grade]
        pct, method = phi((lcb - float(fb["mu"])) / float(fb["sigma"])), "normal"
    return TDetail(c.trust * pct, lcb, se_t, pct, method, c.trust)


def _module_label(cat: SkillCatalog, module: str, tag: str) -> str:
    mark = "✓✓" if tag == "strong" else "✓"
    return f"Тест: модуль {cat.module_names.get(module, module)} {mark}"


def _p_module(ms: ModuleScore, cut: float) -> float:
    """P(θ_m ≥ порог грейда брифа) при нормальном апостериоре модуля."""
    if ms.se <= 0:
        return 1.0 if ms.theta >= cut else 0.0
    return phi((ms.theta - cut) / ms.se)


def skill_value(
    skill_id: str,
    c: CandidateFeatures,
    brief_grade: str,
    cfg: ScoringConfig,
    cat: SkillCatalog,
) -> tuple[float, str | None, str | None, str]:
    """v_k = max(1,0·test_k; 0,9·q·2^(−Δд/90); 0,8·m(d,k)·VSS_d/100). Самозаявки (self_k) нет — DECISIONS п.1.

    Возвращает (v, источник, ссылка, подпись).
    """
    kc = cfg.section("K")
    modules = cat.modules_of(skill_id)
    specific = set(cat.specific_modules_of(skill_id))
    generic = [m for m in modules if m not in specific]
    # Навык с «своим» модулем проверочного набора (PostgreSQL, FastAPI, Redis, очереди): соседний общий модуль теста —
    # лишь косвенное свидетельство. Прямая задача по навыку может его ТОЛЬКО опровергнуть: решена (v ≥ порога) — берём
    # максимум, как у любого свидетельства; не решена (v < порога) — прямое наблюдение вытесняет вывод по соседнему модулю
    # (docs/MATCHING.md). Так провал проверки понижает навык, а успех не наказывает проверенного за 0,9 < 1,0.
    proxy = 1.0
    thr = float(cfg.section("Cov")["confirm_threshold"])
    cut = cfg.cuts[brief_grade]
    best: tuple[float, str | None, str | None, str] = (0.0, None, None, "")
    direct: tuple[float, str | None, str | None, str] | None = None
    # Тест: только модули с отметкой ✓/✓✓ (spec-testing: «~» — мало данных) и только при подтверждённом тесте.
    if c.confirmed:
        tags = set(kc["confirmed_module_tags"])
        for m in generic:
            ms = c.modules.get(m)
            if ms is None or ms.tag not in tags:
                continue
            v = float(kc["test_factor"]) * _p_module(ms, cut) * proxy
            if v > best[0] + 1e-12:
                best = (v, "test", m, _module_label(cat, m, ms.tag))
    # Задачи типа A с тегом навыка или модулем навыка.
    for t in c.tasks:
        if t.kind != "A":
            continue
        is_direct = bool(specific) and (skill_id in t.skills or (t.module is not None and t.module in specific))
        if not is_direct and skill_id not in t.skills and (t.module is None or t.module not in modules):
            continue
        v = float(kc["task_factor"]) * t.q * 2.0 ** (-max(0.0, t.days_ago) / float(kc["task_half_life_days"]))
        v *= proxy
        item = (v, "task", t.task_id, f"Задача «{t.title or t.task_id}» {round(t.q * 5)}/5")
        if is_direct:
            if direct is None or v > direct[0] + 1e-12:
                direct = item
        elif v > best[0] + 1e-12:
            best = item
    if direct is not None and (direct[0] < thr or direct[0] > best[0] + 1e-12):
        best = direct
    # ФСП как доказательство навыка: m(d,k) = max по модулям навыка (fsp.yaml: module_relevance).
    rel = cfg.fsp.get("module_relevance", {})
    factor = float(cfg.fsp.get("skill_evidence_factor", 0.8))
    for d in sorted(c.vss_by_discipline):
        row = rel.get(d, {})
        rel_m = max((float(row.get(mod, 0.0)) for mod in modules), default=0.0)
        fsp_v = factor * rel_m * float(c.vss_by_discipline[d]) / 100.0
        if fsp_v > best[0] + 1e-12:
            best = (fsp_v, "fsp", d, f"ФСП: {_discipline_label(cfg, d)}")
    return best


def skill_sources(
    skill_id: str, c: CandidateFeatures, brief_grade: str, cfg: ScoringConfig, cat: SkillCatalog
) -> list[tuple[str, str, str]]:
    """ВСЕ источники, из которых складывается v_k: (источник, ссылка, подпись), главный (по v) первым.

    skill_value берёт максимум, поэтому для объяснения мало назвать один источник: убери его — и v_k упадёт лишь до
    запасного. «Доказательство довода» по стеку — полный набор (VALIDATION.md §4.6, контрфакт довода №1)."""
    kc = cfg.section("K")
    modules = cat.modules_of(skill_id)
    specific = set(cat.specific_modules_of(skill_id))
    cut = cfg.cuts[brief_grade]
    scored: list[tuple[float, tuple[str, str, str]]] = []
    if c.confirmed:
        tags = set(kc["confirmed_module_tags"])
        for m in (m for m in modules if m not in specific):
            ms = c.modules.get(m)
            if ms is None or ms.tag not in tags:
                continue
            v = float(kc["test_factor"]) * _p_module(ms, cut)
            if v > 0:
                scored.append((v, ("test", m, _module_label(cat, m, ms.tag))))
    for t in c.tasks:
        if t.kind != "A":
            continue
        if not (skill_id in t.skills or (t.module is not None and t.module in modules)):
            continue
        v = float(kc["task_factor"]) * t.q * 2.0 ** (-max(0.0, t.days_ago) / float(kc["task_half_life_days"]))
        if v > 0:
            scored.append((v, ("task", t.task_id, f"Задача «{t.title or t.task_id}» {round(t.q * 5)}/5")))
    rel = cfg.fsp.get("module_relevance", {})
    factor = float(cfg.fsp.get("skill_evidence_factor", 0.8))
    for d in sorted(c.vss_by_discipline):
        rel_m = max((float(rel.get(d, {}).get(mod, 0.0)) for mod in modules), default=0.0)
        v = factor * rel_m * float(c.vss_by_discipline[d]) / 100.0
        if v > 0:
            scored.append((v, ("fsp", d, f"ФСП: {_discipline_label(cfg, d)}")))
    return [s for _, s in sorted(scored, key=lambda x: -x[0])]


def _discipline_label(cfg: ScoringConfig, short: str) -> str:
    for d in cfg.fsp.get("disciplines", ()):
        if d.get("short") == short or d.get("code") == short:
            return str(d.get("label", short))
    return short


def component_K(
    c: CandidateFeatures, brief: BriefSpec, cfg: ScoringConfig, cat: SkillCatalog
) -> tuple[float, tuple[SkillEvidence, ...], int, int]:
    """K = Σ w_k·v_k / Σ w_k (must 1,0, nice 0,4). Возвращает (K, чипы, подтверждено must, всего must)."""
    kc, covc = cfg.section("K"), cfg.section("Cov")
    thr = float(covc["confirm_threshold"])
    chips: list[SkillEvidence] = []
    num = den = 0.0
    must_conf = 0
    seen: set[str] = set()
    for kind, ids, w in (
        ("must", brief.must, float(kc["must_weight"])),
        ("nice", brief.nice, float(kc["nice_weight"])),
    ):
        for sid in ids:
            if sid in seen:
                continue
            seen.add(sid)
            v, src, ref, label = skill_value(sid, c, brief.grade, cfg, cat)
            declared = sid in c.declared_skills
            if v >= thr:
                state = "confirmed"
            elif declared:
                state, label = "declared", "заявлено"
            elif v > 0 or src == "task":
                state = "weak"  # задача по навыку решена слабо или не решена: проверено, но не подтверждено
            else:
                state, label = "no_data", "нет данных"
            if kind == "must" and v >= thr:
                must_conf += 1
            skill = cat.skills.get(sid)
            chips.append(
                SkillEvidence(
                    sid,
                    cat.name(sid),
                    kind,
                    v,
                    src,
                    ref,
                    label,
                    state,
                    declared,
                    bool(skill and skill.untestable),
                )
            )
            num += w * v
            den += w
    if den > 0:
        return num / den, tuple(chips), must_conf, len(set(brief.must))
    # В брифе нет навыков: K по модулям стандартного теста категории брифа (веса blueprint).
    bp = cfg.blueprint(brief.spec)
    if not bp or not c.confirmed:
        return 0.0, (), 0, 0
    tags = set(kc["confirmed_module_tags"])
    cut = cfg.cuts[brief.grade]
    tot = sum(bp.values())
    val = sum(
        q * (_p_module(c.modules[m], cut) if m in c.modules and c.modules[m].tag in tags else 0.0)
        for m, q in bp.items()
    )
    return val / tot if tot else 0.0, (), 0, 0


def component_F(c: CandidateFeatures, brief: BriefSpec, cfg: ScoringConfig) -> FDetail:
    """F = max_d m(d, s_брифа)·VSS_d/100. Нет истории ФСП → F = 0: отсутствие бонуса, а не штраф."""
    rel = cfg.fsp.get("spec_relevance", {})
    best = FDetail(0.0, None, None, None)
    for d in sorted(c.vss_by_discipline):
        m = float(rel.get(d, {}).get(brief.spec, 0.0))
        v = m * float(c.vss_by_discipline[d]) / 100.0
        if v > best.value + 1e-12:
            best = FDetail(v, d, float(c.vss_by_discipline[d]), m)
    return best


def component_A(c: CandidateFeatures, brief: BriefSpec, cfg: ScoringConfig, cat: SkillCatalog) -> ADetail:
    """A = max_r min(1, q_r·(1 + 0,25·[тег r ∈ навыки брифа]))·2^(−Δд_r/45) по задачам A и B за 180 дней."""
    ac = cfg.section("A")
    brief_skills = set(brief.skills)
    brief_modules = {m for s in brief_skills for m in cat.modules_of(s)}
    best = ADetail(0.0, None, None, None, None, False)
    for t in c.tasks:
        if t.days_ago > float(ac["window_days"]):
            continue
        tagged = bool(brief_skills.intersection(t.skills)) or (
            t.module is not None and t.module in brief_modules
        )
        v = min(1.0, t.q * (1.0 + float(ac["tag_bonus"]) * tagged)) * 2.0 ** (
            -max(0.0, t.days_ago) / float(ac["half_life_days"])
        )
        if v > best.value + 1e-12:
            best = ADetail(v, t.task_id, t.title or None, t.days_ago, t.q, tagged)
    return best


def _to_brief_basis(amount: float, amount_net: bool, brief_net: bool, ndfl: float) -> float:
    if amount_net == brief_net:
        return amount
    return amount * (1.0 - ndfl) if brief_net else amount / (1.0 - ndfl)


def component_U(c: CandidateFeatures, brief: BriefSpec, cfg: ScoringConfig) -> UDetail:
    """U = 0,6·SalFit + 0,4·FmtFit; SalFit = 1 при min ≤ to, иначе max(0, 1 − (min/to − 1)/0,25)."""
    uc = cfg.section("U")
    sal_fit, above, known = 1.0, None, False
    if c.salary_min is not None and brief.salary_to:
        known = True
        mn = _to_brief_basis(float(c.salary_min), c.salary_net, brief.salary_net, float(uc["ndfl_rate"]))
        ratio = mn / float(brief.salary_to)
        if ratio > 1.0 + 1e-9:
            above = (ratio - 1.0) * 100.0
            over = ratio - 1.0
            # Приватность минимума (концепция §4.5: по умолчанию работодатель видит только «в вилке / выше»):
            # превышение берём полосой salary_band, иначе точный минимум восстанавливался бы из вклада U.
            band = float(uc.get("salary_band", 0.0))
            if band > 0:
                over = math.ceil(over / band - 1e-9) * band
            sal_fit = max(0.0, 1.0 - over / float(uc["salary_tolerance"]))
    fmt_fit, city_ok = 1.0, True
    if brief.formats and c.formats:
        matrix = uc["format_fit"]
        city_mismatch = bool(brief.city and c.city and brief.city != c.city and not c.relocation)
        fits = []
        for b in sorted(brief.formats):
            f = max(float(matrix.get(b, {}).get(cf, 0.0)) for cf in c.formats)
            if city_mismatch and b in ("office", "hybrid"):
                f = min(f, float(uc["city_mismatch_fit"]))
            fits.append(f)
        fmt_fit = max(fits)
        city_ok = not city_mismatch or "remote" in brief.formats
    elif brief.city and c.city and brief.city != c.city and not c.relocation and brief.formats - {"remote"}:
        fmt_fit, city_ok = float(uc["city_mismatch_fit"]), False
    value = float(uc["salary_weight"]) * sal_fit + float(uc["format_weight"]) * fmt_fit
    return UDetail(value, sal_fit, fmt_fit, above, known, fmt_fit >= 1.0 - 1e-9, city_ok)


def component_G(c: CandidateFeatures, brief: BriefSpec, cfg: ScoringConfig) -> float:
    """G = adjS·adjG (·0,9 при BORDERLINE_PASS). Основная категория = 1,0."""
    gc = cfg.section("G")
    if c.spec == brief.spec or (c.is_fullstack and brief.spec in gc["fullstack_badge_specs"]):
        adj_s = 1.0
    else:
        adj_s = 0.0
        for a, b, w in gc["spec_adjacency"]:
            if {a, b} == {c.spec, brief.spec}:
                adj_s = float(w)
                break
    d = GRADES.index(c.grade) - GRADES.index(brief.grade)
    adj_g = float(gc["grade_above"]) ** d if d > 0 else float(gc["grade_below"]) ** (-d)
    g = adj_s * adj_g
    if c.borderline:
        g *= float(gc["borderline_factor"])
    return g


def component_cond(u: UDetail, cfg: ScoringConfig) -> float:
    """Cond = (1 − s_sal·(1 − SalFit)) · (1 − s_fmt·(1 − FmtFit)): условия как ограничение, а не только 10% бонуса.

    Зачем: работодатель не считает релевантным кандидата, чьё ожидание выше вилки или чей формат/город не подходит;
    при чисто аддитивном U (10 баллов из 100) такой кандидат оставался в топе. Условия выполнены → 1,0, и
    формула сводится к прежней (контрольные примеры §4.2 не меняются). Обоснование — VALIDATION.md, «Условия как ограничение».
    """
    parts = cond_parts(u, cfg)
    return parts["salary"] * parts["format"]


def cond_parts(u: UDetail, cfg: ScoringConfig) -> dict[str, float]:
    """Сомножители Cond по отдельности — для разбора в карточке и снимка версии подборки."""
    cc = cfg.section("Cond")
    return {
        "salary": 1.0 - float(cc["salary_strength"]) * (1.0 - u.sal_fit),
        "format": 1.0 - float(cc["format_strength"]) * (1.0 - u.fmt_fit),
    }


def score_from_components(
    x: Mapping[str, float], weights: Mapping[str, float], G: float = 1.0, Cov: float = 1.0, cond: float = 1.0
) -> tuple[float, dict[str, float]]:
    """c_i = 100·G·Cov·Cond·w_i·x_i, Score = Σ c_i (концепция §4.4 п.1)."""
    c = {k: 100.0 * G * Cov * cond * float(weights[k]) * float(x[k]) for k in COMPONENTS}
    return sum(c.values()), c


# --------------------------------------------------------------------------- сборка


@dataclass(frozen=True)
class ScoringContext:
    cfg: ScoringConfig
    catalog: SkillCatalog
    weights: Mapping[str, float]
    preset: str
    category_stats: CategoryStats

    @classmethod
    def build(
        cls,
        pool: Iterable[CandidateFeatures] = (),
        *,
        preset: str | None = None,
        weights: Mapping[str, float] | None = None,
        cfg: ScoringConfig | None = None,
        catalog: SkillCatalog | None = None,
        category_stats: CategoryStats | None = None,
    ) -> ScoringContext:
        cfg = cfg or _default_config()
        name = preset or cfg.raw["default_preset"]
        if weights is None:
            w = cfg.weights(name)
        else:
            w = {k: float(v) for k, v in weights.items()}
            validate_weights(w, cfg.raw["weight_constraints"])
        stats = category_stats if category_stats is not None else build_category_stats(pool, cfg)
        return cls(cfg, catalog or get_catalog(), _freeze(w), name, stats)


def score_candidate(c: CandidateFeatures, brief: BriefSpec, ctx: ScoringContext) -> ScoreResult:
    cfg, cat = ctx.cfg, ctx.catalog
    t = component_T(c, ctx.category_stats, cfg)
    k_val, chips, must_conf, must_total = component_K(c, brief, cfg, cat)
    f = component_F(c, brief, cfg)
    a = component_A(c, brief, cfg, cat)
    u = component_U(c, brief, cfg)
    g = component_G(c, brief, cfg)
    covc = cfg.section("Cov")
    # Cov: подтверждено (v_k ≥ 0,5) меньше половины must → ×0,8.
    cov = float(covc["factor"]) if must_total and must_conf < float(covc["min_share"]) * must_total else 1.0
    x = {"T": t.value, "K": k_val, "F": f.value, "A": a.value, "U": u.value}
    cparts = cond_parts(u, cfg)
    cond = cparts["salary"] * cparts["format"]
    score, contrib = score_from_components(x, ctx.weights, g, cov, cond)
    tc = cfg.section("T")
    flags = set()
    if c.borderline:
        flags.add("borderline")
    if c.confirmed and c.trust < float(tc["low_trust_below"]) - 1e-9:
        flags.add("low_trust")
    if c.test_days_ago is not None and c.test_days_ago > float(tc["stale_test_days"]):
        flags.add("stale_test")
    if not c.confirmed:
        flags.add("unconfirmed")
    overlap = len(c.declared_skills.intersection(brief.skills))
    return ScoreResult(
        need_id=brief.need_id,
        candidate_id=c.candidate_id,
        spec=c.spec,
        grade=c.grade,
        score=score,
        G=g,
        Cov=cov,
        x=_freeze(x),
        c=_freeze(contrib),
        weights=ctx.weights,
        skills=chips,
        t=t,
        f=f,
        a=a,
        u=u,
        must_total=must_total,
        must_confirmed=must_conf,
        flags=frozenset(flags),
        declared_overlap=overlap,
        features=c,
        cond=cond,
        cond_parts=_freeze(cparts),
    )


def sort_key(r: ScoreResult, cfg: ScoringConfig | None = None) -> tuple[Any, ...]:
    """Score ↓, T ↓, [заявленные навыки ↓], hash(need_id, candidate_id) — детерминированно (концепция §4.2).

    Кандидаты с категорией «по опросу» (без теста) — после подтверждённых (DECISIONS п.5)."""
    tb = (cfg or _default_config()).section("tie_break")
    p = int(tb["score_precision"])
    return (
        0 if (r.confirmed or not tb["confirmed_first"]) else 1,
        -round(r.score, p),
        -round(r.x["T"], p),
        -r.declared_overlap if tb["declared_overlap"] else 0,
        r.tie_hash,
    )


def rank_results(results: Iterable[ScoreResult], cfg: ScoringConfig | None = None) -> list[ScoreResult]:
    cfg = cfg or _default_config()
    return sorted(results, key=lambda r: sort_key(r, cfg))


def score_pool(
    pool: Sequence[CandidateFeatures],
    brief: BriefSpec,
    *,
    preset: str | None = None,
    ctx: ScoringContext | None = None,
) -> list[ScoreResult]:
    """Оценить и отсортировать кандидатов. Перцентили T считаются по самому пулу, если статистика не передана."""
    ctx = ctx or ScoringContext.build(pool, preset=preset)
    return rank_results((score_candidate(c, brief, ctx) for c in pool), ctx.cfg)


# --------------------------------------------------------------------------- банк кандидатов без брифа


@dataclass(frozen=True)
class BankRow:
    """Строка банка (Р9): балл без брифа и его компоненты."""

    candidate_id: str
    spec: str
    grade: str
    score: float
    x: Mapping[str, float]
    confirmed: bool


def bank_score(c: CandidateFeatures, ctx: ScoringContext) -> BankRow:
    """Банк без брифа (концепция §3.2 шаг 7): 0,55·T + 0,25·F + 0,20·A — веса в scoring.yaml (bank).

    F — по собственной специализации кандидата (брифа нет), A — без бонуса за тег (навыков брифа нет)."""
    cfg, cat = ctx.cfg, ctx.catalog
    own = BriefSpec(need_id="bank", spec=c.spec, grade=c.grade)
    x = {
        "T": component_T(c, ctx.category_stats, cfg).value,
        "F": component_F(c, own, cfg).value,
        "A": component_A(c, own, cfg, cat).value,
    }
    w = cfg.section("bank")["weights"]
    score = 100.0 * sum(float(w[k]) * x[k] for k in x)
    return BankRow(c.candidate_id, c.spec, c.grade, score, _freeze(x), c.confirmed)


def rank_bank(
    pool: Sequence[CandidateFeatures], *, ctx: ScoringContext | None = None, query_id: str = "bank"
) -> list[BankRow]:
    """Сортировка банка: подтверждённые тестом выше «по опросу», затем балл ↓, T ↓, hash(query_id, candidate_id)."""
    ctx = ctx or ScoringContext.build(pool)
    p = int(ctx.cfg.section("tie_break")["score_precision"])
    rows = [bank_score(c, ctx) for c in pool]
    return sorted(
        rows,
        key=lambda r: (
            0 if r.confirmed else 1,
            -round(r.score, p),
            -round(r.x["T"], p),
            tie_hash(query_id, f"{r.candidate_id}|{r.spec}|{r.grade}"),
        ),
    )
