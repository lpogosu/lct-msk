"""Публичная сверка стандартизированного PDF-профиля по QR-коду: /api/v/{код} (концепция §3.1.2).

Страница отвечает на вопрос «этот PDF выдала платформа и он не изменён?»: категория, дата подтверждения
тестом, номер сессии, дата данных реестра ФСП. ПДн здесь нет — только то, что было в документе без имени
(для полной версии — инициалы). Неизвестный код — `valid: false` (изменённый или поддельный документ).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Response

from app.api.deps import DbSession, LimiterDep, MetaDep
from app.core.clock import clock
from app.core.errors import error_responses
from app.services.pdf import issue as pdf_issue
from app.services.pdf.schemas import VerifyOut
from app.services.profile.settings import get_profile_settings

router = APIRouter(prefix="/v", tags=["Проверка профиля"], responses=error_responses())


@router.get(
    "/{code}",
    response_model=VerifyOut,
    summary="Сверить PDF-профиль",
    description=(
        "Публичная страница сверки по коду из QR. `valid: true` — документ выдан платформой и не изменён: "
        "категория (специализация · грейд), дата подтверждения тестом, номер сессии, уверенность, интервал на "
        "линейке, данные ФСП из реестра на дату, актуальность сейчас. `valid: false` — такого документа платформа "
        "не выдавала. Доступно без входа; 429 — слишком частые запросы с одного адреса."
    ),
)
def verify(
    response: Response,
    db: DbSession,
    limiter: LimiterDep,
    meta: MetaDep,
    code: Annotated[str, Path(min_length=1, max_length=64, description="Код сверки из QR (32 символа)")],
) -> VerifyOut:
    limiter.check("verify_ip", meta.ip or "unknown", get_profile_settings().rate("verify"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex"
    return VerifyOut.model_validate(pdf_issue.verify(db, code, clock.now()))
