"""Подбор (spec-contact §4): бриф (Need), неизменяемые версии подборки, шортлист,
просмотры карточек, журнал показов, кэш признаков кандидата и псевдонимы «в рамках компании»."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DDL,
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
    event,
    false,
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
from app.models.enums import NeedStatus


class Need(UUIDPkMixin, TimestampMixin, Base):
    """Потребность работодателя (бриф): текст + обязательная вилка (концепция §3.2 шаг 3).

    Пороги вилки (from ≥ 30 000, to ≤ 1,6·from, мягкое предупреждение до 2,0×) — в config/contact.yaml
    и проверяются сервисом; в БД только логический инвариант to ≥ from.
    """

    __tablename__ = "needs"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), index=True)
    author_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    title: Mapped[str | None] = mapped_column(String(200))
    raw_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=NeedStatus.draft.value)
    close_reason: Mapped[str | None] = mapped_column(String(16))  # filled | cancelled
    is_internship: Mapped[bool] = mapped_column(default=False, server_default=false())
    specialization: Mapped[str | None] = mapped_column(String(32))  # основная (после разбора/правки)
    grade: Mapped[str | None] = mapped_column(String(16))
    spec_probs: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    grade_probs: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    # [{"skill": "fastapi", "span": [s, e], "quote": "...", "source": "rule|llm|manual", "conf": 0.9}]
    must_skills: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    nice_skills: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    format: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(120))
    region_code: Mapped[str | None] = mapped_column(String(8))
    salary_from: Mapped[int] = mapped_column(Integer)
    salary_to: Mapped[int] = mapped_column(Integer)
    salary_net: Mapped[bool] = mapped_column(default=True, server_default=text("true"))  # на руки / до НДФЛ
    # [{"category": "backend_python:middle", "prob": 0.74, "kind": "primary|suggested|adjacent|manual", "selected": true}]
    categories: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    parsed: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)  # полный вывод парсера
    clarifying_question: Mapped[dict[str, Any] | None]
    parser_version: Mapped[str | None] = mapped_column(String(32))
    llm_used: Mapped[bool] = mapped_column(default=False, server_default=false())
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("need_versions.id", ondelete="SET NULL", use_alter=True)
    )

    __table_args__ = (CheckConstraint("salary_to >= salary_from", name="salary_range"),)


class NeedVersion(UUIDPkMixin, CreatedAtMixin, Base):
    """Неизменяемая версия подборки v1, v2, … Откат = перенос Need.current_version_id без пересчёта.

    items — полный ранжированный список (≤ 500): [{candidate_id, category, rank, score, components,
    reasons, risk, hidden_by: [...]}]. Фильтр никого не удаляет, а заполняет hidden_by (концепция §4.4 п. 5).
    """

    __tablename__ = "need_versions"

    need_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("needs.id", ondelete="CASCADE"))
    n: Mapped[int] = mapped_column(SmallInteger)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("need_versions.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    need_snapshot: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    filters: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    preset: Mapped[str] = mapped_column(String(32), default="balance")
    weights: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    scoring_config_version: Mapped[str] = mapped_column(String(64))
    items: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    stats: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)

    __table_args__ = (UniqueConstraint("need_id", "n"),)


# Версия подборки — до ~1 МБ JSON (≤ 500 строк с объяснениями). Сжатие TOAST lz4 вместо pglz вдвое удешевляет
# запись (p95 сборки ≤ 300 мс, spec-contact §6.2). Та же команда — в миграции 0004; без lz4 в сборке Postgres
# остаётся сжатие по умолчанию.
NEED_VERSIONS_LZ4 = DDL(
    "DO $$ BEGIN "
    "ALTER TABLE need_versions ALTER COLUMN items SET COMPRESSION lz4; "
    "ALTER TABLE need_versions ALTER COLUMN stats SET COMPRESSION lz4; "
    "EXCEPTION WHEN feature_not_supported OR invalid_parameter_value OR undefined_object THEN NULL; "
    "END $$"
)
event.listen(NeedVersion.__table__, "after_create", NEED_VERSIONS_LZ4.execute_if(dialect="postgresql"))


class Shortlist(Base):
    """Шортлист 📌 брифа — живёт отдельно от версий подборки (≤ 30 на бриф)."""

    __tablename__ = "shortlist_pins"

    need_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("needs.id", ondelete="CASCADE"), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    pinned_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    pinned_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())
    note: Mapped[str | None] = mapped_column(String(300))


class ProfileView(Base):
    """Просмотр слепой карточки (Р6). Условие «пригласить можно только того, чью карточку открывали»
    (DECISIONS п. 7: открыта панель Р6 ИЛИ кандидат в шортлисте)."""

    __tablename__ = "profile_views"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"))
    viewer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    need_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("needs.id", ondelete="SET NULL"))
    at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    __table_args__ = (Index("ix_profile_views_company_candidate", "company_id", "candidate_id"),)


class ImpressionLog(Base):
    """Показ кандидата в подборке: «как меня видят» (К15) и онлайн-метрики воронки."""

    __tablename__ = "impression_log"

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    need_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("needs.id", ondelete="CASCADE"))
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("need_versions.id", ondelete="CASCADE"))
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    rank: Mapped[int] = mapped_column(SmallInteger)
    shown_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())


class MatchingFeatures(Base):
    """Кэш признаков кандидата для подбора: строка на категорию (специализация; Fullstack — отдельной строкой).

    payload — сериализованный app.matching.scoring.CandidateFeatures с АБСОЛЮТНЫМИ отметками времени
    (дни «N дней назад» считаются при чтении по Clock) плюс данные для слепой карточки (обобщённая строка
    ФСП, полоса теста, свежесть). Строки пересобирает app/services/matching/features.py: по хукам
    инвалидации и по расхождению fingerprint источников (тест, профиль, согласия, ФСП, задачи).
    visible — кандидат виден работодателям (категория подтверждена, согласие на слепую карточку,
    режим поиска не «не ищу», нет проверки методистом, возраст ≥ 16).
    """

    __tablename__ = "matching_features"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    specialization: Mapped[str] = mapped_column(String(32), primary_key=True)
    grade: Mapped[str] = mapped_column(String(16))
    visible: Mapped[bool] = mapped_column(default=False, server_default=false())
    has_fsp: Mapped[bool] = mapped_column(default=False, server_default=false())
    payload: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    fingerprint: Mapped[str | None] = mapped_column(String(64))  # NULL — помечено устаревшим хуком
    computed_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    __table_args__ = (
        Index("ix_matching_features_category", "specialization", "grade", postgresql_where=text("visible")),
    )


class CandidateAlias(Base):
    """Как компания видит кандидата до раскрытия: псевдоним «К-4821» = HMAC(company‖candidate) и непрозрачная
    ссылка ref для API (DECISIONS п. 20). Свои у каждой компании — две компании не сопоставят карточки.
    Псевдоним сохраняется при первом показе, редкие совпадения внутри компании разрешаются unique_pseudonym().
    """

    __tablename__ = "candidate_aliases"

    company_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("companies.id", ondelete="CASCADE"), primary_key=True)
    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    ref: Mapped[str] = mapped_column(String(32))
    pseudonym: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(default=utcnow, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("company_id", "ref"),
        UniqueConstraint("company_id", "pseudonym"),
    )
