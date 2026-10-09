"""Детекторы целостности теста и маппинг доверия.

Источник: _internal/design/spec-testing.md §3.10, 01-concept.md §5.2 («Целостность»).
- «Чужой ключ»: ответ ≠ своему ключу, но его хеш равен key_hash экземпляра того же семейства,
  выданного ДРУГОМУ аккаунту; срабатывает, только если доля коллизий ключей в семействе < 2% → жёсткий флаг.
- Person-fit l_z < −2.33 → мягкий.
- «Быстро и верно на трудном»: время < 0.25 медианы семейства, ответ верный, P_j(θ̂_{−j}) < 0.4, ≥ 2 раз → мягкий.
- События: > 3 уходов со вкладки дольше 10 с или вставка в поле ответа → мягкий.
- CUSUM дрейфа семейства → QUARANTINE.
- Доверие: 0 флагов → 1.0 | 1–2 мягких → 0.75 | ≥ 3 мягких или «чужой ключ» → UNDER_REVIEW.
Работодатель флагов не видит; кандидат видит свои и может оспорить.
"""

from __future__ import annotations

import hashlib
import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any

from app.core.configs import load_config
from app.testing.bank.base import canonical
from app.testing.irt import ItemParams, Posterior, Scale, eap, load_scale, person_fit_lz, prob

SOFT = "soft"
HARD = "hard"

KIND_FOREIGN_KEY = "foreign_key"
KIND_LZ = "lz"
KIND_FAST = "fast_correct"
KIND_FOCUS = "focus"
KIND_PASTE = "paste"

UNDER_REVIEW = "UNDER_REVIEW"


def _times_ru(n: int) -> str:
    """«1 раз», «2 раза», «5 раз» — сообщения флагов видит кандидат."""
    m10, m100 = n % 10, n % 100
    word = "раза" if 2 <= m10 <= 4 and not 12 <= m100 <= 14 else "раз"
    return f"{n} {word}"


@dataclass(frozen=True)
class DetectorConfig:
    lz_threshold: float = -2.33
    lz_min_items: int = 8
    fast_ratio: float = 0.25
    fast_p_max: float = 0.4
    fast_min_count: int = 2
    focus_blur_min_s: float = 10.0
    focus_blur_max_count: int = 3
    foreign_key_max_collision: float = 0.02
    foreign_key_quarantine_count: int = 3
    foreign_key_quarantine_days: int = 7
    cusum_shift: float = 0.5
    cusum_h: float = 11.0
    trust_clean: float = 1.0
    trust_soft: float = 0.75
    soft_flags_for_review: int = 3

    @classmethod
    def from_config(cls, testing: dict[str, Any] | None = None) -> DetectorConfig:
        t = testing if testing is not None else load_config("testing")
        d, tr = t["detectors"], t["trust"]
        return cls(
            lz_threshold=float(d["lz_threshold"]),
            lz_min_items=int(d.get("lz_min_items", 8)),
            fast_ratio=float(d["fast_ratio"]),
            fast_p_max=float(d["fast_p_max"]),
            fast_min_count=int(d["fast_min_count"]),
            focus_blur_min_s=float(d["focus_blur_min_s"]),
            focus_blur_max_count=int(d["focus_blur_max_count"]),
            foreign_key_max_collision=float(d["foreign_key_max_collision"]),
            foreign_key_quarantine_count=int(d["foreign_key_quarantine_count"]),
            foreign_key_quarantine_days=int(d["foreign_key_quarantine_days"]),
            cusum_shift=float(d["cusum_shift"]),
            cusum_h=float(d["cusum_h"]),
            trust_clean=float(tr["clean"]),
            trust_soft=float(tr["soft"]),
            soft_flags_for_review=int(tr["soft_flags_for_review"]),
        )


@lru_cache(maxsize=1)
def load_detector_config() -> DetectorConfig:
    return DetectorConfig.from_config()


