"""Машины состояний: тестовая сессия и квалификация (кандидат × специализация).

Источник: _internal/design/spec-testing.md §2.4 (машины состояний), §2.5 (правила), §3.5 (доборы),
§3.11 (свежесть), §3.12 (что отдаём в подбор); 01-concept.md §5.3 (смена грейда).
Всё чистое: время приходит только через инжектируемый Clock, хранение — забота вызывающего кода.

Тестовая сессия:
    CREATED → IN_PROGRESS ⇄ PAUSED (> 30 мин → ABANDONED: попытка засчитана, результат не публикуется)
    IN_PROGRESS ─стоп-правило→ DECIDED{PASSED | FAILED | BORDERLINE_PASS | BORDERLINE_FAIL}
    DECIDED → EXTENSION_OFFERED{UP|DOWN} ─принято ≤ 24 ч→ дочерняя сессия (тот же апостериор)
                                         └─отказ/истекло→ FINAL
    FINAL: 0–2 мягких флага → trust 1.0 | 0.75;  ≥ 3 мягких | «чужой ключ» → UNDER_REVIEW → FINAL(0.75) | INVALIDATED

Квалификация:
    NONE → SURVEY_DONE → TESTING → CONFIRMED(g) | NOT_CONFIRMED;  CONFIRMED(g) → UPGRADE_TESTING → CONFIRMED(g+1) | CONFIRMED(g)
    Принудительного понижения нет никогда; добровольное — считается сменой.
"""

from __future__ import annotations

import dataclasses
import math
import random
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from functools import lru_cache
from typing import Any, Protocol

from app.core.configs import load_config
from app.testing.cat import (
    EXTENSION_KINDS,
    KIND_EXT_DOWN,
    KIND_EXT_UP,
    KIND_INITIAL,
    KIND_RETAKE,
    KIND_UPGRADE,
    PASSED,
    PRIMARY_KINDS,
    TAG_CONFIRMED,
    TAG_STRONG,
    CatConfig,
    CatState,
    Decision,
    FamilyMeta,
    Offer,
    ResponseRecord,
    Selection,
    compute_offers,
    decide,
    express_cat_config,
    load_cat_config,
    load_express_config,
    module_subscores,
    new_state,
    record_answer,
    result_summary,
    select_next,
    start_extension,
    stop_check,
)
from app.testing.detectors import Flag, TrustVerdict, load_detector_config, trust_from_flags
from app.testing.irt import ItemParams, Posterior, Scale, load_scale, se_with_age

# ---------------------------------------------------------------------------
# Часы и относительные даты (DECISIONS п.9: в UI только относительные даты через Clock)
# ---------------------------------------------------------------------------


class Clock(Protocol):
    """Любой объект с now() → aware datetime. app.core.clock.clock подходит без адаптера."""

    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class FixedClock:
    """Управляемые часы для тестов, симуляции и демо."""

    def __init__(self, start: datetime) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, **delta: float) -> datetime:
        self._now += timedelta(**delta)
        return self._now

    def set(self, when: datetime) -> None:
        self._now = when


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _span_ru(delta: timedelta) -> str:
    secs = max(delta.total_seconds(), 0)
    days = math.ceil(secs / 86400 - 1e-9)
    if secs >= 86400:
        return f"{days} {plural_ru(days, 'день', 'дня', 'дней')}"
    hours = math.ceil(secs / 3600 - 1e-9)
    if secs >= 3600:
        return f"{hours} {plural_ru(hours, 'час', 'часа', 'часов')}"
    mins = max(1, math.ceil(secs / 60 - 1e-9))
    return f"{mins} {plural_ru(mins, 'минуту', 'минуты', 'минут')}"


def _days_ru(n: int) -> str:
    """«90 дней», «1 день» — для правил, которые читаются из конфига."""
    return f"{n} {plural_ru(n, 'день', 'дня', 'дней')}"


def until_ru(target: datetime, now: datetime) -> str:
    """«через 14 дней» / «сейчас»."""
    return "сейчас" if target <= now else f"через {_span_ru(target - now)}"


def ago_ru(past: datetime, now: datetime) -> str:
    """«3 дня назад» / «только что»."""
    if now - past < timedelta(minutes=1):
        return "только что"
    return f"{_span_ru(now - past)} назад"


# ---------------------------------------------------------------------------
# Правила
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RulesConfig:
    retake_days: int = 14
    max_attempts: int = 3
    attempts_window_days: int = 90
    change_interval_days: int = 90
    upgrade_retry_days: int = 30
    onboarding_hours: int = 24
    pause_timeout_min: int = 30
    review_max_days: int = 7
    downgrade_respects_change_interval: bool = True
    regular_task_upgrade_hint_p: float = 0.7
    trust_soft: float = 0.75
    evidence_weight_factor: float = 0.8
    se_growth_per_month: float = 0.02
    days_per_month: float = 30.4375
    relevance_tau_days: float = 120.0
    freshness_bands: tuple[tuple[str, str, float | None], ...] = (
        ("fresh", "свежий", 30),
        ("actual", "актуальный", 120),
        ("aging", "устаревает", 240),
        ("stale", "устарел", None),
    )

    @classmethod
    def from_config(cls, testing: dict[str, Any] | None = None) -> RulesConfig:
        t = testing if testing is not None else load_config("testing")
        r, tr, fr = t["rules"], t["trust"], t["freshness"]
        return cls(
            retake_days=int(r["retake_days"]),
            max_attempts=int(r["max_attempts"]),
            attempts_window_days=int(r["attempts_window_days"]),
            change_interval_days=int(r["change_interval_days"]),
            upgrade_retry_days=int(r["upgrade_retry_days"]),
            onboarding_hours=int(r["onboarding_hours"]),
            pause_timeout_min=int(r["pause_timeout_min"]),
            review_max_days=int(r["review_max_days"]),
            downgrade_respects_change_interval=bool(r.get("downgrade_respects_change_interval", True)),
            regular_task_upgrade_hint_p=float(r.get("regular_task_upgrade_hint_p", 0.7)),
            trust_soft=float(tr["soft"]),
            evidence_weight_factor=float(tr["evidence_weight_factor"]),
            se_growth_per_month=float(fr["se_growth_per_month"]),
            days_per_month=float(fr.get("days_per_month", 30.4375)),
            relevance_tau_days=float(fr["relevance_tau_days"]),
            freshness_bands=tuple((b["code"], b["label"], b.get("max_days")) for b in fr["bands"]),
        )


