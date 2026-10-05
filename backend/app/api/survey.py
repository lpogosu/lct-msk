"""Раздел «Опрос» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /survey (шаг опроса с сохранением), GET /survey/current, GET /survey/history, подсказка классификатора специализации (концепция §2.4, К2).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/survey",
    "Опрос",
    "Опрос",
    "POST /survey (шаг опроса с сохранением), GET /survey/current, GET /survey/history, подсказка классификатора специализации (концепция §2.4, К2).",
)
