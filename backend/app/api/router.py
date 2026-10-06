"""Сборка всех роутеров под префиксом /api и описание тегов OpenAPI (на русском)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    admin,
    anticheat,
    applications,
    auth,
    bank,
    candidates,
    companies,
    consents,
    demo,
    fsp,
    health,
    invitations,
    lab,
    needs,
    notifications,
    survey,
    tasks,
    testing,
    vacancies,
    verify,
)

API_PREFIX = "/api"

OPENAPI_TAGS: list[dict[str, str]] = [
    {"name": "Служебное", "description": "Проверка живости и готовности сервиса."},
    {
        "name": "Аутентификация",
        "description": "Регистрация по email с 6-значным кодом (15 минут), вход, восстановление пароля по коду, обновление сессии (refresh в httpOnly-cookie с ротацией), выход.",
    },
    {"name": "Согласия", "description": "Журнал согласий 152-ФЗ: каждое согласие — отдельная запись с версией текста, отзыв без удаления."},
    {"name": "Опрос", "description": "Опрос кандидата: отрасли, действия за 12 месяцев, технологии и стаж, специализация."},
    {"name": "Тестирование", "description": "Адаптивный тест с одноразовыми экземплярами заданий, результат и квалификация."},
    {"name": "Потребности и подборка", "description": "Бриф работодателя с вилкой, категории и охват, версии подборки, шортлист, банк кандидатов."},
    {"name": "Приглашения", "description": "Приглашение с зарплатой, ответы «Да / Да, если… / Нет», раскрытие контактов по взаимному согласию."},
    {"name": "Отклики", "description": "Самостоятельный отклик кандидата на опубликованную вакансию."},
    {"name": "Вакансии", "description": "Опубликованные брифы и «как мы проверим» навыки вакансии."},
    {"name": "Регулярные задачи", "description": "Короткие задания от компаний и тренировочные задачи платформы."},
    {"name": "ФСП", "description": "Привязка ФСП ID (Keycloak) и достижения из реестра ФСП."},
    {"name": "Лаборатория", "description": "Трасса сессии, банк заданий, флаги целостности, валидация, сверки «двойника в буфере» (роль «методист»)."},
    {
        "name": "Антиподсказчик",
        "description": "«Подтверждение за 5 минут» для работодателя (после раскрытия контакта) и песочница жюри «Попробуй списать» (DEMO_MODE).",
    },
    {"name": "Компании", "description": "Профиль компании-работодателя и интеграция с ATS (вебхуки)."},
    {"name": "Кандидат", "description": "Профиль и резюме кандидата, стандартизированный PDF-профиль, настройки приватности и поиска."},
    {"name": "Уведомления", "description": "Уведомления внутри платформы."},
    {"name": "Проверка профиля", "description": "Публичная сверка стандартизированного PDF-профиля по QR-коду."},
    {"name": "Администрирование", "description": "Импорт данных жюри и демо-инструменты."},
]

api_router = APIRouter(prefix=API_PREFIX)
for module in (
    health, auth, consents, companies, candidates, survey, testing, needs, bank, invitations, applications,
    vacancies, tasks, notifications, fsp, verify, lab, anticheat, demo, admin,
):
    api_router.include_router(module.router)
