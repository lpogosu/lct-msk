"""Точка входа FastAPI: фабрика приложения, CORS, обработчики ошибок, роутеры, документация.

Запуск: `uvicorn app.main:app` (в compose — после `alembic upgrade head`).
Документация: /api/docs (Swagger UI), /api/redoc (ReDoc без Google Fonts), /api/openapi.json.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse
from starlette.responses import Response

from app.api.router import API_PREFIX, OPENAPI_TAGS, api_router
from app.core.clock import clock
from app.core.errors import install_error_handlers, internal_error_response
from app.core.rate_limit import RateLimiter
from app.core.settings import Settings, get_settings
from app.mail.sender import Mailer, make_mailer

log = logging.getLogger("vzaimno")

DESCRIPTION = """
**«Взаимно»** — платформа обратного подбора ИТ-специалистов с верифицированным профилем ФСП.

Кандидат проходит опрос и адаптивный тест и получает категорию «специализация × грейд».
Работодатель описывает потребность текстом с обязательной вилкой и получает слепую объяснимую подборку.
Контакты раскрываются только после взаимного «да» и отдельного согласия кандидата.

**Аутентификация.** Access-токен (JWT, 15 минут) — в заголовке `Authorization: Bearer …`.
Refresh-токен — в httpOnly-cookie `vz_refresh` (путь `/api/auth`), обновляется ротацией через `POST /api/auth/refresh`.

**Ошибки.** Любая ошибка возвращается в едином формате `{"code", "message", "details"}`:
`code` — машинный код, `message` — готовый текст на русском, `details` — подробности
(поля формы, `retry_after_s` для 429 и т. п.).
"""


async def _security_headers(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
    """Заголовки безопасности + перехват необработанных исключений.

    Middleware стоит ВНУТРИ CORSMiddleware: 500 превращается в JSON здесь, поэтому ответ проходит
    через CORS и получает Access-Control-Allow-Origin — фронтенд (другой origin) может прочитать
    {code, message}. Обработчик Exception у Starlette срабатывает снаружи CORS и заголовков не ставит.
    """
    try:
        response: Response = await call_next(request)
    except Exception as exc:
        response = internal_error_response(exc)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    if request.url.path.startswith(f"{API_PREFIX}/auth"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _load_demo_clock() -> None:
    """DEMO_MODE: восстановить смещение часов из БД (кнопки «+1 день» переживают перезапуск)."""
    try:
        from app.db.session import get_sessionmaker
        from app.models.system import DemoClock

        with get_sessionmaker()() as db:
            row = db.get(DemoClock, 1)
            if row is not None:
                clock.set_offset(row.offset_seconds)
                log.info("DEMO_MODE: смещение часов %s с", row.offset_seconds)
    except Exception as e:  # БД может быть ещё не готова — демо-часы стартуют без смещения
        log.warning("DEMO_MODE: не удалось прочитать смещение часов: %s", e)


def create_app(settings: Settings | None = None, mailer: Mailer | None = None) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        if settings.demo_mode:
            _load_demo_clock()
        yield

    app = FastAPI(
        title="Взаимно API",
        description=DESCRIPTION,
        version=settings.version,
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=None,
        redoc_url=None,
        openapi_tags=OPENAPI_TAGS,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.mailer = mailer or make_mailer(settings)
    app.state.rate_limiter = RateLimiter()

    # Порядок важен: добавленный позже middleware — внешний. CORS снаружи, заголовки/500 внутри.
    app.middleware("http")(_security_headers)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,  # refresh-cookie
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Retry-After"],
    )
    install_error_handlers(app)
    app.include_router(api_router)

    @app.get(f"{API_PREFIX}/docs", include_in_schema=False)
    def swagger_ui() -> HTMLResponse:
        return get_swagger_ui_html(
            openapi_url=app.openapi_url or "",
            title="Взаимно API — Swagger UI",
            swagger_ui_parameters={"persistAuthorization": True, "displayRequestDuration": True},
        )

    @app.get(f"{API_PREFIX}/redoc", include_in_schema=False)
    def redoc() -> HTMLResponse:
        # Google Fonts отключены: документация не тянет внешние шрифты (152-ФЗ, работа без внешних CDN шрифтов)
        return get_redoc_html(openapi_url=app.openapi_url or "", title="Взаимно API — ReDoc", with_google_fonts=False)

    return app


app = create_app()
