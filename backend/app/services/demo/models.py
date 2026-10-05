"""Таблицы демо-режима и админ-инструментов жюри (миграция 0007_demo).

Модели лежат здесь, а не в app/models: демо — отдельный раздел, который можно отключить целиком, и в
`app.models.__all__` (41 таблица платформы) он не входит. Импорт модуля регистрирует таблицы в Base.metadata;
его делают оба роутера (app/api/demo.py, app/api/admin.py), поэтому любое приложение их видит.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, ForeignKey, Index, SmallInteger, String, false, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, UUIDPkMixin


class DemoPersona(Base):
    """Роль аккаунта в демо-мире: персона сценария (Анна, Илья), ступенчатый кандидат, сотрудник компании,
    служебный логин (жюри, методист, админ) или синтетический кандидат автоответчика.

    profile — параметры автоответчика и подсказки интерфейсу; для синтетического кандидата здесь причина «Нет»
    (G2: «Нет» с причиной из персоны). Метку «синтетический кандидат» в Р6 берёт интерфейс отсюда (kind).
    """

    __tablename__ = "demo_personas"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # persona | staged | synthetic | employer | staff
    key: Mapped[str | None] = mapped_column(String(64), index=True)
    profile: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    __table_args__ = (
        CheckConstraint("kind IN ('persona', 'staged', 'synthetic', 'employer', 'staff')", name="kind"),
    )


class DemoState(Base):
    """Одна строка (id = 1): флаги демо-режима и отметки сборки мира.

    scenario_built_at — момент по Clock, когда seed/reset закончил сценарий: автоответчик отвечает только на
    приглашения, отправленные ПОСЛЕ него (заранее заготовленные статусы «отправлено», «встречное» не трогаем).
    """

    __tablename__ = "demo_state"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    jury_mode: Mapped[bool] = mapped_column(default=False, server_default=false())
    jury_mode_since: Mapped[datetime | None]
    autoresponder: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    seeded_at: Mapped[datetime | None]
    scenario_built_at: Mapped[datetime | None]
    seed_version: Mapped[str | None] = mapped_column(String(32))
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)


class JuryBatch(UUIDPkMixin, CreatedAtMixin, Base):
    """Один импорт жюри (POST /api/admin/import): брифы, кандидаты и размеченные пары одной загрузки."""

    __tablename__ = "jury_batches"

    name: Mapped[str] = mapped_column(String(200))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    n_briefs: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    n_candidates: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    n_unconfirmed: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    n_pairs: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    meta: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)


class JuryCandidate(Base):
    """Импортированный кандидат. payload — признаки подбора (CandidateFeatures как JSON) либо, если теста нет,
    заявленные специализация и грейд с навыками: такой кандидат уходит в группу «категория по опросу,
    не подтверждена» и ранжируется ниже подтверждённых (DECISIONS п. 5). В банк платформы не попадает."""

    __tablename__ = "jury_candidates"

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jury_batches.id", ondelete="CASCADE"), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    confirmed: Mapped[bool] = mapped_column(default=False, server_default=false())
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)


class JuryBrief(Base):
    __tablename__ = "jury_briefs"

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jury_batches.id", ondelete="CASCADE"), primary_key=True)
    brief_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)


class JuryPair(Base):
    """Размеченная пара «бриф × кандидат» (rel 0–3) — вход для метрик P@k и nDCG."""

    __tablename__ = "jury_pairs"

    batch_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("jury_batches.id", ondelete="CASCADE"), primary_key=True)
    brief_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    candidate_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rel: Mapped[int] = mapped_column(SmallInteger)

    __table_args__ = (
        CheckConstraint("rel BETWEEN 0 AND 3", name="rel"),
        Index("ix_jury_pairs_batch_brief", "batch_id", "brief_id"),
    )
