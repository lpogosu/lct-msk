"""Раздел «Приглашения» — заглушка (501). Модуль раздела заменит её настоящим роутером.

Планируется: POST /invitations, POST /invitations/batch, POST /invitations/{id}/view|accept|decline|counter|withdraw, …/counter/accept|decline, …/contacted|dispute|outcome|check (spec-contact §4, К9–К10, Р7–Р8).
"""

from app.api.stubs import make_stub_router

router = make_stub_router(
    "/invitations",
    "Приглашения",
    "Приглашения",
    "POST /invitations, POST /invitations/batch, POST /invitations/{id}/view|accept|decline|counter|withdraw, …/counter/accept|decline, …/contacted|dispute|outcome|check (spec-contact §4, К9–К10, Р7–Р8).",
)
