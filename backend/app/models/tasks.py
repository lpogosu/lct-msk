"""Регулярные короткие задания [доп.] (концепция §5.4, spec-testing §4)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, SmallInteger, String, Text, UniqueConstraint, false, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin
from app.models.enums import AssignmentStatus, EmployerTaskStatus


class EmployerTask(UUIDPkMixin, TimestampMixin, Base):
    """Задача работодателя: тип A (catalog — семейство из каталога, свой экземпляр, автопроверка)
    или тип B (approach — «предложи подход», рубрика 4×0–2). company_id NULL — тренировочная от платформы."""

    __tablename__ = "employer_tasks"

    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    family_id: Mapped[str | None] = mapped_column(String(64))  # тип A
    prompt: Mapped[str | None] = mapped_column(Text)  # тип B
    rubric: Mapped[dict[str, Any] | None]
    audience: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    tags: Mapped[list[str]] = mapped_column(JSONB, default=list, server_default=text("'[]'::jsonb"))
    time_limit_min: Mapped[int] = mapped_column(SmallInteger)
    status: Mapped[str] = mapped_column(String(16), default=EmployerTaskStatus.draft.value)
    is_platform: Mapped[bool] = mapped_column(default=False, server_default=false())
    not_real_work_ack: Mapped[bool] = mapped_column(default=False, server_default=false())  # «это не рабочая задача»
    module: Mapped[str | None] = mapped_column(String(32))  # модуль теста семейства (тип A)
    # «Проверочный набор по вакансии» (DECISIONS 3): задачи собраны под must-навыки брифа для шортлиста
    need_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("needs.id", ondelete="SET NULL"), index=True)
    is_check_set: Mapped[bool] = mapped_column(default=False, server_default=false())


class TaskAssignment(UUIDPkMixin, CreatedAtMixin, Base):
    """Назначение задачи кандидату; для типа A — свой одноразовый экземпляр."""

    __tablename__ = "task_assignments"

    employer_task_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("employer_tasks.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    instance_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("item_instances.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(24), default=AssignmentStatus.offered.value)
    answer: Mapped[Any | None] = mapped_column(JSONB)
    score: Mapped[float | None]
    rubric_scores: Mapped[dict[str, Any] | None]
    reviewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    offered_at: Mapped[datetime | None]
    opened_at: Mapped[datetime | None]
    submitted_at: Mapped[datetime | None]
    reviewed_at: Mapped[datetime | None]
    due_at: Mapped[datetime | None]
    # --- Что нужно подбору (свежесть A, доказательство модуля в K): балл, модуль и момент завершения ---
    kind: Mapped[str | None] = mapped_column(String(16))  # catalog (тип A) | approach (тип B)
    module: Mapped[str | None] = mapped_column(String(32))
    family_id: Mapped[str | None] = mapped_column(String(64))
    is_correct: Mapped[bool | None]
    finished_at: Mapped[datetime | None]  # автопроверка (A) или оценка работодателя (B)
    skipped_at: Mapped[datetime | None]
    source: Mapped[str] = mapped_column(String(16), default="offer", server_default=text("'offer'"))  # offer | check_set
    need_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("needs.id", ondelete="SET NULL"))
    review_comment: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("employer_task_id", "candidate_id"),
        Index("ix_task_assignments_candidate_finished", "candidate_id", "finished_at"),
    )
