"""Синтетическая популяция кандидатов с латентной истиной (концепция §7.1, spec-testing §6.1, spec-contact §6.1).

Каждый кандидат порождается своим генератором `random.Random("<seed>:cand:<i>")`, тест — генератором
`"<seed>:cat:<id>"`. Поэтому популяция из n кандидатов — префикс популяции из 2n: быстрый прогон
(`--quick`) и полный видят одних и тех же первых людей.

Скрытая истина (`Truth`): специализация, грейд, общий уровень, уровень по модулям, реальный стек.
Наблюдаемое: заявленные специализация и грейд, результат CAT на реальном банке (тот же движок, что в
проде), ФСП (VSS считает `app/fsp/vss.py`), регулярные задачи, самоописание с приписанными навыками,
минимум зарплаты, формат, город.

API для демо-seed и других модулей
----------------------------------
    from eval.population import PopulationConfig, generate_population

    pop = generate_population(PopulationConfig(n=200, seed=20261005))
    for cand in pop.candidates:
        feats = cand.features()          # app.matching.scoring.CandidateFeatures или None (тест не сдан)
        card = cand.to_public_dict()     # JSON-совместимый словарь без скрытой истины (для seed/импорта)
        truth = cand.truth               # только для валидации — в продукт не отдавать

`PopulationConfig(run_tests=False)` пропускает CAT (для быстрых демо-данных): тогда `cand.test is None`,
а `features()` возвращает None. Реальных ФИО, компаний и контактов в популяции нет.

Другие точки входа:
    generate_candidate(i, cfg)            # один кандидат — детерминированно по (cfg.seed, i)
    generate_chunk(cfg, indices, True)    # кусок популяции с пересдачей (для процессов; результат не зависит от разбиения)
    retest_of(cand, seed)                 # пересдача на непересекающихся семействах (S3)
    vss_truth_correlation(pop)            # фактическая корреляция VSS с истинным уровнем
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from typing import Any

from app.core.configs import load_config
from app.fsp.config import get_fsp_config
from app.fsp.models import AgeGroup, CompetitionFormat, Level, ResultRole, Source, Stage, StatusType
from app.fsp.schema import FSPProfile, FSPState, NormalizedResult
from app.fsp.vss import build_features
from app.matching.scoring import CandidateFeatures, ModuleScore, TaskResult
from app.matching.skills import get_catalog
from app.testing.cat import (
    CatState,
    FamilyMeta,
    ResponseRecord,
    load_blueprint,
    load_cat_config,
    module_subscores,
)
from app.testing.detectors import drift_probs, load_detector_config
from app.testing.irt import BELOW_INTERN, load_scale
from app.testing.simulate import SIM_EPOCH, SimulatedExaminee, run_simulated_session, sample_claimed_grade
from eval.common import MASTER_SEED, REF_DATE, grade_band, grade_index, real_bank

# --------------------------------------------------------------------------- справочники генератора

# Реальный стек по специализации: (id навыка из config/skills.yaml, базовая вероятность). Навыки с
# вероятностью ≥ 0,95 есть у всех; остальные — чаще у старших грейдов.
SPEC_SKILLS: dict[str, tuple[tuple[str, float], ...]] = {
    "backend_python": (
        ("python", 1.0), ("git", 0.97), ("sql", 0.9), ("rest", 0.85), ("postgresql", 0.75), ("linux", 0.7),
        ("docker", 0.65), ("http", 0.6), ("oop", 0.5), ("pytest", 0.5), ("django", 0.45), ("fastapi", 0.45),
        ("redis", 0.45), ("sqlalchemy", 0.4), ("github", 0.4), ("gitlab", 0.35), ("asyncio", 0.35),
        ("pydantic", 0.35), ("ci_cd", 0.35), ("celery", 0.3), ("drf", 0.3), ("algorithms", 0.3),
        ("data_structures", 0.3), ("microservices", 0.3), ("transactions", 0.3), ("sql_indexes", 0.3),
        ("bash", 0.3), ("flask", 0.25), ("alembic", 0.25), ("caching", 0.25), ("nginx", 0.25), ("kafka", 0.2),
        ("rabbitmq", 0.2), ("kubernetes", 0.15), ("mongodb", 0.15), ("grpc", 0.1), ("aiohttp", 0.1),
        ("elasticsearch", 0.1), ("clickhouse", 0.08), ("idempotency", 0.1), ("db_design", 0.2),
    ),
    "frontend_js": (
        ("javascript", 1.0), ("git", 0.95), ("html", 0.95), ("css", 0.95), ("react", 0.85), ("typescript", 0.7),
        ("rest", 0.6), ("responsive", 0.5), ("flexbox", 0.5), ("npm", 0.5), ("redux", 0.4), ("sass", 0.4),
        ("webpack", 0.4), ("http", 0.4), ("js_linters", 0.4), ("vite", 0.35), ("jest", 0.35), ("nextjs", 0.3),
        ("tailwind", 0.3), ("nodejs", 0.3), ("ajax", 0.3), ("react_hooks", 0.3), ("react_query", 0.25),
        ("event_loop", 0.25), ("vue", 0.2), ("accessibility", 0.15), ("graphql", 0.15), ("storybook", 0.15),
        ("material_ui", 0.15), ("mobx", 0.1), ("zustand", 0.1), ("algorithms", 0.1), ("docker", 0.15),
    ),
}
# Fullstack: обе базы обязательно, остальное — с пониженной вероятностью.
FULLSTACK_CORE = ("python", "javascript", "react", "sql", "git", "rest", "html", "css")
FULLSTACK_FACTOR = 0.7

# Популярные навыки, которые приписывают себе в резюме (если их нет в реальном стеке).
POPULAR_FAKE = (
    "kafka", "kubernetes", "docker", "fastapi", "react", "typescript", "redis", "microservices", "graphql",
    "clickhouse", "nextjs", "grpc", "highload", "system_design", "ci_cd", "aws", "go", "rabbitmq",
)

ROLE_TEXT = {
    "backend_python": "Backend-разработчик на Python",
    "frontend_js": "Frontend-разработчик",
    "fullstack": "Fullstack-разработчик",
}

TASK_TITLES: dict[str, tuple[str, ...]] = {
    "python_core": ("Парсер логов", "Кеш с ограничением по времени", "Обработка CSV-выгрузки"),
    "sql": ("Отчёт по заказам на SQL", "Ускорение медленного запроса"),
    "http_api": ("Идемпотентность платежей", "Пагинация в API"),
    "algo": ("Пересечение интервалов", "Топ частых событий"),
    "git_linux": ("Разбор конфликта слияния", "Скрипт ротации логов"),
    "js_core": ("Дебаунс поиска", "Очередь промисов"),
    "react": ("Форма с валидацией", "Виртуальный список"),
    "html_css": ("Адаптивная карточка товара", "Доступное модальное окно"),
}

FORMAT_PRESETS: tuple[tuple[tuple[str, ...], float], ...] = (
    (("remote",), 0.30),
    (("hybrid", "remote"), 0.30),
    (("office", "hybrid"), 0.25),
    (("office", "hybrid", "remote"), 0.15),
)
INDUSTRIES = ("fintech", "ecommerce", "gov", "industry", "telecom", "media_games", "edtech", "medtech", "logistics")

# Медиана стажа (мес.) по истинному грейду — для бейзлайна B4 «грейд по стажу».
EXPERIENCE_MEDIAN_MONTHS = {BELOW_INTERN: 4, "intern": 8, "junior": 18, "middle": 40, "senior": 80}

# Дисциплины ФСП по истинной специализации (короткие коды из config/fsp.yaml).
FSP_DISCIPLINES: dict[str, tuple[tuple[str, float], ...]] = {
    "backend_python": (("algo", 0.5), ("product", 0.35), ("infosec", 0.1), ("bas", 0.05)),
    "frontend_js": (("product", 0.65), ("algo", 0.3), ("robotics", 0.05)),
    "fullstack": (("product", 0.6), ("algo", 0.35), ("infosec", 0.05)),
}
TEAM_FUNCTION = {"backend_python": "backend", "frontend_js": "frontend", "fullstack": "fullstack"}


# --------------------------------------------------------------------------- конфигурация


@dataclass(frozen=True)
class PopulationConfig:
    n: int = 2000
    seed: int = MASTER_SEED
    spec_mix: tuple[tuple[str, float], ...] = (("backend_python", 0.5), ("frontend_js", 0.4), ("fullstack", 0.1))
    grade_mix: tuple[tuple[str, float], ...] = (
        (BELOW_INTERN, 0.05),
        ("intern", 0.15),
        ("junior", 0.30),
        ("middle", 0.30),
        ("senior", 0.20),
    )
    # Заявленная специализация не совпадает с истинной: 0,11 среди Backend/Frontend ≈ 10% всей популяции
    # (Fullstack проходит один из своих тестов — это не ошибка заявки).
    mismatch_rate: float = 0.11
    module_sd_own: float = 0.35  # модули своей специализации: уровень + N(0; 0,35²)
    module_offset_other: float = -1.5  # модули чужой специализации заметно слабее
    module_sd_other: float = 0.5
    fsp_rate: float = 0.35
    # Корреляция скрытой «силы ФСП» с уровнем. VSS нелинейно насыщается, поэтому 0,6 даёт итоговую
    # корреляцию VSS с истинным уровнем ≈ 0,5 (подобрано без CAT на 1500 кандидатах; фактическая — в отчёте).
    fsp_latent_rho: float = 0.6
    task_rate: float = 0.5
    task_slope: float = 1.2  # q = σ(1,2·(уровень модуля − b)) + шум (spec-contact §6.1)
    task_noise: float = 0.1
    fake_skill_share: float = 0.3  # самоописание = истина + 30% приписанных навыков
    salary_sigma_log: float = 0.15
    salary_round: int = 5000
    experience_sigma_log: float = 0.5
    max_test_age_days: int = 240
    run_tests: bool = True


# --------------------------------------------------------------------------- результат теста


@dataclass(frozen=True)
class ResponseLog:
    """Зачётный (или pretest) ответ сессии с апостериором без этого ответа — для CUSUM, калибровки и r_pb."""

    family_id: str
    module: str
    correct: bool
    is_pretest: bool
    loo_mean: float
    loo_sd: float
    p0: float  # CUSUM: Σ π₋ⱼ(θ)·P(θ; b)
    p1: float  # то же при b − cusum_shift


@dataclass(frozen=True)
class TestOutcome:
    """Итог цепочки сессий (основная часть + доборы) в форме, удобной валидации и подбору."""

    __test__ = False  # не путать pytest

    spec: str
    claimed_grade: str
    decision: str | None
    passed: bool
    p_pass: float | None
    theta_main: float | None
    se_main: float | None
    n_main: int
    n_total: int
    n_shown: int
    theta: float
    se: float
    final_grade: str | None
    borderline: bool
    class_probs: Mapping[str, float]
    subscores: Mapping[str, Mapping[str, Any]]
    families: tuple[str, ...]
    responses: tuple[ResponseLog, ...]
    instances: tuple[str, ...]
    extensions: tuple[Mapping[str, Any], ...]
    n_main_shown: int = 0  # показано в основной части вместе с pretest (DECISIONS п.17: ≤ 20)


def outcome_from_result(
    result: Mapping[str, Any], spec: str, bank_by_id: Mapping[str, FamilyMeta]
) -> TestOutcome:
    """Результат simulate.run_simulated_session → TestOutcome.

    Подбаллы модулей считает прод-функция cat.module_subscores по ответам всей цепочки (как
    TestSession.outcome); параметры заданий берутся из метаданных банка, а не из округлённых полей трассы.
    """
    scale = load_scale()
    cfg = load_cat_config()
    shift = load_detector_config().cusum_shift
    records: list[ResponseRecord] = []
    for it in result["items"]:
        meta = bank_by_id[it["family_id"]]
        ip = meta.item_params(spec, scale)
        records.append(
            ResponseRecord(
                seq=it["seq"],
                family_id=it["family_id"],
                module=it["module"],
                family_grade=it["family_grade"],
                is_pretest=it["is_pretest"],
                correct=bool(it["correct"]),
                b=ip.b,
                c=ip.c,
                a=ip.a,
                phase=it["phase"],
            )
        )
    final_grade = result["final_grade"]
    grade_for_tags = final_grade or result["claimed_grade"]
    state = CatState(
        spec=spec,
        claimed_grade=result["claimed_grade"],
        target_grade=grade_for_tags,
        blueprint=load_blueprint(spec),
        responses=records,
    )
    subs = module_subscores(state, grade_for_tags, scale, cfg)
    full = state.posterior(scale)
    logs: list[ResponseLog] = []
    for r in records:
        loo = full.copy().remove(r.item, r.correct) if not r.is_pretest else full.copy()
        p0, p1 = drift_probs(loo, r.item, shift)
        logs.append(ResponseLog(r.family_id, r.module, r.correct, r.is_pretest, loo.mean, loo.sd, p0, p1))
    return TestOutcome(
        spec=spec,
        claimed_grade=result["claimed_grade"],
        decision=result["decision"],
        passed=bool(result["passed"]),
        p_pass=result["p_pass"],
        theta_main=result["theta_hat"],
        se_main=result["se"],
        n_main=int(result["n"]),
        n_total=int(result["n_total"]),
        n_shown=len(result["items"]),
        theta=float(full.mean),
        se=float(full.sd),
        final_grade=final_grade,
        borderline=bool(result["borderline"]),
        class_probs=dict(result["class_probs"]),
        subscores={m: {"theta": s["theta"], "se": s["se"], "tag": s["tag"], "n": s["n"]} for m, s in subs.items()},
        families=tuple(it["family_id"] for it in result["items"]),
        responses=tuple(logs),
        instances=tuple(result["instances"]),
        extensions=tuple(dict(e) for e in result["extensions"]),
        n_main_shown=sum(1 for it in result["items"] if it["phase"] == 0),
    )


def simulate_test(
    *,
    theta: float,
    module_theta: Mapping[str, float] | None,
    spec: str,
    claimed_grade: str,
    rng: random.Random,
    bank: Sequence[FamilyMeta] | None = None,
    examinee: SimulatedExaminee | None = None,
    seen_families: Sequence[str] = (),
    session_id: str = "sim",
    follow_extensions: bool = True,
) -> TestOutcome:
    """Одна полная CAT-сессия (с доборами) на прод-движке. seen_families исключаются, как при 12-месячном
    запрете повтора (пересдача на непересекающихся семействах)."""
    bank = tuple(bank) if bank is not None else real_bank()
    seen = {fid: SIM_EPOCH for fid in seen_families} or None
    res = run_simulated_session(
        theta,
        spec,
        claimed_grade,
        bank,
        rng,
        module_theta=module_theta,
        examinee=examinee,
        follow_extensions=follow_extensions,
        seen_history=seen,
        session_id=session_id,
    )
    return outcome_from_result(res, spec, _by_id(bank))


@lru_cache(maxsize=8)
def _by_id_cached(bank: tuple[FamilyMeta, ...]) -> dict[str, FamilyMeta]:
    return {f.id: f for f in bank}


def _by_id(bank: Sequence[FamilyMeta]) -> dict[str, FamilyMeta]:
    return _by_id_cached(tuple(bank))


# --------------------------------------------------------------------------- кандидат


@dataclass(frozen=True)
class Truth:
    """Скрытая истина — только для валидации, в продукт и в слепые признаки не попадает."""

    spec: str  # backend_python | frontend_js | fullstack
    grade: str  # below_intern | intern | junior | middle | senior
    theta: float
    module_theta: Mapping[str, float]
    skills: frozenset[str]
    fsp_strength: float | None


@dataclass
class Candidate:
    id: str
    index: int
    truth: Truth
    claimed_spec: str
    claimed_grade: str
    mismatched: bool
    experience_months: int
    declared_skills: frozenset[str]
    fake_skills: frozenset[str]
    self_description: str
    salary_min: int
    salary_net: bool
    formats: frozenset[str]
    city: str
    relocation: bool
    industries: frozenset[str]
    tasks: tuple[TaskResult, ...]
    vss_by_discipline: Mapping[str, float]
    fsp_line: str | None
    fsp_public: tuple[str, ...]
    test_days_ago: float
    test: TestOutcome | None = None
    retest: TestOutcome | None = None

    @property
    def has_fsp(self) -> bool:
        return any(v > 0 for v in self.vss_by_discipline.values())

    @property
    def matched(self) -> bool:
        """Заявленная специализация совпадает с истинной (Fullstack проходит один из своих тестов)."""
        return not self.mismatched

    def features(
        self,
        outcome: TestOutcome | None = None,
        *,
        point_estimate: bool = False,
        declared: frozenset[str] | None = None,
        tasks: tuple[TaskResult, ...] | None = None,
        vss: Mapping[str, float] | None = None,
    ) -> CandidateFeatures | None:
        """Признаки подбора (как их строит прод из квалификации). None — категория не подтверждена тестом.

        point_estimate — абляция «без нижней границы»: SE = 0 и без старения (T по точечной оценке).
        """
        out = outcome if outcome is not None else self.test
        if out is None or out.final_grade is None:
            return None
        modules = {
            m: ModuleScore(theta=float(s["theta"]), se=float(s["se"]), tag=str(s["tag"]))
            for m, s in out.subscores.items()
        }
        v = dict(self.vss_by_discipline if vss is None else vss)
        has = any(x > 0 for x in v.values())
        return CandidateFeatures(
            candidate_id=self.id,
            spec=out.spec,
            grade=out.final_grade,
            theta=out.theta,
            se=0.0 if point_estimate else out.se,
            theta_age_days=0.0 if point_estimate else self.test_days_ago,
            test_days_ago=self.test_days_ago,
            test_session_id=f"sess-{self.id}",
            borderline=out.borderline,
            trust=1.0,
            modules=modules,
            tasks=self.tasks if tasks is None else tasks,
            vss_by_discipline=v,
            fsp_summary=self.fsp_line if has else None,
            fsp_ref=f"fsp-{self.id}" if has else None,
            declared_skills=self.declared_skills if declared is None else declared,
            salary_min=self.salary_min,
            salary_net=self.salary_net,
            salary_visibility="signal",
            formats=self.formats,
            city=self.city,
            relocation=self.relocation,
            industries=self.industries,
        )

    def to_public_dict(self) -> dict[str, Any]:
        """Слепые наблюдаемые данные без скрытой истины (для демо-seed, импорта и набора пар)."""
        t = self.test
        return {
            "candidate_id": self.id,
            "claimed_spec": self.claimed_spec,
            "claimed_grade": self.claimed_grade,
            "category": None
            if t is None or t.final_grade is None
            else {"spec": t.spec, "grade": t.final_grade, "borderline": t.borderline},
            "test": None
            if t is None
            else {
                "decision": t.decision,
                "theta": round(t.theta, 4),
                "se": round(t.se, 4),
                "p_pass": t.p_pass,
                "n_items": t.n_total,
                "test_days_ago": round(self.test_days_ago, 1),
                "modules": {
                    m: {"theta": round(float(s["theta"]), 4), "se": round(float(s["se"]), 4), "tag": s["tag"]}
                    for m, s in t.subscores.items()
                },
            },
            "tasks": [
                {
                    "task_id": x.task_id,
                    "kind": x.kind,
                    "q": x.q,
                    "days_ago": x.days_ago,
                    "title": x.title,
                    "skills": list(x.skills),
                    "module": x.module,
                }
                for x in self.tasks
            ],
            "fsp": {"vss_by_discipline": {k: round(v, 2) for k, v in sorted(self.vss_by_discipline.items())},
                    "employer_line": self.fsp_line} if self.has_fsp else None,
            "declared_skills": sorted(self.declared_skills),
            "self_description": self.self_description,
            "experience_months": self.experience_months,
            "salary_min": self.salary_min,
            "salary_net": self.salary_net,
            "formats": sorted(self.formats),
            "city": self.city,
            "relocation": self.relocation,
            "industries": sorted(self.industries),
        }


@dataclass
class Population:
    cfg: PopulationConfig
    candidates: list[Candidate]
    bank: tuple[FamilyMeta, ...] = field(default_factory=real_bank)

    def by_id(self) -> dict[str, Candidate]:
        return {c.id: c for c in self.candidates}

    def features(self, *, which: str = "test", point_estimate: bool = False) -> list[CandidateFeatures]:
        out = []
        for c in self.candidates:
            o = c.test if which == "test" else c.retest
            f = c.features(o, point_estimate=point_estimate) if o is not None else None
            if f is not None:
                out.append(f)
        return out

    def summary(self) -> dict[str, Any]:
        n = len(self.candidates)
        if not n:
            return {"n": 0}

        def share(pred: Callable[[Candidate], bool]) -> float:
            return sum(1 for c in self.candidates if pred(c)) / n

        tested = [c for c in self.candidates if c.test is not None]
        return {
            "n": n,
            "seed": self.cfg.seed,
            "true_spec": _shares([c.truth.spec for c in self.candidates]),
            "true_grade": _shares([c.truth.grade for c in self.candidates]),
            "mismatched_share": share(lambda c: c.mismatched),
            "fsp_share": share(lambda c: c.has_fsp),
            "tasks_share": share(lambda c: bool(c.tasks)),
            "confirmed_share": (
                sum(1 for c in tested if c.test and c.test.final_grade) / len(tested) if tested else None
            ),
            "fake_skills_mean": sum(len(c.fake_skills) for c in self.candidates) / n,
            "true_skills_mean": sum(len(c.truth.skills) for c in self.candidates) / n,
        }


def _shares(xs: Sequence[str]) -> dict[str, float]:
    out: dict[str, float] = {}
    for x in xs:
        out[x] = out.get(x, 0) + 1
    return {k: v / len(xs) for k, v in sorted(out.items())}


# --------------------------------------------------------------------------- генерация


def _choice(rng: random.Random, weighted: Sequence[tuple[Any, float]]) -> Any:
    total = sum(w for _, w in weighted)
    u = rng.random() * total
    acc = 0.0
    for x, w in weighted:
        acc += w
        if u < acc:
            return x
    return weighted[-1][0]


def _mix_moments(cfg: PopulationConfig) -> tuple[float, float]:
    """Среднее и SD истинного уровня для смеси грейдов (уровень равномерен в полосе) — без выборки,
    чтобы кандидат i не зависел от размера популяции."""
    m1 = m2 = 0.0
    for g, w in cfg.grade_mix:
        lo, hi = grade_band(g)
        mu = (lo + hi) / 2
        var = (hi - lo) ** 2 / 12
        m1 += w * mu
        m2 += w * (var + mu * mu)
    return m1, math.sqrt(max(m2 - m1 * m1, 1e-9))


def _true_skills(rng: random.Random, spec: str, grade: str) -> frozenset[str]:
    gi = grade_index(grade)
    factor = 0.55 + 0.15 * (gi + 1)  # Ниже Стажёра 0,55 … Senior 1,15
    if spec == "fullstack":
        pool: dict[str, float] = {}
        for s in ("backend_python", "frontend_js"):
            for sid, p in SPEC_SKILLS[s]:
                pool[sid] = max(pool.get(sid, 0.0), p * FULLSTACK_FACTOR)
        for sid in FULLSTACK_CORE:
            pool[sid] = 1.0
        items = sorted(pool.items())
    else:
        items = list(SPEC_SKILLS[spec])
    out = set()
    for sid, p in items:
        prob = 1.0 if p >= 0.95 else min(0.98, p * factor)
        if rng.random() < prob:
            out.add(sid)
    return frozenset(out)


def _fake_skills(rng: random.Random, spec: str, true: frozenset[str], share: float) -> frozenset[str]:
    k = max(1, round(share * len(true)))
    own = [sid for s in (("backend_python", "frontend_js") if spec == "fullstack" else (spec,))
           for sid, _ in SPEC_SKILLS[s]]
    cat = get_catalog()
    pool = [s for s in dict.fromkeys(list(POPULAR_FAKE) + own) if s in cat.skills and s not in true]
    rng.shuffle(pool)
    return frozenset(pool[:k])


def _self_description(rng: random.Random, spec: str, months: int, skills: Sequence[str]) -> str:
    cat = get_catalog()
    names = [cat.name(s) for s in skills]
    rng.shuffle(names)
    years = months / 12
    if years < 1:
        exp = f"опыт {months} мес."
    else:
        y = round(years)
        exp = f"опыт около {y} {'года' if y % 10 == 1 and y % 100 != 11 else 'лет'}"  # родительный падеж
    tail = rng.choice(
        (
            "Ищу команду с сильным код-ревью.",
            "Интересуют продуктовые задачи.",
            "Готов к тестовому заданию.",
            "Хочу расти в архитектуре.",
            "",
        )
    )
    return f"{ROLE_TEXT.get(spec, 'Разработчик')}, {exp}. Стек: {', '.join(names)}. {tail}".strip()


def _tasks(rng: random.Random, cid: str, spec: str, claimed_grade: str, truth: Truth, cfg: PopulationConfig) -> tuple[TaskResult, ...]:
    if rng.random() >= cfg.task_rate:
        return ()
    scale = load_scale()
    bp = load_blueprint(spec)
    cat = get_catalog()
    out = []
    for k in range(rng.randint(1, 3)):
        module = _choice(rng, list(bp.items()))
        b = scale.cut(claimed_grade) + rng.gauss(0.0, 0.5)
        th = truth.module_theta.get(module, truth.theta)
        q = 1.0 / (1.0 + math.exp(-cfg.task_slope * (th - b))) + rng.gauss(0.0, cfg.task_noise)
        q = round(min(1.0, max(0.0, q)) * 5) / 5
        skills = sorted(s for s in truth.skills if module in cat.modules_of(s))
        tagged = tuple(rng.sample(skills, min(len(skills), rng.randint(1, 2)))) if skills else ()
        out.append(
            TaskResult(
                task_id=f"t-{cid}-{k + 1}",
                kind="A" if rng.random() < 0.8 else "B",
                q=q,
                days_ago=float(rng.randint(0, 180)),
                title=rng.choice(TASK_TITLES[module]),
                skills=tagged,
                module=module,
            )
        )
    return tuple(out)


def _phi(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _fsp(rng: random.Random, cid: str, idx: int, spec: str, z: float) -> FSPProfile:
    """Результаты ФСП из скрытой «силы» z: выше z — выше уровень соревнований и лучше места."""
    fcfg = get_fsp_config()
    n_res = 1 + int(z > 0.3) + int(z > 1.2) + int(rng.random() < 0.3)
    results = []
    for k in range(n_res):
        short = _choice(rng, FSP_DISCIPLINES[spec])
        disc = fcfg.discipline_by_short(short)
        ls = z + rng.gauss(0.0, 0.7)
        if ls > 1.4:
            level, status, stage, field_size = Level.all_russian, StatusType.championship, Stage.final, rng.randint(30, 80)
        elif ls > 0.7:
            stage = rng.choice((Stage.qualifier, Stage.final))
            level, status = Level.all_russian, StatusType.cup
            field_size = rng.randint(60, 200) if stage == Stage.qualifier else rng.randint(30, 70)
        elif ls > 0.0:
            level, status, stage, field_size = Level.interregional, StatusType.other_sport, Stage.single, rng.randint(20, 60)
        elif ls > -0.8:
            level, status, stage, field_size = Level.regional, StatusType.other_sport, Stage.single, rng.randint(10, 40)
        else:
            level, status, stage, field_size = Level.municipal, StatusType.student, Stage.single, rng.randint(6, 25)
        pct = _phi(z + rng.gauss(0.0, 0.6))
        place = max(1, min(field_size, 1 + int((1.0 - pct) * field_size)))
        team = short != "algo"
        role = (
            ResultRole.personal
            if not team
            else _choice(rng, ((ResultRole.captain, 0.3), (ResultRole.participant, 0.6), (ResultRole.reserve, 0.1)))
        )
        end = REF_DATE - timedelta(days=rng.randint(20, 900))
        results.append(
            NormalizedResult(
                result_id=f"r-{cid}-{k + 1}",
                ekp=f"2026990{idx:05d}{k + 1:04d}",
                group_key=f"g-{cid}-{k + 1}",
                title=f"Соревнование по программированию ({disc.label if disc else short})",
                discipline=short,
                discipline_code=disc.code if disc else short,
                level=level,
                status_type=status,
                stage=stage,
                age_group=AgeGroup.adults,
                format=CompetitionFormat.team if team else CompetitionFormat.personal,
                start_date=end,
                end_date=end,
                field_size=field_size,
                place=place,
                place_to=None,
                score=None,
                max_score=None,
                role=role,
                team_function=TEAM_FUNCTION.get(spec) if team else None,
                source=Source.fsp_mock,
                confirmed=True,
            )
        )
    return FSPProfile(
        sub=f"fsp-{cid}",
        state=FSPState.linked,
        results=results,
        source=Source.fsp_mock,
        fetched_at=datetime(REF_DATE.year, REF_DATE.month, REF_DATE.day, tzinfo=UTC),
    )


def generate_candidate(i: int, cfg: PopulationConfig, *, bank: Sequence[FamilyMeta] | None = None) -> Candidate:
    """Кандидат номер i (детерминированно по cfg.seed и i)."""
    rng = random.Random(f"{cfg.seed}:cand:{i}")
    scale = load_scale()
    cid = f"c{i + 1:04d}"
    spec = _choice(rng, cfg.spec_mix)
    grade = _choice(rng, cfg.grade_mix)
    lo, hi = grade_band(grade)
    theta = rng.uniform(lo, hi)
    own_specs = ("backend_python", "frontend_js") if spec == "fullstack" else (spec,)
    own_modules = {m for s in own_specs for m in load_blueprint(s)}
    all_modules = {m for s in ("backend_python", "frontend_js") for m in load_blueprint(s)}
    module_theta = {
        m: theta + rng.gauss(0.0, cfg.module_sd_own)
        if m in own_modules
        else theta + cfg.module_offset_other + rng.gauss(0.0, cfg.module_sd_other)
        for m in sorted(all_modules)
    }
    skills = _true_skills(rng, spec, grade)
    # Заявленная специализация: Fullstack проходит один из своих тестов; у 10% — чужая специализация.
    mismatched = rng.random() < cfg.mismatch_rate
    if spec == "fullstack":
        claimed_spec = rng.choice(("backend_python", "frontend_js"))
        mismatched = False
    elif mismatched:
        claimed_spec = "frontend_js" if spec == "backend_python" else "backend_python"
    else:
        claimed_spec = spec
    claimed_grade = sample_claimed_grade(grade if grade in scale.grades else scale.grades[0], rng, scale)
    months = max(1, round(EXPERIENCE_MEDIAN_MONTHS[grade] * math.exp(rng.gauss(0.0, cfg.experience_sigma_log))))
    fake = _fake_skills(rng, spec, skills, cfg.fake_skill_share)
    declared = frozenset(skills | fake)
    descr = _self_description(rng, claimed_spec, months, sorted(declared))
    centers = load_config("scoring")["brief_parser"]["grade"]["salary_centers_net"]
    salary = float(centers[claimed_grade]) * math.exp(rng.gauss(0.0, cfg.salary_sigma_log))
    salary_min = int(round(salary / cfg.salary_round) * cfg.salary_round)
    formats = frozenset(_choice(rng, FORMAT_PRESETS))
    cat = get_catalog()
    cities = list(cat.cities)
    u = rng.random()
    city = "moscow" if u < 0.45 else "spb" if u < 0.65 else rng.choice(cities[2:22])
    relocation = rng.random() < 0.2
    industries = frozenset(rng.sample(INDUSTRIES, rng.randint(1, 3)))
    # ФСП: скрытая сила коррелирует с уровнем; VSS считает прод-модуль.
    strength = None
    vss: dict[str, float] = {}
    fsp_line = None
    fsp_public: tuple[str, ...] = ()
    if rng.random() < cfg.fsp_rate:
        mu, sd = _mix_moments(cfg)
        z_theta = (theta - mu) / sd
        strength = cfg.fsp_latent_rho * z_theta + math.sqrt(1 - cfg.fsp_latent_rho**2) * rng.gauss(0.0, 1.0)
        profile = _fsp(rng, cid, i, spec, strength)
        feats = build_features(profile, REF_DATE)
        vss = dict(feats.vss_by_discipline)
        fsp_line = feats.employer_line
        fsp_public = tuple(p.text for p in feats.fsp_public)
    truth = Truth(spec, grade, theta, module_theta, skills, strength)
    tasks = _tasks(rng, cid, claimed_spec, claimed_grade, truth, cfg)
    test_days_ago = float(rng.randint(0, cfg.max_test_age_days))
    cand = Candidate(
        id=cid,
        index=i,
        truth=truth,
        claimed_spec=claimed_spec,
        claimed_grade=claimed_grade,
        mismatched=mismatched,
        experience_months=months,
        declared_skills=declared,
        fake_skills=fake,
        self_description=descr,
        salary_min=salary_min,
        salary_net=True,
        formats=formats,
        city=city,
        relocation=relocation,
        industries=industries,
        tasks=tasks,
        vss_by_discipline=vss,
        fsp_line=fsp_line,
        fsp_public=fsp_public,
        test_days_ago=test_days_ago,
    )
    if cfg.run_tests:
        cand.test = simulate_test(
            theta=theta,
            module_theta=module_theta,
            spec=claimed_spec,
            claimed_grade=claimed_grade,
            rng=random.Random(f"{cfg.seed}:cat:{cid}"),
            bank=bank,
            session_id=f"sess-{cid}",
        )
    return cand


def generate_population(
    cfg: PopulationConfig | None = None,
    *,
    bank: Sequence[FamilyMeta] | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> Population:
    """Сгенерировать популяцию (детерминированно по cfg.seed)."""
    cfg = cfg or PopulationConfig()
    b = tuple(bank) if bank is not None else real_bank()
    cands = []
    for i in range(cfg.n):
        cands.append(generate_candidate(i, cfg, bank=b))
        if progress is not None and (i + 1) % 200 == 0:
            progress(i + 1, cfg.n)
    return Population(cfg, cands, b)


def retest_of(c: Candidate, seed: int, bank: Sequence[FamilyMeta] | None = None) -> TestOutcome | None:
    """Пересдача кандидата: тот же истинный уровень, все семейства первой сессии исключены."""
    if c.test is None:
        return None
    return simulate_test(
        theta=c.truth.theta,
        module_theta=c.truth.module_theta,
        spec=c.claimed_spec,
        claimed_grade=c.claimed_grade,
        rng=random.Random(f"{seed}:retest:{c.id}"),
        bank=bank,
        seen_families=c.test.families,
        session_id=f"retest-{c.id}",
    )


def run_retests(pop: Population, *, only: Callable[[Candidate], bool] | None = None) -> None:
    """S3: пересдача на непересекающихся семействах (семейства первой сессии исключены). Пишет cand.retest."""
    for c in pop.candidates:
        if c.test is None or (only is not None and not only(c)):
            continue
        c.retest = retest_of(c, pop.cfg.seed, pop.bank)


def generate_chunk(cfg: PopulationConfig, indices: Sequence[int], with_retest: bool = False) -> list[Candidate]:
    """Кандидаты с номерами indices (для параллельной генерации: результат не зависит от разбиения)."""
    bank = real_bank()
    out = []
    for i in indices:
        c = generate_candidate(i, cfg, bank=bank)
        if with_retest:
            c.retest = retest_of(c, cfg.seed, bank)
        out.append(c)
    return out


def vss_truth_correlation(pop: Population) -> dict[str, Any]:
    """Фактическая корреляция VSS (лучшая дисциплина) с истинным уровнем у кандидатов с ФСП."""
    from eval import metrics as M

    rows = [(max(c.vss_by_discipline.values()), c.truth.theta) for c in pop.candidates if c.has_fsp]
    if len(rows) < 3:
        return {"n": len(rows), "pearson": None, "spearman": None}
    v, t = [r[0] for r in rows], [r[1] for r in rows]
    return {"n": len(rows), "pearson": M.pearson(v, t), "spearman": M.spearman(v, t)}
