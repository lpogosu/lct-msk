"""Раздел «Уведомления» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: GET /notifications, POST /notifications/{id}/read — уведомления внутри платформы.
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/notifications",
    "Уведомления",
    "Уведомления",
    "GET /notifications, POST /notifications/{id}/read — уведомления внутри платформы.",
)
