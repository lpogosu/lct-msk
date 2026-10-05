"""Раздел «Регулярные задачи» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /tasks (тип A — каталог, тип B — «предложи подход»), GET /tasks/me, POST /tasks/assignments/{id}/submit|review (концепция §5.4, К11, Р11).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/tasks",
    "Регулярные задачи",
    "Регулярные задачи",
    "POST /tasks (тип A — каталог, тип B — «предложи подход»), GET /tasks/me, POST /tasks/assignments/{id}/submit|review (концепция §5.4, К11, Р11).",
)
