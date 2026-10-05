"""Результат разбора резюме: каждое поле — с цитатой (смещения в извлечённом тексте) и источником.

Всё распознанное — «заявлено»: кандидат подтверждает или правит каждое поле (экран К7), а в категорию
и Score оно не попадает никогда (DECISIONS п. 1).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

Source = Literal["rules", "llm"]


class Quote(BaseModel):
    """Цитата для подсветки: text == извлечённый_текст[start:end]."""

    text: str = Field(description="Цитата из резюме")
    start: int = Field(description="Начало цитаты в извлечённом тексте резюме (символ)")
    end: int = Field(description="Конец цитаты (не включая)")
    page: int | None = Field(default=None, description="Страница PDF (с 1)")


class ScalarField(BaseModel):
    value: str | int | None = Field(description="Распознанное значение")
    label: str | None = Field(default=None, description="Подпись для человека (например, «Middle», «Москва»)")
    quote: Quote | None = Field(default=None, description="Откуда взято")
    source: Source = Field(default="rules", description="rules — правила, llm — языковая модель с проверенной цитатой")
    confidence: float = Field(default=1.0, ge=0, le=1, description="Насколько уверены правила (0–1)")


class ListItem(BaseModel):
    id: str = Field(description="Код из справочника (config/skills.yaml)")
    name: str = Field(description="Название для чипа")
    quote: Quote = Field(description="Откуда взято")
    source: Source = Field(default="rules", description="rules | llm")


class ExperiencePeriod(BaseModel):
    start: str = Field(description="Начало, ГГГГ-ММ")
    end: str | None = Field(description="Конец, ГГГГ-ММ; null — по настоящее время")
    months: int = Field(description="Месяцев в периоде (включительно)")
    title: str | None = Field(default=None, description="Должность (если распознана)")
    company: str | None = Field(default=None, description="Компания (если распознана)")
    quote: Quote = Field(description="Строка с датами")


class ExperienceField(BaseModel):
    value: int | None = Field(description="Стаж в месяцах: объединение периодов без двойного счёта пересечений")
    periods: list[ExperiencePeriod] = Field(default_factory=list, description="Найденные периоды работы")
    overlap_months: int = Field(default=0, description="Сколько месяцев пересекались (посчитаны один раз)")


class NameParts(BaseModel):
    last: str | None = None
    first: str | None = None
    middle: str | None = None


class FullNameField(ScalarField):
    parts: NameParts = Field(default_factory=NameParts, description="Фамилия, имя, отчество")


class SectionOut(BaseModel):
    key: str = Field(description="Раздел: header, contacts, summary, experience, education, skills, …")
    title: str = Field(description="Заголовок раздела как в резюме")
    start: int
    end: int


class LLMInfo(BaseModel):
    used: bool = Field(description="Вызывалась ли языковая модель (по умолчанию выключена)")
    provider: str | None = Field(default=None, description="yandexgpt | gigachat")
    added: int = Field(default=0, description="Сколько полей добавила модель (каждое — с проверенной цитатой)")
    rejected: int = Field(default=0, description="Сколько предложений отброшено: нет цитаты в тексте")


class ResumeParse(BaseModel):
    parser_version: str
    pages: int
    text_chars: int
    llm: LLMInfo
    sections: list[SectionOut] = Field(default_factory=list)
    full_name: FullNameField | None = None
    email: ScalarField | None = None
    phone: ScalarField | None = None
    telegram: ScalarField | None = None
    city: ScalarField | None = None
    declared_grade: ScalarField | None = None
    education: ScalarField | None = None
    experience: ExperienceField
    skills: list[ListItem] = Field(default_factory=list)
    roles: list[ListItem] = Field(default_factory=list)
    soft_skills: list[ListItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- загрузка и подтверждение (К7)


class ResumeDraftOut(BaseModel):
    status: Literal["draft"] = Field(default="draft", description="Черновик: поля ждут подтверждения кандидатом")
    parse: ResumeParse = Field(description="Распознанные поля — у каждого цитата для подсветки")
    text: str = Field(description="Извлечённый текст резюме: цитаты — это text[start:end]")
    sha256: str = Field(description="Отпечаток файла")
    uploaded_ago: str = Field(description="Когда загружено (относительно)")
    next_step: str = Field(description="Что сделать дальше")
    note: str = Field(description="Напоминание: распознанное — «заявлено», на категорию и Score не влияет")


class ResumeConfirmIn(BaseModel):
    """Подтверждение черновика: передайте поля, которые кандидат принял или исправил; не переданные не меняются.
    Значение, совпавшее с распознанным, сохраняется с источником «резюме» и цитатой; изменённое — «вручную»."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = Field(default=None, max_length=200, description="ФИО")
    email: EmailStr | None = Field(default=None, description="Email для связи")
    phone: str | None = Field(default=None, max_length=32, description="Телефон")
    telegram: str | None = Field(default=None, max_length=64, description="Telegram")
    city: str | None = Field(default=None, max_length=120, description="Город: код справочника или название")
    education: str | None = Field(default=None, max_length=300, description="Образование")
    declared_grade: Literal["intern", "junior", "middle", "senior"] | None = Field(default=None, description="Грейд как заявлен")
    experience_months: int | None = Field(default=None, ge=0, le=720, description="Стаж в месяцах")
    use_experience_periods: bool = Field(default=True, description="Сохранить распознанные периоды работы")
    skills: list[str] | None = Field(default=None, max_length=80, description="Итоговый список навыков (коды или названия)")
    roles: list[str] | None = Field(default=None, max_length=10, description="Итоговый список ролей (коды)")
    soft_skills: list[str] | None = Field(default=None, max_length=20, description="Итоговый список софт-скиллов (коды)")
    keep_resume_text: bool = Field(
        default=True, description="Сохранить текст резюме: компания увидит его после «да», если вы разрешите поле «Резюме»"
    )
