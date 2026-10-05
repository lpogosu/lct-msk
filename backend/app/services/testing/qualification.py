"""Квалификация кандидата × специализация: хранение доменной Qualification и контракт для подбора.

Контракт для других модулей (подбор, банк кандидатов, PDF-профиль):

    get_qualification_snapshot(db, user_id, specialization, *, now=None) -> QualificationSnapshot | None
    get_qualification_snapshot(db, user_id) -> list[QualificationSnapshot]   # все специализации кандидата

Возвращает то, что нужно механике подбора (spec-testing §3.12): грейд, оценку уровня и её погрешность
с поправкой на давность SE(t), доверие, «на границе», даты подтверждения и последней активности,
подбаллы модулей С ПОГРЕШНОСТЬЮ и теги ✓/✓✓. None — квалификации по специализации нет (опрос не пройден).
Для specialization="fullstack" собирается производная категория: подтверждены обе квалификации,
грейд — меньший из двух, остальные числа — от квалификации с меньшим грейдом. В список «все
специализации» Fullstack не входит (кандидат виден в обеих исходных категориях; спросите явно).

register_qualification_listener(fn) — fn(db, user_id, specialization) вызывается в той же транзакции
после любого изменения квалификации (присвоение, повышение, понижение, проверка методистом), чтобы
подбор мог пересчитать материализованные признаки кандидата.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, overload

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.models.testing import GradeChange as GradeChangeRow
from app.models.testing import Qualification as QualificationRow
from app.services.testing.snapshot import qualification_from_state, qualification_to_state
from app.testing.irt import load_scale
from app.testing.session import (
    Qualification,
    QualStatus,
    category_of,
    freshness_of,
    fullstack_grade,
    load_rules,
)

QualificationListener = Callable[[Session, uuid.UUID, str], None]
_listeners: list[QualificationListener] = []


def register_qualification_listener(fn: QualificationListener) -> QualificationListener:
    """Подписка на изменения квалификации (можно как декоратор)."""
    if fn not in _listeners:
        _listeners.append(fn)
    return fn


def notify_changed(db: Session, user_id: uuid.UUID, spec: str) -> None:
    for fn in list(_listeners):
        fn(db, user_id, spec)


# ---------------------------------------------------------------------------
# Загрузка и сохранение
# ---------------------------------------------------------------------------


def get_row(db: Session, user_id: uuid.UUID, spec: str, *, lock: bool = False) -> QualificationRow | None:
    q = select(QualificationRow).where(QualificationRow.candidate_id == user_id, QualificationRow.specialization == spec)
    if lock:
        q = q.with_for_update()
    return db.scalars(q).first()


def get_or_create_row(db: Session, user_id: uuid.UUID, spec: str, *, lock: bool = True) -> QualificationRow:
    row = get_row(db, user_id, spec, lock=lock)
    if row is None:
        row = QualificationRow(candidate_id=user_id, specialization=spec, status=QualStatus.NONE.value, state={})
        db.add(row)
        db.flush()
        if lock:
            row = get_row(db, user_id, spec, lock=True) or row
    return row


def load_domain(row: QualificationRow) -> Qualification:
    return qualification_from_state(row.state or {}, str(row.candidate_id), row.specialization)


def _uuid_or_none(v: str | None) -> uuid.UUID | None:
    if not v:
        return None
    try:
        return uuid.UUID(str(v))
    except ValueError:
        return None


def save_domain(db: Session, row: QualificationRow, q: Qualification, *, notify: bool = True) -> None:
    """Домен → строка (снимок + денормализованные колонки) и новые записи истории смен грейда."""
    rules = load_rules()
    now = clock.now()
    before_history = len((row.state or {}).get("history") or [])
    row.state = qualification_to_state(q)
    row.status = str(q.status)
    row.grade = q.grade
    row.theta = q.theta
    row.se = q.se
    row.score = round(load_scale().score(q.theta), 1) if q.theta is not None else None
    row.p_pass = q.p_pass
    row.borderline = bool(q.borderline)
    row.trust = q.trust
    row.under_review = bool(q.hidden)
    row.confirmed_at = q.confirmed_at
    row.last_activity_at = q.last_activity_at
    row.source_session_id = _uuid_or_none(q.source_session_id)
    row.onboarding_until = q.onboarding_until
    row.next_change_allowed_at = q.next_change_allowed_at
    row.next_attempt_allowed_at = q.next_attempt_allowed_at
    window = now - timedelta(days=rules.attempts_window_days)
    row.attempts_90d = sum(1 for a in q.attempts if a > window)
    row.subscores = dict(q.subscores)
    row.module_tags = dict(q.module_tags)
    for change in q.history[before_history:]:
        db.add(
            GradeChangeRow(
                candidate_id=row.candidate_id,
                specialization=row.specialization,
                from_grade=change.from_grade,
                to_grade=change.to_grade,
                session_id=_uuid_or_none(change.session_id),
                at=change.at,
                reason=change.reason,
                counts_as_change=change.counts_as_change,
            )
        )
    db.flush()
    if notify:
        notify_changed(db, row.candidate_id, row.specialization)


# ---------------------------------------------------------------------------
# Контракт для подбора
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ModuleEstimate:
    module: str
    name: str
    n: int
    theta: float
    se: float
    tag: str  # strong | confirmed | insufficient | none
    symbol: str  # ✓✓ | ✓ | ~ | ""


@dataclass(frozen=True)
class QualificationSnapshot:
    """Что механика подбора берёт из тестирования (spec-testing §3.12). Внутренние единицы шкалы:
    theta/se — не для показа людям (в UI — балл S = 500 + 100·уровень, DECISIONS п. 10)."""

    user_id: uuid.UUID
    specialization: str
    status: str  # NONE | SURVEY_DONE | TESTING | CONFIRMED | NOT_CONFIRMED | UPGRADE_TESTING
    confirmed: bool
    grade: str | None
    category: dict[str, str] | None  # {"code": "backend_python:middle", "name": "Backend (Python) · Middle"}
    theta: float | None
    se: float | None  # погрешность на момент подтверждения
    se_t: float | None  # SE(t)² = SE² + 0.02·Δмес — с поправкой на давность (§3.11)
    score: int | None
    p_pass: float | None  # уверенность в грейде
    trust: float | None  # 1.0 | 0.75 | None (на проверке)
    evidence_weight: float  # 0.8·trust
    borderline: bool
    visible: bool  # False — сессия на проверке у методиста, кандидат скрыт из витрины
    position_in_grade: float | None  # clip((θ̂ − 0.5·SE(t) − cut(g)) / ширина полосы, 0, 1)
    confirmed_at: datetime | None
    last_activity_at: datetime | None
    relevance: int | None  # актуальность 0–100
    freshness_band: str | None  # fresh | actual | aging | stale
    modules: dict[str, ModuleEstimate] = field(default_factory=dict)
    module_tags: dict[str, str] = field(default_factory=dict)  # только ✓/✓✓: {"python_core": "strong"}
    is_fullstack: bool = False

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["user_id"] = str(self.user_id)
        return d


def _modules(q: Qualification) -> dict[str, ModuleEstimate]:
    out: dict[str, ModuleEstimate] = {}
    for m, s in (q.subscores or {}).items():
        if not isinstance(s, dict) or s.get("theta") is None:
            continue
        out[m] = ModuleEstimate(
            module=m,
            name=str(s.get("name", m)),
            n=int(s.get("n", 0)),
            theta=float(s["theta"]),
            se=float(s.get("se", 0.0)),
            tag=str(s.get("tag", "none")),
            symbol=str(s.get("symbol", "")),
        )
    return out


def _snapshot_of(user_id: uuid.UUID, q: Qualification, now: datetime) -> QualificationSnapshot:
    rules = load_rules()

    class _At:
        def now(self) -> datetime:
            return now

    at = _At()
    h = q.handoff(at)
    fresh = freshness_of(q.last_activity_at, now, rules) if q.last_activity_at else None
    confirmed = bool(h.get("confirmed"))
    return QualificationSnapshot(
        user_id=user_id,
        specialization=q.spec,
        status=str(q.status),
        confirmed=confirmed,
        grade=q.grade if confirmed else None,
        category=h.get("category") if confirmed else None,
        theta=q.theta,
        se=q.se,
        se_t=q.se_now(at, rules),
        score=round(load_scale().score(q.theta)) if q.theta is not None else None,
        p_pass=q.p_pass,
        trust=q.trust,
        evidence_weight=float(h.get("evidence_weight", 0.0)) if confirmed else 0.0,
        borderline=bool(q.borderline) if confirmed else False,
        visible=confirmed and not q.hidden,
        position_in_grade=h.get("position_in_grade") if confirmed else None,
        confirmed_at=q.confirmed_at,
        last_activity_at=q.last_activity_at,
        relevance=fresh["relevance"] if fresh else None,
        freshness_band=fresh["band"] if fresh else None,
        modules=_modules(q),
        module_tags=dict(q.module_tags),
    )


@overload
def get_qualification_snapshot(
    db: Session, user_id: uuid.UUID, specialization: str, *, now: datetime | None = None
) -> QualificationSnapshot | None: ...


@overload
def get_qualification_snapshot(
    db: Session, user_id: uuid.UUID, specialization: None = None, *, now: datetime | None = None
) -> list[QualificationSnapshot]: ...


def get_qualification_snapshot(
    db: Session, user_id: uuid.UUID, specialization: str | None = None, *, now: datetime | None = None
) -> QualificationSnapshot | list[QualificationSnapshot] | None:
    """Снимок квалификации для подбора. Только чтение, без блокировок.

    specialization=None → снимки по всем квалификациям кандидата (без производной Fullstack)."""
    now = now or clock.now()
    if specialization is None:
        return [_snapshot_of(user_id, load_domain(r), now) for r in list_rows(db, user_id)]
    derived = _derived_from(specialization)
    if derived:
        return _fullstack_snapshot(db, user_id, now, derived, specialization)
    row = get_row(db, user_id, specialization)
    if row is None:
        return None
    return _snapshot_of(user_id, load_domain(row), now)


def _derived_from(spec: str) -> list[str]:
    info = next((s for s in load_config("taxonomy")["specializations"] if s["code"] == spec), None)
    if info is None or info.get("status") != "derived":
        return []
    return list(info.get("derived_from") or ())


def _fullstack_snapshot(
    db: Session, user_id: uuid.UUID, now: datetime, parts_of: list[str], spec: str
) -> QualificationSnapshot | None:
    """Fullstack — производная категория (concept §2.1): обе квалификации, грейд = меньший из двух."""
    parts = [get_qualification_snapshot(db, user_id, s, now=now) for s in parts_of]
    if len(parts) != 2 or any(p is None or not p.confirmed for p in parts):
        return None
    b, f = parts  # type: ignore[misc]
    assert b is not None and f is not None
    grade = fullstack_grade(b.grade, f.grade)
    base = b if b.grade == grade else f
    trust = min(b.trust, f.trust) if b.trust is not None and f.trust is not None else None
    return QualificationSnapshot(
        **{
            **{k: getattr(base, k) for k in QualificationSnapshot.__dataclass_fields__},
            "specialization": spec,
            "grade": grade,
            "category": category_of(spec, grade) if grade else None,
            "visible": b.visible and f.visible,
            "trust": trust,
            "evidence_weight": round(load_rules().evidence_weight_factor * trust, 4) if trust is not None else 0.0,
            "modules": {**b.modules, **f.modules},
            "module_tags": {**b.module_tags, **f.module_tags},
            "is_fullstack": True,
        }
    )


def list_rows(db: Session, user_id: uuid.UUID) -> list[QualificationRow]:
    return list(
        db.scalars(
            select(QualificationRow)
            .where(QualificationRow.candidate_id == user_id)
            .order_by(QualificationRow.created_at, QualificationRow.specialization)
        )
    )


def grade_changes(db: Session, user_id: uuid.UUID, spec: str) -> list[GradeChangeRow]:
    return list(
        db.scalars(
            select(GradeChangeRow)
            .where(GradeChangeRow.candidate_id == user_id, GradeChangeRow.specialization == spec)
            .order_by(GradeChangeRow.at, func.coalesce(GradeChangeRow.to_grade, ""))
        )
    )
