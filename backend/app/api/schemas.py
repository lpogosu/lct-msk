"""DTO платформенных маршрутов (аутентификация, согласия, служебные). Описания полей — на русском:
они попадают в OpenAPI."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import ConsentPurpose


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Аутентификация ---


class RegisterRequest(_Strict):
    email: EmailStr = Field(description="Email — логин и адрес для кода подтверждения", examples=["maria@example.com"])
    password: str = Field(
        min_length=1,
        max_length=256,
        description="Пароль: минимум 8 символов, хотя бы одна буква и одна цифра",
        examples=["Kod-2026-ok"],
    )
    role: Literal["candidate", "employer"] = Field(
        description="Роль: candidate — «Ищу работу», employer — «Нанимаю»", examples=["candidate"]
    )
    consent_personal_data: bool = Field(
        description="Отдельное согласие на обработку персональных данных (152-ФЗ). Должно быть true",
        examples=[True],
    )
    consent_text_version: str | None = Field(
        default=None,
        max_length=32,
        description="Версия текста согласия, которую видел пользователь (GET /api/consents/documents)",
        examples=["2026-10-05"],
    )

    birth_year: int | None = Field(
        default=None,
        ge=1940,
        le=2026,
        description=(
            "Год рождения (только для роли candidate). Младше 15 лет — 422 `age_too_young`, регистрация отклоняется. "
            "15–17 лет — после входа потребуется согласие законного представителя"
        ),
        examples=[2001],
    )

    @field_validator("consent_personal_data")
    @classmethod
    def _consent_given(cls, v: bool) -> bool:
        if not v:
            raise ValueError("Нужно согласие на обработку персональных данных")
        return v


class RegisterResponse(BaseModel):
    user_id: uuid.UUID = Field(description="Идентификатор аккаунта")
    email: str = Field(description="Email в нормализованном виде")
    role: str = Field(description="Роль аккаунта")
    status: str = Field(description="Статус аккаунта: pending_email до подтверждения")
    code_sent: bool = Field(description="Письмо с кодом отправлено (false — сбой почты, запросите код повторно)")
    code_ttl_s: int = Field(description="Срок действия кода, секунд")
    resend_available_in_s: int = Field(description="Через сколько секунд можно отправить код ещё раз")


_CODE_RE = re.compile(r"^\s*\d{6}\s*$")


def _six_digits(v: str) -> str:
    if not _CODE_RE.match(v):
        raise ValueError("Код — 6 цифр из письма")
    return v.strip()


_CODE_FIELD_DOC = {"pattern": r"^\d{6}$"}
# Своя проверка вместо pattern=: сообщение об ошибке по-русски и по делу («6 цифр»)
SixDigitCode = Annotated[str, AfterValidator(_six_digits)]


class VerifyEmailRequest(_Strict):
    email: EmailStr = Field(description="Email из регистрации")
    code: SixDigitCode = Field(
        max_length=16, description="6-значный код из письма", examples=["042917"], json_schema_extra=_CODE_FIELD_DOC
    )
    password: str = Field(
        min_length=1,
        max_length=256,
        description=(
            "Пароль, указанный при регистрации (экран О3 передаёт его из формы О2; при переходе по ссылке "
            "из письма — спрашивает). Аккаунт, созданный через ФСП ID без пароля, получает этот пароль"
        ),
        examples=["Kod-2026-ok"],
    )


class ResendCodeRequest(_Strict):
    email: EmailStr = Field(description="Email из регистрации")


class ResendCodeResponse(BaseModel):
    sent: bool = Field(description="Письмо отправлено")
    code_ttl_s: int = Field(description="Срок действия нового кода, секунд")
    resend_available_in_s: int = Field(description="Через сколько секунд можно отправить ещё раз")
    sends_left_this_hour: int = Field(description="Сколько отправок осталось в текущем часе")


class LoginRequest(_Strict):
    email: EmailStr = Field(description="Email аккаунта")
    password: str = Field(min_length=1, max_length=256, description="Пароль")


class PasswordResetRequest(_Strict):
    email: EmailStr = Field(description="Email аккаунта", examples=["maria@example.com"])


class PasswordResetRequestResponse(BaseModel):
    code_ttl_s: int = Field(description="Срок действия кода, секунд")
    resend_available_in_s: int = Field(description="Через сколько секунд можно запросить код ещё раз")


class PasswordResetConfirmRequest(_Strict):
    email: EmailStr = Field(description="Email аккаунта")
    code: SixDigitCode = Field(
        max_length=16, description="6-значный код из письма", examples=["042917"], json_schema_extra=_CODE_FIELD_DOC
    )
    new_password: str = Field(
        min_length=1,
        max_length=256,
        description="Новый пароль: минимум 8 символов, хотя бы одна буква и одна цифра",
        examples=["Novyi-2026"],
    )


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Идентификатор аккаунта")
    email: str = Field(description="Email")
    role: str = Field(description="Роль: candidate | employer | methodist | admin")
    status: str = Field(description="Статус: pending_email | active | blocked")
    email_verified: bool = Field(description="Email подтверждён")
    company_id: uuid.UUID | None = Field(default=None, description="Компания работодателя (для employer)")
    created_at: datetime = Field(description="Дата регистрации")


class TokenResponse(BaseModel):
    access_token: str = Field(description="Access-JWT для заголовка Authorization: Bearer")
    token_type: Literal["bearer"] = Field(default="bearer", description="Тип токена")
    expires_in: int = Field(description="Срок жизни access-токена, секунд")
    user: UserOut = Field(description="Текущий пользователь")


# --- Согласия ---


class ConsentDocumentOut(BaseModel):
    purpose: ConsentPurpose = Field(description="Цель согласия")
    version: str = Field(description="Актуальная версия текста")
    title: str = Field(description="Название документа")
    summary: str = Field(description="Краткое содержание простыми словами")
    required_for_registration: bool = Field(description="Обязательно при регистрации")
    grantable_in_journal: bool = Field(description="Можно дать через журнал согласий (иначе — в контексте действия)")
    revocable_in_journal: bool = Field(
        description="Можно отозвать кнопкой в журнале (согласие на обработку ПДн отзывается только с удалением аккаунта)"
    )


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID = Field(description="Идентификатор записи журнала")
    purpose: str = Field(description="Цель согласия")
    text_version: str = Field(description="Версия текста, с которой согласился пользователь")
    granted_at: datetime = Field(description="Когда дано")
    revoked_at: datetime | None = Field(default=None, description="Когда отозвано (null — действует)")
    scope: dict[str, Any] = Field(default_factory=dict, description="Уточнение (например, компания для раскрытия)")
    active: bool = Field(description="Действует ли согласие")


class ConsentGrantRequest(_Strict):
    purpose: ConsentPurpose = Field(description="Цель согласия (кроме contact_disclosure — оно даётся в приглашении/отклике)")
    text_version: str | None = Field(default=None, max_length=32, description="Версия текста, которую видел пользователь")


# --- Служебное ---


class HealthOut(BaseModel):
    status: Literal["ok", "degraded"] = Field(description="ok — всё работает; degraded — БД недоступна")
    db: Literal["ok", "unavailable"] = Field(description="Состояние PostgreSQL")
    version: str = Field(description="Версия бэкенда")
    demo_mode: bool = Field(description="Включён ли DEMO_MODE")
    pool_ready: bool | None = Field(default=None, description="Готов ли пул одноразовых заданий (#14); null — не определено")
    pool_progress: float | None = Field(default=None, description="Доля готовых семейств заданий, 0…1")
    time: datetime = Field(description="Текущее время по часам приложения (в DEMO_MODE — со смещением)")


class LiveOut(BaseModel):
    status: Literal["ok"] = Field(description="Процесс жив")


# --- Удаление аккаунта (152-ФЗ, право на удаление) ---


class AccountDeleteRequest(_Strict):
    password: str | None = Field(
        default=None,
        max_length=256,
        description="Текущий пароль — подтверждение удаления. Для аккаунта без пароля (вход через ФСП ID) не нужен",
    )
    confirm: str | None = Field(
        default=None,
        max_length=32,
        description="Только для аккаунта без пароля: слово «УДАЛИТЬ» вместо пароля",
        examples=["УДАЛИТЬ"],
    )


class AccountDeleteResponse(BaseModel):
    deleted: Literal[True] = Field(description="Аккаунт удалён безвозвратно")
    role: Literal["candidate", "employer"]
    message: str = Field(description="Что произошло, готовый текст на русском")
    removed: list[str] = Field(description="Что стёрто")
    kept: list[str] = Field(description="Что остаётся и почему (без персональных данных)")
    company_closed: bool = Field(default=False, description="Работодатель был последним в компании: компания закрыта")
    invitations_withdrawn: int = Field(default=0, description="Сколько ждущих ответа приглашений отозвано (работодатель)")
