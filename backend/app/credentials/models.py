"""Реестр выданных паспортов навыков. Подписанный документ хранится целиком (скачивание «ещё раз»),
но в нём нет ПДн сверх выбора кандидата (имя — только если он его включил). Удаление аккаунта удаляет записи:
проверка такого файла вернёт «в реестре выдачи не найден» — паспорт перестаёт быть действительным."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Integer, Sequence, String, false
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base, CreatedAtMixin, UUIDPkMixin


class IssuedCredential(UUIDPkMixin, CreatedAtMixin, Base):
    __tablename__ = "issued_credentials"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(40), unique=True)  # код для /v/{code}
    kind: Mapped[str] = mapped_column(String(16))  # category | fsp
    title: Mapped[str] = mapped_column(String(200))
    credential_id: Mapped[str] = mapped_column(String(120), unique=True)  # поле id внутри VC (urn:uuid:…)
    status_index: Mapped[int] = mapped_column(Integer, Sequence("credential_status_idx"), unique=True)
    document: Mapped[dict[str, Any]]  # подписанный VC целиком
    include_name: Mapped[bool] = mapped_column(default=False, server_default=false())
    valid_until: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
