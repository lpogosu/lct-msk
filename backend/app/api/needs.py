"""Раздел «Потребности и подборка» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /needs (разбор брифа), GET /needs/{id}/categories, POST /needs/{id}/selections, PUT /needs/{id}/pins/{cid}, банк кандидатов (spec-contact §4, Р2–Р6, Р9).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/needs",
    "Потребности и подборка",
    "Потребности и подборка",
    "POST /needs (разбор брифа), GET /needs/{id}/categories, POST /needs/{id}/selections, PUT /needs/{id}/pins/{cid}, банк кандидатов (spec-contact §4, Р2–Р6, Р9).",
)
