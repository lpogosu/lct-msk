"""Кандидат: профиль и настройки приватности, ответы опроса, материализованные признаки для подбора."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import ForeignKey, Index, Integer, SmallInteger, String, Text, false, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.configs import load_config
from app.db.base import JSONB_EMPTY_ARRAY, JSONB_EMPTY_OBJECT, Base, TimestampMixin, UUIDPkMixin
from app.models.enums import SalaryType, SalaryVisibility, SearchMode

if TYPE_CHECKING:
    from app.models.users import User


def _default_weekly_invite_limit() -> int | None:
    """Концепция §3.1 п. 10: 3/5/10/∞ в неделю, по умолчанию 5 (config/contact.yaml → candidate_limits)."""
    return load_config("contact")["candidate_limits"]["default_weekly"]

def _disclosure_defaults() -> dict[str, bool]:
    """Видимость полей после раскрытия контактов (К13): поле → разрешено ли компании после «да».

    Словарь полей — тот же, что у модуля контакта (config/contact.yaml): поля с правилом
    `disclosed: if_allowed`, по умолчанию включены `disclosure.default_fields`. Свои имена ключей
    (contact_email, education) модуль контакта молча отбросил бы — email кандидата не раскрылся бы никогда.
    """
    cfg = load_config("contact")
    disclosable = [k for k, rule in cfg["visibility"]["fields"].items() if rule.get("disclosed") == "if_allowed"]
    defaults = set(cfg["disclosure"]["default_fields"])
    return {k: k in defaults for k in disclosable}


DEFAULT_FIELD_VISIBILITY: dict[str, bool] = _disclosure_defaults()

# Ключ видимости (словарь контакта) → колонка профиля, где лежит значение. None — колонки пока нет.
DISCLOSURE_FIELD_COLUMNS: dict[str, str | None] = {
    "full_name": "full_name",
    "email": "contact_email",
    "phone": "phone",
    "telegram": "telegram",
    "resume": "resume_text",
    "current_employer": "current_employer",
    "university": "education",
    "photo_url": None,
}


class CandidateProfile(TimestampMixin, Base):
    """Профиль кандидата 1:1 с User. Создаётся при подтверждении email.

    Резюме и заявленные навыки — только «заявлено» (серые чипы), в категорию и Score
    не входят никогда (CLAUDE.md, DECISIONS п. 1).
    """

    __tablename__ = "candidate_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)

    # --- Поиск и приватность (К13) ---
    search_mode: Mapped[str] = mapped_column(String(16), default=SearchMode.open.value)
    salary_min: Mapped[int | None]  # RUB/мес
    salary_type: Mapped[str] = mapped_column(String(8), default=SalaryType.net.value)
    salary_visibility: Mapped[str] = mapped_column(String(8), default=SalaryVisibility.signal.value)
    # Лимит новых приглашений в неделю: 3/5/10; NULL = без ограничений (∞), только явным выбором кандидата.
    # Умолчание (5) — из config/contact.yaml, ставится в __init__: профиль, созданный без значения (seed,
    # импорт), не должен молча получить «без ограничений». Не column default — тот затёр бы явный None.
    weekly_invite_limit: Mapped[int | None] = mapped_column(SmallInteger)
    formats: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, server_default=text("'{}'"))
    region_code: Mapped[str | None] = mapped_column(String(8))  # код субъекта РФ (видно до раскрытия)
    city: Mapped[str | None] = mapped_column(String(120))  # видно после раскрытия
    relocation: Mapped[bool] = mapped_column(default=False, server_default=false())
    available_from: Mapped[date | None]
    blocked_company_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), default=list, server_default=text("'{}'")
    )
    field_visibility: Mapped[dict[str, Any]] = mapped_column(
        default=lambda: dict(DEFAULT_FIELD_VISIBILITY), server_default=JSONB_EMPTY_OBJECT
    )
    industries: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list, server_default=text("'{}'"))

    # --- Раскрываемая часть (ПДн): только CandidateDisclosed ---
    full_name: Mapped[str | None] = mapped_column(String(200))
    contact_email: Mapped[str | None] = mapped_column(String(254))
    phone: Mapped[str | None] = mapped_column(String(32))
    telegram: Mapped[str | None] = mapped_column(String(64))
    current_employer: Mapped[str | None] = mapped_column(String(200))
    education: Mapped[str | None] = mapped_column(String(300))

    # --- Резюме (К7): распознанные поля с цитатами и статусом подтверждения ---
    resume_text: Mapped[str | None] = mapped_column(Text)
    resume_file_sha256: Mapped[str | None] = mapped_column(String(64))
    resume_fields: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    experience: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    experience_months: Mapped[int | None] = mapped_column(Integer)
    # [{"skill": "fastapi", "source": "resume|survey|manual", "quote": "..."}] — серые «заявлено»
    declared_skills: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    declared_roles: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)

    # --- Несовершеннолетние (концепция §4.3): до 16 не видны, 16–17 — только стажировки ---
    birth_year: Mapped[int | None] = mapped_column(SmallInteger)
    is_minor: Mapped[bool] = mapped_column(default=False, server_default=false())
    guardian_consent: Mapped[bool] = mapped_column(default=False, server_default=false())

    user: Mapped[User] = relationship(back_populates="candidate_profile")

    def __init__(self, **kwargs: Any) -> None:
        kwargs.setdefault("weekly_invite_limit", _default_weekly_invite_limit())
        super().__init__(**kwargs)


class SurveyResponse(UUIDPkMixin, TimestampMixin, Base):
    """Опрос (К2): 4 шага, сохраняется после каждого шага; история версий видна в «Мой грейд»."""

    __tablename__ = "survey_responses"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    version: Mapped[int] = mapped_column(default=1, server_default=text("1"))  # версия анкеты/классификатора
    step: Mapped[int] = mapped_column(SmallInteger, default=0, server_default=text("0"))  # пройдено шагов 0..4
    answers: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    industries: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list, server_default=text("'{}'"))
    spec_probs: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    chosen_spec: Mapped[str | None] = mapped_column(String(32))  # код из config/taxonomy.yaml
    suggested_spec: Mapped[str | None] = mapped_column(String(32))
    stack_declared: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    experience_months: Mapped[int | None]
    self_assessment: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    suggested_grade: Mapped[str | None] = mapped_column(String(16))
    completed_at: Mapped[datetime | None]


class CandidateFeatures(Base):
    """Признаки кандидата для подбора, материализуются по событиям (spec-contact §4).

    Ключ (кандидат, специализация): Fullstack-кандидат виден в обеих категориях.
    """

    __tablename__ = "candidate_features"

    candidate_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    specialization: Mapped[str] = mapped_column(String(32), primary_key=True)
    grade: Mapped[str | None] = mapped_column(String(16))
    category: Mapped[str | None] = mapped_column(String(64), index=True)  # "backend_python:middle"
    test_outcome: Mapped[str | None] = mapped_column(String(32))
    theta: Mapped[float | None]
    se: Mapped[float | None]
    theta_lcb: Mapped[float | None]
    t_pct: Mapped[float | None]
    trust: Mapped[float | None]
    test_at: Mapped[datetime | None]
    # {skill: {"v": float, "source": "test|task|fsp|declared", "ref_id": str, "at": iso}}
    skill_levels: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    vss_by_discipline: Mapped[dict[str, Any]] = mapped_column(default=dict, server_default=JSONB_EMPTY_OBJECT)
    has_fsp: Mapped[bool] = mapped_column(default=False, server_default=false())
    fsp_public: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    best_tasks: Mapped[list[Any]] = mapped_column(default=list, server_default=JSONB_EMPTY_ARRAY)
    visible: Mapped[bool] = mapped_column(default=False, server_default=false())  # в банке (категория + согласие)
    updated_at: Mapped[datetime | None]

    __table_args__ = (Index("ix_candidate_features_visible_spec", "specialization", "grade", postgresql_where=text("visible")),)


