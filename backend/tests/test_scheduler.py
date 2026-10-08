"""Встроенный планировщик (хардненинг п. 9): расписание, прогон задач, защита от двойного запуска, lifespan."""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, text

from app.core.settings import Settings
from app.models.users import AuditLog
from app.services.scheduler import Scheduler, build_jobs, runner, scheduler_enabled, scheduler_lifespan

NOW = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, env="test", mail_backend="console", **kw)


def test_config_defines_minute_sweep_and_nightly_jobs():
    jobs = {j.name: j for j in build_jobs()}
    assert set(jobs) == {"contact_sweep", "calibration", "retention", "task_offers"}
    assert jobs["task_offers"].every_s == 3600
    assert jobs["contact_sweep"].every_s == 60 and jobs["contact_sweep"].at_utc is None
    assert jobs["calibration"].at_utc is not None and jobs["calibration"].every_s is None
    assert jobs["retention"].at_utc is not None and jobs["calibration"].at_utc != jobs["retention"].at_utc


def test_disabled_in_tests_and_by_flag():
    assert scheduler_enabled(_settings()) is False  # VZ_ENV=test — никогда
    dev = Settings(_env_file=None, env="dev", mail_backend="console")
    assert scheduler_enabled(dev) is True  # config/scheduler.yaml: enabled
    assert scheduler_enabled(Settings(_env_file=None, env="dev", mail_backend="console", scheduler_enabled=False)) is False
    assert scheduler_enabled(Settings(_env_file=None, env="test", mail_backend="console", scheduler_enabled=True)) is False


def test_interval_job_is_due_after_startup_delay_and_reschedules():
    job = next(j for j in build_jobs() if j.name == "contact_sweep")
    sched = Scheduler(_settings(), lambda: None, jobs=[job], now_fn=lambda: NOW)  # type: ignore[arg-type]
    sched.start()
    assert not job.due(NOW) and job.due(NOW + timedelta(seconds=sched.config["startup_delay_s"] + 1))
    job.mark_ran(NOW + timedelta(seconds=20))
    assert not job.due(NOW + timedelta(seconds=70)) and job.due(NOW + timedelta(seconds=81))


def test_daily_job_runs_once_per_day_after_its_time():
    job = next(j for j in build_jobs() if j.name == "calibration")  # 00:00 UTC
    midnight = datetime(2026, 10, 6, 0, 0, tzinfo=UTC)
    assert job.due(midnight + timedelta(minutes=1))  # ещё не запускалась
    job.mark_ran(midnight + timedelta(minutes=1))
    assert not job.due(midnight + timedelta(hours=10))  # сегодня уже было
    assert job.due(midnight + timedelta(days=1, minutes=1))  # завтра — снова
    late = next(j for j in build_jobs() if j.name == "retention")  # 00:30 UTC: до 00:30 не due
    assert not late.due(midnight + timedelta(minutes=10)) and late.due(midnight + timedelta(minutes=31))


@pytest.fixture
def factory(db_session):
    @contextmanager
    def _factory():
        yield db_session  # сессия теста живёт в транзакции, которую откатывает фикстура

    return _factory


def test_run_job_executes_with_session_and_audits_nightly_job(factory, db_session):
    clock_now = [NOW]
    sched = Scheduler(_settings(), factory, now_fn=lambda: clock_now[0])  # type: ignore[arg-type]
    job = next(j for j in sched.jobs if j.name == "retention")
    result = sched.run_job(job)
    assert result is not None and "unverified_accounts" in result
    rows = db_session.scalars(select(AuditLog).where(AuditLog.action == "scheduler.retention")).all()
    assert len(rows) == 1 and rows[0].actor_role == "system" and rows[0].at == NOW
    # после перезапуска «сегодня уже было» берётся из журнала: суточная задача не запускается второй раз
    again = Scheduler(_settings(), factory, now_fn=lambda: NOW + timedelta(hours=3))  # type: ignore[arg-type]
    again.start()
    retention = next(j for j in again.jobs if j.name == "retention")
    assert retention.last_run == NOW and not retention.due(NOW + timedelta(hours=3))
    assert retention.due(NOW + timedelta(days=1))


def test_sweep_job_runs_real_sweep(factory, db_session, frozen_clock):
    sched = Scheduler(_settings(), factory)  # type: ignore[arg-type]
    job = next(j for j in sched.jobs if j.name == "contact_sweep")
    out = sched.run_job(job)
    assert out is not None and {"invitations", "applications", "webhook_attempts"} <= set(out)
    assert job.runs == 1 and job.failures == 0


def test_calibration_job_runs_real_calibration(factory, db_session):
    sched = Scheduler(_settings(), factory)  # type: ignore[arg-type]
    job = next(j for j in sched.jobs if j.name == "calibration")
    out = sched.run_job(job)
    assert out is not None and {"run_id", "specializations", "n_responses", "families_updated", "quarantined"} <= set(out)
    assert db_session.scalar(select(func.count()).select_from(AuditLog).where(AuditLog.action == "scheduler.calibration")) == 1


def test_failed_job_is_logged_and_scheduler_survives(factory):
    def boom(db, settings, opts):
        raise RuntimeError("сбой")

    job = runner.JobSpec("contact_sweep", boom, {}, every_s=60)
    sched = Scheduler(_settings(), factory, jobs=[job], now_fn=lambda: NOW)  # type: ignore[arg-type]
    assert sched.run_job(job) is None
    assert job.failures == 1 and job.runs == 1 and job.next_run == NOW + timedelta(seconds=60)


def test_job_is_skipped_when_another_process_holds_the_lock(factory, db_session):
    calls: list[int] = []

    def counted(db, settings, opts):
        calls.append(1)
        return {}

    job = runner.JobSpec("lock_probe", counted, {}, every_s=60)
    sched = Scheduler(_settings(), factory, jobs=[job], now_fn=lambda: NOW)  # type: ignore[arg-type]
    engine = db_session.get_bind().engine
    key = runner._lock_key("lock_probe")
    with engine.connect() as other:  # «другой процесс» держит блокировку на своём соединении
        assert other.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
        assert sched.run_job(job) is None and calls == []
        other.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
        other.commit()
    assert sched.run_job(job) == {} and calls == [1]


def test_lifespan_runs_jobs_in_background_and_cancels_cleanly(factory, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(
        runner,
        "scheduler_config",
        lambda: {
            "enabled": True,
            "tick_s": 0.02,
            "startup_delay_s": 0,
            "jobs": {"contact_sweep": {"enabled": True, "every_s": 0.05, "limit": 10}},
        },
    )
    monkeypatch.setitem(runner.JOB_FUNCTIONS, "contact_sweep", lambda db, s, o: calls.append(1) or {})
    app = SimpleNamespace(state=SimpleNamespace(scheduler_session_factory=factory))
    settings = Settings(_env_file=None, env="dev", mail_backend="console")

    async def go() -> None:
        async with scheduler_lifespan(app, settings):
            await asyncio.sleep(0.6)
            assert getattr(app.state, "scheduler", None) is not None

    asyncio.run(go())
    assert len(calls) >= 2


def test_lifespan_is_noop_in_tests():
    app = SimpleNamespace(state=SimpleNamespace())

    async def go() -> None:
        async with scheduler_lifespan(app, _settings()):
            assert not hasattr(app.state, "scheduler")

    asyncio.run(go())
