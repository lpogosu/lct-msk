"""Сборка всех роутеров под префиксом /api и описание тегов OpenAPI (на русском)."""

from __future__ import annotations

from fastapi import APIRouter

from app.api import (
    admin,
    applications,
    auth,
    consents,
    fsp,
    health,
    invitations,
    lab,
    needs,
    survey,
    tasks,
    testing,
    vacancies,
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
    {"name": "Лаборатория", "description": "Трасса сессии, банк заданий, флаги целостности, валидация (роль «методист»)."},
    {"name": "Администрирование", "description": "Импорт данных жюри и демо-инструменты."},
]

api_router = APIRouter(prefix=API_PREFIX)
for module in (health, auth, consents, survey, testing, needs, invitations, applications, vacancies, tasks, fsp, lab, admin):
    api_router.include_router(module.router)
