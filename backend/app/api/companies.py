"""Профиль компании (экран Р1): описание и способы связи, «так видит кандидат», надёжность R с разложением,
квота приглашений, URL вебхука ATS и журнал доставок (концепция §3.2 шаг 2, §4.3, §8.2).

Без заполненного профиля (название, направление, описание, способ связи) приглашения не отправляются.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import CurrentUser, DbSession, LimiterDep, require_roles
from app.core.errors import bad_request, conflict, error_responses, not_found
from app.models.company import Company, WebhookDelivery
from app.models.enums import UserRole
from app.models.users import User
from app.services.contact import webhooks
from app.services.contact.common import cfg, check_rate, now, rel
from app.services.contact.company import company_of, missing_fields, quota_dict, sync_completion
from app.services.contact.invitations import complaints_count
from app.services.contact.profiles import company_card
from app.services.contact.reliability import company_reliability, reliability_dict
from app.services.contact.schemas import (
    CompanyCardOut,
    CompanyMeOut,
    CompanyProfileIn,
    ReliabilityOut,
    WebhookDeliveryOut,
    WebhookLogOut,
    WebhookSettingsOut,
)

router = APIRouter(prefix="/companies", tags=["Компании"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")
_TG_RE = re.compile(r"^@?[A-Za-z][A-Za-z0-9_]{4,31}$")
_PHONE_DIGITS = (10, 15)


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _contact_methods(raw: dict[str, str | None]) -> dict[str, str]:
    """Способы связи компании видны кандидату до решения — проверяем формат, чтобы по ним можно было связаться."""
    out: dict[str, str] = {}
    email = _clean(raw.get("email"))
    if email:
        if not _EMAIL_RE.match(email):
            raise bad_request("contact_email_invalid", "Проверьте email для связи")
        out["email"] = email.lower()
    tg = _clean(raw.get("telegram"))
    if tg:
        tg = tg.removeprefix("https://t.me/").removeprefix("t.me/")
        if not _TG_RE.match(tg):
            raise bad_request("contact_telegram_invalid", "Telegram — имя пользователя вида @company_hr")
        out["telegram"] = "@" + tg.lstrip("@")
    phone = _clean(raw.get("phone"))
    if phone:
        digits = re.sub(r"\D", "", phone)
        if not _PHONE_DIGITS[0] <= len(digits) <= _PHONE_DIGITS[1] or re.search(r"[^\d\s()+\-]", phone):
            raise bad_request("contact_phone_invalid", "Проверьте номер телефона: 10–15 цифр")
        out["phone"] = phone
    return out


def _webhook_settings(company: Company) -> WebhookSettingsOut:
    wc = cfg().webhooks
    return WebhookSettingsOut(
        url=company.webhook_url,
        secret=company.webhook_secret if company.webhook_url else None,
        signature_header=wc.signature_header,
        event_header=wc.event_header,
        event_id_header=wc.event_id_header,
    )


def _me(db: DbSession, user: User, company: Company) -> CompanyMeOut:
    at = now()
    missing = missing_fields(company)
    return CompanyMeOut(
        id=company.id,
        email_domain=company.email_domain,
        personal_domain=company.personal_domain,
        name=company.name,
        field=company.field,
        description=company.description,
        contact_methods={k: str(v) for k, v in (company.contact_methods or {}).items() if v},
        inn=company.inn,
        website=company.website,
        profile_complete=not missing,
        missing=missing,
        webhook=_webhook_settings(company),
        quota=quota_dict(db, company, user, at),  # type: ignore[arg-type]
        reliability=reliability_dict(company_reliability(db, company.id, at)),  # type: ignore[arg-type]
        complaints_90d=complaints_count(db, company.id, cfg().reliability.discrepancy.window_days),
        preview=company_card(db, company, at),  # type: ignore[arg-type]
    )


@router.get(
    "/me",
    response_model=CompanyMeOut,
    summary="Профиль моей компании",
    description=(
        "Профиль компании (Р1): поля, заполненность (`missing` — чего не хватает для отправки приглашений), "
        "квота «осталось N/60», надёжность R с разложением и превью «так видит кандидат». Только работодатель."
    ),
)
def get_me(user: EmployerUser, db: DbSession) -> CompanyMeOut:
    company = company_of(db, user)
    return _me(db, user, company)


@router.put(
    "/me",
    response_model=CompanyMeOut,
    summary="Изменить профиль компании",
    description=(
        "Обновляет переданные поля (не переданные не меняются, пустая строка очищает необязательное поле). "
        "Способы связи: email, Telegram, телефон — хотя бы один обязателен для «профиль заполнен». ИНН — "
        "необязательно (10 или 12 цифр). `webhook_url` — публичный http(s)-адрес ATS для подписанных событий; "
        "адреса внутренней сети (localhost, частные и служебные сети, имена без домена) запрещены; при первом "
        "задании выдаётся секрет подписи. 400 — неверный формат поля."
    ),
)
def put_me(body: CompanyProfileIn, user: EmployerUser, db: DbSession) -> CompanyMeOut:
    company = company_of(db, user)
    fields = body.model_fields_set
    cp = cfg().company_profile
    if "name" in fields:
        company.name = _clean(body.name)
    if "field" in fields:
        company.field = _clean(body.field)
    if "description" in fields:
        desc = _clean(body.description)
        if desc and len(desc) > cp.description_max_len:
            raise bad_request("description_too_long", f"Описание — не длиннее {cp.description_max_len} символов")
        company.description = desc
    if "contact_methods" in fields:
        raw = body.contact_methods.model_dump() if body.contact_methods else {}
        company.contact_methods = _contact_methods(raw)
    if "inn" in fields:
        inn = _clean(body.inn)
        if inn and (not inn.isdigit() or len(inn) not in cp.inn_lengths):
            raise bad_request("inn_invalid", "ИНН — 10 цифр для организации или 12 для ИП")
        company.inn = inn
    if "website" in fields:
        site = _clean(body.website)
        if site and not re.match(r"^https?://\S+\.\S+$", site):
            raise bad_request("website_invalid", "Адрес сайта должен начинаться с http:// или https://")
        company.website = site
    if "webhook_url" in fields:
        url = _clean(body.webhook_url)
        if url:
            problem = webhooks.validate_url(url)
            if problem:
                raise bad_request("webhook_url_invalid", problem)
            if not company.webhook_secret:
                company.webhook_secret = webhooks.new_secret()
        company.webhook_url = url
    sync_completion(company, now())
    company.updated_at = now()
    db.commit()
    return _me(db, user, company)


@router.get(
    "/me/preview",
    response_model=CompanyCardOut,
    summary="Так видит кандидат",
    description="Карточка компании в приглашении: название, описание, способы связи, надёжность R с разложением.",
)
def preview(user: EmployerUser, db: DbSession) -> CompanyCardOut:
    company = company_of(db, user)
    return CompanyCardOut.model_validate(company_card(db, company, now()))


def _delivery_out(row: WebhookDelivery) -> WebhookDeliveryOut:
    at = now()
    return WebhookDeliveryOut(
        event_id=row.event_id,
        event_type=row.event_type,
        target_url=row.target_url,
        status=row.status,
        attempts=row.attempts,
        response_code=row.response_code,
        response_excerpt=row.response_excerpt,
        created_text=rel(row.created_at, at),
        last_attempt_text=rel(row.last_attempt_at, at),
        next_attempt_text=rel(row.next_attempt_at, at) if row.status == "pending" else None,
    )


@router.get(
    "/me/webhooks",
    response_model=WebhookLogOut,
    summary="Журнал доставок вебхуков",
    description=(
        "Последние 20 доставок (тип события, код ответа, попытки). Созревшие повторы отправляются при открытии "
        "журнала. Подпись: заголовок `X-Vzaimno-Signature: sha256=<HMAC-SHA256(секрет, тело)>`, идемпотентность — "
        "`X-Vzaimno-Event-Id`. Контакты кандидата приходят только при действующем согласии."
    ),
)
def webhook_log(user: EmployerUser, db: DbSession) -> WebhookLogOut:
    company = company_of(db, user)
    webhooks.deliver_due(db, company.id)
    db.commit()
    return WebhookLogOut(url=company.webhook_url, deliveries=[_delivery_out(r) for r in webhooks.deliveries_log(db, company.id)])


@router.post(
    "/me/webhooks/test",
    response_model=WebhookDeliveryOut,
    summary="Проверить вебхук",
    description="Отправляет тестовое событие `ping` на URL вебхука и возвращает результат доставки. 409 — URL не задан.",
)
def webhook_test(user: EmployerUser, db: DbSession, limiter: LimiterDep) -> WebhookDeliveryOut:
    company = company_of(db, user)
    check_rate(limiter, "webhook_test", company.id, cfg().http_limits.webhook_test_per_minute)
    if not company.webhook_url:
        raise conflict("webhook_not_configured", "Сначала укажите URL вебхука в профиле компании")
    row = webhooks.send_test(db, company)
    db.commit()
    assert row is not None
    return _delivery_out(row)


@router.post(
    "/me/webhooks/rotate-secret",
    response_model=WebhookSettingsOut,
    summary="Новый секрет подписи",
    description="Выдаёт новый секрет HMAC-SHA256; старый перестаёт действовать сразу. 409 — URL вебхука не задан.",
)
def webhook_rotate(user: EmployerUser, db: DbSession) -> WebhookSettingsOut:
    company = company_of(db, user)
    if not company.webhook_url:
        raise conflict("webhook_not_configured", "Сначала укажите URL вебхука в профиле компании")
    company.webhook_secret = webhooks.new_secret()
    db.commit()
    return _webhook_settings(company)


@router.get(
    "/{company_id}/reliability",
    response_model=ReliabilityOut,
    summary="Надёжность компании",
    description=(
        "R = (5·0,8 + good) / (5 + good + bad) с разложением: «отвечает на отклики в срок», «связывается после "
        "принятия», «условия совпали». При < 5 событиях — «мало данных». Доступно любому вошедшему пользователю."
    ),
)
def reliability(company_id: uuid.UUID, user: CurrentUser, db: DbSession) -> ReliabilityOut:
    company = db.get(Company, company_id)
    if company is None:
        raise not_found("company_not_found", "Компания не найдена")
    return ReliabilityOut.model_validate(reliability_dict(company_reliability(db, company.id, now())))
