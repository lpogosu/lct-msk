"""DTO PDF-профиля и публичной страницы сверки (экран К8, /v/{код})."""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class ProfilePdfInfoOut(BaseModel):
    variant: Literal["blind", "full"] = Field(description="blind — слепая (без имени и контактов), full — полная")
    code: str = Field(description="Код сверки документа")
    verify_url: str = Field(description="Ссылка на страницу сверки (она же — в QR на листе)")
    issued_on: date = Field(description="Дата выдачи")
    download_url: str = Field(description="Скачать PDF")
    includes: list[str] = Field(description="Что попадёт на лист")
    hidden: list[str] = Field(default_factory=list, description="Что скрыто в этой версии")


class VerifyCategoryOut(BaseModel):
    code: str | None = Field(default=None, description="Код категории")
    name: str | None = Field(default=None, description="Категория: специализация · грейд")
    borderline: bool = Field(default=False, description="Тест сдан на границе грейда")


class VerifyOut(BaseModel):
    valid: bool = Field(description="Документ выдан платформой и не изменён")
    code: str = Field(description="Проверяемый код")
    message: str = Field(description="Итог сверки одной фразой")
    variant: Literal["blind", "full"] | None = Field(default=None, description="Версия документа")
    variant_label: str | None = Field(default=None, description="«Слепая версия» / «Полная версия»")
    issued_on: date | None = Field(default=None, description="Дата выдачи")
    holder: dict[str, Any] = Field(default_factory=dict, description="Псевдоним (слепая) или инициалы (полная) — без ПДн")
    category: VerifyCategoryOut | None = Field(default=None, description="Категория из документа")
    confirmed_on: date | None = Field(default=None, description="Дата подтверждения категории тестом")
    confidence: float | None = Field(default=None, description="Уверенность в грейде (0–1)")
    session_ref: str | None = Field(default=None, description="Номер сессии теста")
    interval: list[int] | None = Field(default=None, description="80%-интервал балла на линейке 300–600")
    score: int | None = Field(default=None, description="Точный балл (только в полной версии)")
    modules: list[dict[str, Any]] = Field(default_factory=list, description="Модули теста ✓ / ✓✓")
    fsp: dict[str, Any] = Field(default_factory=dict, description="ФСП: привязан ли, источник, дата данных реестра")
    freshness: dict[str, Any] | None = Field(default=None, description="Актуальность профиля на дату выдачи")
    actual: bool | None = Field(default=None, description="Совпадает ли категория документа с текущей")
    actual_note: str | None = Field(default=None, description="Пояснение к актуальности")