@lru_cache(maxsize=1)
def load_rules() -> RulesConfig:
    return RulesConfig.from_config()


class SessionError(Exception):
    """Нарушение правил/недопустимый переход. message_ru можно показывать пользователю."""

    def __init__(self, code: str, message_ru: str, available_at: datetime | None = None) -> None:
        super().__init__(message_ru)
        self.code = code
        self.message_ru = message_ru
        self.available_at = available_at


# ---------------------------------------------------------------------------
# Тестовая сессия
# ---------------------------------------------------------------------------


class SessionStatus(StrEnum):
    CREATED = "CREATED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    ABANDONED = "ABANDONED"
    DECIDED = "DECIDED"
    EXTENSION_OFFERED = "EXTENSION_OFFERED"
    FINAL = "FINAL"
    UNDER_REVIEW = "UNDER_REVIEW"
    INVALIDATED = "INVALIDATED"


CLOSED_STATUSES = frozenset(
    {SessionStatus.FINAL, SessionStatus.UNDER_REVIEW, SessionStatus.INVALIDATED, SessionStatus.ABANDONED}
)

# Для сообщений пользователю (CLAUDE.md: тексты API — на русском, без внутренних кодов)
STATUS_LABELS_RU = {
    SessionStatus.CREATED: "ещё не начат",
    SessionStatus.IN_PROGRESS: "идёт",
    SessionStatus.PAUSED: "на паузе",
    SessionStatus.ABANDONED: "прерван",
    SessionStatus.DECIDED: "уже завершён",
    SessionStatus.EXTENSION_OFFERED: "ждёт решения о доборе",
    SessionStatus.FINAL: "закрыт",
    SessionStatus.UNDER_REVIEW: "на проверке у методиста",
    SessionStatus.INVALIDATED: "аннулирован",
}


@dataclass(frozen=True)
class SessionOutcome:
    """Итог сессии для квалификации."""

    session_id: str
    kind: str
    status: str
    decision: str | None
    target_grade: str
    confirmed_grade: str | None  # грейд, который даёт эта сессия (с учётом базового грейда при доборе вверх)
    borderline: bool
    p_pass: float | None
    theta: float | None
    se: float | None
    pending_offers: bool
    trust: float | None
    under_review: bool
    extended: bool = False  # принят добор: итог цепочки решит дочерняя сессия
    subscores: Mapping[str, Mapping[str, Any]] = field(default_factory=dict)
    parent_id: str | None = None  # добор: родительская сессия цепочки (тот же апостериор)
    started_at: datetime | None = None  # для добора — момент принятия предложения
    claimed_grade: str | None = None  # грейд, заявленный в начале попытки (для добора вниз — исходный)


