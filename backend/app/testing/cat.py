"""Адаптивный тест (CAT): выбор задания, pretest-слоты, стоп, решение, доборы, подбаллы модулей.

Источник: _internal/design/spec-testing.md §3.4 (выбор), §3.5 (стоп и решение), §3.6 (подбаллы),
01-concept.md §5.2–5.3. Движок чистый: без БД и HTTP. Он работает с абстрактными метаданными
семейств (FamilyMeta) и не зависит от наличия реального банка — адаптер meta_from_family()
превращает семейство из app.testing.bank в FamilyMeta по контракту bank/base.py.

Экземпляр-клон выбирает вызывающий код (API: следующий невыданный, FOR UPDATE SKIP LOCKED);
движок решает только «какое семейство и почему».
"""

from __future__ import annotations

import dataclasses
import random
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from app.core.configs import load_config
from app.testing.irt import ItemParams, Posterior, Scale, b_from_expert, eap, info_at, load_scale

# Статусы семейства (§2.4)
DRAFT = "draft"
REVIEWED = "reviewed"
PRETEST = "pretest"
OPERATIONAL = "operational"
QUARANTINE = "quarantine"
RETIRED = "retired"

# Решения (§3.5)
PASSED = "PASSED"
FAILED = "FAILED"
BORDERLINE_PASS = "BORDERLINE_PASS"
BORDERLINE_FAIL = "BORDERLINE_FAIL"

# Виды сессий (§4 TestSession.kind)
KIND_INITIAL = "initial"
KIND_RETAKE = "retake"
KIND_UPGRADE = "upgrade"
KIND_EXT_UP = "ext_up"
KIND_EXT_DOWN = "ext_down"
KIND_SIM = "sim"
KIND_PREVIEW = "preview"
PRIMARY_KINDS = frozenset({KIND_INITIAL, KIND_RETAKE, KIND_SIM, KIND_PREVIEW})
EXTENSION_KINDS = frozenset({KIND_EXT_UP, KIND_EXT_DOWN})

# Теги модулей (§3.6)
TAG_STRONG = "strong"
TAG_CONFIRMED = "confirmed"
TAG_INSUFFICIENT = "insufficient"
TAG_NONE = "none"
TAG_SYMBOLS = {TAG_STRONG: "✓✓", TAG_CONFIRMED: "✓", TAG_INSUFFICIENT: "~", TAG_NONE: ""}
TAG_LABELS_RU = {
    TAG_STRONG: "сильный",
    TAG_CONFIRMED: "подтверждён",
    TAG_INSUFFICIENT: "мало данных",
    TAG_NONE: "не подтверждён",
}
LEVEL_LABELS_RU = {"below": "ниже грейда", "at": "на уровне грейда", "above": "выше грейда"}


# ---------------------------------------------------------------------------
# Конфигурация
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatConfig:
    n_min: int = 8
    n_max: int = 20
    p_stop_high: float = 0.90
    p_stop_low: float = 0.10
    coverage_min_quota: float = 0.15
    topk_early: int = 5
    topk_early_items: int = 4
    topk_late: int = 3
    exposure_cap: float = 0.45
    exposure_cap_large_bank: float = 0.25
    large_bank_size: int = 90
    min_eligible_before_relax: int = 3
    family_cooldown_days: int = 365
    borderline_pass_min: float = 0.5
    pretest_count: int = 2
    pretest_pos_min: int = 3
    pretest_pos_max: int = 14
    pretest_max_b_distance: float = 1.0
    default_time_limit_s: int = 90
    timer_grace_s: int = 5
    mcq_max_share: float = 0.30
    ext_max_items: int = 8
    offer_up_min_p: float = 0.30
    confirm_lower_p: float = 0.90
    ext_pass_p: float = 0.90
    ext_window_hours: int = 24
    subscore_prior_sd: float = 0.6
    subscore_min_items: int = 2
    tag_p: float = 0.8
    confirmed_floor_grade: str = "junior"
    # Контроль экспозиции Симпсона–Хеттера (config/testing.yaml → quality.sympson_hetter). Выключен по умолчанию.
    sh_enabled: bool = False
    sh_k: Mapping[str, float] = field(default_factory=dict)

    @classmethod
    def from_config(cls, testing: dict[str, Any] | None = None) -> CatConfig:
        t = testing if testing is not None else load_config("testing")
        c, e, s = t["cat"], t["extension"], t["subscores"]
        pt = c["pretest"]
        sh = (t.get("quality") or {}).get("sympson_hetter") or {}
        return cls(
            n_min=int(c["n_min"]),
            n_max=int(c["n_max"]),
            p_stop_high=float(c["p_stop_high"]),
            p_stop_low=float(c["p_stop_low"]),
            coverage_min_quota=float(c["coverage_min_quota"]),
            topk_early=int(c["topk_early"]),
            topk_early_items=int(c["topk_early_items"]),
            topk_late=int(c["topk_late"]),
            exposure_cap=float(c["exposure_cap"]),
            exposure_cap_large_bank=float(c["exposure_cap_large_bank"]),
            large_bank_size=int(c["large_bank_size"]),
            min_eligible_before_relax=int(c["min_eligible_before_relax"]),
            family_cooldown_days=int(c["family_cooldown_days"]),
            borderline_pass_min=float(c["borderline_pass_min"]),
            pretest_count=int(pt["count"]),
            pretest_pos_min=int(pt["pos_min"]),
            pretest_pos_max=int(pt["pos_max"]),
            pretest_max_b_distance=float(pt["max_b_distance"]),
            default_time_limit_s=int(c.get("default_time_limit_s", 90)),
            timer_grace_s=int(c.get("timer_grace_s", 5)),
            mcq_max_share=float(c.get("mcq_max_share", 0.30)),
            ext_max_items=int(e["max_items"]),
            offer_up_min_p=float(e["offer_up_min_p"]),
            confirm_lower_p=float(e["confirm_lower_p"]),
            ext_pass_p=float(e["pass_p"]),
            ext_window_hours=int(e["window_hours"]),
            subscore_prior_sd=float(s["prior_sd"]),
            subscore_min_items=int(s["min_items"]),
            tag_p=float(s["tag_p"]),
            confirmed_floor_grade=str(s.get("confirmed_floor_grade", "junior")),
            sh_enabled=bool(sh.get("enabled", False)),
            sh_k={str(k): float(v) for k, v in (sh.get("k") or {}).items()},
        )


