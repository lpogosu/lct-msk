"""Планировщик: расписание, прогон задач, защита от двойного запуска, asyncio-цикл.

Три задачи (config/scheduler.yaml):
- contact_sweep — каждую минуту: ленивые сроки приглашений и откликов для всех сразу (истекло, «нет ответа», напоминания)
  и повтор вебхуков ATS (app/services/contact/sweep.py);
- calibration — ночью: калибровка заданий и CUSUM дрейфа по всем тестируемым специализациям (то же, что
  POST /api/lab/calibrate без специализации; spec-testing §3.9–3.10);
- retention — ночью: сроки хранения персональных данных (app/services/account/retention.py).

Как устроено. Цикл раз в `tick_s` смотрит, какая задача подошла, и запускает её в потоке (БД синхронная) со своей сессией.
Расписание считается по настенному времени UTC, а не по часам платформы (DEMO_MODE сдвигает их для демонстрации сроков):
сдвиг «+1 день» не должен запускать ночные задачи. Суточные задачи помнят прошлый запуск в журнале аудита
(scheduler.<имя>) — после перезапуска в ту же ночь второй прогон не делается. Несколько процессов (контейнеров) —
задачу берёт тот, кто получил рекомендательную блокировку PostgreSQL (pg_try_advisory_lock на отдельном соединении:
транзакции самих задач коммитятся по ходу дела, а блокировка живёт, пока задача не закончена); остальные пропускают.
Исключение в задаче логируется, цикл живёт дальше, следующий прогон — по расписанию.
"""

from __future__ import annotations

import asyncio
import logging
import time as _time
import zlib
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.configs import load_config
from app.core.settings import Settings
from app.models.users import AuditLog

log = logging.getLogger("vzaimno.scheduler")

AUDIT_PREFIX = "scheduler."
LOCK_NAMESPACE = 0x5A_41_49  # старшая часть ключа блокировки: чужие advisory-локи с тем же числом не пересекутся

JobFn = Callable[[Session, Settings, dict[str, Any]], dict[str, Any]]


@dataclass
class JobSpec:
    name: str
    fn: JobFn
    options: dict[str, Any]
    every_s: float | None = None  # интервальная задача
    at_utc: time | None = None  # суточная задача (время суток UTC)
    audited: bool = False  # писать итог в журнал аудита (нужно суточным: по нему определяется «сегодня уже было»)
    last_run: datetime | None = None
    next_run: datetime | None = None
    runs: int = 0
    failures: int = 0
    last_result: dict[str, Any] | None = field(default=None, repr=False)

    def due(self, now: datetime) -> bool:
        if self.every_s is not None:
            return self.next_run is not None and now >= self.next_run
        assert self.at_utc is not None
        today_at = datetime.combine(now.date(), self.at_utc, tzinfo=UTC)
        return now >= today_at and (self.last_run is None or self.last_run < today_at)

    def mark_ran(self, now: datetime) -> None:
        self.last_run = now
        self.runs += 1
        if self.every_s is not None:
            self.next_run = now + timedelta(seconds=self.every_s)


# --------------------------------------------------------------------------- задачи


def job_contact_sweep(db: Session, settings: Settings, opts: dict[str, Any]) -> dict[str, Any]:
    from app.services.contact import sweep

    secret = settings.pseudonym_secret.get_secret_value()
    if settings.demo_mode:
        # DEMO_MODE: часы платформы сдвигает демо-панель — не пересекаемся с ней (общий замок часов)
        from app.services.demo import clockctl

        with clockctl.CLOCK_LOCK:
            return sweep(db, secret=secret, limit=int(opts["limit"]))
    return sweep(db, secret=secret, limit=int(opts["limit"]))


def job_calibration(db: Session, settings: Settings, opts: dict[str, Any]) -> dict[str, Any]:
    from app.services.testing import lab

    out = lab.calibrate(db, None)
    db.commit()
    drift = sum(
        1 for rep in out.report.values() for d in rep.get("drift", ()) if d.get("action") == "quarantine"
    )
    return {
        "run_id": str(out.run_id),
        "specializations": out.specializations,
        "n_responses": out.n_responses,
        "families_updated": out.families_updated,
        "quarantined": drift,
    }


def job_retention(db: Session, settings: Settings, opts: dict[str, Any]) -> dict[str, Any]:
    from app.services.account import retention_sweep

    return retention_sweep(db)


JOB_FUNCTIONS: dict[str, JobFn] = {
    "contact_sweep": job_contact_sweep,
    "calibration": job_calibration,
    "retention": job_retention,
}


# --------------------------------------------------------------------------- конфиг → задачи


def scheduler_config() -> dict[str, Any]:
    return load_config("scheduler")


def scheduler_enabled(settings: Settings) -> bool:
    """VZ_SCHEDULER_ENABLED важнее конфига; в тестах (VZ_ENV=test) планировщик не запускается никогда."""
    if settings.env == "test":
        return False
    if settings.scheduler_enabled is not None:
        return settings.scheduler_enabled
    return bool(scheduler_config()["enabled"])


def _parse_at(value: str) -> time:
    hh, _, mm = str(value).partition(":")
    return time(int(hh), int(mm or 0), tzinfo=UTC)


