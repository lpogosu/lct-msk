"""Тестирование (spec-testing §4): параметры семейств, экземпляры-клоны, сессии CAT, ответы,
события, флаги, квалификации, смены грейда, дрейф, калибровка, прогоны валидации.

Специализации, модули и blueprint — из config/taxonomy.yaml (таблиц нет, храним коды).
Семейства заданий — код в app/testing/bank (id семейства — строка, например "py.slicing.step").
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import (
    JSONB_EMPTY_ARRAY,
    JSONB_EMPTY_OBJECT,
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPkMixin,
)
from app.models.enums import (
    ACTIVE_SESSION_STATUSES,
    EXCLUSIVE_SESSION_KINDS,
    FamilyStatus,
    FlagStatus,
    QualificationStatus,
    SessionStatus,
)


def _sql_in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)  # StrEnum форматируется своим значением


class ItemFamilyParams(Base):
    """Параметры семейства на шкале специализации. Общие модули (http_api, algo) — своя b на каждой шкале."""

    __tablename__ = "item_family_params"

    family_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    specialization: Mapped[str] = mapped_column(String(32), primary_key=True)
    module: Mapped[str] = mapped_column(String(32), index=True)
    b: Mapped[float]
    sigma_b: Mapped[float]
    is_anchor: Mapped[bool] = mapped_column(default=False, server_default=false())  # b якоря не обновляется
    status: Mapped[str] = mapped_column(String(16), default=FamilyStatus.operational.value)
    source: Mapped[str] = mapped_column(String(16), default="prior")  # prior (эксперты) | map (уточнено)
    n_responses: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    r_pb: Mapped[float | None]
    cusum_s: Mapped[float] = mapped_column(default=0.0, server_default=text("0"))
    exposure_30d: Mapped[float] = mapped_column(default=0.0, server_default=text("0"))
    params_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    calibrated_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())


class ItemInstance(UUIDPkMixin, CreatedAtMixin, Base):
    """Одноразовый экземпляр-клон. Выдаётся одному человеку и никогда не повторяется.

    key_enc — ключ (зашифрован/сериализован модулем тестирования); key_hash — для детектора
    «чужого ключа» (ответ совпал с ключом экземпляра, выданного другому аккаунту).
    """

    __tablename__ = "item_instances"

    family_id: Mapped[str] = mapped_column(String(64))
    family_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    seed: Mapped[int] = mapped_column(BigInteger)
    rendered: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    key_enc: Mapped[str] = mapped_column(Text)
    key_hash: Mapped[str] = mapped_column(String(64), index=True)
    distractors: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    explanation: Mapped[str | None] = mapped_column(Text)
    variant_code: Mapped[str] = mapped_column(String(8))
    issued_to_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_sessions.id", ondelete="SET NULL"))
    issued_to_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    issued_at: Mapped[datetime | None]

    __table_args__ = (
        UniqueConstraint("family_id", "seed"),
        # Выбор следующего невыданного экземпляра: FOR UPDATE SKIP LOCKED по этому индексу
        Index("ix_item_instances_unissued", "family_id", "seed", postgresql_where=text("issued_at IS NULL")),
    )


class TestSession(UUIDPkMixin, CreatedAtMixin, Base):
    """Сессия адаптивного теста: CREATED → IN_PROGRESS ⇄ PAUSED → DECIDED → … (spec-testing §2.4)."""

    __tablename__ = "test_sessions"
    __test__ = False  # не путать pytest: класс называется Test*

    candidate_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    specialization: Mapped[str] = mapped_column(String(32))
    blueprint_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    params_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    kind: Mapped[str] = mapped_column(String(16))
    parent_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_sessions.id", ondelete="SET NULL"))
    claimed_grade: Mapped[str] = mapped_column(String(16))
    target_cut: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(24), default=SessionStatus.created.value)
    decision: Mapped[str | None] = mapped_column(String(24))
    theta: Mapped[float | None]
    se: Mapped[float | None]
    p_pass: Mapped[float | None]
    class_probs: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    n_items: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    trust: Mapped[float | None]
    seed: Mapped[int] = mapped_column(BigInteger)  # seed выбора заданий (воспроизводимая трасса)
    state: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)  # состояние движка
    logging_consent: Mapped[bool] = mapped_column(default=False, server_default=false())
    demo_assisted: Mapped[bool] = mapped_column(default=False, server_default=false())  # DECISIONS п. 4
    started_at: Mapped[datetime | None]
    paused_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]

    __table_args__ = (
        # «Одна активная сессия на аккаунт» (spec-testing §2.5) — гарантия на уровне БД
        Index(
            "uq_test_sessions_one_active",
            "candidate_id",
            unique=True,
            postgresql_where=text(
                f"status IN ({_sql_in(ACTIVE_SESSION_STATUSES)}) AND kind IN ({_sql_in(EXCLUSIVE_SESSION_KINDS)})"
            ),
        ),
        # Значения статуса — словарь app/testing (заглавными). Другой регистр/опечатка обходили бы индекс выше
        CheckConstraint(f"status IN ({_sql_in(tuple(SessionStatus))})", name="status"),
    )


class TestResponse(UUIDPkMixin, Base):
    """Ответ на задание сессии + трасса оценки и причина выбора (экран Л1)."""

    __tablename__ = "test_responses"
    __test__ = False

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_sessions.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(SmallInteger)
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("item_instances.id", ondelete="RESTRICT"), index=True)
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    module: Mapped[str] = mapped_column(String(32))
    is_pretest: Mapped[bool] = mapped_column(default=False, server_default=false())
    shown_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    deadline_at: Mapped[datetime | None]
    answered_at: Mapped[datetime | None]
    rt_ms: Mapped[int | None] = mapped_column(Integer)
    answer: Mapped[Any | None] = mapped_column(JSONB)
    is_correct: Mapped[bool | None]
    distractor_tag: Mapped[str | None] = mapped_column(String(64))
    theta_after: Mapped[float | None]
    se_after: Mapped[float | None]
    p_pass_after: Mapped[float | None]
    selection_reason: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)

    __table_args__ = (UniqueConstraint("session_id", "seq"),)


class SessionEvent(Base):
    """События клиента во время теста (журналируются только с согласием test_logging)."""

    __tablename__ = "session_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True)
    ts: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    type: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)


class Flag(UUIDPkMixin, CreatedAtMixin, Base):
    """Флаг целостности сессии. Работодатель флагов не видит; кандидат видит свои и может оспорить."""

    __tablename__ = "flags"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    severity: Mapped[str] = mapped_column(String(8))
    value: Mapped[float | None]
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_sessions.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    status: Mapped[str] = mapped_column(String(16), default=FlagStatus.open.value)
    dispute_text: Mapped[str | None] = mapped_column(String(1000))
    disputed_at: Mapped[datetime | None]
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None]


class Qualification(UUIDPkMixin, TimestampMixin, Base):
    """Квалификация кандидата по специализации: NONE → … → CONFIRMED(g) (spec-testing §2.4)."""

    __tablename__ = "qualifications"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    specialization: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default=QualificationStatus.none.value)
    grade: Mapped[str | None] = mapped_column(String(16))
    theta: Mapped[float | None]
    se: Mapped[float | None]
    score: Mapped[float | None]  # S = 500 + 100·θ (в UI без «θ», DECISIONS п. 10)
    p_pass: Mapped[float | None]
    borderline: Mapped[bool] = mapped_column(default=False, server_default=false())
    trust: Mapped[float | None]
    under_review: Mapped[bool] = mapped_column(default=False, server_default=false())
    confirmed_at: Mapped[datetime | None]
    last_activity_at: Mapped[datetime | None]
    source_session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_sessions.id", ondelete="SET NULL"))
    onboarding_until: Mapped[datetime | None]  # первичный тест + доборы 24 ч не считаются сменой
    next_change_allowed_at: Mapped[datetime | None]
    next_attempt_allowed_at: Mapped[datetime | None]
    attempts_90d: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    subscores: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    module_tags: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    # Полное состояние доменной квалификации app.testing.session.Qualification (попытки, цепочка сессий,
    # проверки методистом, снимок для отката). Колонки выше — денормализация для чтения и фильтров.
    state: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)

    __table_args__ = (UniqueConstraint("candidate_id", "specialization"),)


class GradeChange(UUIDPkMixin, Base):
    """История смен грейда (принудительного понижения не бывает — ТЗ)."""

    __tablename__ = "grade_changes"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    specialization: Mapped[str] = mapped_column(String(32))
    from_grade: Mapped[str | None] = mapped_column(String(16))
    to_grade: Mapped[str | None] = mapped_column(String(16))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("test_sessions.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    reason: Mapped[str] = mapped_column(String(32))  # initial | upgrade | voluntary_downgrade | ext_down | ...
    counts_as_change: Mapped[bool] = mapped_column(default=True, server_default=text("true"))


class DriftEvent(UUIDPkMixin, Base):
    """Срабатывание CUSUM дрейфа семейства → карантин (концепция §5.2)."""

    __tablename__ = "drift_events"

    family_id: Mapped[str] = mapped_column(String(64), index=True)
    specialization: Mapped[str | None] = mapped_column(String(32))
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    statistic: Mapped[str] = mapped_column(String(32))
    value: Mapped[float]
    action: Mapped[str] = mapped_column(String(32))


class CalibrationRun(UUIDPkMixin, Base):
    """Прогон MAP-уточнения b (фаза 1) — ночная задача или кнопка в лаборатории."""

    __tablename__ = "calibration_runs"

    phase: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    n_responses: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    families_updated: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    report: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    params_version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class ValidationRun(UUIDPkMixin, CreatedAtMixin, Base):
    """Результат `make validate` (концепция §7): тот же код движков, фиксированные seed."""

    __tablename__ = "validation_runs"

    kind: Mapped[str] = mapped_column(String(32), index=True)
    config: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    seeds: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    metrics: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    git_sha: Mapped[str | None] = mapped_column(String(40))