@lru_cache(maxsize=1)
def load_cat_config() -> CatConfig:
    return CatConfig.from_config()


@dataclass(frozen=True)
class ExpressConfig:
    """Экспресс-тест жюри (config/testing.yaml → express): короче основной части, остальное — тот же движок."""

    time_budget_s: int
    time_budget_min_items: int
    item_time_limit_s: int


def express_cat_config(testing: dict[str, Any] | None = None) -> CatConfig:
    """Параметры CAT для экспресса: те же пороги уверенности, меньше заданий, без pretest и без контроля покрытия."""
    t = testing if testing is not None else load_config("testing")
    e = t["express"]
    return dataclasses.replace(
        CatConfig.from_config(t),
        n_min=int(e["n_min"]),
        n_max=int(e["n_max"]),
        pretest_count=int(e["pretest_count"]),
        coverage_min_quota=float(e["coverage_min_quota"]),
    )


def load_express_config(testing: dict[str, Any] | None = None) -> ExpressConfig:
    t = testing if testing is not None else load_config("testing")
    e = t["express"]
    return ExpressConfig(
        time_budget_s=int(e["time_budget_s"]),
        time_budget_min_items=int(e["time_budget_min_items"]),
        item_time_limit_s=int(e["item_time_limit_s"]),
    )


def load_blueprint(spec: str, taxonomy: dict[str, Any] | None = None) -> dict[str, float]:
    """Квоты модулей стандартного теста специализации (config/taxonomy.yaml)."""
    tax = taxonomy if taxonomy is not None else load_config("taxonomy")
    for s in tax["specializations"]:
        if s["code"] == spec:
            if not s.get("blueprint"):
                raise ValueError(f"Для специализации «{s['name']}» тест пока недоступен")
            return {m: float(q) for m, q in s["blueprint"].items()}
    raise ValueError(f"Неизвестная специализация: {spec}")


def module_names(taxonomy: dict[str, Any] | None = None) -> dict[str, str]:
    tax = taxonomy if taxonomy is not None else load_config("taxonomy")
    return {code: m.get("name", code) for code, m in tax.get("modules", {}).items()}


# ---------------------------------------------------------------------------
# Метаданные семейства (абстрактный источник заданий)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScaleParam:
    """Параметры семейства на шкале одной специализации (FamilyParams)."""

    b: float
    sigma_b: float


@dataclass(frozen=True, eq=False)
class FamilyMeta:
    """Всё, что движку нужно знать о семействе. Ключи и шаблоны сюда не попадают."""

    id: str
    module: str
    specs: tuple[str, ...]
    target_grade: str
    position: float = 0.0
    c: float = 0.0
    enemies: tuple[str, ...] = ()
    is_anchor: bool = False
    status: str = OPERATIONAL
    params: Mapping[str, ScaleParam] = field(default_factory=dict)  # spec → (b, σ_b); пусто → экспертный приор
    exposure_30d: float = 0.0
    free_instances: int | None = None  # None — неизвестно (считаем, что есть); 0 — пул исчерпан
    format: str | None = None
    time_limit_s: int | None = None
    title: str | None = None

    def scale_param(self, spec: str, scale: Scale, sigma_b_default: float | None = None) -> ScaleParam:
        if spec in self.params:
            return self.params[spec]
        sb = scale.sigma_b_agree if sigma_b_default is None else sigma_b_default
        return ScaleParam(b=b_from_expert(self.target_grade, self.position, scale), sigma_b=sb)

    def item_params(self, spec: str, scale: Scale) -> ItemParams:
        p = self.scale_param(spec, scale)
        return ItemParams.from_family(p.b, p.sigma_b, self.c, scale)


