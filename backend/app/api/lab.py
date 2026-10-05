"""Раздел «Лаборатория» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: GET /lab/sessions/{id}/trace, GET /lab/bank, GET /lab/bank/{family}/clones, GET /lab/flags, GET /lab/validation, POST /lab/calibrate (концепция §9.4, Л1–Л4).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/lab",
    "Лаборатория",
    "Лаборатория",
    "GET /lab/sessions/{id}/trace, GET /lab/bank, GET /lab/bank/{family}/clones, GET /lab/flags, GET /lab/validation, POST /lab/calibrate (концепция §9.4, Л1–Л4).",
)