@dataclass(frozen=True)
class Flag:
    kind: str  # foreign_key | lz | fast_correct | focus | paste
    severity: str  # soft | hard
    message_ru: str
    value: float | None = None
    family_id: str | None = None
    source_session_id: str | None = None
    source_account_id: str | None = None
    status: str = "open"  # open | dismissed | confirmed

    def to_dict(self, *, for_candidate: bool = False) -> dict[str, Any]:
        """Для методиста (Л3: «чужой ключ → источник: сессия A») или для самого кандидата.

        Кандидат видит свои флаги и может их оспорить, но источник «чужого ключа» — это сессия
        ДРУГОГО человека: её id кандидату не отдаём (§2.3, приватность).
        """
        out = {
            "kind": self.kind,
            "severity": self.severity,
            "message_ru": self.message_ru,
            "value": self.value,
            "family_id": self.family_id,
            "status": self.status,
        }
        if not for_candidate:
            out["source_session_id"] = self.source_session_id
        return out


# ---------------------------------------------------------------------------
# «Чужой ключ»
# ---------------------------------------------------------------------------


def answer_hash(family_id: str, normalized_answer: Any) -> str:
    """Хеш нормализованного ответа — та же формула, что Instance.key_hash (bank/base.py)."""
    return hashlib.sha256(f"{family_id}|{canonical(normalized_answer)}".encode()).hexdigest()


def key_collision_rate(key_hashes: Sequence[str]) -> float:
    """Доля коллизий ключей в пуле семейства: 1 − (различных / всего)."""
    if not key_hashes:
        return 1.0
    return 1.0 - len(set(key_hashes)) / len(key_hashes)


@dataclass(frozen=True)
class IssuedInstance:
    """Экземпляр, уже выданный кому-то: для поиска источника чужого ключа."""

    instance_id: str
    family_id: str
    key_hash: str
    account_id: str
    session_id: str


def detect_foreign_key(
    *,
    family_id: str,
    answer_key_hash: str,
    own_key_hash: str,
    account_id: str,
    issued: Iterable[IssuedInstance],
    family_collision_rate: float,
    cfg: DetectorConfig | None = None,
) -> Flag | None:
    """Жёсткий флаг, если ответ совпал с ключом экземпляра, выданного ДРУГОМУ аккаунту.

    При доле коллизий ≥ 2% совпадение может быть случайным — флаг не ставим (§3.10).
    """
    cfg = cfg or load_detector_config()
    if answer_key_hash == own_key_hash:
        return None  # верный ответ на свой экземпляр
    if family_collision_rate >= cfg.foreign_key_max_collision:
        return None
    for inst in issued:
        if inst.family_id == family_id and inst.key_hash == answer_key_hash and inst.account_id != account_id:
            return Flag(
                kind=KIND_FOREIGN_KEY,
                severity=HARD,
                message_ru="Ответ совпал с ключом варианта, выданного другому кандидату",
                value=1.0,
                family_id=family_id,
                source_session_id=inst.session_id,
                source_account_id=inst.account_id,
            )
    return None


def family_needs_quarantine_by_foreign_keys(
    flag_times: Iterable[datetime], now: datetime, cfg: DetectorConfig | None = None
) -> bool:
    """≥ 3 «чужих ключа» по семейству за 7 дней → QUARANTINE семейства."""
    cfg = cfg or load_detector_config()
    horizon = now - timedelta(days=cfg.foreign_key_quarantine_days)
    return sum(1 for t in flag_times if t >= horizon) >= cfg.foreign_key_quarantine_count


# ---------------------------------------------------------------------------
# Person-fit и «быстро и верно»
# ---------------------------------------------------------------------------


