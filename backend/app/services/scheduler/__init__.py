"""Встроенный планировщик фоновых задач (хардненинг п. 9; config/scheduler.yaml).

asyncio-цикл запускается в lifespan приложения (app/main.py) и раз в минуту применяет сроки приглашений и откликов
(sweep), а ночью — калибровку заданий с CUSUM дрейфа и чистку по срокам хранения. В тестах (VZ_ENV=test) не запускается.
"""

from app.services.scheduler.runner import (
    JobSpec,
    Scheduler,
    build_jobs,
    scheduler_enabled,
    scheduler_lifespan,
)

__all__ = ["JobSpec", "Scheduler", "build_jobs", "scheduler_enabled", "scheduler_lifespan"]
