"""Раздел «Тестирование» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /tests/sessions, GET /tests/sessions/{id}/next, POST …/answer (идемпотентно), POST …/events, POST …/extension, GET …/result, GET …/review, GET /tests/qualification (spec-testing §4, К3–К6).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/tests",
    "Тестирование",
    "Тестирование",
    "POST /tests/sessions, GET /tests/sessions/{id}/next, POST …/answer (идемпотентно), POST …/events, POST …/extension, GET …/result, GET …/review, GET /tests/qualification (spec-testing §4, К3–К6).",
)