@dataclass
class TestSession:
    __test__ = False  # не путать pytest: это не тест-класс

    id: str
    account_id: str
    spec: str
    kind: str
    claimed_grade: str
    cat: CatState
    created_at: datetime
    status: SessionStatus = SessionStatus.CREATED
    parent_id: str | None = None
    base_grade: str | None = None  # добор вверх: грейд, который остаётся при неудаче
    base_borderline: bool = False
    started_at: datetime | None = None
    paused_at: datetime | None = None
    decided_at: datetime | None = None
    finished_at: datetime | None = None
    pending: Selection | None = None
    pending_shown_at: datetime | None = None
    decision: Decision | None = None
    offers: tuple[Offer, ...] = ()
    offers_expire_at: datetime | None = None
    accepted_offer: Offer | None = None
    extended_by: str | None = None
    flags: list[Flag] = field(default_factory=list)
    trust: float | None = None
    params_version: str = ""
    last_activity_at: datetime | None = None  # последний запрос кандидата (для обрыва без явной паузы)
    express: bool = False  # экспресс-тест жюри (DEMO_MODE): короткий CAT, грейд не присваивается, в метрики не идёт

    def cat_cfg(self, cfg: CatConfig | None = None) -> CatConfig:
        """Параметры CAT этой сессии: явно переданные, иначе экспресс-набор (для экспресса) или основной."""
        if cfg is not None:
            return cfg
        return express_cat_config() if self.express else load_cat_config()

    def express_time_up(self, clock: Clock) -> bool:
        """Бюджет времени экспресса исчерпан (и заданий уже хватает, чтобы решать): решение по данным ответам."""
        if not self.express or self.started_at is None:
            return False
        ex = load_express_config()
        return (
            (clock.now() - self.started_at).total_seconds() >= ex.time_budget_s
            and self.cat.n_scored >= ex.time_budget_min_items
        )

    # --- создание ---
    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        account_id: str,
        spec: str,
        claimed_grade: str,
        clock: Clock,
        rng: random.Random,
        kind: str = KIND_INITIAL,
        base_grade: str | None = None,
        blueprint: Mapping[str, float] | None = None,
        scale: Scale | None = None,
        cfg: CatConfig | None = None,
        express: bool = False,
    ) -> TestSession:
        """claimed_grade — целевой грейд сессии (для повышения это g+1, а base_grade = g)."""
        scale = scale or load_scale()
        if express and cfg is None:
            cfg = express_cat_config()
        cat = new_state(spec, claimed_grade, rng, kind=kind, blueprint=blueprint, scale=scale, cfg=cfg)
        return cls(
            id=session_id,
            account_id=account_id,
            spec=spec,
            kind=kind,
            claimed_grade=claimed_grade,
            cat=cat,
            created_at=clock.now(),
            base_grade=base_grade,
            params_version=scale.params_version,
            express=express,
        )

    # --- вспомогательное ---
    def _require(self, *allowed: SessionStatus) -> None:
        if self.status not in allowed:
            raise SessionError("bad_state", f"Действие недоступно: тест {STATUS_LABELS_RU[self.status]}")

    @property
    def is_open(self) -> bool:
        return self.status in (SessionStatus.CREATED, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED)

    def progress(self, cfg: CatConfig | None = None) -> dict[str, Any]:
        """Для плеера: «задание 7 · обычно 12–20» (pretest неотличимы — считаем все позиции)."""
        cfg = self.cat_cfg(cfg)
        shown = len(self.cat.responses) + (1 if self.pending else 0)
        if self.express:
            return {"position": shown, "text_ru": f"экспресс · задание {shown} из {cfg.n_max}"}
        return {"position": shown, "text_ru": f"задание {shown} · обычно 12–{cfg.n_max}"}

    # --- ход теста ---
    def start(self, clock: Clock) -> None:
        self._require(SessionStatus.CREATED)
        self.status = SessionStatus.IN_PROGRESS
        self.started_at = self.last_activity_at = clock.now()

    def next_item(
        self,
        families: Sequence[FamilyMeta],
        rng: random.Random,
        clock: Clock,
        *,
        seen_history: Mapping[str, datetime] | None = None,
        scale: Scale | None = None,
        cfg: CatConfig | None = None,
    ) -> Selection | None:
        """Идемпотентно: пока на задание не ответили, возвращает его же. None — сессия решена."""
        if self.status in (SessionStatus.DECIDED, SessionStatus.EXTENSION_OFFERED) or self.status in CLOSED_STATUSES:
            return None
        self._require(SessionStatus.IN_PROGRESS)
        self.last_activity_at = clock.now()
        if self.pending is not None:
            return self.pending
        if self.express_time_up(clock):
            self._decide("time_budget", clock, scale, cfg)
            return None
        sel = select_next(
            self.cat, families, rng, now=clock.now(), seen_history=seen_history, scale=scale, cfg=self.cat_cfg(cfg)
        )
        if sel is None:
            self._decide("bank_exhausted", clock, scale, cfg)
            return None
        if self.express:
            limit = load_express_config().item_time_limit_s
            if sel.time_limit_s > limit:
                sel = dataclasses.replace(sel, time_limit_s=limit)
        self.pending = sel
        self.pending_shown_at = clock.now()
        return sel

    def deadline(self, cfg: CatConfig | None = None) -> datetime | None:
        """Серверный дедлайн текущего задания: лимит семейства + 5 с запаса."""
        cfg = self.cat_cfg(cfg)
        if self.pending is None or self.pending_shown_at is None:
            return None
        return self.pending_shown_at + timedelta(seconds=self.pending.time_limit_s + cfg.timer_grace_s)

    def answer(
        self,
        seq: int,
        correct: bool,
        clock: Clock,
        *,
        rt_ms: int | None = None,
        instance_id: str | None = None,
        scale: Scale | None = None,
        cfg: CatConfig | None = None,
    ) -> ResponseRecord:
        """Идемпотентный ответ: повтор с тем же seq возвращает уже записанный ответ. Таймаут = неверно."""
        for r in self.cat.responses:
            if r.seq == seq:
                return r
        self._require(SessionStatus.IN_PROGRESS)
        if self.pending is None or self.pending.seq != seq:
            raise SessionError("no_pending_item", "Это задание сейчас не выдано")
        dl = self.deadline(cfg)
        timed_out = dl is not None and clock.now() > dl
        if rt_ms is None and self.pending_shown_at is not None:
            rt_ms = int((clock.now() - self.pending_shown_at).total_seconds() * 1000)
        rec = record_answer(
            self.cat, self.pending, correct, scale=scale, rt_ms=rt_ms, timed_out=timed_out, instance_id=instance_id
        )
        self.pending = None
        self.pending_shown_at = None
        self.last_activity_at = clock.now()
        reason = stop_check(self.cat, scale, self.cat_cfg(cfg))
        if reason is None and self.express_time_up(clock):
            reason = "time_budget"
        if reason:
            self._decide(reason, clock, scale, cfg)
        return rec

    def _decide(self, reason: str, clock: Clock, scale: Scale | None = None, cfg: CatConfig | None = None) -> None:
        scale = scale or load_scale()
        cfg = self.cat_cfg(cfg)
        self.decision = decide(self.cat, reason, scale, cfg)
        # Экспресс — оценка, а не попытка получить грейд: доборов нет (их даёт только полный тест)
        self.offers = () if self.express else compute_offers(self.cat, self.decision, scale, cfg)
        self.decided_at = clock.now()
        if self.offers:
            self.status = SessionStatus.EXTENSION_OFFERED
            self.offers_expire_at = self.decided_at + timedelta(hours=cfg.ext_window_hours)
        else:
            self.status = SessionStatus.DECIDED

    def pause(self, clock: Clock) -> None:
        self._require(SessionStatus.IN_PROGRESS)
        self.status = SessionStatus.PAUSED
        self.paused_at = clock.now()

    def resume(self, clock: Clock, rules: RulesConfig | None = None) -> SessionStatus:
        """Возврат после обрыва ≤ 30 мин; иначе ABANDONED (попытка засчитана, результат не публикуется)."""
        rules = rules or load_rules()
        self._require(SessionStatus.PAUSED)
        assert self.paused_at is not None
        if clock.now() - self.paused_at > timedelta(minutes=rules.pause_timeout_min):
            self.abandon(clock)
        else:
            self.status = SessionStatus.IN_PROGRESS
            self.paused_at = None
            self.last_activity_at = clock.now()
        return self.status

    def abandon(self, clock: Clock) -> None:
        """Обрыв > 30 мин или явный выход: только из открытой сессии (решённую так не «обнулить»)."""
        self._require(SessionStatus.CREATED, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED)
        self.status = SessionStatus.ABANDONED
        self.finished_at = clock.now()
        self.pending = None

    def tick(self, clock: Clock, rules: RulesConfig | None = None, cfg: CatConfig | None = None) -> SessionStatus:
        """Таймеры: обрыв > 30 мин → ABANDONED; предложение добора > 24 ч → FINAL.

        Обрыв засчитывается и без явной паузы (закрыли вкладку): тишина > 30 мин после серверного
        дедлайна задания или после последнего запроса, если задание ещё не выдано (§2.4).
        """
        rules = rules or load_rules()
        now = clock.now()
        timeout = timedelta(minutes=rules.pause_timeout_min)
        if self.status == SessionStatus.PAUSED and self.paused_at is not None:
            if now - self.paused_at > timeout:
                self.abandon(clock)
        elif self.status == SessionStatus.IN_PROGRESS:
            idle_from = self.deadline(cfg) if self.pending is not None else self.last_activity_at
            if idle_from is not None and now - idle_from > timeout:
                self.abandon(clock)
        elif self.status == SessionStatus.EXTENSION_OFFERED and self.offers_expire_at and now > self.offers_expire_at:
            self.close(clock)
        return self.status

    # --- доборы ---
    def accept_extension(
        self,
        direction: str,
        clock: Clock,
        *,
        child_id: str,
        scale: Scale | None = None,
        cfg: CatConfig | None = None,
    ) -> TestSession:
        """Принять добор (≤ 24 ч): дочерняя сессия продолжает тот же апостериор.

        Подтверждение «ваших ответов достаточно» — это добор вниз на 0 заданий: решается сразу.
        """
        scale = scale or load_scale()
        cfg = cfg or load_cat_config()
        self._require(SessionStatus.EXTENSION_OFFERED)
        if self.offers_expire_at and clock.now() > self.offers_expire_at:
            self.close(clock)
            hours = cfg.ext_window_hours
            raise SessionError(
                "offer_expired", f"Предложение добора истекло ({hours} {plural_ru(hours, 'час', 'часа', 'часов')})"
            )
        offer = next((o for o in self.offers if o.direction == direction), None)
        if offer is None:
            raise SessionError("no_offer", "Такой добор не предлагался")
        child_cat = start_extension(self.cat, offer)
        kind = KIND_EXT_UP if direction == "up" else KIND_EXT_DOWN
        base_grade: str | None = None
        base_borderline = False
        if direction == "up" and self.decision is not None:
            # при неудаче добора вверх остаётся грейд, полученный в этой сессии
            base_grade = self.granted_grade()
            base_borderline = self.decision.borderline
        child = TestSession(
            id=child_id,
            account_id=self.account_id,
            spec=self.spec,
            kind=kind,
            claimed_grade=self.claimed_grade,
            cat=child_cat,
            created_at=clock.now(),
            status=SessionStatus.IN_PROGRESS,
            parent_id=self.id,
            base_grade=base_grade,
            base_borderline=base_borderline,
            started_at=clock.now(),
            last_activity_at=clock.now(),
            params_version=self.params_version,
        )
        self.accepted_offer = offer
        self.extended_by = child_id
        self.close(clock)
        reason = stop_check(child.cat, scale, cfg)
        if reason:
            child._decide(reason, clock, scale, cfg)
        return child

    def decline_extension(self, clock: Clock) -> TrustVerdict:
        self._require(SessionStatus.EXTENSION_OFFERED)
        return self.close(clock)

    # --- флаги, закрытие, проверка ---
    def add_flags(self, flags: Sequence[Flag]) -> None:
        self.flags.extend(flags)

    def close(self, clock: Clock) -> TrustVerdict:
        """DECIDED | EXTENSION_OFFERED → FINAL (trust 1.0 | 0.75) или UNDER_REVIEW (§3.10)."""
        self._require(SessionStatus.DECIDED, SessionStatus.EXTENSION_OFFERED)
        verdict = trust_from_flags(self.flags)
        self.finished_at = clock.now()
        if verdict.under_review:
            self.status = SessionStatus.UNDER_REVIEW
            self.trust = None
        else:
            self.status = SessionStatus.FINAL
            self.trust = verdict.trust
        return verdict

    def review(self, keep: bool, clock: Clock) -> None:
        """Решение методиста: оставить (trust 0.75) или аннулировать."""
        self._require(SessionStatus.UNDER_REVIEW)
        if keep:
            self.status = SessionStatus.FINAL
            self.trust = load_detector_config().trust_soft
        else:
            self.status = SessionStatus.INVALIDATED
            self.trust = None
        self.finished_at = clock.now()

    # --- итог ---
    def granted_grade(self) -> str | None:
        d = self.decision
        if d is None or self.express:  # экспресс категорию не присваивает: точность не та (DECISIONS п. 29)
            return None
        if self.kind in PRIMARY_KINDS or self.kind == KIND_EXT_DOWN:
            return d.target_grade if d.passed else None
        if self.kind == KIND_EXT_UP:
            # Повышение только при P ≥ 0.90; BORDERLINE не повышает (гистерезис)
            return d.target_grade if d.code == PASSED else self.base_grade
        if self.kind == KIND_UPGRADE:
            return d.target_grade if d.code == PASSED else None
        return None

    def outcome(self, scale: Scale | None = None, cfg: CatConfig | None = None) -> SessionOutcome:
        d = self.decision
        granted = None if self.status in (SessionStatus.ABANDONED, SessionStatus.INVALIDATED) else self.granted_grade()
        borderline = False
        if granted is not None and d is not None:
            borderline = d.borderline if granted == d.target_grade else self.base_borderline
        subs: dict[str, Any] = {}
        if d is not None and granted is not None:
            subs = module_subscores(self.cat, granted, scale, cfg)
        return SessionOutcome(
            session_id=self.id,
            kind=self.kind,
            status=str(self.status),
            decision=d.code if d else None,
            target_grade=self.cat.target_grade,
            confirmed_grade=granted,
            borderline=borderline,
            p_pass=d.p_pass if d else None,
            theta=d.theta if d else None,
            se=d.se if d else None,
            pending_offers=self.status == SessionStatus.EXTENSION_OFFERED,
            trust=self.trust,
            under_review=self.status == SessionStatus.UNDER_REVIEW,
            extended=self.extended_by is not None,
            subscores=subs,
            parent_id=self.parent_id,
            started_at=self.started_at or self.created_at,
            claimed_grade=self.claimed_grade,
        )

    def result(self, scale: Scale | None = None, cfg: CatConfig | None = None) -> dict[str, Any]:
        """Экран результата К4: линейка, облако, «решено по уровням», модули, развилка."""
        granted = self.granted_grade()
        out = result_summary(self.cat, self.decision, grade_for_tags=granted, scale=scale, cfg=self.cat_cfg(cfg))
        out["status"] = str(self.status)
        out["confirmed_grade"] = granted
        out["offers"] = [o.to_dict() for o in self.offers] if self.status == SessionStatus.EXTENSION_OFFERED else []
        out["params_version"] = self.params_version
        return out