def meta_from_family(
    fam: Any,
    *,
    params: Mapping[str, ScaleParam] | None = None,
    status: str = OPERATIONAL,
    exposure_30d: float = 0.0,
    free_instances: int | None = None,
) -> FamilyMeta:
    """Адаптер: семейство банка (контракт app/testing/bank/base.py) → FamilyMeta (утиная типизация)."""
    return FamilyMeta(
        id=fam.id,
        module=fam.module,
        specs=tuple(fam.specs),
        target_grade=fam.target_grade,
        position=float(getattr(fam, "position", 0.0)),
        c=float(getattr(fam, "c", 0.0)),
        enemies=tuple(getattr(fam, "enemies", ())),
        is_anchor=bool(getattr(fam, "is_anchor", False)),
        status=status,
        params=dict(params or {}),
        exposure_30d=exposure_30d,
        free_instances=free_instances,
        format=getattr(fam, "format", None),
        time_limit_s=getattr(fam, "time_limit_s", None),
        title=getattr(fam, "title", None),
    )


# ---------------------------------------------------------------------------
# Состояние сессии
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Selection:
    seq: int  # позиция в цепочке сессий (1-based)
    family_id: str
    module: str
    family_grade: str
    is_pretest: bool
    item: ItemParams
    time_limit_s: int
    reason: dict[str, Any]
    unavailable_modules: frozenset[str] = frozenset()
    journal: tuple[dict[str, Any], ...] = ()


@dataclass
class ResponseRecord:
    seq: int
    family_id: str
    module: str
    family_grade: str
    is_pretest: bool
    correct: bool
    b: float
    c: float
    a: float
    phase: int = 0
    target_grade: str = ""
    theta_after: float | None = None
    se_after: float | None = None
    p_after: float | None = None
    rt_ms: int | None = None
    timed_out: bool = False
    instance_id: str | None = None
    selection_reason: dict[str, Any] = field(default_factory=dict)

    @property
    def item(self) -> ItemParams:
        return ItemParams(b=self.b, c=self.c, a=self.a)


@dataclass
class CatState:
    spec: str
    claimed_grade: str
    target_grade: str
    blueprint: dict[str, float]
    pretest_positions: tuple[int, ...] = ()
    kind: str = KIND_INITIAL
    phase: int = 0
    phase_start_scored: int = 0
    responses: list[ResponseRecord] = field(default_factory=list)
    log: list[dict[str, Any]] = field(default_factory=list)
    unavailable_modules: set[str] = field(default_factory=set)

    # --- производные величины ---
    @property
    def next_seq(self) -> int:
        return len(self.responses) + 1

    @property
    def scored(self) -> list[ResponseRecord]:
        """Зачётные ответы: pretest в оценку не входит (§3.3)."""
        return [r for r in self.responses if not r.is_pretest]

    @property
    def n_scored(self) -> int:
        return sum(1 for r in self.responses if not r.is_pretest)

    @property
    def n_phase_scored(self) -> int:
        return self.n_scored - self.phase_start_scored

    @property
    def n_pretest(self) -> int:
        return sum(1 for r in self.responses if r.is_pretest)

    @property
    def seen_family_ids(self) -> set[str]:
        return {r.family_id for r in self.responses}

    def module_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.responses:
            if not r.is_pretest:
                out[r.module] = out.get(r.module, 0) + 1
        return out

    def posterior(self, scale: Scale | None = None) -> Posterior:
        """Апостериор по всем зачётным ответам цепочки: доборы продолжают ТОТ ЖЕ апостериор (§3.3)."""
        return eap(((r.item, r.correct) for r in self.scored), scale or load_scale())

    def p_pass(self, scale: Scale | None = None) -> float:
        scale = scale or load_scale()
        return self.posterior(scale).p_at_least(scale.cut(self.target_grade))

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["pretest_positions"] = list(self.pretest_positions)
        d["unavailable_modules"] = sorted(self.unavailable_modules)
        return d

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> CatState:
        d = dict(d)
        d["responses"] = [ResponseRecord(**r) for r in d.get("responses", [])]
        d["pretest_positions"] = tuple(d.get("pretest_positions", ()))
        d["unavailable_modules"] = set(d.get("unavailable_modules", ()))
        return cls(**d)


@dataclass(frozen=True)
class Decision:
    code: str
    target_grade: str
    p_pass: float
    theta: float
    se: float
    n_items: int  # зачётных заданий в этой фазе (основная часть или добор)
    n_total: int  # зачётных заданий во всей цепочке
    reason: str  # confidence | max_items | bank_exhausted

    @property
    def passed(self) -> bool:
        return self.code in (PASSED, BORDERLINE_PASS)

    @property
    def borderline(self) -> bool:
        return self.code in (BORDERLINE_PASS, BORDERLINE_FAIL)

    def to_dict(self) -> dict[str, Any]:
        d = dataclasses.asdict(self)
        d["passed"] = self.passed
        d["borderline"] = self.borderline
        return d


