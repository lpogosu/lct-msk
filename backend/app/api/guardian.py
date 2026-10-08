"""Публичное подтверждение согласия законного представителя по ссылке из письма (кандидаты 15–17 лет, SECURITY.md п. 1.4)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import DbSession, LimiterDep, MetaDep, SettingsDep
from app.core.clock import clock
from app.core.errors import error_responses
from app.services.profile import guardian

router = APIRouter(prefix="/guardian", tags=["Кандидат"], responses=error_responses(400, 401, 403, 404, 409, 422, 429, extra={410: "Срок ссылки истёк"}))


class GuardianConfirmIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=20, max_length=200, description="Токен из ссылки в письме")


class GuardianConfirmOut(BaseModel):
    confirmed: bool
    message: str


@router.post(
    "/confirm",
    response_model=GuardianConfirmOut,
    summary="Подтвердить согласие законного представителя",
    description=(
        "Без входа: открывает представитель по ссылке из письма. Токен одноразовый и действует `guardian.token_ttl_hours`. "
        "404 `guardian_token_invalid` — ссылка неверна или уже использована; 410 `guardian_token_expired` — срок вышел."
    ),
)
def confirm(body: GuardianConfirmIn, db: DbSession, settings: SettingsDep, limiter: LimiterDep, meta: MetaDep) -> GuardianConfirmOut:
    limiter.check("guardian_ip", meta.ip or "unknown", settings.rate("guardian_ip"))
    return GuardianConfirmOut.model_validate(guardian.confirm(db, body.token, clock.now(), ip=meta.ip))
