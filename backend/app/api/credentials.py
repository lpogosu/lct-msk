"""Цифровой паспорт навыков: подписанные W3C Verifiable Credentials / Open Badges 3.0 (docs/CREDENTIALS.md).

Кандидат: что можно выпустить, выпуск, скачивание JSON, QR, отзыв. Публично: did.json издателя, список отзыва,
проверка по коду из QR и проверка загруженного файла. Издатель — демонстрационный, не официальный документ ФСП.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, LimiterDep, MetaDep, SettingsDep, require_roles
from app.core.audit import audit
from app.core.clock import clock
from app.core.errors import ApiError, bad_request, error_responses
from app.credentials import keys, service
from app.credentials.builders import verify_url
from app.credentials.config import get_credentials_config
from app.models.enums import UserRole
from app.models.users import User
from app.services.profile.settings import get_profile_settings

CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]

router = APIRouter(prefix="/credentials", tags=["Паспорт навыков"], responses=error_responses())
wellknown_router = APIRouter(tags=["Паспорт навыков"], include_in_schema=False)


# --- схемы -----------------------------------------------------------------------------------


class CredentialOfferOut(BaseModel):
    kind: Literal["category", "fsp"]
    ref: str = Field(description="Что выпускать: код специализации или id достижения (виден только владельцу)")
    title: str
    detail: str


class CredentialIssueIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["category", "fsp"]
    ref: str = Field(min_length=1, max_length=128)
    include_name: bool = Field(default=False, description="Указать ФИО в паспорте. По умолчанию — только псевдонимный идентификатор")


class CredentialOut(BaseModel):
    id: uuid.UUID
    kind: Literal["category", "fsp"]
    title: str
    code: str
    verify_url: str
    qr: str = Field(description="QR-код ссылки проверки (PNG, data URI)")
    issued_at: datetime
    valid_until: datetime
    revoked_at: datetime | None
    include_name: bool
    status: Literal["active", "revoked", "expired"]
    demo_issuer: bool = True
    disclaimer: str


class CredentialListOut(BaseModel):
    items: list[CredentialOut]
    offers: list[CredentialOfferOut]
    issuer_did: str
    disclaimer: str


class CredentialCheckOut(BaseModel):
    id: str
    ok: bool
    label: str
    reason: str | None = None


class CredentialResultOut(BaseModel):
    kind: Literal["score", "sem"]
    value: str


class CredentialSummaryOut(BaseModel):
    title: str
    achievement: str
    kind: Literal["category", "fsp"]
    issuer_name: str
    issuer_id: str
    issued_on: str
    valid_until: str | None
    tested_on: str | None
    results: list[CredentialResultOut]
    narrative: str | None
    holder_name: str | None = Field(default=None, description="Только при проверке файла, где владелец сам указал имя")


class VerifyCredentialOut(BaseModel):
    valid: bool
    message: str
    checks: list[CredentialCheckOut]
    reasons: list[str] = Field(description="Что не так — по-русски (пусто, если всё в порядке)")
    summary: CredentialSummaryOut | None
    demo_issuer: bool = True
    disclaimer: str


# --- помощники -------------------------------------------------------------------------------


def _out(row: Any, settings: SettingsDep, now: datetime) -> CredentialOut:
    cfg = get_credentials_config()
    url = verify_url(settings, row.code)
    status = "revoked" if row.revoked_at else ("expired" if row.valid_until <= now else "active")
    return CredentialOut(
        id=row.id,
        kind=row.kind,
        title=row.title,
        code=row.code,
        verify_url=f"/v/{row.code}",
        qr=service.qr_data_uri(url),
        issued_at=row.created_at,
        valid_until=row.valid_until,
        revoked_at=row.revoked_at,
        include_name=row.include_name,
        status=status,
        disclaimer=cfg.disclaimer,
    )


def _verify_out(res: service.VerifyResult) -> VerifyCredentialOut:
    cfg = get_credentials_config()
    message = (
        "Паспорт подлинный: подписан издателем, не изменён, не отозван, срок не истёк."
        if res.valid
        else "Проверка не пройдена: " + "; ".join(res.reasons) + "."
    )
    return VerifyCredentialOut(
        valid=res.valid,
        message=message,
        checks=[CredentialCheckOut(id=c.id, ok=c.ok, label=c.label, reason=c.reason) for c in res.checks],
        reasons=res.reasons,
        summary=CredentialSummaryOut.model_validate(res.summary) if res.summary else None,
        disclaimer=cfg.disclaimer,
    )


def _key_error(e: keys.SigningKeyUnavailable) -> ApiError:
    return ApiError(503, "issuer_key_unavailable", "Издатель паспортов временно недоступен: не настроен ключ подписи")


# --- кандидат --------------------------------------------------------------------------------


@router.get("/me", response_model=CredentialListOut, summary="Мои паспорта и что можно выпустить")
def my_credentials(user: CandidateUser, db: DbSession, settings: SettingsDep) -> CredentialListOut:
    now = clock.now()
    try:
        did = keys.issuer_did(settings)
    except keys.SigningKeyUnavailable as e:  # pragma: no cover
        raise _key_error(e) from e
    return CredentialListOut(
        items=[_out(r, settings, now) for r in service.list_for_user(db, user.id)],
        offers=[CredentialOfferOut(kind=o.kind, ref=o.ref, title=o.title, detail=o.detail) for o in service.available_offers(db, user, now)],  # type: ignore[arg-type]
        issuer_did=did,
        disclaimer=get_credentials_config().disclaimer,
    )


@router.post("/me", response_model=CredentialOut, status_code=201, summary="Выпустить паспорт")
def issue_credential(body: CredentialIssueIn, user: CandidateUser, db: DbSession, settings: SettingsDep, meta: MetaDep) -> CredentialOut:
    now = clock.now()
    try:
        row = service.issue(db, user, settings, kind=body.kind, ref=body.ref, include_name=body.include_name, now=now)
    except keys.SigningKeyUnavailable as e:
        raise _key_error(e) from e
    audit(db, "credential.issued", actor_id=user.id, actor_role=user.role, entity_type="credential", entity_id=row.id, ip=meta.ip, kind=row.kind)
    db.commit()
    return _out(row, settings, now)


@router.get(
    "/me/{credential_id}/file",
    summary="Скачать паспорт (JSON)",
    response_class=JSONResponse,
    description="Подписанный документ целиком (Open Badges 3.0, proof eddsa-jcs-2022). Его можно отправить кому угодно и проверить на странице /v.",
)
def download_credential(credential_id: uuid.UUID, user: CandidateUser, db: DbSession) -> Response:
    row = service.get_owned(db, user.id, credential_id)
    body = json.dumps(row.document, ensure_ascii=False, indent=2)
    return Response(
        content=body,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="vzaimno-passport-{row.code[:8]}.json"', "Cache-Control": "no-store"},
    )


@router.post("/me/{credential_id}/revoke", response_model=CredentialOut, summary="Отозвать паспорт")
def revoke_credential(credential_id: uuid.UUID, user: CandidateUser, db: DbSession, settings: SettingsDep, meta: MetaDep) -> CredentialOut:
    now = clock.now()
    row = service.get_owned(db, user.id, credential_id)
    service.revoke(db, row, now)
    audit(db, "credential.revoked", actor_id=user.id, actor_role=user.role, entity_type="credential", entity_id=row.id, ip=meta.ip)
    db.commit()
    return _out(row, settings, now)


# --- публичное -------------------------------------------------------------------------------


@router.get("/did.json", summary="DID-документ издателя", include_in_schema=False)
def did_json_alias(settings: SettingsDep) -> JSONResponse:
    return _did_response(settings)


def _did_response(settings: SettingsDep) -> JSONResponse:
    try:
        doc = keys.did_document(settings)
    except keys.SigningKeyUnavailable as e:
        raise _key_error(e) from e
    return JSONResponse(doc, media_type="application/did+json", headers={"Cache-Control": "public, max-age=300"})


@wellknown_router.get("/.well-known/did.json")
def did_json(settings: SettingsDep) -> JSONResponse:
    """did:web: публичный ключ издателя по стандартному адресу /.well-known/did.json."""
    return _did_response(settings)


@router.get("/status/{list_id}", summary="Список отзыва (Bitstring Status List)")
def status_list(list_id: Annotated[str, Path(max_length=16)], db: DbSession, settings: SettingsDep) -> JSONResponse:
    if list_id != get_credentials_config().status_list_id:
        raise ApiError(404, "status_list_not_found", "Список отзыва не найден")
    try:
        doc = service.build_status_list(db, settings, clock.now())
    except keys.SigningKeyUnavailable as e:
        raise _key_error(e) from e
    return JSONResponse(doc, media_type="application/vc+ld+json", headers={"Cache-Control": "no-store"})


@router.get(
    "/verify/{code}",
    response_model=VerifyCredentialOut,
    summary="Проверить паспорт по коду из QR",
    description="Публично, без входа и без ПДн (имя владельца не показывается). Каждая проверка отдельно: формат, издатель, подпись, срок, реестр, отзыв.",
)
def verify_by_code(
    code: Annotated[str, Path(min_length=1, max_length=64)],
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> VerifyCredentialOut:
    limiter.check("verify_ip", meta.ip or "unknown", get_profile_settings().rate("verify"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    try:
        res = service.verify_code(db, settings, code, clock.now())
    except keys.SigningKeyUnavailable as e:
        raise _key_error(e) from e
    if res is None:
        raise ApiError(404, "credential_not_found", "Паспорт с таким кодом не выдавался платформой")
    return _verify_out(res)


@router.post(
    "/verify",
    response_model=VerifyCredentialOut,
    summary="Проверить загруженный файл паспорта",
    description="Тело запроса — содержимое JSON-файла как есть (Content-Type любой). Изменённый хотя бы на один байт файл не пройдёт проверку подписи.",
    openapi_extra={"requestBody": {"required": True, "content": {"application/json": {"schema": {"type": "object"}}}}},
)
async def verify_file(request: Request, response: Response, db: DbSession, settings: SettingsDep, limiter: LimiterDep, meta: MetaDep) -> VerifyCredentialOut:
    limiter.check("verify_ip", meta.ip or "unknown", get_profile_settings().rate("verify"))
    response.headers["Cache-Control"] = "no-store"
    cfg = get_credentials_config()
    raw = await request.body()
    if len(raw) > cfg.max_body_bytes:
        raise bad_request("file_too_large", "Файл слишком большой для паспорта")
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return _verify_out(
            service.VerifyResult(False, [service.Check("format", False, "Файл читается как JSON", "Файл повреждён: это не корректный JSON")])
        )
    try:
        res = service.verify_document(db, settings, doc, clock.now(), with_name=True)
    except keys.SigningKeyUnavailable as e:
        raise _key_error(e) from e
    return _verify_out(res)
