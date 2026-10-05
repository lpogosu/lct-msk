"""Раздел «Демо-режим» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /demo/clock (+1 день / +3 дня), POST /demo/reset, GET /demo/accounts (ступенчатые демо-аккаунты), режим жюри.
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/demo",
    "Администрирование",
    "Демо-режим",
    "POST /demo/clock (+1 день / +3 дня), POST /demo/reset, GET /demo/accounts (ступенчатые демо-аккаунты), режим жюри.",
)