@dataclass(frozen=True)
class Offer:
    direction: str  # up | down
    target_grade: str
    p_already: float  # P(θ ≥ cut(target)) по уже данным ответам
    instant: bool  # вниз и P ≥ 0.90 → подтверждение одним нажатием, 0 заданий
    max_items: int
    text_ru: str

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def new_state(
    spec: str,
    claimed_grade: str,
    rng: random.Random,
    *,
    kind: str = KIND_INITIAL,
    target_grade: str | None = None,
    blueprint: Mapping[str, float] | None = None,
    scale: Scale | None = None,
    cfg: CatConfig | None = None,
) -> CatState:
    """Новая сессия. Заявленный грейд задаёт ТОЛЬКО целевой порог и первое задание — приор общий (§3.3).

    Позиции pretest-слотов (3–14) назначаются случайно при создании (§3.4 п.6).
    """
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    scale.cut(claimed_grade)  # валидация грейда
    target = target_grade or claimed_grade
    bp = dict(blueprint) if blueprint is not None else load_blueprint(spec)
    positions = range(cfg.pretest_pos_min, cfg.pretest_pos_max + 1)
    k = min(cfg.pretest_count, len(positions))
    pretest = tuple(sorted(rng.sample(list(positions), k))) if kind in PRIMARY_KINDS or kind == KIND_UPGRADE else ()
    return CatState(
        spec=spec, claimed_grade=claimed_grade, target_grade=target, blueprint=bp, pretest_positions=pretest, kind=kind
    )


# ---------------------------------------------------------------------------
# Выбор следующего задания (§3.4)
# ---------------------------------------------------------------------------


def _enemy_block(seen: set[str], families: Sequence[FamilyMeta]) -> set[str]:
    """Пары-враги взаимоисключаются (в обе стороны)."""
    by_id = {f.id: f for f in families}
    blocked: set[str] = set()
    for s in seen:
        if s in by_id:
            blocked.update(by_id[s].enemies)
    for f in families:
        if seen.intersection(f.enemies):
            blocked.add(f.id)
    return blocked


def _recently_seen(
    seen_history: Mapping[str, datetime] | None, now: datetime | None, cooldown_days: int
) -> set[str]:
    """Семейства, которые кандидат видел за последние 12 месяцев (в прошлых сессиях)."""
    if not seen_history:
        return set()
    if now is None:
        return set(seen_history)
    horizon = timedelta(days=cooldown_days)
    return {fid for fid, ts in seen_history.items() if now - ts < horizon}