def detect_person_fit(
    items: Sequence[ItemParams],
    xs: Sequence[bool | int],
    scale: Scale | None = None,
    cfg: DetectorConfig | None = None,
) -> Flag | None:
    """l_z при θ̂ по всем зачётным ответам; мягкий флаг при l_z < −2.33."""
    scale = scale or load_scale()
    cfg = cfg or load_detector_config()
    if len(items) < cfg.lz_min_items:
        return None
    theta = eap(zip(items, xs, strict=True), scale).mean
    lz = person_fit_lz(items, xs, theta)
    if lz is None or lz >= cfg.lz_threshold:
        return None
    return Flag(
        kind=KIND_LZ,
        severity=SOFT,
        message_ru="Необычный рисунок ответов: трудные решены, а лёгкие — нет",
        value=round(lz, 3),
    )


@dataclass(frozen=True)
class TimedResponse:
    family_id: str
    item: ItemParams
    correct: bool
    rt_ms: int | None


def detect_fast_correct(
    responses: Sequence[TimedResponse],
    median_rt_ms: Mapping[str, float],
    scale: Scale | None = None,
    cfg: DetectorConfig | None = None,
) -> Flag | None:
    """Верно, быстрее 0.25 медианы семейства и «трудно» для этого кандидата: P_j(θ̂_{−j}) < 0.4; ≥ 2 раз."""
    scale = scale or load_scale()
    cfg = cfg or load_detector_config()
    full = eap(((r.item, r.correct) for r in responses), scale)
    hits: list[str] = []
    for r in responses:
        med = median_rt_ms.get(r.family_id)
        if not r.correct or r.rt_ms is None or not med:
            continue
        if r.rt_ms >= cfg.fast_ratio * med:
            continue
        theta_loo = full.copy().remove(r.item, r.correct).mean
        if float(prob(theta_loo, r.item)) < cfg.fast_p_max:
            hits.append(r.family_id)
    if len(hits) < cfg.fast_min_count:
        return None
    return Flag(
        kind=KIND_FAST,
        severity=SOFT,
        message_ru=f"Трудные для вас задания решены очень быстро ({_times_ru(len(hits))})",
        value=float(len(hits)),
    )


def median_rt(rts: Iterable[int | float]) -> float | None:
    vals = [float(v) for v in rts if v is not None]
    return statistics.median(vals) if vals else None


# ---------------------------------------------------------------------------
# События фронта: уход со вкладки, вставка
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SessionEvent:
    ts: datetime
    type: str  # blur | focus | paste | reconnect
    payload: Mapping[str, Any] = field(default_factory=dict)


def detect_events(
    events: Sequence[SessionEvent], end: datetime | None = None, cfg: DetectorConfig | None = None
) -> list[Flag]:
    """> 3 уходов со вкладки дольше 10 с → мягкий «focus»; вставка в поле ответа → мягкий «paste»."""
    cfg = cfg or load_detector_config()
    flags: list[Flag] = []
    long_blurs = 0
    blur_start: datetime | None = None
    pastes = 0
    for ev in sorted(events, key=lambda e: e.ts):
        if ev.type == "blur":
            blur_start = blur_start or ev.ts
        elif ev.type == "focus" and blur_start is not None:
            if (ev.ts - blur_start).total_seconds() > cfg.focus_blur_min_s:
                long_blurs += 1
            blur_start = None
        elif ev.type == "paste" and ev.payload.get("field", "answer") == "answer":
            pastes += 1
    if blur_start is not None and end is not None and (end - blur_start).total_seconds() > cfg.focus_blur_min_s:
        long_blurs += 1
    if long_blurs > cfg.focus_blur_max_count:
        flags.append(
            Flag(
                kind=KIND_FOCUS,
                severity=SOFT,
                message_ru=f"Уход со вкладки дольше {int(cfg.focus_blur_min_s)} с: {_times_ru(long_blurs)}",
                value=float(long_blurs),
            )
        )
    if pastes:
        flags.append(
            Flag(kind=KIND_PASTE, severity=SOFT, message_ru="Вставка текста в поле ответа", value=float(pastes))
        )
    return flags


# ---------------------------------------------------------------------------
# Доверие
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TrustVerdict:
    trust: float | None  # None — до решения методиста
    under_review: bool
    soft: int
    hard: int

    @property
    def status(self) -> str:
        return UNDER_REVIEW if self.under_review else "OK"


