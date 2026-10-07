"""Интеграция с ATS работодателя [доп.] (концепция §8.2, DECISIONS 29).

Две части:
- `/api/companies/me/ats/*` — настройки в профиле компании (JWT работодателя): тип ATS, токен Хантфлоу (шифруется),
  API-ключи компании, «Проверить подключение», «Журнал ATS»;
- `/api/ats/v1/*` — pull-API и приём статусов для системы найма (заголовок `Authorization: Bearer vzk_…`, области доступа).
Исходящие вебхуки (подписанные события) остаются прежними — см. /api/companies/me/webhooks.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import DbSession, LimiterDep, SettingsDep, require_roles
from app.core.audit import audit
from app.core.errors import conflict, error_responses, forbidden, not_found
from app.models.company import Company
from app.models.enums import UserRole
from app.models.integration import AtsLog, CompanyApiKey
from app.models.users import User
from app.services.ats import huntflow, keys, pull
from app.services.ats import settings as ats_settings
from app.services.ats import status as status_svc
from app.services.contact.common import cfg, check_rate, now, rel
from app.services.contact.company import company_of
from app.services.contact.store import Ctx

settings_router = APIRouter(prefix="/companies/me/ats", tags=["Компании"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))
v1_router = APIRouter(prefix="/ats/v1", tags=["Интеграция ATS"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

EmployerUser = Annotated[User, Depends(require_roles(UserRole.employer))]


# ---------------------------------------------------------------------------------------------- настройки компании


class AtsSettingsOut(BaseModel):
    ats_type: str = Field(description="none | webhook | huntflow")
    ats_types: dict[str, str] = Field(description="Варианты выбора: код → название")
    mode: Literal["none", "emulator", "real"] = Field(description="emulator — запросы только записываются в журнал; real — отправляются")
    base_url: str | None = None
    base_url_default: str
    account_id: str | None = None
    token_set: bool
    token_hint: str | None = Field(default=None, description="Последние 4 знака токена; сам токен не возвращается никогда")
    webhook_url: str | None = Field(default=None, description="URL вебхука из профиля (для типа «Вебхук»)")
    scopes: dict[str, str] = Field(description="Области доступа API-ключей: код → описание")


class AtsSettingsIn(BaseModel):
    ats_type: str
    base_url: str | None = None
    account_id: str | None = None
    token: str | None = Field(default=None, description="Токен Хантфлоу; не передан — не менять, пустая строка — удалить")


class AtsCheckOut(BaseModel):
    ok: bool
    mode: str
    message: str


class AtsLogItem(BaseModel):
    id: int
    direction: Literal["out", "in"]
    mode: str
    kind: str
    method: str | None = None
    url: str | None = None
    request: dict[str, Any]
    status: str
    response_code: int | None = None
    response: dict[str, Any]
    when: str | None = Field(default=None, description="Относительное время словами")
    created_at: datetime


class AtsLogOut(BaseModel):
    mode: str
    items: list[AtsLogItem]


class ApiKeyOut(BaseModel):
    id: uuid.UUID
    name: str
    key_id: str = Field(description="Публичная часть ключа (vzk_<key_id>_…)")
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None = None
    revoked: bool = False


class ApiKeyCreateIn(BaseModel):
    name: str = Field(max_length=80)
    scopes: list[str]


class ApiKeyIssuedOut(ApiKeyOut):
    key: str = Field(description="Ключ целиком — показывается ОДИН раз, в БД хранится только хеш")


def _settings_out(db: DbSession, company: Company) -> AtsSettingsOut:
    row = ats_settings.get(db, company.id)
    return AtsSettingsOut(
        ats_type=row.ats_type if row else "none",
        ats_types=cfg().ats.types,
        mode=ats_settings.mode_of(row),  # type: ignore[arg-type]
        base_url=row.base_url if row else None,
        base_url_default=cfg().ats.huntflow.default_base_url,
        account_id=row.account_id if row else None,
        token_set=bool(row and row.token_enc),
        token_hint=ats_settings.token_hint(row),
        webhook_url=company.webhook_url,
        scopes=cfg().ats.scopes,
    )


def _key_out(k: CompanyApiKey) -> dict[str, Any]:
    return {
        "id": k.id, "name": k.name, "key_id": k.key_id, "scopes": list(k.scopes or []),
        "created_at": k.created_at, "last_used_at": k.last_used_at, "revoked": k.revoked_at is not None,
    }


@settings_router.get("", response_model=AtsSettingsOut, summary="Настройки ATS компании", description="Тип интеграции, режим (эмулятор или боевой), адрес и признак токена; сам токен не возвращается.")
def get_settings(user: EmployerUser, db: DbSession) -> AtsSettingsOut:
    return _settings_out(db, company_of(db, user))


@settings_router.put(
    "",
    response_model=AtsSettingsOut,
    summary="Сохранить настройки ATS",
    description=(
        "Тип: `none` (нет), `webhook` (подписанные события на URL из профиля), `huntflow` (адаптер Хантфлоу API v2). "
        "Без токена Хантфлоу работает эмулятор: запросы записываются в «Журнал ATS», в сеть ничего не уходит. "
        "Токен хранится зашифрованным и обратно не отдаётся. Адрес не может вести во внутреннюю сеть (SSRF)."
    ),
)
def put_settings(body: AtsSettingsIn, user: EmployerUser, db: DbSession) -> AtsSettingsOut:
    company = company_of(db, user)
    ats_settings.save(
        db, company.id, type_=body.ats_type, base_url=body.base_url, account_id=body.account_id, token=body.token,
        fields=body.model_fields_set,
    )
    audit(db, "ats.settings_changed", actor_id=user.id, actor_role="employer", entity_type="company", entity_id=company.id, ats_type=body.ats_type)
    db.commit()
    return _settings_out(db, company)


@settings_router.post(
    "/check",
    response_model=AtsCheckOut,
    summary="Проверить подключение",
    description="Эмулятор: записывает в журнал запрос `GET /accounts`, как отправил бы адаптер. Реальный режим: делает этот запрос. 409 — тип не «Хантфлоу».",
)
def check_connection(user: EmployerUser, db: DbSession, limiter: LimiterDep) -> dict[str, Any]:
    company = company_of(db, user)
    check_rate(limiter, "ats_check", company.id, cfg().http_limits.webhook_test_per_minute)
    ad = huntflow.adapter_for(db, company.id, company.name)
    if ad is None:
        raise conflict("ats_not_huntflow", "Выберите тип «Хантфлоу» и сохраните настройки — тогда подключение можно проверить")
    out = ad.check()
    db.commit()
    return out


@settings_router.get("/log", response_model=AtsLogOut, summary="Журнал ATS", description="Последние запросы адаптера (эмулятор — записанные, реальный режим — отправленные) и входящие статусы из ATS.")
def ats_log(user: EmployerUser, db: DbSession) -> dict[str, Any]:
    company = company_of(db, user)
    rows = db.scalars(select(AtsLog).where(AtsLog.company_id == company.id).order_by(AtsLog.id.desc()).limit(cfg().ats.log_size))
    at = now()
    items = [
        {
            "id": r.id, "direction": r.direction, "mode": r.mode, "kind": r.kind, "method": r.method, "url": r.url,
            "request": r.request, "status": r.status, "response_code": r.response_code, "response": r.response,
            "when": rel(r.created_at, at), "created_at": r.created_at,
        }
        for r in rows
    ]
    return {"mode": ats_settings.mode_of(ats_settings.get(db, company.id)), "items": items}


@settings_router.get("/keys", response_model=list[ApiKeyOut], summary="API-ключи компании", description="Действующие ключи без секретов: название, публичная часть, области, последнее использование.")
def list_keys(user: EmployerUser, db: DbSession) -> list[dict[str, Any]]:
    company = company_of(db, user)
    return [_key_out(k) for k in keys.active_keys(db, company.id)]


@settings_router.post(
    "/keys",
    response_model=ApiKeyIssuedOut,
    status_code=201,
    summary="Создать API-ключ",
    description="Ключ с областями доступа (`vacancies:read`, `invitations:read`, `applications:read`, `contacts:read`, `status:write`). Показывается один раз.",
)
def create_key(body: ApiKeyCreateIn, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    company = company_of(db, user)
    row, plain = keys.create(db, company.id, user, body.name, body.scopes)
    audit(db, "ats.key_created", actor_id=user.id, actor_role="employer", entity_type="api_key", entity_id=row.id, scopes=list(row.scopes))
    db.commit()
    return {**_key_out(row), "key": plain}


@settings_router.post("/keys/{key_pk}/rotate", response_model=ApiKeyIssuedOut, summary="Перевыпустить ключ", description="Старый ключ перестаёт работать сразу; новый наследует название и области. Показывается один раз.")
def rotate_key(key_pk: uuid.UUID, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    company = company_of(db, user)
    new, plain = keys.rotate(db, keys.get_owned(db, company.id, key_pk), user)
    audit(db, "ats.key_rotated", actor_id=user.id, actor_role="employer", entity_type="api_key", entity_id=new.id)
    db.commit()
    return {**_key_out(new), "key": plain}


@settings_router.delete("/keys/{key_pk}", response_model=ApiKeyOut, summary="Отозвать ключ", description="Ключ перестаёт работать сразу.")
def revoke_key(key_pk: uuid.UUID, user: EmployerUser, db: DbSession) -> dict[str, Any]:
    company = company_of(db, user)
    row = keys.get_owned(db, company.id, key_pk)
    keys.revoke(row)
    audit(db, "ats.key_revoked", actor_id=user.id, actor_role="employer", entity_type="api_key", entity_id=row.id)
    db.commit()
    return _key_out(row)


# ------------------------------------------------------------------------------------------------------- /ats/v1


class KeyContext:
    def __init__(self, key: CompanyApiKey, company: Company) -> None:
        self.key = key
        self.company = company


def api_key(scope: str):  # noqa: ANN201
    """Зависимость: ключ из заголовка, нужная область, лимит запросов на ключ."""

    def dep(
        db: DbSession,
        limiter: LimiterDep,
        authorization: Annotated[str | None, Header(description="Bearer vzk_<id>_<секрет>")] = None,
    ) -> KeyContext:
        key, company = keys.authenticate(db, authorization)
        if scope not in (key.scopes or []):
            raise forbidden("ats_scope_missing", f"У ключа нет области доступа «{scope}»", {"scope": scope})
        check_rate(limiter, "ats_key", key.id, cfg().ats.rate_per_minute)
        return KeyContext(key, company)

    return dep


class Salary(BaseModel):
    from_: int = Field(alias="from")
    to: int
    net: bool = Field(description="true — на руки, false — до вычета НДФЛ")
    currency: str = "RUB"

    model_config = {"populate_by_name": True}


class VacancyItem(BaseModel):
    id: uuid.UUID
    need_id: uuid.UUID | None = None
    title: str
    description: str
    specialization: str | None = None
    grade: str | None = None
    salary: Salary
    format: str | None = None
    city: str | None = None
    status: str
    published_at: datetime | None = None
    updated_at: datetime


class CandidateBlock(BaseModel):
    pseudonym: str = Field(description="Псевдоним «К-4821» — единственный идентификатор кандидата до раскрытия")
    contacts_open: bool = Field(description="Есть действующее согласие на раскрытие контактов")
    contacts: dict[str, str] | None = Field(default=None, description="null, если нет `contacts:read` или нет действующего согласия; иначе full_name/email/phone/telegram — те, что разрешил кандидат")


class TermsBlock(BaseModel):
    ver: int
    title: str
    salary: Salary
    format: str | None = None
    sha256: str


class InvitationItem(BaseModel):
    id: uuid.UUID
    vacancy_id: uuid.UUID | None = None
    need_id: uuid.UUID | None = None
    status: str
    status_label: str
    stage: str | None = Field(default=None, description="interview | offer | null")
    outcome: str | None = None
    outcome_reason: str | None = None
    terms: TermsBlock | None = None
    sent_at: datetime
    responded_at: datetime | None = None
    accepted_at: datetime | None = None
    contacted_at: datetime | None = None
    closed_at: datetime | None = None
    candidate: CandidateBlock
    updated_at: datetime


class ApplicationItem(BaseModel):
    id: uuid.UUID
    vacancy_id: uuid.UUID
    status: str
    status_label: str
    reject_reason: str | None = None
    score: int | float | None = None
    category: Any = None
    submitted_at: datetime
    responded_at: datetime | None = None
    candidate: CandidateBlock
    updated_at: datetime


class VacancyPage(BaseModel):
    items: list[VacancyItem]
    next_cursor: str | None = Field(description="null — конец; иначе передайте как `cursor`")
    server_time: datetime


class InvitationPage(BaseModel):
    items: list[InvitationItem]
    next_cursor: str | None
    server_time: datetime


class ApplicationPage(BaseModel):
    items: list[ApplicationItem]
    next_cursor: str | None
    server_time: datetime


class MeOut(BaseModel):
    company: str | None
    scopes: list[str]
    key_name: str
    server_time: datetime


class StatusIn(BaseModel):
    status: str = Field(description="Приглашения: contacted | interview | offer | hired | rejected. Отклики: viewed | interested | rejected")
    reason: str | None = Field(default=None, description="Для rejected: приглашения — skills_missing | position_closed | chose_other; отклики — skills_missing | salary | position_closed | chose_other | other")
    skill_ids: list[str] = Field(default_factory=list, description="Для reason=skills_missing: навыки брифа")
    comment: str | None = Field(default=None, max_length=300)
    external_id: str | None = Field(default=None, max_length=64, description="Ваш id кандидата/отклика (пишется в журнал)")


class StatusOut(BaseModel):
    applied: bool = Field(description="false — запись уже была в этом состоянии (повтор), ничего не менялось")
    replayed: bool = Field(default=False, description="true — ответ воспроизведён по Idempotency-Key")
    kind: Literal["invitation", "application"]
    item: dict[str, Any] = Field(description="Запись в том же виде, что и в списке")


Since = Annotated[datetime | None, Query(description="Только записи, изменённые не раньше этого момента (ISO 8601, включительно)")]
Cursor = Annotated[str | None, Query(description="next_cursor из предыдущего ответа")]
Limit = Annotated[int | None, Query(ge=1, description="Размер страницы (по умолчанию и максимум — config/contact.yaml → ats)")]


@v1_router.get("/me", response_model=MeOut, summary="Проверка ключа", description="Компания и области доступа ключа — для «проверить подключение» на стороне ATS.")
def v1_me(ctx: Annotated[KeyContext, Depends(api_key("vacancies:read"))]) -> dict[str, Any]:
    return {"company": ctx.company.name, "scopes": list(ctx.key.scopes or []), "key_name": ctx.key.name, "server_time": now()}


@v1_router.get("/vacancies", response_model=VacancyPage, summary="Вакансии", description="Опубликованные и закрытые вакансии компании. Область `vacancies:read`. Порядок — по изменению, курсорная пагинация.")
def v1_vacancies(
    ctx: Annotated[KeyContext, Depends(api_key("vacancies:read"))], db: DbSession, updated_since: Since = None, cursor: Cursor = None, limit: Limit = None
) -> dict[str, Any]:
    from app.models.contact import Vacancy

    rows, nxt = pull.paginate(db, Vacancy, pull.company_vacancy_filter(ctx.company.id), updated_since=updated_since, cursor=cursor, limit=limit)
    db.commit()
    return {"items": [pull.vacancy_item(v) for v in rows], "next_cursor": nxt, "server_time": now()}


@v1_router.get(
    "/invitations",
    response_model=InvitationPage,
    summary="Приглашения со статусами",
    description=(
        "Приглашения компании. Область `invitations:read`; контакты кандидата (`candidate.contacts`) — только с `contacts:read` "
        "и действующим согласием, иначе блока нет. Фильтры: `status`, `updated_since`; курсорная пагинация."
    ),
)
def v1_invitations(
    ctx: Annotated[KeyContext, Depends(api_key("invitations:read"))], db: DbSession, updated_since: Since = None, cursor: Cursor = None,
    limit: Limit = None, status: Annotated[str | None, Query(description="Только в этом статусе")] = None,
) -> dict[str, Any]:
    from app.models.contact import Invitation

    contacts = "contacts:read" in (ctx.key.scopes or [])
    rows, nxt = pull.paginate(db, Invitation, pull.company_invitation_filter(ctx.company.id, status), updated_since=updated_since, cursor=cursor, limit=limit)
    out = [pull.invitation_item(db, r, with_contacts=contacts) for r in rows]
    db.commit()
    return {"items": out, "next_cursor": nxt, "server_time": now()}


@v1_router.get("/invitations/{invitation_id}", response_model=InvitationItem, summary="Приглашение", description="Одно приглашение компании; контакты кандидата — по тем же правилам, что в списке. Область `invitations:read`.")
def v1_invitation(invitation_id: uuid.UUID, ctx: Annotated[KeyContext, Depends(api_key("invitations:read"))], db: DbSession) -> dict[str, Any]:
    row = status_svc.owned_invitation(db, ctx.company.id, invitation_id)
    if row is None:
        raise not_found("invitation_not_found", "Приглашение не найдено")
    out = pull.invitation_item(db, row, with_contacts="contacts:read" in (ctx.key.scopes or []))
    db.commit()
    return out


@v1_router.get(
    "/applications",
    response_model=ApplicationPage,
    summary="Отклики со статусами",
    description="Отклики кандидатов на вакансии компании. Область `applications:read`; контакты — по тем же правилам, что у приглашений.",
)
def v1_applications(
    ctx: Annotated[KeyContext, Depends(api_key("applications:read"))], db: DbSession, settings: SettingsDep, updated_since: Since = None,
    cursor: Cursor = None, limit: Limit = None, status: Annotated[str | None, Query(description="Только в этом статусе")] = None,
) -> dict[str, Any]:
    from app.models.contact import Application

    contacts = "contacts:read" in (ctx.key.scopes or [])
    rows, nxt = pull.paginate(db, Application, pull.company_application_filter(db, ctx.company.id, status), updated_since=updated_since, cursor=cursor, limit=limit)
    secret = settings.pseudonym_secret.get_secret_value()
    out = [pull.application_item(db, r, ctx.company.id, secret, with_contacts=contacts) for r in rows]
    db.commit()
    return {"items": out, "next_cursor": nxt, "server_time": now()}


@v1_router.get("/applications/{application_id}", response_model=ApplicationItem, summary="Отклик", description="Один отклик на вакансию компании. Область `applications:read`; контакты — при `contacts:read` и действующем согласии.")
def v1_application(application_id: uuid.UUID, ctx: Annotated[KeyContext, Depends(api_key("applications:read"))], db: DbSession, settings: SettingsDep) -> dict[str, Any]:
    row = status_svc.owned_application(db, ctx.company.id, application_id)
    if row is None:
        raise not_found("application_not_found", "Отклик не найден")
    out = pull.application_item(db, row, ctx.company.id, settings.pseudonym_secret.get_secret_value(), with_contacts="contacts:read" in (ctx.key.scopes or []))
    db.commit()
    return out


def _replay(db: DbSession, company_id: uuid.UUID, idem: str | None) -> AtsLog | None:
    if not idem:
        return None
    return db.scalar(select(AtsLog).where(AtsLog.company_id == company_id, AtsLog.idempotency_key == idem))


def _push_status(
    kind: Literal["invitation", "application"], entity_id: uuid.UUID, body: StatusIn, ctx: KeyContext, db: DbSession,
    settings: SettingsDep, idem: str | None,
) -> dict[str, Any]:
    if idem is not None and not (0 < len(idem) <= 80):
        from app.core.errors import bad_request

        raise bad_request("ats_idempotency_key", "Idempotency-Key — строка до 80 знаков")
    idem_key = f"{kind}:{idem}" if idem else None
    prev = _replay(db, ctx.company.id, idem_key)
    if prev is not None:
        if prev.entity_id != str(entity_id) or prev.kind != f"status.{body.status}":
            raise conflict("ats_idempotency_conflict", "Этот Idempotency-Key уже использован для другого запроса")
        return {**prev.response, "replayed": True}
    row: Any = (
        status_svc.owned_invitation(db, ctx.company.id, entity_id) if kind == "invitation" else status_svc.owned_application(db, ctx.company.id, entity_id)
    )
    if row is None:
        raise not_found(f"{kind}_not_found", "Приглашение не найдено" if kind == "invitation" else "Отклик не найден")
    actor = status_svc.actor_for(db, ctx.company, ctx.key.created_by)
    cx = Ctx(db=db, mailer=None, secret=settings.pseudonym_secret.get_secret_value())
    log = AtsLog(
        company_id=ctx.company.id, created_at=now(), direction="in", mode="api", kind=f"status.{body.status}", method="POST",
        url=f"/api/ats/v1/{kind}s/{entity_id}/status",
        request={"status": body.status, "reason": body.reason, "comment": body.comment, "external_id": body.external_id},
        status="rejected", entity_type=kind, entity_id=str(entity_id),
    )
    try:
        if kind == "invitation":
            applied = status_svc.apply_invitation(cx, row, body.status, actor, reason=body.reason, skill_ids=body.skill_ids, comment=body.comment)
        else:
            applied = status_svc.apply_application(cx, row, body.status, actor, reason=body.reason, skill_ids=body.skill_ids, comment=body.comment)
    except Exception as e:
        # Отказ тоже виден в «Журнале ATS»: понятно, почему статус не принят
        db.rollback()
        log.response = {"code": getattr(e, "code", "error"), "message": getattr(e, "message", str(e))}
        log.response_code = getattr(e, "status_code", 500)
        db.add(log)
        db.commit()
        raise
    cx.finish()
    db.refresh(row)
    secret = settings.pseudonym_secret.get_secret_value()
    contacts = "contacts:read" in (ctx.key.scopes or [])
    item = pull.invitation_item(db, row, with_contacts=contacts) if kind == "invitation" else pull.application_item(db, row, ctx.company.id, secret, with_contacts=contacts)
    resp = {"applied": applied, "replayed": False, "kind": kind, "item": item}
    log.status = "applied" if applied else "replayed"
    log.response_code = 200
    log.response = _jsonable(resp)
    log.idempotency_key = idem_key
    db.add(log)
    audit(db, "ats.status_received", actor_id=actor, actor_role="employer", entity_type=kind, entity_id=entity_id, status=body.status, applied=applied)
    db.commit()
    return resp


def _jsonable(obj: Any) -> Any:
    import json

    return json.loads(json.dumps(obj, ensure_ascii=False, default=str))


@v1_router.post(
    "/invitations/{invitation_id}/status",
    response_model=StatusOut,
    summary="Статус из ATS: приглашение",
    description=(
        "Передать статус кандидата из ATS (`contacted`, `interview`, `offer`, `hired`, `rejected` + `reason`). Переход проходит через нашу "
        "машину состояний: недопустимый — 409 с русским объяснением. Повтор уже достигнутого состояния — 200, `applied: false`. "
        "Заголовок `Idempotency-Key` — повтор запроса вернёт прежний ответ (`replayed: true`). Область `status:write`. "
        "Надёжность компании обновляется как при ручной отметке."
    ),
)
def v1_invitation_status(
    invitation_id: uuid.UUID, body: StatusIn, ctx: Annotated[KeyContext, Depends(api_key("status:write"))], db: DbSession, settings: SettingsDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    return _push_status("invitation", invitation_id, body, ctx, db, settings, idempotency_key)


@v1_router.post(
    "/applications/{application_id}/status",
    response_model=StatusOut,
    summary="Статус из ATS: отклик",
    description="`viewed`, `interested`, `rejected` (+ `reason`). Правила те же, что у приглашений: машина состояний откликов, 409 при недопустимом переходе, идемпотентность по `Idempotency-Key`.",
)
def v1_application_status(
    application_id: uuid.UUID, body: StatusIn, ctx: Annotated[KeyContext, Depends(api_key("status:write"))], db: DbSession, settings: SettingsDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, Any]:
    return _push_status("application", application_id, body, ctx, db, settings, idempotency_key)


router = APIRouter()
router.include_router(settings_router)
router.include_router(v1_router)