def select_next(
    state: CatState,
    families: Sequence[FamilyMeta],
    rng: random.Random,
    *,
    now: datetime | None = None,
    seen_history: Mapping[str, datetime] | None = None,
    scale: Scale | None = None,
    cfg: CatConfig | None = None,
) -> Selection | None:
    """Выбрать следующее семейство. None — допустимых семейств не осталось (банк исчерпан)."""
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    names = module_names()
    spec = state.spec
    t = scale.cut(state.target_grade)
    seq = state.next_seq
    seen = state.seen_family_ids
    blocked = _enemy_block(seen, families)
    recent = _recently_seen(seen_history, now, cfg.family_cooldown_days)
    base = [
        f
        for f in families
        if spec in f.specs
        and f.id not in seen
        and f.id not in blocked
        and f.id not in recent
        and f.free_instances != 0
    ]
    journal: list[dict[str, Any]] = []

    # Pretest-слот: случайное семейство из pretest-пула с |b_prior − t| ≤ 1; в оценку не входит (§3.4 п.6)
    if state.phase == 0 and seq in state.pretest_positions:
        pool = sorted(
            (
                f
                for f in base
                if f.status == PRETEST and abs(f.scale_param(spec, scale).b - t) <= cfg.pretest_max_b_distance
            ),
            key=lambda f: f.id,
        )
        if pool:
            f = pool[rng.randrange(len(pool))]
            reason = {
                "kind": "pretest",
                "seq": seq,
                "target_grade": state.target_grade,
                "target_cut": t,
                "module": f.module,
                "chosen": f.id,
                "pool_size": len(pool),
                "text_ru": (
                    f"Пробное задание на позиции {seq}: не влияет на результат, "
                    "нужно для калибровки новых семейств. Выбрано случайно из подходящих по уровню."
                ),
            }
            return Selection(
                seq=seq,
                family_id=f.id,
                module=f.module,
                family_grade=f.target_grade,
                is_pretest=True,
                item=f.item_params(spec, scale),
                time_limit_s=f.time_limit_s or cfg.default_time_limit_s,
                reason=reason,
                unavailable_modules=frozenset(state.unavailable_modules),
            )
        journal.append(
            {"seq": seq, "event": "pretest_slot_skipped", "text_ru": "Нет подходящих пробных заданий — слот занят зачётным"}
        )

    ops = [f for f in base if f.status == OPERATIONAL and f.module in state.blueprint]
    if not ops:
        return None
    by_module: dict[str, list[FamilyMeta]] = {}
    for f in ops:
        by_module.setdefault(f.module, []).append(f)

    # Модуль с наибольшим дефицитом квоты: k* = argmax (q_k·(n+1) − n_k) (§3.4 п.3)
    n = state.n_scored
    counts = state.module_counts()
    order = list(state.blueprint)
    deficits = {m: q * (n + 1) - counts.get(m, 0) for m, q in state.blueprint.items()}
    available = [m for m in order if by_module.get(m)]
    unavailable = frozenset(m for m in order if not by_module.get(m))
    k_star = max(available, key=lambda m: (round(deficits[m], 9), state.blueprint[m], -order.index(m)))

    # Экспозиция за 30 дней < r_max; если допустимых < 3 — лимит снимается с записью в журнал (§3.4 п.2)
    bank_size = sum(1 for f in families if spec in f.specs and f.status == OPERATIONAL)
    cap = cfg.exposure_cap_large_bank if bank_size >= cfg.large_bank_size else cfg.exposure_cap
    cands = by_module[k_star]
    within = [f for f in cands if f.exposure_30d < cap]
    relaxed = False
    if len(within) < cfg.min_eligible_before_relax and len(cands) > len(within):
        within = cands
        relaxed = True
        journal.append(
            {
                "seq": seq,
                "event": "exposure_cap_relaxed",
                "module": k_star,
                "text_ru": f"В модуле «{names.get(k_star, k_star)}» меньше {cfg.min_eligible_before_relax} "
                "допустимых семейств — лимит экспозиции снят",
            }
        )

    # Доля заданий с угадыванием (c > 0) ≤ 30% (§2.5): угадывание снижает информацию. Держим долю
    # в любой момент теста — длина заранее неизвестна. Мягко: если в модуле нет заданий без
    # угадывания, правило не блокирует выбор.
    n_guess = sum(1 for r in state.scored if r.c > 0)
    mcq_capped = False
    if n_guess + 1 > cfg.mcq_max_share * (n + 1) + 1e-9:
        no_guess = [f for f in within if f.c <= 0]
        if no_guess and len(no_guess) < len(within):
            within = no_guess
            mcq_capped = True

    # Информация максимизируется у порога, а не у θ̂: для классификации это оптимально (§3.4 п.1, 4)
    k = cfg.topk_early if n < cfg.topk_early_items else cfg.topk_late
    ranked = sorted(
        ((info_at(t, f.item_params(spec, scale)), f) for f in within), key=lambda p: (-p[0], p[1].id)
    )
    sh_rejected: list[str] = []
    if cfg.sh_enabled:
        # Симпсон–Хеттер (1985): выбранное задание предъявляется с вероятностью K_i, иначе берём следующее по
        # информативности. Ветка живёт только при sh_enabled: по умолчанию генератор не расходуется (docs/TESTING_MECHANISM.md).
        # Отклонённые ранее в этой сессии не возвращаются (классическая схема: один «бросок» на задание и кандидата);
        # список хранится в selection_reason ответов, отдельного состояния не нужно.
        refused = {f for r in state.responses for f in r.selection_reason.get("sh_rejected", ())}
        sh_pool = [p for p in ranked if p[1].id not in refused] or ranked
        while True:
            top_ranked = sh_pool[:k]
            info, chosen = top_ranked[rng.randrange(len(top_ranked))]
            k_i = cfg.sh_k.get(chosen.id, 1.0)
            if k_i >= 1.0 or len(sh_pool) <= 1 or rng.random() < k_i:
                break
            sh_rejected.append(chosen.id)
            sh_pool = [p for p in sh_pool if p[1].id != chosen.id]
    else:
        top_ranked = ranked[:k]
        info, chosen = top_ranked[rng.randrange(len(top_ranked))]
    grade_name = scale.grade_name(state.target_grade)
    mname = names.get(k_star, k_star)
    text = (
        f"Цель — порог «{grade_name}». Модуль «{mname}» сильнее всего отставал по квоте "
        f"(дефицит {deficits[k_star]:.2f}). Из top-{len(top_ranked)} семейств по информативности у порога "
        f"выбрано случайно: {chosen.id}."
    )
    if relaxed:
        text += " Лимит экспозиции снят: допустимых семейств меньше трёх."
    if mcq_capped:
        text += " Задания с угадыванием ответа пропущены: их доля в тесте не выше 30%."
    reason = {
        "kind": "operational",
        "seq": seq,
        "phase": state.phase,
        "target_grade": state.target_grade,
        "target_cut": t,
        "module": k_star,
        "deficit": round(deficits[k_star], 4),
        "deficits": {m: round(d, 4) for m, d in deficits.items()},
        "k": k,
        "top_k": [
            {"family_id": f.id, "info": round(i, 4), "b": round(f.scale_param(spec, scale).b, 3)} for i, f in top_ranked
        ],
        "chosen": chosen.id,
        "chosen_info": round(info, 4),
        "exposure_cap": cap,
        "exposure_relaxed": relaxed,
        "mcq_capped": mcq_capped,
        "text_ru": text,
    }
    if cfg.sh_enabled:
        reason["sh_rejected"] = sh_rejected
    return Selection(
        seq=seq,
        family_id=chosen.id,
        module=chosen.module,
        family_grade=chosen.target_grade,
        is_pretest=False,
        item=chosen.item_params(spec, scale),
        time_limit_s=chosen.time_limit_s or cfg.default_time_limit_s,
        reason=reason,
        unavailable_modules=unavailable,
        journal=tuple(journal),
    )