def trust_from_flags(flags: Iterable[Flag], cfg: DetectorConfig | None = None) -> TrustVerdict:
    """0 флагов → 1.0 | 1–2 мягких → 0.75 | ≥ 3 мягких или «чужой ключ» → UNDER_REVIEW (§3.10)."""
    cfg = cfg or load_detector_config()
    active = [f for f in flags if f.status != "dismissed"]
    soft = sum(1 for f in active if f.severity == SOFT)
    hard = sum(1 for f in active if f.severity == HARD)
    if hard or soft >= cfg.soft_flags_for_review:
        return TrustVerdict(trust=None, under_review=True, soft=soft, hard=hard)
    return TrustVerdict(trust=cfg.trust_soft if soft else cfg.trust_clean, under_review=False, soft=soft, hard=hard)


def evaluate_session(
    items: Sequence[ItemParams],
    xs: Sequence[bool | int],
    *,
    family_ids: Sequence[str] | None = None,
    rts_ms: Sequence[int | None] | None = None,
    median_rt_ms: Mapping[str, float] | None = None,
    events: Sequence[SessionEvent] = (),
    end: datetime | None = None,
    extra_flags: Iterable[Flag] = (),
    scale: Scale | None = None,
    cfg: DetectorConfig | None = None,
) -> list[Flag]:
    """Все поведенческие детекторы по зачётным ответам сессии (чужой ключ проверяется при ответе)."""
    scale = scale or load_scale()
    cfg = cfg or load_detector_config()
    flags: list[Flag] = list(extra_flags)
    f = detect_person_fit(items, xs, scale, cfg)
    if f:
        flags.append(f)
    if family_ids is not None and rts_ms is not None and median_rt_ms:
        timed = [
            TimedResponse(fid, it, bool(x), rt)
            for fid, it, x, rt in zip(family_ids, items, xs, rts_ms, strict=True)
        ]
        f = detect_fast_correct(timed, median_rt_ms, scale, cfg)
        if f:
            flags.append(f)
    flags.extend(detect_events(events, end, cfg))
    return flags


# ---------------------------------------------------------------------------
# CUSUM дрейфа семейства
# ---------------------------------------------------------------------------


def drift_probs(post_loo: Posterior, item: ItemParams, shift: float) -> tuple[float, float]:
    """p0 = Σ_θ π_{−j}(θ)·P(θ; b), p1 — то же при b − shift (семейство «стало легче»)."""
    p0 = post_loo.expected(item)
    p1 = post_loo.expected(ItemParams(b=item.b - shift, c=item.c, a=item.a))
    return p0, p1


def cusum_increment(x: bool | int, p0: float, p1: float) -> float:
    eps = 1e-12
    p0 = min(max(p0, eps), 1 - eps)
    p1 = min(max(p1, eps), 1 - eps)
    return math.log(p1 / p0) if x else math.log((1 - p1) / (1 - p0))


@dataclass
class CusumState:
    s: float = 0.0
    n: int = 0
    alarm_at: int | None = None  # номер ответа, на котором S впервые превысил h

    def step(self, x: bool | int, p0: float, p1: float, h: float) -> bool:
        """S ← max(0, S + LLR); True, если S > h (семейство → QUARANTINE)."""
        self.n += 1
        self.s = max(0.0, self.s + cusum_increment(x, p0, p1))
        if self.s > h and self.alarm_at is None:
            self.alarm_at = self.n
        return self.s > h


def cusum_scan(
    observations: Iterable[tuple[bool | int, float, float]], h: float | None = None
) -> tuple[list[float], int | None]:
    """Прогон CUSUM по последовательности (x, p0, p1). Возвращает путь S и номер первой тревоги."""
    h = load_detector_config().cusum_h if h is None else h
    st = CusumState()
    path: list[float] = []
    for x, p0, p1 in observations:
        st.step(x, p0, p1, h)
        path.append(st.s)
    return path, st.alarm_at