def build_jobs(config: dict[str, Any] | None = None) -> list[JobSpec]:
    cfg = config or scheduler_config()
    jobs: list[JobSpec] = []
    for name, opts in cfg["jobs"].items():
        if not opts.get("enabled", True):
            continue
        fn = JOB_FUNCTIONS[name]
        if "every_s" in opts:
            jobs.append(JobSpec(name, fn, opts, every_s=float(opts["every_s"])))
        else:
            jobs.append(JobSpec(name, fn, opts, at_utc=_parse_at(opts["at_utc"]), audited=True))
    return jobs


def _lock_key(name: str) -> int:
    return (LOCK_NAMESPACE << 32) | zlib.crc32(name.encode())


class Scheduler:
    """Расписание и прогон задач. now_fn и session_factory подменяются в тестах; цикл — run_forever()."""

    def __init__(
        self,
        settings: Settings,
        session_factory: Callable[[], Session],
        *,
        jobs: list[JobSpec] | None = None,
        now_fn: Callable[[], datetime] = lambda: datetime.now(UTC),
        config: dict[str, Any] | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.config = config or scheduler_config()
        self.jobs = jobs if jobs is not None else build_jobs(self.config)
        self.now_fn = now_fn
        self._started = False

    def start(self) -> None:
        """Первые сроки: интервальные — после стартовой паузы; суточные — по журналу (было ли сегодня)."""
        now = self.now_fn()
        delay = float(self.config.get("startup_delay_s", 0))
        for job in self.jobs:
            if job.every_s is not None:
                job.next_run = now + timedelta(seconds=delay)
            else:
                job.last_run = self._last_audited_run(job.name)
        self._started = True

    def _last_audited_run(self, name: str) -> datetime | None:
        try:
            with self.session_factory() as db:
                return db.scalar(select(func.max(AuditLog.at)).where(AuditLog.action == AUDIT_PREFIX + name))
        except Exception:  # noqa: BLE001 — БД ещё не готова: считаем, что запусков не было
            log.warning("Планировщик: не удалось прочитать прошлый запуск %s", name)
            return None

    def due_jobs(self) -> list[JobSpec]:
        now = self.now_fn()
        return [j for j in self.jobs if j.due(now)]

    def run_job(self, job: JobSpec) -> dict[str, Any] | None:
        """Один прогон в текущем потоке. None — пропущен (задачу ведёт другой процесс) или упал."""
        started = _time.monotonic()
        try:
            with self.session_factory() as db:
                with db.get_bind().engine.connect() as lock_conn:
                    key = _lock_key(job.name)
                    if not lock_conn.execute(select(func.pg_try_advisory_lock(key))).scalar():
                        log.debug("Планировщик: %s выполняет другой процесс", job.name)
                        job.mark_ran(self.now_fn())
                        return None
                    try:
                        result = job.fn(db, self.settings, job.options)
                        if job.audited:
                            entry = audit(
                                db,
                                AUDIT_PREFIX + job.name,
                                actor_role="system",
                                entity_type="scheduler",
                                seconds=round(_time.monotonic() - started, 2),
                                **{k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))},
                            )
                            entry.at = self.now_fn()  # настенное время: часы демо не должны сдвигать «когда было»
                        db.commit()
                    finally:
                        lock_conn.execute(select(func.pg_advisory_unlock(key)))
                        lock_conn.commit()
        except Exception:
            job.failures += 1
            job.mark_ran(self.now_fn())
            log.exception("Планировщик: задача %s завершилась ошибкой (следующий прогон по расписанию)", job.name)
            return None
        job.mark_ran(self.now_fn())
        job.last_result = result
        log.info("Планировщик: %s выполнена за %.2f с: %s", job.name, _time.monotonic() - started, result)
        return result

    def tick(self) -> int:
        """Запустить всё, что подошло (синхронно). Возвращает число запущенных задач."""
        if not self._started:
            self.start()
        due = self.due_jobs()
        for job in due:
            self.run_job(job)
        return len(due)

    async def run_forever(self) -> None:
        tick_s = float(self.config["tick_s"])
        await asyncio.to_thread(self.start)
        while True:
            try:
                for job in self.due_jobs():
                    await asyncio.to_thread(self.run_job, job)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 — цикл не должен падать
                log.exception("Планировщик: сбой цикла")
            await asyncio.sleep(tick_s)


@asynccontextmanager
async def scheduler_lifespan(app: Any, settings: Settings) -> AsyncIterator[None]:
    """Фоновая задача на время жизни приложения. app.state.scheduler_loop = False или VZ_ENV=test — не запускать;
    фабрику сессий можно подменить через app.state.scheduler_session_factory."""
    if not scheduler_enabled(settings) or not getattr(app.state, "scheduler_loop", True):
        yield
        return
    from app.db.session import get_sessionmaker

    factory = getattr(app.state, "scheduler_session_factory", None) or get_sessionmaker()
    scheduler = Scheduler(settings, factory)
    app.state.scheduler = scheduler
    task = asyncio.create_task(scheduler.run_forever(), name="vzaimno-scheduler")
    log.info("Планировщик запущен: %s", ", ".join(j.name for j in scheduler.jobs))
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
