"""Сборка и сброс демо-мира: пул заданий → синтетическая популяция → персоны → сценарий → файл с учётными данными.

seed_world идемпотентен: уже созданные кандидаты пропускаются (по demo_personas), сценарий пересобирается только если
его ещё нет или передан force. reset_world быстрый (секунды): тяжёлая популяция с тестами остаётся, стираются данные
контакта и аккаунты персон, затем персоны и сценарий строятся заново по тем же правилам.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, text
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.settings import Settings
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.contact import Application, ContactDisclosure, Cooldown, Invitation
from app.models.enums import UserRole
from app.models.matching import CandidateAlias, ImpressionLog, Need, ProfileView, Shortlist
from app.models.system import DemoClock
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.testing import ItemInstance, TestSession
from app.models.users import Consent, User
from app.services.demo import accounts, clockctl, personas
from app.services.demo.apidriver import Api
from app.services.demo.cfg import section
from app.services.demo.models import DemoPersona
from app.services.demo.scenario import Scenario
from app.services.demo.state import fence_scenario, get_state, stamp_scenario
from app.services.matching import features as matching_features
from app.services.testing import pool as testing_pool

log = logging.getLogger("vzaimno.demo.world")
Say = Callable[[str], None]
BACKEND_DIR = Path(__file__).resolve().parents[3]


def _quiet(_: str) -> None:
    return None


# --------------------------------------------------------------------------- пул


def ensure_pool(db: Session, say: Say = _quiet) -> dict[str, int]:
    """Экземпляры заданий: приоры семейств, замена пула с предсказуемыми seed, долив до нормы. Идемпотентно."""
    cfg = section("pool")
    created = testing_pool.seed_family_params(db)
    db.commit()
    stats = testing_pool.pool_stats(db)
    removed = 0
    if any(s.legacy_free for s in stats.values()):
        removed = testing_pool.rotate_legacy(db)
        db.commit()
        say(f"пул: заменено свободных экземпляров со старыми seed: {removed}")
    generated = 0
    errors = 0

    def progress(rep: Any) -> None:
        nonlocal generated, errors
        db.commit()
        generated += rep.generated
        if rep.error:
            errors += 1
            say(f"  ! {rep.family_id}: {rep.error}")

    for spec in cfg["specs"]:
        testing_pool.ensure_pool(
            db, spec=spec, n=int(cfg["n_per_family"]), min_free=int(cfg["min_free"]), threads=int(cfg["threads"]),
            progress=progress,
        )
    db.commit()
    return {"family_params": created, "rotated": removed, "generated": generated, "errors": errors}


# --------------------------------------------------------------------------- популяция


def seed_population(
    settings: Settings, db: Session, n: int, workers: int, say: Say = _quiet, exclude_subs: set[str] | None = None
) -> dict[str, Any]:
    """Создать кандидатов 0..n-1. workers > 1 — процессы (каждый со своим подключением к БД); 1 — в этом процессе."""
    from app.services.demo import (
        population,  # зависит от eval/ (его нет в образе бэкенда): только для seed на хосте
    )

    fsp_map = population.fsp_assignment(n, exclude=exclude_subs or set())
    todo = [i for i in range(n) if i not in population.synthetic_ids(db)]
    say(f"популяция: {n - len(todo)} уже есть, создаю {len(todo)}; привязок ФСП запланировано {len(fsp_map)}")
    total = {"done": 0, "skipped": n - len(todo), "failed": 0, "errors": []}
    if not todo:
        return total
    db.commit()
    if workers <= 1:
        res = [population.worker_chunk((todo, n, fsp_map))]
    else:
        chunks = [todo[w::workers] for w in range(workers)]
        ctx = get_context("spawn")
        with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
            res = list(ex.map(population.worker_chunk, [(c, n, fsp_map) for c in chunks if c]))
    for r in res:
        total["done"] += r["done"]
        total["skipped"] += r["skipped"]
        total["failed"] += r["failed"]
        total["errors"].extend(r["errors"])
    return total


def seed_extra_population(
    settings: Settings, db: Session, specs: Sequence[str], workers: int, say: Say = _quiet, count: int | None = None
) -> dict[str, Any]:
    """Добавить кандидатов отдельных специализаций (QA, DevOps) к уже собранному миру, ничего не пересобирая.

    Аддитивно: свои диапазоны номеров (config/demo.yaml → population.extra_specs), уже созданные пропускаются,
    персоны, компании и сценарий не трогаются. ФСП-привязок нет: мок-реестр (60 спортсменов) уже занят основной
    популяцией, а отсутствие ФСП — не штраф (F = 0). count — сколько на каждую специализацию (по умолчанию из конфига)."""
    from app.services.demo import population

    total: dict[str, Any] = {"done": 0, "skipped": 0, "failed": 0, "errors": [], "by_spec": {}}
    existing = set(population.synthetic_ids(db))
    for spec in specs:
        indices = population.extra_indices(spec, count)
        todo = [i for i in indices if i not in existing]
        say(f"{spec}: {len(indices) - len(todo)} уже есть, создаю {len(todo)}")
        res = {"done": 0, "skipped": len(indices) - len(todo), "failed": 0, "errors": []}
        if todo:
            db.commit()
            if workers <= 1:
                parts = [population.worker_chunk((todo, max(indices) + 1, {}, spec))]
            else:
                ctx = get_context("spawn")
                chunks = [todo[w::workers] for w in range(workers)]
                with ProcessPoolExecutor(max_workers=workers, mp_context=ctx) as ex:
                    parts = list(ex.map(population.worker_chunk, [(c, max(indices) + 1, {}, spec) for c in chunks if c]))
            for r in parts:
                res["done"] += r["done"]
                res["skipped"] += r["skipped"]
                res["failed"] += r["failed"]
                res["errors"].extend(r["errors"])
        for k in ("done", "skipped", "failed"):
            total[k] += res[k]
        total["errors"].extend(res["errors"])
        total["by_spec"][spec] = res
    matching_features.ensure_fresh(db)  # новые подтверждённые категории попадают в кэш признаков подбора
    db.commit()
    return total


# --------------------------------------------------------------------------- чистка перед пересборкой


def synthetic_and_persona_ids(db: Session) -> list[Any]:
    return list(db.scalars(select(DemoPersona.user_id).where(DemoPersona.kind.in_(("synthetic", "persona", "staged")))))


def purge_contact_data(db: Session) -> dict[str, int]:
    """Стереть данные контакта демо-кандидатов (приглашения, отклики, раскрытия, паузы, уведомления, просмотры) и
    всё, что принадлежит демо-компаниям. Тесты, профили и ФСП кандидатов остаются."""
    ids = synthetic_and_persona_ids(db)
    demo_companies = list(db.scalars(select(Company.id).where(Company.is_demo.is_(True))))
    demo_hr = list(db.scalars(select(User.id).where(User.company_id.in_(demo_companies)))) if demo_companies else []
    out: dict[str, int] = {}
    if ids:
        inv_ids = list(db.scalars(select(Invitation.id).where(Invitation.candidate_id.in_(ids))))
        app_ids = list(db.scalars(select(Application.id).where(Application.candidate_id.in_(ids))))
        if inv_ids or app_ids:
            db.execute(text("DELETE FROM feedback WHERE target_id = ANY(:ids)"), {"ids": inv_ids + app_ids})
        db.execute(text("DELETE FROM reliability_events WHERE candidate_id = ANY(:ids)"), {"ids": ids})
        out["invitations"] = int(db.execute(delete(Invitation).where(Invitation.candidate_id.in_(ids))).rowcount or 0)
        out["applications"] = int(db.execute(delete(Application).where(Application.candidate_id.in_(ids))).rowcount or 0)
        db.execute(delete(ContactDisclosure).where(ContactDisclosure.candidate_id.in_(ids)))
        db.execute(delete(Cooldown).where(Cooldown.candidate_id.in_(ids)))
        db.execute(delete(Shortlist).where(Shortlist.candidate_id.in_(ids)))
        db.execute(delete(ProfileView).where(ProfileView.candidate_id.in_(ids)))
        db.execute(delete(ImpressionLog).where(ImpressionLog.candidate_id.in_(ids)))
        db.execute(delete(CandidateAlias).where(CandidateAlias.candidate_id.in_(ids)))
        db.execute(delete(Consent).where(Consent.user_id.in_(ids), Consent.purpose == "contact_disclosure"))
        # назначения задач работодателей (платформенные — результаты популяции — остаются)
        company_tasks = select(EmployerTask.id).where(EmployerTask.company_id.is_not(None))
        db.execute(delete(TaskAssignment).where(TaskAssignment.candidate_id.in_(ids), TaskAssignment.employer_task_id.in_(company_tasks)))
        db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:ids)"), {"ids": ids})
    if demo_hr:
        db.execute(text("DELETE FROM notifications WHERE user_id = ANY(:ids)"), {"ids": demo_hr})
    if demo_companies:
        # брифы, версии, шортлисты, вакансии, приглашения, задачи, вебхуки, надёжность — каскадом по company_id
        out["companies"] = int(db.execute(delete(Company).where(Company.id.in_(demo_companies))).rowcount or 0)
    db.flush()
    return out


def drop_personas(db: Session) -> list[str]:
    """Удалить аккаунты персон и ступенчатых кандидатов (сессии, опрос, квалификации — каскадом) и «Марию», если
    она успела зарегистрироваться. Хеши паролей и id запоминаются для пересоздания."""
    emails: list[str] = []
    rows = db.execute(
        select(User.id, User.email, User.password_hash)
        .join(DemoPersona, DemoPersona.user_id == User.id)
        .where(DemoPersona.kind.in_(("persona", "staged")))
    ).all()
    for uid, email, pw in rows:
        accounts.REUSE[email] = (uid, pw)
        emails.append(email)
    ids = [r[0] for r in rows]
    maria = db.scalar(select(User).where(User.email == section("accounts")["fresh_registration_email"]))
    if maria is not None:
        ids.append(maria.id)
    if ids:
        db.execute(delete(User).where(User.id.in_(ids)))
    db.flush()
    return emails


def reset_clock(db: Session) -> None:
    clock.set_offset(0)
    row = db.get(DemoClock, 1)
    if row is not None:
        row.offset_seconds = 0
    db.flush()


# --------------------------------------------------------------------------- учётные данные


def credentials(settings: Settings) -> list[accounts.Credential]:
    """Все демо-логины. Пароли выводятся из VZ_APP_SECRET (accounts.password_for), поэтому восстанавливаются без БД."""
    out: list[accounts.Credential] = []
    for key, c in section("staff").items():
        where = "Лаборатория Л1–Л4: /api/lab/*" if c["role"] == "methodist" else "Администрирование: /api/admin/*"
        out.append(accounts.Credential(key, c["role"], c["email"], c["title"], accounts.password_for(settings, f"staff:{key}"), where))
    for c in section("companies"):
        out.append(accounts.Credential(
            f"employer:{c['key']}", "employer", c["hr_email"], f"HR компании {c['name']}",
            accounts.password_for(settings, f"employer:{c['key']}"),
            "Работодатель (сценарий §11: «Северный код»)" if c["key"] == "severny_kod" else "Работодатель",
        ))
    p = section("personas")
    out.append(accounts.Credential("anna", "candidate", p["anna"]["email"], "Анна — Backend Middle, призёр ФСП, тест на 12-м задании",
                                   accounts.password_for(settings, "persona:anna"), "Кандидат (телефон) — сценарий §11, 0:50"))
    out.append(accounts.Credential("ilya", "candidate", p["ilya"]["email"], "Илья — Backend Middle без ФСП, SQL не подтверждён",
                                   accounts.password_for(settings, "persona:ilya"), "Кандидат — сценарий §11, 3:55"))
    for st in section("staged"):
        out.append(accounts.Credential(st["key"], "candidate", st["email"], st["title"],
                                       accounts.password_for(settings, f"staged:{st['key']}"), "Ступенчатый аккаунт кандидата"))
    return out


def write_credentials_file(settings: Settings, path: Path | None = None) -> Path:
    """backend/var/demo_accounts.md (в .gitignore). Пароли — только сюда, в stdout их не печатаем."""
    cfg = section("accounts")
    path = path or (BACKEND_DIR / cfg["credentials_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    creds = credentials(settings)
    lines = [
        "# Демо-аккаунты «Взаимно»",
        "",
        "Файл создаёт `make seed` / `make reset-demo`; в git не попадает (`backend/var/` в .gitignore).",
        "Пароли выводятся из `VZ_APP_SECRET`: тот же секрет — те же пароли (`make demo-accounts` пересоздаст файл).",
        "Все компании, люди и контакты вымышленные. Письма стенда видно в Mailpit: http://localhost:8025",
        "",
        "## Мария — регистрация вживую (сценарий §11, 0:00)",
        "",
        f"Аккаунт заранее не создан. Зарегистрируйтесь как **{cfg['fresh_registration_email']}** с любым паролем от 8 символов, "
        "код подтверждения придёт в Mailpit (http://localhost:8025). `make reset-demo` удалит этот аккаунт.",
        "",
    ]
    groups = (
        ("Жюри, методист, администратор", lambda c: c.role in ("methodist", "admin")),
        ("Работодатели", lambda c: c.role == "employer"),
        ("Кандидаты-персоны и ступенчатые аккаунты", lambda c: c.role == "candidate"),
    )
    for title, pred in groups:
        lines += [f"## {title}", "", "| Логин (email) | Пароль | Кто | Где использовать |", "|---|---|---|---|"]
        for c in filter(pred, creds):
            lines.append(f"| `{c.email}` | `{c.password}` | {c.title} | {c.where} |")
        lines.append("")
    lines += [
        "## Синтетические кандидаты",
        "",
        f"Около {section('population')['size']} кандидатов (`cNNNN@{section('population')['email_domain']}`) создаются без пароля: "
        "входа у них нет, на приглашения жюри отвечает автоответчик (DEMO_MODE, 30–90 с).",
        "",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- сборка и сброс


def build_personas_and_scenario(db: Session, settings: Settings, say: Say = _quiet) -> dict[str, Any]:
    """Персоны, ступенчатые аккаунты, компании и сценарий. Вызывается из seed и reset после очистки."""
    out: dict[str, Any] = {}
    fence_scenario(db)  # пока строится история, автоответчик (в другом процессе) её не трогает
    try:
        say("служебные аккаунты (жюри, методист, админ)")
        personas.ensure_staff(db, settings)
        db.commit()
        say("Анна: опрос, ФСП, сессия на 12-м задании")
        personas.build_anna(db, settings)
        say("Илья: Backend Middle без ФСП, SQL не подтверждён")
        personas.build_ilya(db, settings)
        say("ступенчатые аккаунты кандидата")
        personas.build_staged(db, settings)
        matching_features.ensure_fresh(db)
        db.commit()
        api = Api(db, settings)
        try:
            sc = Scenario(db, settings, api, say)
            out["scenario"] = sc.run().as_dict()
        finally:
            api.close()
        matching_features.ensure_fresh(db)
        personas.keep_alive(db)
        db.commit()
    finally:
        stamp_scenario(db)  # даже при сбое: живые приглашения жюри после этого момента получают ответы
        db.commit()
    return out


def seed_world(
    settings: Settings,
    db: Session,
    *,
    candidates: int | None = None,
    workers: int = 4,
    say: Say = _quiet,
    force_scenario: bool = False,
    skip_pool: bool = False,
    add_specs: Sequence[str] = (),
) -> dict[str, Any]:
    """Полная идемпотентная сборка. Возвращает счётчики для отчёта.

    add_specs (например, ("qa", "devops")) — аддитивный режим: только добавить кандидатов этих специализаций (пул
    для них доливается, популяция, персоны и сценарий не трогаются); candidates здесь — сколько на каждую специализацию."""
    t0 = datetime.now(UTC)
    out: dict[str, Any] = {}
    if add_specs:
        if not skip_pool:
            say("пул одноразовых экземпляров")
            out["pool"] = ensure_pool(db, say)
        out["population"] = seed_extra_population(settings, db, add_specs, workers, say, count=candidates)
        out["counts"] = counts(db)
        out["credentials_file"] = str(write_credentials_file(settings))
        out["seconds"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
        return out
    n = int(candidates if candidates is not None else section("population")["size"])
    if not skip_pool:
        say("пул одноразовых экземпляров")
        out["pool"] = ensure_pool(db, say)
    exclude = {section("personas")["anna"]["fsp_sub"]}
    out["population"] = seed_population(settings, db, n, workers, say, exclude_subs=exclude)
    state = get_state(db)
    if force_scenario or state.scenario_built_at is None:
        if state.scenario_built_at is not None:
            say("сценарий уже был — пересобираю")
        purge_contact_data(db)
        drop_personas(db)
        db.commit()
        try:
            out.update(build_personas_and_scenario(db, settings, say))
        finally:
            accounts.REUSE.clear()
    else:
        say("сценарий уже собран (используйте --force или reset-demo, чтобы пересобрать)")
        personas.ensure_staff(db, settings)
        db.commit()
    reset_clock(db)
    state = get_state(db)
    state.seeded_at = state.seeded_at or clock.now()
    db.commit()
    out["credentials_file"] = str(write_credentials_file(settings))
    out["counts"] = counts(db)
    out["seconds"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
    return out


def reset_world(settings: Settings, db: Session, *, say: Say = _quiet) -> dict[str, Any]:
    """Быстрый сброс к состоянию seed: часы, данные контакта, персоны, сценарий. Популяцию и пул не трогает."""
    t0 = datetime.now(UTC)
    with clockctl.CLOCK_LOCK:
        say("сброс часов и данных контакта")
        reset_clock(db)
        purged = purge_contact_data(db)
        drop_personas(db)
        db.commit()
        try:
            out = build_personas_and_scenario(db, settings, say)
        finally:
            accounts.REUSE.clear()
        state = get_state(db)
        state.jury_mode = bool(section("jury_mode")["default"])
        state.jury_mode_since = None
        state.autoresponder = bool(section("autoresponder")["enabled"])
        db.commit()
    from app.services.demo import sink

    sink.clear()
    out["purged"] = purged
    out["credentials_file"] = str(write_credentials_file(settings))
    out["counts"] = counts(db)
    out["seconds"] = round((datetime.now(UTC) - t0).total_seconds(), 1)
    return out


# --------------------------------------------------------------------------- счётчики


def counts(db: Session) -> dict[str, int]:
    def n(model: Any, *where: Any) -> int:
        return int(db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    from app.models.contact import Vacancy
    from app.models.fsp import FspLink
    from app.models.testing import Flag, TestResponse

    inst_free = n(ItemInstance, ItemInstance.issued_at.is_(None))
    return {
        "candidates_synthetic": n(DemoPersona, DemoPersona.kind == "synthetic"),
        "candidates_with_category": int(db.scalar(text("SELECT count(DISTINCT candidate_id) FROM qualifications WHERE status = 'CONFIRMED'")) or 0),
        "test_sessions": n(TestSession),
        "test_responses": n(TestResponse),
        "flags": n(Flag),
        "fsp_links": n(FspLink),
        "companies_demo": n(Company, Company.is_demo.is_(True)),
        "needs": n(Need),
        "invitations": n(Invitation),
        "vacancies": n(Vacancy),
        "applications": n(Application),
        "employer_tasks": n(EmployerTask, EmployerTask.company_id.is_not(None)),
        "platform_assignments": n(TaskAssignment),
        "profiles": n(CandidateProfile),
        "users_demo": n(User, User.is_demo.is_(True)),
        "free_instances": inst_free,
    }


def invitation_status_counts(db: Session) -> dict[str, int]:
    return {s: int(c) for s, c in db.execute(select(Invitation.status, func.count()).group_by(Invitation.status))}


__all__ = [
    "UserRole",
    "counts",
    "credentials",
    "ensure_pool",
    "invitation_status_counts",
    "reset_world",
    "seed_world",
    "write_credentials_file",
]