def record_answer(
    state: CatState,
    selection: Selection,
    correct: bool,
    *,
    scale: Scale | None = None,
    rt_ms: int | None = None,
    timed_out: bool = False,
    instance_id: str | None = None,
) -> ResponseRecord:
    """Записать ответ на выданное задание. Таймаут = неверно (§2.5)."""
    scale = scale or load_scale()
    if selection.seq != state.next_seq:
        raise ValueError(f"Ответ на позицию {selection.seq}, ожидалась {state.next_seq}")
    rec = ResponseRecord(
        seq=selection.seq,
        family_id=selection.family_id,
        module=selection.module,
        family_grade=selection.family_grade,
        is_pretest=selection.is_pretest,
        correct=bool(correct) and not timed_out,
        b=selection.item.b,
        c=selection.item.c,
        a=selection.item.a,
        phase=state.phase,
        target_grade=state.target_grade,
        rt_ms=rt_ms,
        timed_out=timed_out,
        instance_id=instance_id,
        selection_reason=dict(selection.reason),
    )
    state.responses.append(rec)
    state.unavailable_modules = set(selection.unavailable_modules)
    state.log.extend(selection.journal)
    post = state.posterior(scale)
    rec.theta_after = round(post.mean, 4)
    rec.se_after = round(post.sd, 4)
    rec.p_after = round(post.p_at_least(scale.cut(state.target_grade)), 4)
    return rec


# ---------------------------------------------------------------------------
# Стоп и решение (§3.5)
# ---------------------------------------------------------------------------


def coverage_ok(state: CatState, cfg: CatConfig | None = None) -> bool:
    """Каждый модуль с квотой ≥ 15% встречен; модули без единого допустимого семейства не блокируют стоп."""
    cfg = cfg or load_cat_config()
    counts = state.module_counts()
    return all(
        counts.get(m, 0) >= 1 or m in state.unavailable_modules
        for m, q in state.blueprint.items()
        if q >= cfg.coverage_min_quota - 1e-9
    )


def stop_check(state: CatState, scale: Scale | None = None, cfg: CatConfig | None = None) -> str | None:
    """Причина остановки (confidence | max_items) или None, если тест продолжается."""
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    p = state.p_pass(scale)
    confident = p >= cfg.p_stop_high or p <= cfg.p_stop_low
    if state.phase == 0:
        n = state.n_scored
        if n >= cfg.n_max:
            return "max_items"
        if n >= cfg.n_min and confident and coverage_ok(state, cfg):
            return "confidence"
        return None
    # Добор: тот же апостериор, ≤ 8 заданий; может завершиться и на 0 заданиях («уже подтверждён»)
    if confident:
        return "confidence"
    if state.n_phase_scored >= cfg.ext_max_items:
        return "max_items"
    return None


def decide(
    state: CatState, reason: str, scale: Scale | None = None, cfg: CatConfig | None = None
) -> Decision:
    """PASSED P ≥ 0.9 | FAILED P ≤ 0.1 | на максимуме: BORDERLINE_PASS 0.5 ≤ P < 0.9, иначе BORDERLINE_FAIL."""
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    post = state.posterior(scale)
    p = post.p_at_least(scale.cut(state.target_grade))
    if p >= cfg.p_stop_high:
        code = PASSED
    elif p <= cfg.p_stop_low:
        code = FAILED
    elif p >= cfg.borderline_pass_min:
        code = BORDERLINE_PASS
    else:
        code = BORDERLINE_FAIL
    return Decision(
        code=code,
        target_grade=state.target_grade,
        p_pass=round(p, 4),
        theta=round(post.mean, 4),
        se=round(post.sd, 4),
        n_items=state.n_phase_scored,
        n_total=state.n_scored,
        reason=reason,
    )


