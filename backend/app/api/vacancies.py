"""Раздел «Вакансии» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: Публикация брифа как вакансии, список по Score для кандидата, POST /vacancies/{id}/test-profile, GET /vacancies/{id}/test-preview (концепция §5.5, К12, Р10).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/vacancies",
    "Вакансии",
    "Вакансии",
    "Публикация брифа как вакансии, список по Score для кандидата, POST /vacancies/{id}/test-profile, GET /vacancies/{id}/test-preview (концепция §5.5, К12, Р10).",
)
