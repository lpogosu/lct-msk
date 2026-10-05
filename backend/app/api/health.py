"""Служебные маршруты: проверка живости процесса и готовности (БД)."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession, SettingsDep
from app.api.schemas import HealthOut, LiveOut
from app.core.clock import clock
from app.core.errors import error_responses

log = logging.getLogger("vzaimno.health")

router = APIRouter(prefix="/health", tags=["Служебное"], responses=error_responses())


@router.get(
    "",
    response_model=HealthOut,
    summary="Готовность сервиса",
    description="Проверяет доступность PostgreSQL. 200 — всё работает, 503 — БД недоступна (status=degraded).",
    responses={503: {"model": HealthOut, "description": "БД недоступна"}},
)
def health(response: Response, db: DbSession, settings: SettingsDep) -> HealthOut:
    db_state = "ok"
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        log.warning("Проверка БД не прошла: %s", e)
        db_state = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        status="ok" if db_state == "ok" else "degraded",
        db=db_state,
        version=settings.version,
        demo_mode=settings.demo_mode,
        time=clock.now(),
    )


@router.get(
    "/live",
    response_model=LiveOut,
    summary="Процесс жив",
    description="Не обращается к БД: используется healthcheck контейнера.",
)
def live() -> LiveOut:
    return LiveOut(status="ok")
