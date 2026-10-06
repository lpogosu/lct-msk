"""Таблицы «Антиподсказчика» (миграция 0008_anticheat).

Лежат рядом с сервисом, а не в app.models: раздел можно отключить целиком, а `app.models.__all__` описывает
таблицы платформы. Импорт модуля регистрирует таблицы в Base.metadata (его делают хуки теста, сервисы и роутеры).

- AnticheatTwin — двойник экземпляра: текст для буфера, позиция изменённого литерала, ключи (шифрованные, как ключи
  экземпляров). Ключ двойника на клиент не уходит никогда: клиент получает только clipboard_text и render_id.
- AnticheatEvidence — одно наблюдение: ответ на задание с двойником и результат его сверки (совпал с ключом экрана,
  с ключом копии, с типичной ошибкой…). Контекст (время копирования, вставка в своё поле) собирается при чтении из
  журнала событий. Решение методиста по записи — review_*; автоматического бана нет.
- AnticheatSandboxRun — мини-тест «Попробуй списать» для жюри (DEMO_MODE): его двойники и результат; сессия
  помечена demo_assisted и в метрики не входит.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Float,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, UUIDPkMixin

REVIEW_OPEN = "open"
REVIEW_VERIFICATION = "verification_assigned"
REVIEW_DISMISSED = "dismissed"
REVIEW_STATUSES = (REVIEW_OPEN, REVIEW_VERIFICATION, REVIEW_DISMISSED)


class AnticheatTwin(Base):
    """Двойник экземпляра пула (строка на экземпляр). status = none — безопасного двойника нет (причина в reason)."""

    __tablename__ = "anticheat_twins"

    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("item_instances.id", ondelete="CASCADE"), primary_key=True)
    family_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(8))  # ok | none
    reason: Mapped[str | None] = mapped_column(String(48))
    twin_version: Mapped[str | None] = mapped_column(String(16))
    twin_key_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    # Всё остальное по двойнику (без ключей): класс, роль, литерал, гейты, тексты экрана и копии, ε
    data: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    # Шифртекст {key, twin_key, err_trace_keys} (keystore.encrypt_key): ключи и типичные ответы на копию
    secret_enc: Mapped[str | None] = mapped_column(Text)
    render_id: Mapped[str | None] = mapped_column(String(32))
    shown_in_session_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("test_sessions.id", ondelete="SET NULL"), index=True
    )  # где двойник выдан клиенту (счётчик twin_rate_per_session)
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class AnticheatEvidence(UUIDPkMixin, CreatedAtMixin, Base):
    """Ответ на задание с двойником: классификация относительно ключа экрана и ключа копии."""

    __tablename__ = "anticheat_evidence"

    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_sessions.id", ondelete="CASCADE"), index=True)
    response_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("test_responses.id", ondelete="CASCADE"))
    instance_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("item_instances.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(SmallInteger)
    family_id: Mapped[str] = mapped_column(String(64))
    variant_code: Mapped[str] = mapped_column(String(8))
    answer: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # correct | twin_trace | misconception | other
    matched: Mapped[str] = mapped_column(String(16))  # key | twin_key | twin_err | twin_token | distractor | none
    strength: Mapped[str | None] = mapped_column(String(8))  # exact | err_twin | token
    tag: Mapped[str | None] = mapped_column(String(64))  # тег заблуждения
    mutation_class: Mapped[str] = mapped_column(String(32))
    mutation_subclass: Mapped[str] = mapped_column(String(64))
    honest_p: Mapped[float] = mapped_column(Float)
    render_id: Mapped[str | None] = mapped_column(String(32))
    review_status: Mapped[str] = mapped_column(String(24), default=REVIEW_OPEN, server_default=REVIEW_OPEN, index=True)
    review_note: Mapped[str | None] = mapped_column(String(500))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]
    demo_assisted: Mapped[bool] = mapped_column(default=False, server_default=false())

    __table_args__ = (UniqueConstraint("response_id"), Index("ix_anticheat_evidence_created", "created_at"))


class AnticheatSandboxRun(Base):
    """Мини-тест «Попробуй списать»: двойники (с шифрованными ключами) и результат проверяющего."""

    __tablename__ = "anticheat_sandbox_runs"

    test_id: Mapped[str] = mapped_column(String(16), primary_key=True)
    reviewer_seed: Mapped[str] = mapped_column(String(32))
    items: Mapped[list[Any]] = mapped_column(default=list, server_default=text("'[]'::jsonb"))
    answers: Mapped[dict[str, Any] | None]
    copied: Mapped[list[Any] | None]
    result: Mapped[dict[str, Any] | None]
    demo_assisted: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    review_status: Mapped[str] = mapped_column(String(24), default=REVIEW_OPEN, server_default=REVIEW_OPEN)
    review_note: Mapped[str | None] = mapped_column(String(500))
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    reviewed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    scored_at: Mapped[datetime | None]
