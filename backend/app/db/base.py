"""Декларативная база ORM: соглашение об именах ограничений и общие миксины.

Соглашение об именах нужно Alembic: без него автогенерация не может стабильно
переименовывать/удалять безымянные ограничения.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, MetaData, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.clock import utcnow

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
    type_annotation_map = {
        datetime: DateTime(timezone=True),
        dict[str, Any]: JSONB,
        list[Any]: JSONB,
    }


class UUIDPkMixin:
    """UUID-ключ: не перечисляется подбором (дополнительная защита от IDOR поверх проверки владельца)."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4, server_default=func.gen_random_uuid())


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())


# Серверные умолчания для JSONB, чтобы сырые INSERT (seed, импорт) не падали на NOT NULL.
JSONB_EMPTY_OBJECT = text("'{}'::jsonb")
JSONB_EMPTY_ARRAY = text("'[]'::jsonb")
