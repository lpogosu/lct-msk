"""Единый формат ошибок API: {"code": str, "message": str (рус.), "details": {...} | [...] | null}.

- code — стабильный машинный код (snake_case), по нему фронтенд выбирает экран/подсказку;
- message — готовая фраза для человека на русском;
- details — подробности (поля формы, retry_after_s, attempts_left и т.п.).

Каждый роутер документирует ответы 400/401/403/404/409/422/429 через `error_responses()`.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("vzaimno.errors")


class ErrorResponse(BaseModel):
    """Тело любой ошибки API."""

    code: str = Field(description="Машинный код ошибки", examples=["invalid_credentials"])
    message: str = Field(description="Сообщение для пользователя на русском", examples=["Неверный email или пароль"])
    details: dict[str, Any] | list[Any] | None = Field(default=None, description="Подробности (необязательно)")


class ApiError(Exception):
    """Бросайте из сервисов и роутеров: обработчик превратит в ErrorResponse с нужным HTTP-статусом."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | list[Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers

    def to_response(self) -> JSONResponse:
        body = ErrorResponse(code=self.code, message=self.message, details=self.details).model_dump()
        return JSONResponse(status_code=self.status_code, content=body, headers=self.headers)


# --- Удобные конструкторы частых ошибок ---


def bad_request(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(400, code, message, details)


def unauthorized(code: str = "unauthorized", message: str = "Требуется вход в систему", details: Any = None) -> ApiError:
    return ApiError(401, code, message, details, headers={"WWW-Authenticate": "Bearer"})


def forbidden(code: str = "forbidden", message: str = "Недостаточно прав для этого действия", details: Any = None) -> ApiError:
    return ApiError(403, code, message, details)


def not_found(code: str = "not_found", message: str = "Запись не найдена", details: Any = None) -> ApiError:
    return ApiError(404, code, message, details)


def conflict(code: str, message: str, details: Any = None) -> ApiError:
    return ApiError(409, code, message, details)


def too_many_requests(retry_after_s: float, code: str = "rate_limited", message: str | None = None) -> ApiError:
    seconds = max(1, int(round(retry_after_s + 0.4999)))
    return ApiError(
        429,
        code,
        message or f"Слишком много запросов. Повторите через {seconds} с",
        {"retry_after_s": seconds},
        headers={"Retry-After": str(seconds)},
    )


def not_implemented(section: str) -> ApiError:
    return ApiError(
        501,
        "not_implemented",
        f"Раздел «{section}» ещё в разработке",
        {"section": section},
    )


# --- Документация ответов для OpenAPI ---

_STATUS_DOCS: dict[int, tuple[str, str, str]] = {
    400: ("Некорректный запрос", "bad_request", "Запрос не может быть выполнен"),
    401: ("Требуется вход или токен недействителен", "unauthorized", "Требуется вход в систему"),
    403: ("Недостаточно прав", "forbidden", "Недостаточно прав для этого действия"),
    404: ("Не найдено", "not_found", "Запись не найдена"),
    409: ("Конфликт состояния", "conflict", "Действие противоречит текущему состоянию"),
    422: ("Ошибка валидации полей", "validation_error", "Проверьте заполнение полей"),
    429: ("Слишком много запросов", "rate_limited", "Слишком много запросов. Повторите через 30 с"),
    500: ("Внутренняя ошибка", "internal_error", "Внутренняя ошибка сервера. Мы уже разбираемся"),
    501: ("Раздел в разработке", "not_implemented", "Раздел «Опрос» ещё в разработке"),
    503: ("Сервис временно недоступен", "service_unavailable", "Сервис временно недоступен"),
}

STANDARD_ERRORS: tuple[int, ...] = (400, 401, 403, 404, 409, 422, 429)


def error_responses(*statuses: int, extra: dict[int, str] | None = None) -> dict[int | str, dict[str, Any]]:
    """`responses=` для FastAPI: каждая ошибка описана моделью ErrorResponse и примером.

    error_responses() без аргументов → стандартный набор 400/401/403/404/409/422/429.
    extra → переопределить описание статуса для конкретного маршрута.
    """
    out: dict[int | str, dict[str, Any]] = {}
    for status in statuses or STANDARD_ERRORS:
        title, code, message = _STATUS_DOCS[status]
        desc = (extra or {}).get(status, title)
        out[status] = {
            "model": ErrorResponse,
            "description": desc,
            "content": {"application/json": {"example": {"code": code, "message": message, "details": None}}},
        }
    return out


# --- Перевод ошибок валидации pydantic на русский ---

_PYDANTIC_RU: dict[str, str] = {
    "missing": "Обязательное поле",
    "string_too_short": "Слишком короткое значение",
    "string_too_long": "Слишком длинное значение",
    "string_type": "Ожидается строка",
    "string_pattern_mismatch": "Значение не соответствует формату",
    "int_parsing": "Ожидается целое число",
    "int_type": "Ожидается целое число",
    "float_parsing": "Ожидается число",
    "bool_parsing": "Ожидается да/нет",
    "bool_type": "Ожидается да/нет",
    "literal_error": "Недопустимое значение",
    "enum": "Недопустимое значение",
    "value_error": "Некорректное значение",
    "greater_than": "Слишком маленькое значение",
    "greater_than_equal": "Слишком маленькое значение",
    "less_than": "Слишком большое значение",
    "less_than_equal": "Слишком большое значение",
    "json_invalid": "Некорректный JSON",
    "model_attributes_type": "Ожидается объект",
    "dict_type": "Ожидается объект",
    "list_type": "Ожидается список",
    "uuid_parsing": "Некорректный идентификатор",
    "uuid_type": "Некорректный идентификатор",
    "datetime_parsing": "Некорректная дата и время",
    "date_parsing": "Некорректная дата",
    "extra_forbidden": "Лишнее поле",
    "too_short": "Слишком мало элементов",
    "too_long": "Слишком много элементов",
}


def _has_cyrillic(text: str) -> bool:
    return any("а" <= ch <= "я" or ch == "ё" for ch in text.lower())


def _plural_ru(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def _count_ru(n: Any, kind: str) -> str:
    """«1 символ», «3 символа», «8 символов» (элементов — аналогично)."""
    try:
        k = int(n)
    except (TypeError, ValueError):
        return f"{n} {kind}"
    forms = ("символ", "символа", "символов") if kind == "chars" else ("элемент", "элемента", "элементов")
    return f"{k} {_plural_ru(k, *forms)}"


def _ru_validation_message(err: dict[str, Any]) -> str:
    etype = err.get("type", "")
    ctx = err.get("ctx") or {}
    if etype == "value_error":
        # Наши валидаторы пишут русский текст в ValueError — показываем его без префикса pydantic.
        reason = ctx.get("error")
        text = str(reason) if reason is not None else err.get("msg", "")
        text = text.removeprefix("Value error, ")
        if _has_cyrillic(text):
            return text
        # Английский текст библиотек (email-validator и т. п.) пользователю не показываем
        if "email" in text.lower():
            return "Некорректный адрес электронной почты"
        return _PYDANTIC_RU["value_error"]
    if etype in ("string_too_short", "too_short") and "min_length" in ctx:
        return f"Минимум {_count_ru(ctx['min_length'], 'chars' if etype == 'string_too_short' else 'items')}"
    if etype in ("string_too_long", "too_long") and "max_length" in ctx:
        return f"Максимум {_count_ru(ctx['max_length'], 'chars' if etype == 'string_too_long' else 'items')}"
    if etype in ("literal_error", "enum") and "expected" in ctx:
        # pydantic пишет "'a', 'b' or 'c'" — союз «or» по-английски, заменяем перечислением
        expected = str(ctx["expected"]).replace(" or ", ", ")
        return f"Допустимые значения: {expected}"
    return _PYDANTIC_RU.get(etype, "Некорректное значение")


def validation_details(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for err in errors:
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path", "header", "cookie")]
        out.append({"field": ".".join(loc) or None, "message": _ru_validation_message(err), "type": err.get("type")})
    return out


# --- Обработчики ---


async def _api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
    return exc.to_response()


_HTTP_CODES = {
    400: ("bad_request", "Некорректный запрос"),
    401: ("unauthorized", "Требуется вход в систему"),
    403: ("forbidden", "Недостаточно прав для этого действия"),
    404: ("not_found", "Ресурс не найден"),
    405: ("method_not_allowed", "Метод не поддерживается"),
    409: ("conflict", "Конфликт состояния"),
    413: ("payload_too_large", "Слишком большой запрос"),
    415: ("unsupported_media_type", "Неподдерживаемый формат данных"),
    429: ("rate_limited", "Слишком много запросов"),
}


async def _http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    code, message = _HTTP_CODES.get(exc.status_code, ("http_error", "Ошибка запроса"))
    # Сообщение от FastAPI по умолчанию английское — подменяем; русское из кода оставляем.
    detail = exc.detail if isinstance(exc.detail, str) and any("а" <= c.lower() <= "я" for c in exc.detail) else None
    body = ErrorResponse(code=code, message=detail or message).model_dump()
    return JSONResponse(status_code=exc.status_code, content=body, headers=getattr(exc, "headers", None))


async def _validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    body = ErrorResponse(
        code="validation_error",
        message="Проверьте заполнение полей",
        details=validation_details(list(exc.errors())),
    ).model_dump()
    return JSONResponse(status_code=422, content=body)


def internal_error_response(exc: BaseException) -> JSONResponse:
    """500 в едином формате. Вызывается и из обработчика Starlette, и из middleware приложения
    (внутри CORS — иначе браузер не увидел бы тело ответа, см. app/main.py)."""
    log.error("Необработанная ошибка: %s", exc, exc_info=(type(exc), exc, exc.__traceback__))
    body = ErrorResponse(code="internal_error", message="Внутренняя ошибка сервера. Мы уже разбираемся").model_dump()
    return JSONResponse(status_code=500, content=body)


async def _unhandled_handler(_: Request, exc: Exception) -> JSONResponse:
    return internal_error_response(exc)


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error_handler)  # type: ignore[arg-type]
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)  # type: ignore[arg-type]
    app.add_exception_handler(RequestValidationError, _validation_handler)  # type: ignore[arg-type]
    app.add_exception_handler(Exception, _unhandled_handler)
