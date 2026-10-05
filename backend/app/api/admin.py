"""Раздел «Администрирование» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /admin/import (кандидаты и брифы жюри), демо-инструменты advance/reset в DEMO_MODE (концепция §7.3, DECISIONS п. 5).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/admin",
    "Администрирование",
    "Администрирование",
    "POST /admin/import (кандидаты и брифы жюри), демо-инструменты advance/reset в DEMO_MODE (концепция §7.3, DECISIONS п. 5).",
)
