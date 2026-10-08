"""DTO профиля кандидата (экраны К7, К13, К15). Описания полей попадают в OpenAPI — на русском.

Всё, что кандидат пишет о себе (форма и резюме), — «заявлено»: серые чипы и подсказка стажа при выборе
грейда. В категорию и в Score это не входит никогда (DECISIONS п. 1).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodeName(BaseModel):
    code: str = Field(description="Код из справочника")
    name: str = Field(description="Название для человека")


class DeclaredItemOut(BaseModel):
    id: str = Field(description="Код из справочника (config/skills.yaml)")
    name: str = Field(description="Название для чипа")
    source: str = Field(description="Откуда: resume — из резюме, manual — вручную, survey — из опроса")
    quote: str | None = Field(default=None, description="Цитата из резюме (если из резюме)")


class ExperiencePeriodIO(BaseModel):
    start: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Начало, ГГГГ-ММ")
    end: str | None = Field(default=None, pattern=r"^\d{4}-(0[1-9]|1[0-2])$", description="Конец, ГГГГ-ММ; пусто — по настоящее время")
    title: str | None = Field(default=None, max_length=200, description="Должность")
    company: str | None = Field(default=None, max_length=200, description="Компания")


class ResumeStateOut(BaseModel):
    status: Literal["none", "draft", "confirmed"] = Field(description="none — не загружено; draft — ждёт подтверждения; confirmed — поля подтверждены")
    uploaded_ago: str | None = Field(default=None, description="Когда загружено (относительно)")
    pages: int | None = Field(default=None, description="Страниц в PDF")
    has_text: bool = Field(default=False, description="Текст резюме сохранён (его увидит компания после раскрытия, если вы разрешите)")


class GuardianStatusOut(BaseModel):
    """Согласие законного представителя кандидата 15–17 лет (SECURITY.md п. 1.4)."""

    required: bool = Field(description="Нужно ли согласие (кандидату 15–17 лет)")
    state: Literal["not_required", "missing", "pending", "expired", "confirmed"] = Field(
        description="missing — письмо не отправлено; pending — ждём подтверждения; expired — ссылка устарела; confirmed — согласие есть"
    )
    state_label: str = Field(default="", description="Готовая подпись состояния")
    guardian_name: str | None = Field(default=None, description="ФИО представителя")
    guardian_email_masked: str | None = Field(default=None, description="Email представителя с маской: i***@mail.ru")
    employer_rule: str | None = Field(default=None, description="Какое правило увидит работодатель")


class GuardianRequestIn(_In):
    guardian_name: str = Field(min_length=5, max_length=200, description="ФИО законного представителя (родитель, опекун)")
    guardian_email: EmailStr = Field(description="Email представителя — на него придёт ссылка подтверждения")


class ProfileOut(BaseModel):
    user_id: uuid.UUID = Field(description="Идентификатор аккаунта")
    account_email: str = Field(description="Email аккаунта (логин)")
    full_name: str | None = Field(default=None, description="ФИО — раскрывается только после взаимного «да»")
    contact_email: str | None = Field(default=None, description="Email для связи (по умолчанию — email аккаунта)")
    phone: str | None = Field(default=None, description="Телефон")
    telegram: str | None = Field(default=None, description="Telegram")
    city: CodeName | None = Field(default=None, description="Город (работодатель видит только субъект РФ до раскрытия)")
    city_text: str | None = Field(default=None, description="Город, если его нет в справочнике")
    region: CodeName | None = Field(default=None, description="Субъект РФ")
    current_employer: str | None = Field(default=None, description="Текущий работодатель — скрыт до раскрытия")
    education: str | None = Field(default=None, description="Образование (необязательно) — скрыто до раскрытия")
    birth_year: int | None = Field(default=None, description="Год рождения — никогда не показывается, только политика по несовершеннолетним")
    declared_grade: CodeName | None = Field(default=None, description="Грейд «как заявлен» — подсказка, категорию даёт только тест")
    roles: list[DeclaredItemOut] = Field(default_factory=list, description="Роли")
    skills: list[DeclaredItemOut] = Field(default_factory=list, description="Заявленные навыки (серые чипы «заявлено»)")
    soft_skills: list[DeclaredItemOut] = Field(default_factory=list, description="Софт-скиллы")
    experience_months: int | None = Field(default=None, description="Стаж в месяцах")
    experience_label: str | None = Field(default=None, description="Стаж словами: «6 лет 2 мес.»")
    experience: list[ExperiencePeriodIO] = Field(default_factory=list, description="Периоды работы")
    industries: list[CodeName] = Field(default_factory=list, description="Отрасли интереса")
    resume: ResumeStateOut = Field(description="Состояние загруженного резюме")
    missing: list[str] = Field(default_factory=list, description="Что ещё стоит заполнить")
    note: str = Field(description="Напоминание: заявленное не влияет на категорию и место в подборке")
    guardian: GuardianStatusOut | None = Field(default=None, description="Согласие законного представителя (кандидаты 15–17 лет)")


class ProfileUpdateIn(_In):
    """Ручная форма (К7): передайте изменённые поля; не переданные не меняются, null — очистить."""

    full_name: str | None = Field(default=None, max_length=200, description="ФИО")
    contact_email: EmailStr | None = Field(default=None, description="Email для связи")
    phone: str | None = Field(default=None, max_length=32, description="Телефон, например +7 915 204-18-73")
    telegram: str | None = Field(default=None, max_length=64, description="Telegram: @ник или ссылка t.me/…")
    city: str | None = Field(default=None, max_length=120, description="Город: код справочника (moscow) или название")
    current_employer: str | None = Field(default=None, max_length=200, description="Текущий работодатель")
    education: str | None = Field(default=None, max_length=300, description="Образование")
    birth_year: int | None = Field(default=None, ge=1940, le=2026, description="Год рождения. Младше 15 лет (с учётом сомнения по году) — 422; 15–17 лет — нужно согласие представителя")
    declared_grade: Literal["intern", "junior", "middle", "senior"] | None = Field(default=None, description="Грейд как заявлен")
    roles: list[str] | None = Field(default=None, max_length=10, description="Коды ролей (config/skills.yaml: roles)")
    skills: list[str] | None = Field(default=None, max_length=80, description="Навыки: коды или названия (FastAPI, PostgreSQL)")
    soft_skills: list[str] | None = Field(default=None, max_length=20, description="Коды софт-скиллов")
    experience_months: int | None = Field(default=None, ge=0, le=720, description="Стаж в месяцах")
    experience: list[ExperiencePeriodIO] | None = Field(default=None, max_length=30, description="Периоды работы")
    industries: list[str] | None = Field(default=None, max_length=10, description="Коды отраслей (config/taxonomy.yaml)")


# --------------------------------------------------------------------------- К13: приватность и поиск


class FieldVisibilityOut(BaseModel):
    field: str = Field(description="Поле, раскрываемое компании после взаимного «да»")
    label: str = Field(description="Подпись")
    allowed: bool = Field(description="Разрешено ли раскрывать")
    contact: bool = Field(description="Это канал связи (нужен хотя бы один)")


class BlockedCompanyOut(BaseModel):
    id: uuid.UUID = Field(description="Компания")
    name: str = Field(description="Название")


class PrivacyOptionsOut(BaseModel):
    weekly_limits: list[int | None] = Field(description="Варианты лимита приглашений в неделю (null — без ограничений)")
    formats: list[CodeName] = Field(description="Форматы работы")
    search_modes: list[CodeName] = Field(description="Режимы поиска")
    regions: list[CodeName] = Field(description="Субъекты РФ")


class BankStatusOut(BaseModel):
    visible: bool = Field(description="Работодатели видят вашу слепую карточку в подборке и банке")
    reasons: list[str] = Field(default_factory=list, description="Почему вас сейчас нет в банке (если нет)")


class PrivacyOut(BaseModel):
    search_mode: str = Field(description="active — активно ищу, open — открыт к предложениям, hidden — не ищу (скрыт из банка)")
    salary_min: int | None = Field(default=None, description="Минимум зарплаты, ₽/мес")
    salary_type: str = Field(description="net — на руки, gross — до НДФЛ")
    salary_visibility: str = Field(description="signal — работодатель видит только «в вилке ✓ / выше» (по умолчанию); exact — число")
    weekly_invite_limit: int | None = Field(default=None, description="Новых приглашений в неделю: 3/5/10; null — без ограничений")
    formats: list[str] = Field(default_factory=list, description="Подходящие форматы")
    region: CodeName | None = Field(default=None, description="Субъект РФ (виден до раскрытия)")
    city: CodeName | None = Field(default=None, description="Город (виден после раскрытия)")
    relocation: bool = Field(description="Готов к переезду")
    available_from: date | None = Field(default=None, description="Готов выйти с")
    blocked_companies: list[BlockedCompanyOut] = Field(default_factory=list, description="Блок-лист: эти компании вас не видят")
    field_visibility: list[FieldVisibilityOut] = Field(default_factory=list, description="Что компания увидит после взаимного «да»")
    bank: BankStatusOut = Field(description="Видны ли вы работодателям")
    options: PrivacyOptionsOut = Field(description="Справочники для формы")


class PrivacyUpdateIn(_In):
    """Передайте изменённые поля; не переданные не меняются. weekly_invite_limit: null — без ограничений."""

    search_mode: Literal["active", "open", "hidden"] | None = Field(default=None, description="Режим поиска")
    salary_min: int | None = Field(default=None, ge=0, le=10_000_000, description="Минимум, ₽/мес (null — не указывать)")
    salary_type: Literal["net", "gross"] | None = Field(default=None, description="На руки / до НДФЛ")
    salary_visibility: Literal["signal", "exact"] | None = Field(default=None, description="Видимость минимума")
    weekly_invite_limit: int | None = Field(default=None, description="3 / 5 / 10 или null (без ограничений)")
    formats: list[Literal["office", "hybrid", "remote"]] | None = Field(default=None, max_length=3, description="Форматы")
    region_code: str | None = Field(default=None, max_length=8, description="Код субъекта РФ (77 — Москва)")
    city: str | None = Field(default=None, max_length=120, description="Город: код справочника или название")
    relocation: bool | None = Field(default=None, description="Готов к переезду")
    available_from: date | None = Field(default=None, description="Готов выйти с")
    blocked_company_ids: list[uuid.UUID] | None = Field(default=None, max_length=200, description="Блок-лист компаний")
    field_visibility: dict[str, bool] | None = Field(default=None, description="Поле → раскрывать ли после «да»")


class PreviewIn(_In):
    privacy: PrivacyUpdateIn | None = Field(default=None, description="Черновик настроек — превью без сохранения")
    offer_salary_to: int | None = Field(
        default=None, ge=0, le=10_000_000, description="Верх вилки примерного приглашения — чтобы увидеть значок «в вилке ✓ / выше»"
    )


class PreviewOut(BaseModel):
    blind: dict[str, Any] = Field(description="Слепая карточка — так вас видит работодатель до взаимного «да»")
    disclosed: dict[str, Any] = Field(description="Раскрытая карточка — так вас увидит компания после «да» и согласия")
    pii_in_blind: list[str] = Field(description="ПДн в слепой карточке (должно быть пусто — контрактная проверка)")
    in_bank: BankStatusOut = Field(description="Видны ли вы работодателям при этих настройках")
    notes: list[str] = Field(default_factory=list, description="Пояснения к превью")


class CompanySearchOut(BaseModel):
    id: uuid.UUID = Field(description="Компания")
    name: str = Field(description="Название")
    field: str | None = Field(default=None, description="Направление деятельности")
    blocked: bool = Field(description="Уже в блок-листе")


class ConsentJournalOut(BaseModel):
    id: uuid.UUID = Field(description="Запись журнала (отзыв — POST /api/consents/{id}/revoke)")
    purpose: str = Field(description="Цель")
    title: str = Field(description="Название документа")
    text_version: str = Field(description="Версия текста")
    granted_ago: str = Field(description="Когда дано (относительно)")
    revoked_ago: str | None = Field(default=None, description="Когда отозвано")
    active: bool = Field(description="Действует")
    revocable: bool = Field(description="Можно отозвать кнопкой в журнале")
    effect_of_revoke: str = Field(description="Что произойдёт при отзыве")
    scope: dict[str, Any] = Field(default_factory=dict, description="Уточнение (компания для раскрытия)")


# --------------------------------------------------------------------------- К15: как меня видят


class ReasonCount(BaseModel):
    reason: str = Field(description="Причина")
    count: int = Field(description="Сколько раз")


class MissingSkillOut(BaseModel):
    skill: str = Field(description="Код навыка")
    name: str = Field(description="Навык")
    count: int = Field(description="В скольких подборках он был обязательным и не подтверждён")
    action: str = Field(description="Что сделать: задача по модулю или тест на повышение")


class VisibilityStatsOut(BaseModel):
    period_days: int = Field(description="Окно, дней")
    enough_data: bool = Field(description="Достаточно ли показов для выводов (≥ 5)")
    message: str = Field(description="Короткий вывод для кандидата")
    impressions: int = Field(description="Показы в подборках")
    selections: int = Field(description="В скольких подборках вы были")
    top10: int = Field(description="В скольких подборках вы были в топ-10")
    median_position: float | None = Field(default=None, description="Медианная позиция")
    profile_views: int = Field(description="Сколько компаний открыли вашу слепую карточку")
    invitations: int = Field(description="Приглашений за период")
    missing_skill: MissingSkillOut | None = Field(default=None, description="Навык, которого чаще всего не хватает")
    decline_reasons: list[ReasonCount] = Field(default_factory=list, description="Причины отказов компаний")
