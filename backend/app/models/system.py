"""Служебные таблицы: смещение демо-часов (только DEMO_MODE)."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, SmallInteger, func, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import Base


class DemoClock(Base):
    """Одна строка (id = 1): смещение Clock в секундах, переживает перезапуск бэкенда.
    Кнопки «+1 день / +3 дня» и «сбросить демо» (концепция §9.4, экран «Д»)."""

    __tablename__ = "demo_clock"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, default=1)
    offset_seconds: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)
