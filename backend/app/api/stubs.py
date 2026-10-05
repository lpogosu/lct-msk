"""Заглушка раздела API, который ещё не реализован: любой запрос → 501 с русским сообщением.

Никакого поддельного поведения: заглушка не возвращает данных и не меняет состояние.
Модуль раздела заменяет `router = make_stub_router(...)` на настоящий APIRouter с тем же prefix и тегом.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.errors import error_responses, not_implemented

_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE"]


def make_stub_router(prefix: str, tag: str, section: str, planned: str) -> APIRouter:
    """prefix — путь раздела (/survey), tag — тег OpenAPI, section — название раздела для сообщения,
    planned — какие маршруты здесь появятся (попадает в описание OpenAPI)."""
    router = APIRouter(prefix=prefix, tags=[tag], responses=error_responses(400, 401, 403, 404, 409, 422, 429, 501))
    description = f"Раздел «{section}» в разработке: любой запрос возвращает 501 `not_implemented`. Планируется: {planned}"

    def _stub() -> None:
        raise not_implemented(section)

    def _stub_path(path: str) -> None:
        raise not_implemented(section)

    slug = prefix.strip("/").replace("-", "_")
    # По маршруту на метод: у каждой операции OpenAPI свой operationId
    for method in _METHODS:
        router.add_api_route(
            "",
            _stub,
            methods=[method],
            summary=f"{section}: в разработке",
            description=description,
            operation_id=f"stub_{slug}_{method.lower()}",
            status_code=501,
        )
        router.add_api_route(
            "/{path:path}",
            _stub_path,
            methods=[method],
            summary=f"{section}: в разработке",
            description=description,
            operation_id=f"stub_{slug}_{method.lower()}_path",
            status_code=501,
        )
    return router