# ---------------------------------------------------------------------------
# Квалификация
# ---------------------------------------------------------------------------


class QualStatus(StrEnum):
    NONE = "NONE"
    SURVEY_DONE = "SURVEY_DONE"
    TESTING = "TESTING"
    CONFIRMED = "CONFIRMED"
    NOT_CONFIRMED = "NOT_CONFIRMED"
    UPGRADE_TESTING = "UPGRADE_TESTING"


@dataclass(frozen=True)
class GradeChange:
    from_grade: str | None
    to_grade: str | None
    at: datetime
    reason: str  # initial | onboarding_extension | upgrade | voluntary_downgrade | invalidated
    session_id: str | None
    counts_as_change: bool


@dataclass(frozen=True)
class RuleCheck:
    ok: bool
    code: str
    message_ru: str
    available_at: datetime | None = None


@dataclass
class Qualification:
    account_id: str
    spec: str
    status: QualStatus = QualStatus.NONE
    grade: str | None = None
    borderline: bool = False
    theta: float | None = None
    se: float | None = None
    p_pass: float | None = None
    trust: float | None = None
    hidden: bool = False  # сессия на проверке у методиста → скрыт из витрины (≤ 7 дней)
    review_deadline: datetime | None = None
    confirmed_at: datetime | None = None
    first_confirmed_at: datetime | None = None
    onboarding_until: datetime | None = None
    last_activity_at: datetime | None = None
    next_change_allowed_at: datetime | None = None
    next_attempt_allowed_at: datetime | None = None
    attempts: list[datetime] = field(default_factory=list)
    history: list[GradeChange] = field(default_factory=list)
    source_session_id: str | None = None
    active_session_id: str | None = None
    module_tags: dict[str, str] = field(default_factory=dict)
    module_theta: dict[str, float] = field(default_factory=dict)
    subscores: dict[str, Any] = field(default_factory=dict)
    prev_snapshot: dict[str, Any] | None = None  # для отката при аннулировании сессии
    # Цепочка текущей попытки: первичная сессия + её доборы (общий апостериор) | тест на повышение.
    # Флаги любой сессии цепочки действуют на весь её результат (§2.4, §3.10).
    chain_ids: list[str] = field(default_factory=list)
    chain_trust: float | None = None  # min доверия по закрытым сессиям цепочки
    grade_session_ids: list[str] = field(default_factory=list)  # на каких сессиях держится текущий грейд
    reviews: list[str] = field(default_factory=list)  # сессии на проверке у методиста
    # Политика пересдач (ответы постановщиков, DECISIONS п. 29): самый низкий грейд, на котором не
    # подтвердились, и признак «бесплатной» попытки на грейд ниже — она не сжигает пересдачу.
    last_failed_grade: str | None = None
    attempt_free: bool = False

    # --- опрос ---
    def record_survey(self, clock: Clock) -> None:
        """NONE → SURVEY_DONE. Повторный опрос статус не меняет (история опросов хранится отдельно)."""
        if self.status == QualStatus.NONE:
            self.status = QualStatus.SURVEY_DONE

    # --- первичный тест и пересдача ---
    def _is_lower_than_failed(self, grade: str | None, scale: Scale) -> bool:
        """Грейд строго ниже того, на котором кандидат не подтвердился в прошлой попытке."""
        if grade is None or self.last_failed_grade is None:
            return False
        if grade not in scale.grades or self.last_failed_grade not in scale.grades:
            return False
        return scale.grade_index(grade) < scale.grade_index(self.last_failed_grade)

    def can_start_test(
        self, clock: Clock, rules: RulesConfig | None = None, grade: str | None = None, scale: Scale | None = None
    ) -> RuleCheck:
        """grade — грейд, на который кандидат хочет пройти тест. Провал на грейде g не сжигает попытку на
        грейде ниже g: пока идёт ожидание пересдачи (или исчерпан лимит), тест на более низкий грейд
        доступен сразу и в лимит попыток не засчитывается (DECISIONS п. 29, docs/TESTING_MECHANISM.md)."""
        rules = rules or load_rules()
        now = clock.now()
        if self.status == QualStatus.NONE:
            return RuleCheck(False, "survey_required", "Сначала пройдите опрос — это около 3 минут")
        if self.status in (QualStatus.TESTING, QualStatus.UPGRADE_TESTING):
            return RuleCheck(False, "already_testing", "Тест уже идёт — продолжите его")
        if self.status == QualStatus.CONFIRMED:
            return RuleCheck(False, "already_confirmed", "Грейд уже подтверждён — доступен только тест на повышение")
        if self.status == QualStatus.NOT_CONFIRMED:
            blocked: RuleCheck | None = None
            window_start = now - timedelta(days=rules.attempts_window_days)
            recent = sorted(a for a in self.attempts if a > window_start)
            if len(recent) >= rules.max_attempts:
                avail = recent[0] + timedelta(days=rules.attempts_window_days)
                blocked = RuleCheck(
                    False,
                    "attempts_limit",
                    f"Не больше {rules.max_attempts} попыток за {rules.attempts_window_days} дней. "
                    f"Следующая попытка — {until_ru(avail, now)}",
                    avail,
                )
            elif self.next_attempt_allowed_at and now < self.next_attempt_allowed_at:
                blocked = RuleCheck(
                    False,
                    "retake_cooldown",
                    f"Повтор теста будет доступен {until_ru(self.next_attempt_allowed_at, now)}",
                    self.next_attempt_allowed_at,
                )
            if blocked is not None:
                if self._is_lower_than_failed(grade, scale or load_scale()):
                    return RuleCheck(
                        True, "lower_grade_free", "Тест на более низкий грейд доступен сразу и не расходует пересдачу"
                    )
                return blocked
        return RuleCheck(True, "ok", "Можно начинать тест")

    def lower_grades_open(self, clock: Clock, rules: RulesConfig | None = None, scale: Scale | None = None) -> list[str]:
        """Грейды, на которые тест доступен СЕЙЧАС вопреки ожиданию пересдачи (пусто, если ожидания нет)."""
        scale = scale or load_scale()
        if self.can_start_test(clock, rules).ok:
            return []
        return [g for g in scale.grades if self.can_start_test(clock, rules, g, scale).ok]

    def start_test(
        self, session_id: str, clock: Clock, rules: RulesConfig | None = None, grade: str | None = None
    ) -> str:
        """SURVEY_DONE | NOT_CONFIRMED → TESTING. Возвращает вид сессии (initial | retake)."""
        check = self.can_start_test(clock, rules, grade)
        if not check.ok:
            raise SessionError(check.code, check.message_ru, check.available_at)
        kind = KIND_RETAKE if self.status == QualStatus.NOT_CONFIRMED else KIND_INITIAL
        self.status = QualStatus.TESTING
        self.attempt_free = check.code == "lower_grade_free"
        if not self.attempt_free:
            self.attempts.append(clock.now())
        self.active_session_id = session_id
        self.onboarding_until = None
        self.chain_ids, self.chain_trust = [session_id], None
        return kind

    # --- повышение ---
    def can_start_upgrade(self, clock: Clock, scale: Scale | None = None, rules: RulesConfig | None = None) -> RuleCheck:
        scale = scale or load_scale()
        rules = rules or load_rules()
        now = clock.now()
        if self.status != QualStatus.CONFIRMED or self.grade is None:
            return RuleCheck(False, "not_confirmed", "Повышение доступно только с подтверждённым грейдом")
        if scale.next_grade(self.grade) is None:
            return RuleCheck(False, "top_grade", "У вас максимальный грейд, доступный в тесте")
        if self.hidden:
            return RuleCheck(False, "under_review", "Дождитесь решения методиста по прошлой сессии")
        if self.next_change_allowed_at and now < self.next_change_allowed_at:
            return RuleCheck(
                False,
                "change_interval",
                f"Сменить грейд можно не чаще раза в {_days_ru(rules.change_interval_days)}: тест на повышение — "
                f"{until_ru(self.next_change_allowed_at, now)}",
                self.next_change_allowed_at,
            )
        if self.next_attempt_allowed_at and now < self.next_attempt_allowed_at:
            return RuleCheck(
                False,
                "upgrade_cooldown",
                f"Следующая попытка повышения — {until_ru(self.next_attempt_allowed_at, now)}",
                self.next_attempt_allowed_at,
            )
        return RuleCheck(True, "ok", "Можно пройти тест на повышение")

    def start_upgrade(
        self, session_id: str, clock: Clock, scale: Scale | None = None, rules: RulesConfig | None = None
    ) -> str:
        """CONFIRMED(g) → UPGRADE_TESTING. Возвращает целевой грейд g+1 (тест на непересекающихся семействах)."""
        scale = scale or load_scale()
        check = self.can_start_upgrade(clock, scale, rules)
        if not check.ok:
            raise SessionError(check.code, check.message_ru, check.available_at)
        assert self.grade is not None
        self.status = QualStatus.UPGRADE_TESTING
        self.active_session_id = session_id
        self.chain_ids, self.chain_trust = [session_id], None
        return scale.next_grade(self.grade) or self.grade

    # --- применение итогов сессий ---
    def in_onboarding(self, now: datetime) -> bool:
        return self.onboarding_until is not None and now <= self.onboarding_until

    def _snapshot(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "grade": self.grade,
            "borderline": self.borderline,
            "theta": self.theta,
            "se": self.se,
            "p_pass": self.p_pass,
            "trust": self.trust,
            "confirmed_at": self.confirmed_at,
            "source_session_id": self.source_session_id,
            "module_tags": dict(self.module_tags),
            "module_theta": dict(self.module_theta),
            "subscores": dict(self.subscores),
            "grade_session_ids": list(self.grade_session_ids),
        }

    def _take_result(self, o: SessionOutcome, now: datetime) -> None:
        self.theta, self.se, self.p_pass = o.theta, o.se, o.p_pass
        self.borderline = o.borderline
        self.source_session_id = o.session_id
        self.confirmed_at = now
        self.last_activity_at = now
        self.subscores = {k: dict(v) for k, v in o.subscores.items()}
        self.module_tags = {k: v["tag"] for k, v in o.subscores.items() if v.get("tag") in (TAG_CONFIRMED, TAG_STRONG)}
        self.module_theta = {k: v["theta"] for k, v in o.subscores.items()}

    def _dropped_level(self, o: SessionOutcome) -> bool:
        """Подтверждён грейд ниже заявленного (провал целевого грейда → добор вниз)."""
        scale = load_scale()
        c, g = o.claimed_grade, o.confirmed_grade
        if c is None or g is None or c not in scale.grades or g not in scale.grades:
            return False
        return scale.grade_index(g) < scale.grade_index(c)

    def _note_failed(self, o: SessionOutcome) -> None:
        """Запомнить, на каком грейде не подтвердились (самый низкий из проваленных в цепочке)."""
        scale = load_scale()
        t = o.target_grade
        if t not in scale.grades:
            return
        cur = self.last_failed_grade
        if cur in scale.grades and o.kind in EXTENSION_KINDS and scale.grade_index(cur) < scale.grade_index(t):
            return
        self.last_failed_grade = t

    def _join_chain(self, session_id: str) -> None:
        if session_id not in self.chain_ids:
            self.chain_ids.append(session_id)

    def _apply_trust(self, o: SessionOutcome, now: datetime, rules: RulesConfig) -> None:
        """Доверие и проверка методистом с учётом всей цепочки (§2.4, §3.10).

        Доборы продолжают тот же апостериор, поэтому флаги родителя (например, «чужой ключ» в
        проваленной основной части) действуют и на грейд, полученный добором: доверие цепочки — min
        по её сессиям, а любая сессия на проверке скрывает кандидата из витрины до решения методиста.
        """
        if o.under_review and o.session_id not in self.reviews:
            self.reviews.append(o.session_id)
            if self.review_deadline is None:
                self.review_deadline = now + timedelta(days=rules.review_max_days)
        self.hidden = bool(self.reviews)
        if o.trust is not None and o.session_id in self.chain_ids:
            self.chain_trust = o.trust if self.chain_trust is None else min(self.chain_trust, o.trust)
        # Доверие грейда меняет только цепочка, на которой грейд и держится (не проваленное повышение)
        if self.chain_trust is not None and set(self.chain_ids) & set(self.grade_session_ids):
            self.trust = self.chain_trust

    def _in_current_chain(self, o: SessionOutcome, now: datetime) -> bool:
        """Добор относится к текущей цепочке. Срок 24 ч проверяет сессия при принятии предложения,
        поэтому добор, принятый в окне и законченный позже, не теряется (онбординг, concept §5.3)."""
        if o.session_id in self.chain_ids:
            return True
        if o.kind not in EXTENSION_KINDS:
            return False
        if o.parent_id is not None:
            return o.parent_id in self.chain_ids
        return self.in_onboarding(o.started_at or now)

    def _belongs_to_attempt(self, o: SessionOutcome) -> bool:
        """Сессия текущей попытки (start_test/start_upgrade) или её добор. Повторно применённый итог
        старой попытки не должен менять новую (например, перевести идущую пересдачу в NOT_CONFIRMED)."""
        if not self.chain_ids or o.session_id in self.chain_ids:
            return True
        return o.kind in EXTENSION_KINDS and (o.parent_id is None or o.parent_id in self.chain_ids)

    def apply(self, o: SessionOutcome, clock: Clock, rules: RulesConfig | None = None) -> None:
        """Применить итог сессии (вызывать при решении и при закрытии сессии; повтор безопасен)."""
        rules = rules or load_rules()
        now = clock.now()
        in_progress = self.status in (QualStatus.TESTING, QualStatus.UPGRADE_TESTING)
        if in_progress and not self._belongs_to_attempt(o):
            self._apply_trust(o, now, rules)  # итог чужой попытки: важна только проверка методистом
            return
        if o.status == SessionStatus.ABANDONED:
            if self.status == QualStatus.TESTING:
                self.status = QualStatus.NOT_CONFIRMED
                if not self.attempt_free:  # бесплатная попытка на грейд ниже ожидание не продлевает
                    self.next_attempt_allowed_at = now + timedelta(days=rules.retake_days)
                self.attempt_free = False
            elif self.status == QualStatus.UPGRADE_TESTING:
                self.status = QualStatus.CONFIRMED
                self.next_attempt_allowed_at = now + timedelta(days=rules.upgrade_retry_days)
            self.active_session_id = None
            return
        if o.decision is None:
            return

        if self.status == QualStatus.TESTING:
            # Пока грейда нет, идёт одна попытка: все сессии (основная часть и доборы) — её цепочка
            self._join_chain(o.session_id)
            if self.onboarding_until is None:
                # Онбординг: первичный тест + доборы в течение 24 ч сменой грейда не считаются
                self.onboarding_until = now + timedelta(hours=rules.onboarding_hours)
            if o.confirmed_grade is not None:
                self.prev_snapshot = self._snapshot()
                self.history.append(GradeChange(None, o.confirmed_grade, now, "initial", o.session_id, False))
                self.status = QualStatus.CONFIRMED
                self.grade = o.confirmed_grade
                self.first_confirmed_at = self.first_confirmed_at or now
                self.next_change_allowed_at = now + timedelta(days=rules.change_interval_days)
                if self._dropped_level(o):
                    # Заявил Middle, подтвердился Junior: грейд выше — не «прыжок», а повтор того же уровня,
                    # его можно пересдать через retake_days, а не через интервал смены грейда (DECISIONS п. 29)
                    self.next_change_allowed_at = now + timedelta(days=rules.retake_days)
                    self.last_failed_grade = o.claimed_grade
                self.attempt_free = False
                self._take_result(o, now)
                self.grade_session_ids = list(self.chain_ids)
            elif not o.pending_offers and not o.extended and SessionStatus(o.status) in CLOSED_STATUSES:
                self.status = QualStatus.NOT_CONFIRMED
                self._note_failed(o)
                if not self.attempt_free:
                    self.next_attempt_allowed_at = now + timedelta(days=rules.retake_days)
                self.attempt_free = False
                self.active_session_id = None
            self._apply_trust(o, now, rules)
            return

        if self.status == QualStatus.CONFIRMED:
            if not self._in_current_chain(o, now):
                self._apply_trust(o, now, rules)  # чужая для грейда сессия: важна только проверка методистом
                return
            self._join_chain(o.session_id)
            if o.kind in EXTENSION_KINDS and o.confirmed_grade is not None and o.confirmed_grade != self.grade:
                self.history.append(
                    GradeChange(self.grade, o.confirmed_grade, now, "onboarding_extension", o.session_id, False)
                )
                self.grade = o.confirmed_grade
                self._take_result(o, now)
                self.grade_session_ids = list(self.chain_ids)
            self._apply_trust(o, now, rules)
            return

        if self.status == QualStatus.UPGRADE_TESTING:
            if o.kind != KIND_UPGRADE:
                self._apply_trust(o, now, rules)
                return
            self._join_chain(o.session_id)
            self.last_activity_at = now
            self.active_session_id = None
            if o.confirmed_grade is not None and o.decision == PASSED:
                self.prev_snapshot = self._snapshot()
                self.history.append(GradeChange(self.grade, o.confirmed_grade, now, "upgrade", o.session_id, True))
                self.grade = o.confirmed_grade
                self.next_change_allowed_at = now + timedelta(days=rules.change_interval_days)
                self._take_result(o, now)
                self.grade_session_ids = list(self.chain_ids)
            else:
                # Неудача грейд не снижает (принудительного понижения нет никогда); повтор через 30 дней
                self.next_attempt_allowed_at = now + timedelta(days=rules.upgrade_retry_days)
            self.status = QualStatus.CONFIRMED
            self._apply_trust(o, now, rules)

    # --- добровольное понижение ---
    def voluntary_downgrade(self, clock: Clock, scale: Scale | None = None, rules: RulesConfig | None = None) -> str:
        """CONFIRMED(g) → CONFIRMED(g−1). Считается сменой грейда (вне онбординга — не чаще раза в 90 дней)."""
        scale = scale or load_scale()
        rules = rules or load_rules()
        now = clock.now()
        if self.status != QualStatus.CONFIRMED or self.grade is None:
            raise SessionError("not_confirmed", "Понизить можно только подтверждённый грейд")
        lower = scale.prev_grade(self.grade)
        if lower is None:
            raise SessionError("lowest_grade", "Ниже Стажёра перейти нельзя")
        onboarding = self.in_onboarding(now)
        if (
            rules.downgrade_respects_change_interval
            and not onboarding
            and self.next_change_allowed_at
            and now < self.next_change_allowed_at
        ):
            raise SessionError(
                "change_interval",
                f"Сменить грейд можно не чаще раза в {_days_ru(rules.change_interval_days)}: "
                f"{until_ru(self.next_change_allowed_at, now)}",
                self.next_change_allowed_at,
            )
        self.history.append(GradeChange(self.grade, lower, now, "voluntary_downgrade", None, not onboarding))
        self.grade = lower
        self.borderline = False
        if not onboarding:
            self.next_change_allowed_at = now + timedelta(days=rules.change_interval_days)
        return lower

    # --- проверка методистом ---
    def resolve_review(
        self, keep: bool, clock: Clock, rules: RulesConfig | None = None, *, session_id: str | None = None
    ) -> None:
        """Решение методиста: оставить (trust 0.75, снова в витрине) или аннулировать.

        Аннулирование сессии, на которой держится грейд, откатывает к состоянию до её цепочки: доборы
        продолжают тот же апостериор, поэтому цепочка аннулируется целиком. Аннулирование сессии,
        которая грейд не меняла (например, проваленное повышение), грейд не трогает.
        session_id = None — решение по всем открытым проверкам.
        """
        rules = rules or load_rules()
        now = clock.now()
        targets = [session_id] if session_id is not None else list(self.reviews)
        affects_grade = session_id is None or session_id in self.grade_session_ids
        self.reviews = [s for s in self.reviews if s not in targets]
        self.hidden = bool(self.reviews)
        if not self.reviews:
            self.review_deadline = None
        if keep:
            if affects_grade:
                self.trust = rules.trust_soft
            return
        if not affects_grade or self.status not in (QualStatus.CONFIRMED, QualStatus.UPGRADE_TESTING):
            return
        snap = self.prev_snapshot or {}
        prev_grade = snap.get("grade")
        self.history.append(GradeChange(self.grade, prev_grade, now, "invalidated", self.source_session_id, False))
        if prev_grade is None:
            self.status = QualStatus.NOT_CONFIRMED
            self.grade = None
            self.theta = self.se = self.p_pass = self.trust = None
            self.module_tags, self.module_theta, self.subscores = {}, {}, {}
            self.grade_session_ids = []
            self.next_attempt_allowed_at = now + timedelta(days=rules.retake_days)
        else:
            for k, v in snap.items():
                setattr(self, k, v)
            self.status = QualStatus.CONFIRMED

    def review_overdue(self, clock: Clock) -> bool:
        return self.hidden and self.review_deadline is not None and clock.now() > self.review_deadline

    # --- свежесть (§3.11) ---
    def se_now(self, clock: Clock, rules: RulesConfig | None = None) -> float | None:
        rules = rules or load_rules()
        if self.se is None or self.last_activity_at is None:
            return self.se
        months = (clock.now() - self.last_activity_at).total_seconds() / 86400 / rules.days_per_month
        return se_with_age(self.se, months, rules.se_growth_per_month)

    def freshness(self, clock: Clock, rules: RulesConfig | None = None) -> dict[str, Any] | None:
        rules = rules or load_rules()
        if self.last_activity_at is None:
            return None
        return freshness_of(self.last_activity_at, clock.now(), rules) | {"se_now": self.se_now(clock, rules)}

    # --- регулярные задачи типа A (concept §5.4) ---
    def apply_regular_task(
        self,
        item: ItemParams,
        correct: bool,
        clock: Clock,
        *,
        scale: Scale | None = None,
        rules: RulesConfig | None = None,
    ) -> dict[str, Any]:
        """Апостериор с приором N(θ̂, SE²(t)). Грейд НЕ меняется; P(θ ≥ c_{g+1}) ≥ 0.7 → подсказка о повышении."""
        scale = scale or load_scale()
        rules = rules or load_rules()
        if self.theta is None or self.se is None or self.grade is None:
            raise SessionError("not_confirmed", "Задача влияет на уровень только при подтверждённом грейде")
        se_t = self.se_now(clock, rules) or self.se
        post = Posterior.prior(scale, mu=self.theta, sd=se_t).add(item, correct)
        self.theta, self.se = round(post.mean, 4), round(post.sd, 4)
        self.last_activity_at = clock.now()
        nxt = scale.next_grade(self.grade)
        p_next = post.p_at_least(scale.cut(nxt)) if nxt else 0.0
        hint = nxt is not None and p_next >= rules.regular_task_upgrade_hint_p
        return {
            "theta": self.theta,
            "se": self.se,
            "p_next": round(p_next, 4),
            "upgrade_hint": hint,
            "hint_text_ru": "Вам доступен тест на повышение" if hint else None,
        }

    # --- что отдаём в подбор (§3.12) ---
    def handoff(self, clock: Clock, scale: Scale | None = None, rules: RulesConfig | None = None) -> dict[str, Any]:
        scale = scale or load_scale()
        rules = rules or load_rules()
        if self.status not in (QualStatus.CONFIRMED, QualStatus.UPGRADE_TESTING) or self.grade is None:
            return {"confirmed": False, "spec": self.spec}
        se_t = self.se_now(clock, rules) or 0.0
        theta = self.theta if self.theta is not None else scale.cut(self.grade)
        # Консервативно: нижняя оценка θ̂ − 0.5·SE(t) внутри полосы грейда
        pos = (theta - 0.5 * se_t - scale.cut(self.grade)) / scale.band_width(self.grade)
        trust = self.trust if self.trust is not None else 0.0
        return {
            "confirmed": True,
            "spec": self.spec,
            "grade": self.grade,
            "category": category_of(self.spec, self.grade),
            "borderline": self.borderline,
            "visible": not self.hidden,
            "theta": self.theta,
            "se": self.se,
            "se_now": round(se_t, 4),
            "position_in_grade": round(min(1.0, max(0.0, pos)), 4),
            "evidence_weight": round(rules.evidence_weight_factor * trust, 4),
            "confidence": self.p_pass,
            "trust": self.trust,
            "confirmed_at": self.confirmed_at,
            "module_tags": dict(self.module_tags),
            "module_theta": dict(self.module_theta),
            "freshness": self.freshness(clock, rules),
        }