def compute_offers(
    state: CatState, decision: Decision, scale: Scale | None = None, cfg: CatConfig | None = None
) -> tuple[Offer, ...]:
    """Развилка после решения (§3.5, concept §5.3).

    Сдал → добор вверх при P(θ ≥ cut(g+1)) ≥ 0.30 (не больше одной ступени); если уже P ≥ 0.90 —
    подтверждение одним нажатием (повышение и так только при P ≥ 0.90).
    Не сдал → если P(θ ≥ cut(g−1)) ≥ 0.90 — подтверждение одним нажатием; иначе добор вниз ≤ 8 заданий.
    Добор вниз идёт цепочкой до Стажёра; после добора вверх и теста на повышение развилки нет.
    """
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    post = state.posterior(scale)
    g = decision.target_grade
    offers: list[Offer] = []
    if state.kind in PRIMARY_KINDS and decision.passed:
        nxt = scale.next_grade(g)
        if nxt is not None:
            p_up = post.p_at_least(scale.cut(nxt))
            if p_up >= cfg.offer_up_min_p:
                instant = p_up >= cfg.ext_pass_p
                name = scale.grade_name(nxt)
                offers.append(
                    Offer(
                        direction="up",
                        target_grade=nxt,
                        p_already=round(p_up, 4),
                        instant=instant,
                        max_items=0 if instant else cfg.ext_max_items,
                        text_ru=(
                            f"Ваших ответов достаточно, чтобы подтвердить {name} ({round(100 * p_up)}%). Подтвердить?"
                            if instant
                            else f"Попробовать {name} сейчас: добор до {cfg.ext_max_items} "
                            "заданий, прошлые ответы засчитываются"
                        ),
                    )
                )
    elif (state.kind in PRIMARY_KINDS or state.kind == KIND_EXT_DOWN) and not decision.passed:
        # Грейды, по которым уже данных ответов хватает для «ниже порога» (P ≤ 0.10), пропускаем:
        # стоп-правило действует и в доборе, и такой добор закончился бы провалом на 0 заданиях
        # (§3.5 «вниз цепочкой до Стажёра» — сразу на ближайший нерешённый грейд).
        lower = scale.prev_grade(g)
        while lower is not None and post.p_at_least(scale.cut(lower)) <= cfg.p_stop_low:
            lower = scale.prev_grade(lower)
        if lower is not None:
            p_low = post.p_at_least(scale.cut(lower))
            instant = p_low >= cfg.confirm_lower_p
            name = scale.grade_name(lower)
            text = (
                f"Ваших ответов достаточно, чтобы подтвердить {name} ({round(100 * p_low)}%). Подтвердить?"
                if instant
                else f"Добор на {name}: до {cfg.ext_max_items} заданий, прошлые ответы засчитываются"
            )
            offers.append(
                Offer(
                    direction="down",
                    target_grade=lower,
                    p_already=round(p_low, 4),
                    instant=instant,
                    max_items=0 if instant else cfg.ext_max_items,
                    text_ru=text,
                )
            )
    return tuple(offers)


def start_extension(state: CatState, offer: Offer) -> CatState:
    """Добор: дочерняя сессия с ТЕМ ЖЕ апостериором — прошлые ответы засчитываются (concept §5.3)."""
    return CatState(
        spec=state.spec,
        claimed_grade=state.claimed_grade,
        target_grade=offer.target_grade,
        blueprint=dict(state.blueprint),
        pretest_positions=(),
        kind=KIND_EXT_UP if offer.direction == "up" else KIND_EXT_DOWN,
        phase=state.phase + 1,
        phase_start_scored=state.n_scored,
        responses=[dataclasses.replace(r, selection_reason=dict(r.selection_reason)) for r in state.responses],
        log=list(state.log),
        unavailable_modules=set(state.unavailable_modules),
    )


# ---------------------------------------------------------------------------
# Подбаллы модулей и теги стека (§3.6)
# ---------------------------------------------------------------------------


def module_tag(n_k: int, p_floor: float, p_grade: float, cfg: CatConfig | None = None) -> str:
    """✓ при n_k ≥ 2 и P(θ_k ≥ c_Junior) ≥ 0.8; ✓✓ при P(θ_k ≥ c_g) ≥ 0.8; «~» при n_k < 2.

    ✓✓ — усиление ✓, поэтому требует и условия ✓ (для g ≤ Junior ✓✓ совпадает с ✓).
    """
    cfg = cfg or load_cat_config()
    if n_k < cfg.subscore_min_items:
        return TAG_INSUFFICIENT
    if p_floor >= cfg.tag_p:
        return TAG_STRONG if p_grade >= cfg.tag_p else TAG_CONFIRMED
    return TAG_NONE


