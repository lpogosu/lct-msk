"""Доверие и безопасность: метка «Проверенная компания» и жалобы кандидатов с очередью модерации.

Метка добровольная и ничего не блокирует (config/trust.yaml, docs/TRUST_AND_SAFETY.md).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, UUIDPkMixin


class CompanyVerification(Base):
    """Результат проверки реквизитов компании (одна запись на компанию; повторная подача перезаписывает).

    level: none | requisites | domain. Отзыв методистом/админом — revoked_at != NULL: уровень «none» для всех экранов,
    подать заново с теми же данными нельзя до снятия отзыва (иначе отзыв бессмыслен).
    """

    __tablename__ = "company_verifications"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    inn: Mapped[str] = mapped_column(String(12), index=True)
    ogrn: Mapped[str] = mapped_column(String(15))
    level: Mapped[str] = mapped_column(String(16), default="none")
    checks: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    registry_name: Mapped[str | None] = mapped_column(String(300))
    submitted_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoked_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))
    revoke_note: Mapped[str | None] = mapped_column(String(300))


class TrustComplaint(UUIDPkMixin, CreatedAtMixin, Base):
    """Жалоба кандидата на приглашение или вакансию. Компания о ней не уведомляется (защита кандидата)."""

    __tablename__ = "trust_complaints"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    reporter_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    target_type: Mapped[str] = mapped_column(String(16))  # invitation | vacancy
    target_id: Mapped[uuid.UUID]
    reason: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(16), default="open")  # open | upheld | dismissed
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolved_at: Mapped[datetime | None]
    resolution_note: Mapped[str | None] = mapped_column(String(300))

    __table_args__ = (
        UniqueConstraint("reporter_id", "target_type", "target_id", name="uq_trust_complaints_once"),
        Index("ix_trust_complaints_status_created", "status", "created_at"),
    )
