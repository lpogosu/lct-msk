"""Служебные маршруты: проверка живости процесса и готовности (БД)."""

from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Query, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.api.deps import DbSession, SettingsDep
from app.api.schemas import HealthOut, LiveOut
from app.core.clock import clock
from app.core.errors import error_responses
from app.services.testing import pool
from app.services.testing.schemas import PoolReadinessOut

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
    db_state: Literal["ok", "unavailable"] = "ok"
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as e:
        log.warning("Проверка БД не прошла: %s", e)
        db_state = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    pool_ready: bool | None = None
    pool_progress: float | None = None
    if db_state == "ok":
        try:
            r = pool.readiness(db)
            pool_ready, pool_progress = r.ready, r.progress
        except SQLAlchemyError as e:  # таблицы пула ещё не созданы (миграции идут) — не роняем health
            log.info("Готовность пула не определена: %s", e)
            db.rollback()
    return HealthOut(
        pool_ready=pool_ready,
        pool_progress=pool_progress,
        status="ok" if db_state == "ok" else "degraded",
        db=db_state,
        version=settings.version,
        demo_mode=settings.demo_mode,
        time=clock.now(),
    )


@router.get(
    "/pool",
    response_model=PoolReadinessOut,
    summary="Готовность пула заданий",
    description=(
        "Без входа. После `docker compose up` пул одноразовых заданий греет сервис pool (≈ 2 минуты): "
        "пока `ready=false`, тест не стартует, а экран брифинга и /jury показывают «Готовим одноразовые задания…» "
        "с прогрессом. `?specialization=` — готовность по одной специализации."
    ),
)
def pool_readiness(db: DbSession, specialization: str | None = Query(default=None, max_length=32)) -> PoolReadinessOut:
    return PoolReadinessOut.model_validate(pool.readiness(db, specialization).to_dict())


@router.get(
    "/live",
    response_model=LiveOut,
    summary="Процесс жив",
    description="Не обращается к БД: используется healthcheck контейнера.",
)
def live() -> LiveOut:
    return LiveOut(status="ok")
