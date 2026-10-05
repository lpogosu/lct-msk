"""Пользователи и аутентификация: User, EmailCode, RefreshToken, Consent, AuditLog."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Identity, Index, String, false, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin
from app.models.enums import ConsentPurpose, UserRole, UserStatus

if TYPE_CHECKING:
    from app.models.candidate import CandidateProfile
    from app.models.company import Company


def _in(values: Any) -> str:
    return ", ".join(f"'{v.value}'" for v in values)


class User(UUIDPkMixin, TimestampMixin, Base):
    """Аккаунт. Роль задаётся при регистрации; методист и админ — только сидом/админом."""

    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(254), unique=True)  # всегда в нижнем регистре
    email_domain: Mapped[str] = mapped_column(String(253), index=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16), default=UserStatus.pending_email.value)
    email_verified: Mapped[bool] = mapped_column(default=False, server_default=false())
    email_verified_at: Mapped[datetime | None]
    # NULL — аккаунт создан входом через ФСП ID и пароль ещё не задан (DECISIONS п. 12)
    password_hash: Mapped[str | None] = mapped_column(String(255))
    company_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("companies.id", ondelete="SET NULL"), index=True)
    last_login_at: Mapped[datetime | None]
    is_demo: Mapped[bool] = mapped_column(default=False, server_default=false())

    company: Mapped[Company | None] = relationship(back_populates="members", lazy="selectin")
    candidate_profile: Mapped[CandidateProfile | None] = relationship(back_populates="user", uselist=False, lazy="select")

    __table_args__ = (
        CheckConstraint(f"role IN ({_in(UserRole)})", name="role"),
        CheckConstraint(f"status IN ({_in(UserStatus)})", name="status"),
        CheckConstraint("email = lower(email)", name="email_lower"),
    )

    @property
    def is_active(self) -> bool:
        return self.status == UserStatus.active.value


class EmailCode(UUIDPkMixin, CreatedAtMixin, Base):
    """Одноразовый 6-значный код. Храним только HMAC; новый код гасит предыдущие (invalidated_at)."""

    __tablename__ = "email_codes"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    purpose: Mapped[str] = mapped_column(String(32))
    email: Mapped[str] = mapped_column(String(254))  # куда отправлен
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    sent_at: Mapped[datetime | None]  # NULL — письмо не ушло (SMTP-ошибка), в лимит отправок не считается
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    consumed_at: Mapped[datetime | None]
    invalidated_at: Mapped[datetime | None]

    __table_args__ = (Index("ix_email_codes_user_purpose_created", "user_id", "purpose", "created_at"),)


class RefreshToken(UUIDPkMixin, CreatedAtMixin, Base):
    """Refresh-токен (в БД только sha256). family_id — цепочка ротаций одного входа."""

    __tablename__ = "refresh_tokens"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[uuid.UUID] = mapped_column(index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    revoked_reason: Mapped[str | None] = mapped_column(String(32))  # rotated | logout | reuse_detected | ...
    replaced_by_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("refresh_tokens.id", ondelete="SET NULL"))
    user_agent: Mapped[str | None] = mapped_column(String(512))
    ip: Mapped[str | None] = mapped_column(String(64))


class Consent(UUIDPkMixin, Base):
    """Журнал согласий (152-ФЗ): каждое согласие — отдельная запись с версией текста.

    Отзыв не удаляет запись, а ставит revoked_at; повторное согласие — новая запись.
    scope — уточнение: для contact_disclosure {"company_id", "basis", "basis_id"}.
    """

    __tablename__ = "consents"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    purpose: Mapped[str] = mapped_column(String(32))
    text_version: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    revoked_at: Mapped[datetime | None]
    scope: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    ip: Mapped[str | None] = mapped_column(String(64))
    user_agent: Mapped[str | None] = mapped_column(String(512))

    __table_args__ = (
        CheckConstraint(f"purpose IN ({_in(ConsentPurpose)})", name="purpose"),
        Index("ix_consents_active", "user_id", "purpose", postgresql_where=text("revoked_at IS NULL")),
    )

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class AuditLog(Base):
    """Журнал событий безопасности и значимых действий (вход, выдача токенов, согласия, раскрытия)."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now(), index=True)
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor_role: Mapped[str | None] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(64), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(64))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
