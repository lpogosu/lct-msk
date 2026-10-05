"""Компания-работодатель, журнал вебхуков ATS, снимки надёжности."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, String, Text, false, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import utcnow
from app.db.base import JSONB_EMPTY_OBJECT, Base, CreatedAtMixin, TimestampMixin, UUIDPkMixin
from app.models.enums import WebhookStatus

if TYPE_CHECKING:
    from app.models.users import User


class Company(UUIDPkMixin, TimestampMixin, Base):
    """Компания. Создаётся при подтверждении email работодателя (концепция §3.2 шаг 1).

    Корпоративный домен → одна компания на домен (квоты и надёжность по домену).
    Личный домен (gmail, mail.ru, …) → своя компания на аккаунт с пометкой «личный email»
    (DECISIONS п. 8: квота на аккаунт). Профиль (Р1) заполняется позже; пока name пусто,
    отправлять приглашения нельзя.
    """

    __tablename__ = "companies"

    email_domain: Mapped[str] = mapped_column(String(253), index=True)
    personal_domain: Mapped[bool] = mapped_column(default=False, server_default=false())
    name: Mapped[str | None] = mapped_column(String(200))
    field: Mapped[str | None] = mapped_column(String(200))  # направление деятельности
    description: Mapped[str | None] = mapped_column(Text)
    # {"email": "...", "telegram": "@...", "phone": "+7..."} — показываются кандидату до решения
    contact_methods: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    inn: Mapped[str | None] = mapped_column(String(12))  # необязательно; сверка по ЕГРЮЛ — концепция §8
    website: Mapped[str | None] = mapped_column(String(300))
    webhook_url: Mapped[str | None] = mapped_column(String(500))  # ATS [доп.], §8.2
    webhook_secret: Mapped[str | None] = mapped_column(String(128))  # ключ HMAC-SHA256 подписи событий
    profile_completed_at: Mapped[datetime | None]
    is_demo: Mapped[bool] = mapped_column(default=False, server_default=false())

    members: Mapped[list[User]] = relationship(back_populates="company")

    __table_args__ = (
        Index(
            "uq_companies_corporate_domain",
            "email_domain",
            unique=True,
            postgresql_where=text("NOT personal_domain"),
        ),
    )


class WebhookDelivery(UUIDPkMixin, CreatedAtMixin, Base):
    """Журнал доставок вебхуков ATS (концепция §8.2: последние 20, код ответа). event_id — идемпотентность."""

    __tablename__ = "webhook_deliveries"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[uuid.UUID] = mapped_column(unique=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64))  # invitation.accepted, application.submitted, ...
    target_url: Mapped[str] = mapped_column(String(500))
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    status: Mapped[str] = mapped_column(String(16), default=WebhookStatus.pending.value)
    attempts: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    response_code: Mapped[int | None]
    response_excerpt: Mapped[str | None] = mapped_column(String(500))
    last_attempt_at: Mapped[datetime | None]
    next_attempt_at: Mapped[datetime | None]


class ReliabilitySnapshot(UUIDPkMixin, Base):
    """Снимок надёжности R = (5·0.8 + good)/(5 + good + bad) с разложением (концепция §4.3)."""

    __tablename__ = "reliability_snapshots"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    computed_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    r: Mapped[float]
    good: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    bad: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    breakdown: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
