"""Связь с ФСП ID и кэш достижений из реестра (концепция §6.5).

Отвязка: строка FspLink и кэш достижений удаляются, событие пишется в журнал согласий.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, SmallInteger, String, UniqueConstraint, false, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, UUIDPkMixin
from app.models.enums import AchievementStatus, FspSyncStatus


class FspLink(UUIDPkMixin, CreatedAtMixin, Base):
    """fsp_id = claim `sub` из id_token Keycloak (realm fsp). Один ФСП ID — один аккаунт."""

    __tablename__ = "fsp_links"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), unique=True)
    fsp_id: Mapped[str] = mapped_column(String(128), unique=True)
    claims: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    is_demo: Mapped[bool] = mapped_column(default=False, server_default=false())  # «демо-вход ФСП»
    linked_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    last_sync_at: Mapped[datetime | None]
    sync_status: Mapped[str] = mapped_column(String(16), default=FspSyncStatus.pending.value)
    sync_error: Mapped[str | None] = mapped_column(String(500))


class FspAchievementCache(UUIDPkMixin, Base):
    """Нормализованная запись реестра (результат/разряд/роль). hidden — кандидат скрыл:
    скрытый результат не участвует в F (иначе скрытая накрутка, концепция §6.2)."""

    __tablename__ = "fsp_achievements"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    fsp_id: Mapped[str] = mapped_column(String(128))
    external_id: Mapped[str] = mapped_column(String(128))  # id записи в реестре
    kind: Mapped[str] = mapped_column(String(16))  # result | rank | role
    discipline: Mapped[str | None] = mapped_column(String(32))
    level: Mapped[str | None] = mapped_column(String(32))
    year: Mapped[int | None] = mapped_column(SmallInteger)
    ekp_number: Mapped[str | None] = mapped_column(String(16))  # номер ЕКП (только в выписке после раскрытия)
    place_from: Mapped[int | None]
    place_to: Mapped[int | None]
    field_size: Mapped[int | None]
    role: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), default=AchievementStatus.confirmed.value)
    hidden: Mapped[bool] = mapped_column(default=False, server_default=false())
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    fetched_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "external_id"),)
