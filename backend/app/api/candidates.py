"""Раздел «Профиль кандидата» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: GET/PUT /candidates/me (профиль, резюме вручную), POST /candidates/me/resume (PDF с автораспознаванием), GET /candidates/me/profile.pdf (стандартизированный PDF), настройки приватности и поиска К13, GET /candidates/me/preview (так вас видят).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/candidates",
    "Кандидат",
    "Профиль кандидата",
    "GET/PUT /candidates/me (профиль, резюме вручную), POST /candidates/me/resume (PDF с автораспознаванием), GET /candidates/me/profile.pdf (стандартизированный PDF), настройки приватности и поиска К13, GET /candidates/me/preview (так вас видят).",
)
