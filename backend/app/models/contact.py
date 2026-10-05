"""Контакт (spec-contact §2.3, §4): приглашение-контракт, снимки условий, журнал переходов,
раскрытие контактов (отдельное согласие), вакансии и отклики, обратная связь, паузы."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utcnow
from app.db.base import (
    JSONB_EMPTY_ARRAY,
    JSONB_EMPTY_OBJECT,
    Base,
    CreatedAtMixin,
    TimestampMixin,
    UUIDPkMixin,
)
from app.models.enums import (
    PAIR_ACTIVE_INVITATION_STATUSES,
    ApplicationStatus,
    InvitationStatus,
    VacancyStatus,
)


def _sql_in(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{v}'" for v in values)


class Vacancy(UUIDPkMixin, TimestampMixin, Base):
    """Опубликованный бриф [доп.] (концепция §3.2 шаг 11). test_profile — «как мы проверим» (spec-testing §4)."""

    __tablename__ = "vacancies"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    need_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("needs.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    specialization: Mapped[str | None] = mapped_column(String(32))
    grade: Mapped[str | None] = mapped_column(String(16))
    salary_from: Mapped[int] = mapped_column(Integer)
    salary_to: Mapped[int] = mapped_column(Integer)
    salary_net: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    format: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(16), default=VacancyStatus.draft.value)
    published_at: Mapped[datetime | None]
    test_profile: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)

    __table_args__ = (CheckConstraint("salary_to >= salary_from", name="salary_range"),)


class Invitation(UUIDPkMixin, TimestampMixin, Base):
    """Приглашение — ядро механики контакта. Переходы — таблица (from, action, actor) → to,
    каждый пишется в InvitationEvent; сроки применяются лениво при чтении (концепция §4.3)."""

    __tablename__ = "invitations"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    sender_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    need_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("needs.id", ondelete="SET NULL"), index=True)
    vacancy_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("vacancies.id", ondelete="SET NULL"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    batch_id: Mapped[uuid.UUID | None]
    pseudonym: Mapped[str] = mapped_column(String(16))  # «К-4821» = HMAC(secret, company‖candidate)
    status: Mapped[str] = mapped_column(String(24), default=InvitationStatus.sent.value)
    current_terms_ver: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    respond_by: Mapped[datetime]
    viewed_at: Mapped[datetime | None]
    responded_at: Mapped[datetime | None]
    counter_respond_by: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    contact_due_at: Mapped[datetime | None]  # accepted_at + 3 рабочих дня
    contacted_at: Mapped[datetime | None]
    disputed_at: Mapped[datetime | None]  # «со мной не связались»
    decline_reason: Mapped[str | None] = mapped_column(String(32))
    decline_comment: Mapped[str | None] = mapped_column(String(300))
    withdraw_reason: Mapped[str | None] = mapped_column(String(300))
    outcome: Mapped[str | None] = mapped_column(String(16))  # interview | hired | not_matched
    outcome_reason: Mapped[dict[str, Any] | None]
    score_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    rank_at_send: Mapped[int | None] = mapped_column(SmallInteger)
    why_you: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    auto_disclose_on_counter: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    flags: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)  # «требует осторожности», ...

    __table_args__ = (
        # «Одно активное приглашение на пару компания—кандидат» (концепция §4.3); accepted тоже занимает пару
        Index(
            "uq_invitations_active_pair",
            "company_id",
            "candidate_id",
            unique=True,
            postgresql_where=text(f"status IN ({_sql_in(PAIR_ACTIVE_INVITATION_STATUSES)})"),
        ),
        Index("ix_invitations_company_created", "company_id", "created_at"),
    )


class OfferTerms(UUIDPkMixin, CreatedAtMixin, Base):
    """Неизменяемый снимок условий (v1 — работодатель, v2 — после принятого встречного). sha256 — антифейк §8.1."""

    __tablename__ = "offer_terms"

    invitation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invitations.id", ondelete="CASCADE"))
    ver: Mapped[int] = mapped_column(SmallInteger)
    author: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text)
    salary_from: Mapped[int] = mapped_column(Integer)
    salary_to: Mapped[int] = mapped_column(Integer)
    salary_net: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    format: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(120))
    start_date: Mapped[date | None]
    contact_method: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    conditions: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    sha256: Mapped[str] = mapped_column(String(64))

    __table_args__ = (UniqueConstraint("invitation_id", "ver"),)


class InvitationEvent(Base):
    """Журнал переходов приглашения (from → to, кто, когда, полезная нагрузка)."""

    __tablename__ = "invitation_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    invitation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invitations.id", ondelete="CASCADE"), index=True)
    from_status: Mapped[str | None] = mapped_column(String(24))
    to_status: Mapped[str] = mapped_column(String(24))
    action: Mapped[str] = mapped_column(String(32))
    actor_role: Mapped[str] = mapped_column(String(16))  # employer | candidate | system
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)


class ContactDisclosure(UUIDPkMixin, Base):
    """Раскрытие контактов кандидата компании — только с отдельным согласием (152-ФЗ).

    CandidateDisclosed отдаётся работодателю только при активной записи (revoked_at IS NULL).
    """

    __tablename__ = "contact_disclosures"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    basis: Mapped[str] = mapped_column(String(16))  # invitation | application
    basis_id: Mapped[uuid.UUID]
    fields: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consents.id", ondelete="SET NULL"))
    consent_text_ver: Mapped[str] = mapped_column(String(32))
    granted_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    revoked_at: Mapped[datetime | None]

    __table_args__ = (
        Index("ix_contact_disclosures_pair", "candidate_id", "company_id"),
        Index("ix_contact_disclosures_consent", "consent_id"),
    )


class Application(UUIDPkMixin, TimestampMixin, Base):
    """Отклик кандидата на вакансию [доп.]: контакты видны сразу (согласие отдельное)."""

    __tablename__ = "applications"

    vacancy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("vacancies.id", ondelete="CASCADE"), index=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default=ApplicationStatus.submitted.value)
    consent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("consents.id", ondelete="SET NULL"))
    score_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    reject_reason: Mapped[str | None] = mapped_column(String(32))
    reject_comment: Mapped[str | None] = mapped_column(String(300))
    viewed_at: Mapped[datetime | None]
    responded_at: Mapped[datetime | None]
    sla_due_at: Mapped[datetime | None]  # 7 дней на ответ, иначе no_response

    __table_args__ = (UniqueConstraint("vacancy_id", "candidate_id"),)


class Feedback(UUIDPkMixin, CreatedAtMixin, Base):
    """Причина отказа/итога со ссылками на навыки брифа («каких навыков не хватило»)."""

    __tablename__ = "feedback"

    target_type: Mapped[str] = mapped_column(String(16))  # invitation | application
    target_id: Mapped[uuid.UUID] = mapped_column(index=True)
    direction: Mapped[str] = mapped_column(String(24))  # candidate_to_company | company_to_candidate
    reason_code: Mapped[str] = mapped_column(String(32))
    skill_ids: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    comment: Mapped[str | None] = mapped_column(String(300))


class OutcomeCheck(Base):
    """Через 10 дней после «да»: «условия совпали?» → надёжность R."""

    __tablename__ = "outcome_checks"

    invitation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("invitations.id", ondelete="CASCADE"), primary_key=True)
    asked_at: Mapped[datetime]
    answered_at: Mapped[datetime | None]
    answer: Mapped[str | None] = mapped_column(String(16))  # match | lower_salary | no_vacancy | other


class Cooldown(Base):
    """Пауза для пары компания—кандидат (30 дней после отказа, 14 после expired; разблокировка по зарплате)."""

    __tablename__ = "cooldowns"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    until: Mapped[datetime]
    reason: Mapped[str] = mapped_column(String(32))
    salary_unlock_from: Mapped[int | None] = mapped_column(Integer)
    source_invitation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("invitations.id", ondelete="SET NULL"))
    is_active: Mapped[bool] = mapped_column(default=True, server_default=text("true"))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow, onupdate=utcnow, server_default=func.now())

