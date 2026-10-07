"""Таблицы чата и интеграции с ATS (миграция 0010_chat_ats): сообщения, API-ключи компании, настройки ATS, «Журнал ATS».

Не входят в app.models.__all__ (ядро платформы — 41 таблица), но импортируются пакетом, поэтому попадают в
Base.metadata для Alembic и тестов. Сервисы: app/services/chat, app/services/ats.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import JSONB_EMPTY_ARRAY, JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, UUIDPkMixin


class ChatMessage(UUIDPkMixin, CreatedAtMixin, Base):
    """Сообщение переписки по приглашению или отклику. read_at — когда его прочла противоположная сторона."""

    __tablename__ = "chat_messages"

    # Порядок сообщений и курсор «только новые»: время одинаково у сообщений в одну секунду (часы демо замораживаются)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    kind: Mapped[str] = mapped_column(String(12))  # invitation | application
    ref_id: Mapped[uuid.UUID]
    sender_role: Mapped[str] = mapped_column(String(10))  # employer | candidate
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    body: Mapped[str] = mapped_column(Text)
    read_at: Mapped[datetime | None]

    __table_args__ = (
        CheckConstraint("kind IN ('invitation', 'application')", name="kind"),
        CheckConstraint("sender_role IN ('employer', 'candidate')", name="sender_role"),
        Index("ix_chat_messages_thread", "kind", "ref_id", "seq"),
    )


class CompanyApiKey(UUIDPkMixin, CreatedAtMixin, Base):
    """API-ключ компании для /api/ats/v1. В БД — только sha256 секрета (ключ показывается один раз)."""

    __tablename__ = "company_api_keys"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    name: Mapped[str] = mapped_column(String(80))
    key_id: Mapped[str] = mapped_column(String(16), unique=True)  # публичная часть «vzk_<key_id>_<секрет>»
    key_hash: Mapped[str] = mapped_column(String(64))
    scopes: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    last_used_at: Mapped[datetime | None]
    revoked_at: Mapped[datetime | None]


class AtsSettings(Base):
    """Настройки ATS компании: тип (none | webhook | huntflow), адрес и зашифрованный токен (Fernet)."""

    __tablename__ = "ats_settings"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    ats_type: Mapped[str] = mapped_column(String(16), default="none", server_default=text("'none'"))
    base_url: Mapped[str | None] = mapped_column(String(300))
    account_id: Mapped[str | None] = mapped_column(String(32))
    token_enc: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=text("now()"))


class AtsLog(Base):
    """«Журнал ATS»: исходящие запросы адаптера (эмулятор — записанные, реальный режим — отправленные) и входящие
    обращения ATS к /api/ats/v1/.../status. idempotency_key — повтор того же запроса возвращает прежний ответ."""

    __tablename__ = "ats_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=text("now()"))
    direction: Mapped[str] = mapped_column(String(3))  # out | in
    mode: Mapped[str] = mapped_column(String(10))  # emulator | real | api
    kind: Mapped[str] = mapped_column(String(40))  # applicant.create, vacancy.attach, status.interview, check, ...
    method: Mapped[str | None] = mapped_column(String(8))
    url: Mapped[str | None] = mapped_column(String(500))
    request: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    status: Mapped[str] = mapped_column(String(12))  # recorded | sent | failed | applied | rejected | replayed
    response_code: Mapped[int | None]
    response: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    entity_type: Mapped[str | None] = mapped_column(String(16))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    idempotency_key: Mapped[str | None] = mapped_column(String(80))

    __table_args__ = (
        Index("ix_ats_log_company_created", "company_id", "created_at"),
        Index(
            "uq_ats_log_idem",
            "company_id",
            "idempotency_key",
            unique=True,
            postgresql_where=text("idempotency_key IS NOT NULL"),
        ),
    )
