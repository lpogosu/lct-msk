"""DTO раздела «ФСП ID и достижения» (экран К14). Описания полей попадают в OpenAPI — на русском."""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.api.schemas import TokenResponse

FspStateCode = Literal["not_linked", "linked_no_results", "awaiting_protocol", "linked"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FspLoginStartOut(BaseModel):
    authorization_url: str = Field(description="Адрес страницы входа ФСП ID: откройте его в браузере")
    state: str = Field(description="Одноразовый state входа (его же ФСП ID вернёт в адрес возврата)")
    expires_in: int = Field(description="Сколько секунд действует начатый вход")
    mode: Literal["link", "login"] = Field(description="link — привязка к текущему аккаунту, login — вход с лендинга")
    redirect_uri: str = Field(description="Куда ФСП ID вернёт браузер (страница фронтенда)")


class FspAvailabilityOut(BaseModel):
    fsp_id_available: bool = Field(description="ФСП ID (Keycloak) отвечает — можно входить через него")
    demo_login_available: bool = Field(description="Доступен «демо-вход ФСП» (подписанный мок-токен, только для демо)")
    issuer: str = Field(description="Адрес ФСП ID (realm fsp)")
    registry_label: str = Field(description="Подпись источника данных: «Демо-реестр ФСП» на стенде")


class DemoAthleteOut(BaseModel):
    sub: str = Field(description="ФСП ID демо-спортсмена (= sub в токене)")
    label: str = Field(description="Вымышленные фамилия и инициалы")
    summary: str = Field(description="Что о нём в демо-реестре (как увидит работодатель)")
    state: FspStateCode = Field(description="Состояние профиля ФСП")


class DemoLoginIn(_In):
    sub: str = Field(min_length=1, max_length=128, description="ФСП ID демо-спортсмена из GET /api/fsp/demo/athletes")
    mode: Literal["link", "login"] = Field(
        default="link", description="link — привязать к текущему аккаунту (нужен вход), login — войти или создать аккаунт"
    )
    email: EmailStr | None = Field(
        default=None, description="Для mode=login: email нового аккаунта (по умолчанию — демо-адрес спортсмена, письмо придёт в Mailpit)"
    )
    consent_personal_data: bool = Field(default=False, description="Для mode=login: согласие на обработку ПДн (нужно для нового аккаунта)")
    consent_fsp_enrichment: bool = Field(default=False, description="Сразу дать согласие на обогащение профиля данными ФСП")


class FspResultOut(BaseModel):
    id: str = Field(description="Идентификатор результата в реестре (для «показывать / скрыть»)")
    title: str = Field(description="Соревнование")
    discipline: str = Field(description="Дисциплина")
    level: str = Field(description="Уровень соревнования")
    year: int = Field(description="Год")
    place: str = Field(description="Место из N (видите только вы; работодатель — после раскрытия)")
    role: str | None = Field(default=None, description="Роль в команде / личный зачёт")
    outcome: str = Field(description="Призёр / финалист / участник")
    confirmed: bool = Field(description="Протокол опубликован, результат подтверждён")
    hidden: bool = Field(description="Вы скрыли результат: он не участвует в оценке и не виден работодателям")
    counted: bool = Field(description="Учитывается в оценке (подтверждён, не скрыт, дисциплина известна)")
    employer_text: str | None = Field(default=None, description="Как результат видит работодатель до раскрытия (обобщённо)")
    status_label: str = Field(description="«Подтверждён» / «Ожидает протокол» / «Скрыт»")


class FspStrengthOut(BaseModel):
    discipline: str = Field(description="Дисциплина")
    strength: float = Field(description="Подтверждённая сила по дисциплине, 0–100 (в ранжировании — не больше 15 баллов из 100)")


class FspStatusOut(BaseModel):
    state: FspStateCode = Field(description="not_linked / linked_no_results / awaiting_protocol / linked")
    status_line: str = Field(description="Строка состояния для кандидата")
    linked: bool = Field(description="ФСП ID привязан")
    is_demo: bool = Field(default=False, description="Привязка сделана «демо-входом ФСП»")
    linked_ago: str | None = Field(default=None, description="Когда привязан (относительно)")
    consent_fsp_enrichment: bool = Field(description="Дано согласие на обогащение профиля данными ФСП")
    consent_text_version: str = Field(description="Актуальная версия текста согласия fsp_enrichment")
    enrichment_requested: bool = Field(
        default=False,
        description=(
            "Кандидат отметил согласие на обогащение при первом входе через ФСП ID, но оно ещё не применено: "
            "применяется после подтверждения email (следующий вход через ФСП ID или кнопка согласия)"
        ),
    )
    preview_only: bool = Field(
        description="Данные показаны для предпросмотра: без согласия они не сохраняются и не влияют на подбор"
    )
    source_label: str | None = Field(default=None, description="Источник: «Демо-реестр ФСП» / «Реестр ФСП»")
    data_ago: str | None = Field(default=None, description="Когда получены данные реестра (относительно)")
    sync_status: str | None = Field(default=None, description="ok / error / pending — последняя синхронизация")
    sync_error: str | None = Field(default=None, description="Почему не удалось обновить данные")
    results: list[FspResultOut] = Field(default_factory=list, description="Результаты из реестра")
    badges: list[str] = Field(default_factory=list, description="Разряды и роли: «КМС до 2027», «судья-эксперт (1К)»")
    employer_line: str = Field(description="Строка ФСП в слепой карточке работодателя")
    strength: list[FspStrengthOut] = Field(default_factory=list, description="Подтверждённая сила по дисциплинам")
    extract: str | None = Field(default=None, description="Выписка из протокола (моноширинный текст) — так её увидит компания после раскрытия")
    pending_count: int = Field(default=0, description="Сколько результатов ждут протокола")
    note: str = Field(description="Пояснение: ФСП не влияет на грейд, отсутствие ФСП — не штраф")


class FspHideIn(_In):
    hidden: bool = Field(description="true — скрыть результат, false — снова показывать")


class FspEnrichmentIn(_In):
    text_version: str | None = Field(default=None, max_length=32, description="Версия текста согласия, которую видел кандидат")


class VerificationPendingOut(BaseModel):
    user_id: uuid.UUID = Field(description="Аккаунт, созданный входом через ФСП ID")
    email: str = Field(description="Куда отправлен код подтверждения")
    code_sent: bool = Field(description="Письмо отправлено (false — сбой почты или лимит, запросите код повторно)")
    code_ttl_s: int = Field(description="Срок действия кода, секунд")
    resend_available_in_s: int = Field(description="Через сколько секунд можно отправить код ещё раз")
    next_step: str = Field(description="Что сделать дальше")


class FspAuthResultOut(BaseModel):
    result: Literal["linked", "logged_in", "email_verification_required"] = Field(
        description="linked — ФСП ID привязан; logged_in — вход выполнен; email_verification_required — подтвердите email кодом"
    )
    demo: bool = Field(default=False, description="Вход выполнен «демо-входом ФСП» (подписанный мок-токен)")
    token: TokenResponse | None = Field(default=None, description="Сессия (для logged_in); refresh — в httpOnly-cookie")
    verification: VerificationPendingOut | None = Field(default=None, description="Для email_verification_required")
    fsp: FspStatusOut | None = Field(default=None, description="Состояние ФСП после привязки")