def freshness_of(last_activity: datetime, now: datetime, rules: RulesConfig | None = None) -> dict[str, Any]:
    """Актуальность A = 100·exp(−Δдней/120) и полоса: свежий < 30 / актуальный < 120 / устаревает < 240 / устарел."""
    rules = rules or load_rules()
    days = max((now - last_activity).total_seconds() / 86400, 0.0)
    band, label = rules.freshness_bands[-1][0], rules.freshness_bands[-1][1]
    for code, lab, max_days in rules.freshness_bands:
        if max_days is not None and days < max_days:
            band, label = code, lab
            break
    return {
        "days": round(days, 2),
        "relevance": round(100 * math.exp(-days / rules.relevance_tau_days)),
        "band": band,
        "band_ru": label,
        "since_ru": ago_ru(last_activity, now),
    }


def category_of(spec: str, grade: str, taxonomy: dict[str, Any] | None = None) -> dict[str, str]:
    """Категория = f(специализация, подтверждённый грейд) — детерминированно (§3.7)."""
    tax = taxonomy if taxonomy is not None else load_config("taxonomy")
    spec_name = next((s["name"] for s in tax["specializations"] if s["code"] == spec), spec)
    grade_name = next((g["name"] for g in tax["grades"] if g["code"] == grade), grade)
    return {"code": f"{spec}:{grade}", "name": f"{spec_name} · {grade_name}"}


def fullstack_grade(backend_grade: str | None, frontend_grade: str | None, scale: Scale | None = None) -> str | None:
    """Fullstack — производная категория: подтверждены обе квалификации, грейд = минимальный из двух."""
    scale = scale or load_scale()
    if backend_grade is None or frontend_grade is None:
        return None
    return min(backend_grade, frontend_grade, key=scale.grade_index)