def module_subscores(
    state: CatState,
    grade: str | None = None,
    scale: Scale | None = None,
    cfg: CatConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """Подбалл модуля — EAP по заданиям модуля с приором N(θ̂, 0.6²): сжатие к общему уровню."""
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    names = module_names()
    grade = grade or state.target_grade
    theta_hat = state.posterior(scale).mean
    c_floor = scale.cut(cfg.confirmed_floor_grade)
    c_grade = scale.cut(grade)
    nxt = scale.next_grade(grade)
    modules = list(state.blueprint) + sorted({r.module for r in state.scored} - set(state.blueprint))
    out: dict[str, dict[str, Any]] = {}
    for m in modules:
        rs = [r for r in state.scored if r.module == m]
        post = eap(((r.item, r.correct) for r in rs), scale, mu=theta_hat, sd=cfg.subscore_prior_sd)
        th = post.mean
        p_floor = post.p_at_least(c_floor)
        p_grade = post.p_at_least(c_grade)
        tag = module_tag(len(rs), p_floor, p_grade, cfg)
        if th < c_grade:
            level = "below"
        elif nxt is not None and th >= scale.cut(nxt):
            level = "above"
        else:
            level = "at"
        out[m] = {
            "module": m,
            "name": names.get(m, m),
            "n": len(rs),
            "correct": sum(1 for r in rs if r.correct),
            "theta": round(th, 4),
            "se": round(post.sd, 4),
            "p_floor": round(p_floor, 4),
            "p_grade": round(p_grade, 4),
            "tag": tag,
            "symbol": TAG_SYMBOLS[tag],
            "tag_ru": TAG_LABELS_RU[tag],
            "level": level if len(rs) >= cfg.subscore_min_items else None,
            "level_ru": LEVEL_LABELS_RU[level] if len(rs) >= cfg.subscore_min_items else "мало данных",
        }
    return out


def level_breakdown(state: CatState, scale: Scale | None = None) -> tuple[dict[str, list[int]], str]:
    """«Решено по уровням»: Junior 3/3, Middle 6/8, Senior 1/2 — по целевым грейдам семейств."""
    scale = scale or load_scale()
    counts: dict[str, list[int]] = {}
    for r in state.scored:
        c = counts.setdefault(r.family_grade, [0, 0])
        c[0] += int(r.correct)
        c[1] += 1
    ordered = {g: counts[g] for g in scale.grades if g in counts}
    text = ", ".join(f"{scale.grade_name(g)} {c}/{n}" for g, (c, n) in ordered.items())
    return ordered, text


def trace(state: CatState) -> list[dict[str, Any]]:
    """Экран «Трасса»: шаги с оценкой после ответа и причиной выбора."""
    return [
        {
            "seq": r.seq,
            "phase": r.phase,
            "family_id": r.family_id,
            "module": r.module,
            "family_grade": r.family_grade,
            "is_pretest": r.is_pretest,
            "correct": r.correct,
            "b": round(r.b, 3),
            "target_grade": r.target_grade,
            "theta_after": r.theta_after,
            "se_after": r.se_after,
            "p_after": r.p_after,
            "reason": r.selection_reason,
        }
        for r in state.responses
    ]


def stop_text_ru(decision: Decision, scale: Scale | None = None, n_shown: int | None = None) -> str:
    """Объяснение кандидату (§3.13), без «θ».

    n_shown — сколько заданий кандидат реально видел (pretest неотличимы от зачётных, поэтому
    номер последнего задания считаем по всем показанным).
    """
    scale = scale or load_scale()
    name = scale.grade_name(decision.target_grade)
    pct = round(100 * decision.p_pass)
    n = n_shown if n_shown is not None else decision.n_total
    if decision.code == PASSED:
        return f"Тест остановился на {n}-м задании: на {pct}% уверены, что ваш уровень выше порога {name}."
    if decision.code == FAILED:
        return f"Тест остановился на {n}-м задании: на {100 - pct}% уверены, что ваш уровень пока ниже порога {name}."
    if decision.code == BORDERLINE_PASS:
        return f"{name} подтверждён на границе: уверенность {pct}% после {n} заданий."
    return f"{name} не подтверждён: уверенность {pct}% после {n} заданий — результат на границе."


def result_summary(
    state: CatState,
    decision: Decision | None = None,
    *,
    grade_for_tags: str | None = None,
    scale: Scale | None = None,
    cfg: CatConfig | None = None,
) -> dict[str, Any]:
    scale = scale or load_scale()
    cfg = cfg or load_cat_config()
    post = state.posterior(scale)
    by_level, by_level_text = level_breakdown(state, scale)
    out = post.summary(state.target_grade)
    out.update(
        {
            "spec": state.spec,
            "claimed_grade": state.claimed_grade,
            "kind": state.kind,
            "n_scored": state.n_scored,
            "n_pretest": state.n_pretest,
            "by_level": by_level,
            "by_level_text": by_level_text,
            "subscores": module_subscores(state, grade_for_tags, scale, cfg),
        }
    )
    if decision is not None:
        out["decision"] = decision.to_dict()
        out["stop_text"] = stop_text_ru(decision, scale, n_shown=len(state.responses))
    return out


def instance_overlap(a: Iterable[str], b: Iterable[str]) -> int:
    """Пересечение экземпляров у пары сессий (должно быть 0)."""
    return len(set(a) & set(b))


def info_curve(meta: FamilyMeta, spec: str, scale: Scale | None = None, step: float = 0.25) -> list[tuple[float, float]]:
    """Кривая информации семейства (экран «Два кандидата — два теста»)."""
    scale = scale or load_scale()
    item = meta.item_params(spec, scale)
    n = int(round((scale.grid_max - scale.grid_min) / step)) + 1
    return [
        (round(scale.grid_min + i * step, 4), round(info_at(scale.grid_min + i * step, item), 5)) for i in range(n)
    ]


__all__ = [
    "BORDERLINE_FAIL",
    "BORDERLINE_PASS",
    "FAILED",
    "PASSED",
    "CatConfig",
    "CatState",
    "Decision",
    "FamilyMeta",
    "Offer",
    "ResponseRecord",
    "ScaleParam",
    "Selection",
    "compute_offers",
    "coverage_ok",
    "decide",
    "load_blueprint",
    "load_cat_config",
    "meta_from_family",
    "module_subscores",
    "new_state",
    "record_answer",
    "result_summary",
    "select_next",
    "start_extension",
    "stop_check",
    "trace",
]
