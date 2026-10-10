"""Синтетические кандидаты демо-мира из eval/population.py — того же генератора с латентной истиной, что у валидации.

Кандидат создаётся «как настоящий»: аккаунт и профиль (ORM), согласия (app.core.consents), опрос (сервис опроса),
настоящая сессия адаптивного теста на выданных экземплярах (app.services.demo.sim) — с симулянтом, чей уровень
равен скрытой истине генератора. Время теста сдвигается в прошлое на `test_days_ago` дней генератора, поэтому
свежесть тестов в подборке разная, а трассы в лаборатории привязаны к реальным датам.

Один кандидат — одна транзакция и одна отметка demo_personas('synthetic'): прерванный seed продолжается с того же места,
повторный запуск уже созданных пропускает.
"""

from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Mapping
from dataclasses import replace
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.settings import Settings
from app.models.enums import UserRole
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.users import User
from app.services.demo import accounts
from app.services.demo.cfg import section
from app.services.demo.clockctl import clock_at
from app.services.demo.models import DemoPersona
from app.services.demo.profiles import apply_profile, ensure_consents, fill_survey, link_fsp
from app.services.demo.sim import run_session
from app.services.fsp_link import service as fsp
from app.services.testing import pool as testing_pool
from app.testing.irt import load_scale
from app.testing.simulate import SimulatedExaminee
from eval.population import Candidate, PopulationConfig, generate_candidate

log = logging.getLogger("vzaimno.demo.population")

TESTED_SPECS = ("backend_python", "frontend_js")  # две составляющие Fullstack; QA и DevOps — отдельные специализации


def population_config(n: int | None = None) -> PopulationConfig:
    c = section("population")
    return PopulationConfig(n=int(n if n is not None else c["size"]), seed=int(c["seed"]), run_tests=False)


def extra_config(spec: str) -> PopulationConfig:
    """Добавочные кандидаты одной специализации (QA, DevOps): тот же генератор, но все с верной заявкой и своим тестом."""
    x = extra_indices(spec)
    return replace(population_config(max(x) + 1 if x else 1), spec_mix=((spec, 1.0),), mismatch_rate=0.0)


def extra_indices(spec: str, count: int | None = None) -> list[int]:
    """Номера добавочных кандидатов специализации: отдельный диапазон от index_base (config/demo.yaml → extra_specs)."""
    ex = section("population")["extra_specs"]
    if spec not in ex:
        raise KeyError(f"Для специализации {spec!r} нет диапазона в config/demo.yaml → population.extra_specs")
    return [int(ex[spec]["index_base"]) + j for j in range(int(count if count is not None else ex[spec]["count"]))]


def candidate_email(index: int) -> str:
    return f"c{index + 1:04d}@{section('population')['email_domain']}"


def _pick(rng: random.Random, mix: Mapping[str, float]) -> str:
    u, acc = rng.random(), 0.0
    items = list(mix.items())
    for k, w in items:
        acc += float(w)
        if u < acc:
            return k
    return items[-1][0]


def contacts_for(index: int, full_name: str) -> tuple[str, str, str]:
    """Контакты вымышленного кандидата: email в зоне .example, телефон «000», telegram с префиксом demo_."""
    names = section("names")
    first, last = full_name.split(" ", 1)
    email = f"{accounts.translit(first)}.{accounts.translit(last)}.{index + 1:03d}@mail.example.org"
    phone = f"{names['phone_prefix']}{index // 100 % 100:02d}-{index % 100:02d}"
    telegram = f"@{names['telegram_prefix']}{accounts.translit(last)}{index + 1}"
    return email, phone, telegram


def platform_tasks(db: Session, user: User, cand: Candidate) -> int:
    """Результаты регулярных задач из генератора → тренировочные задачи платформы и завершённые назначения."""
    t = section("tasks")
    n = 0
    now = clock.now()
    for res in cand.tasks[: int(t["platform_assignments_per_candidate_max"])]:
        kind = "catalog" if res.kind == "A" else "approach"
        task = EmployerTask(
            kind=kind,
            title=res.title or t["platform_titles"].get(res.module or "", "Тренировочная задача"),
            tags=list(res.skills),
            time_limit_min=int(t["platform_time_limit_min"]),
            status="active",
            is_platform=True,
            module=res.module,
            not_real_work_ack=True,
        )
        db.add(task)
        db.flush()
        done = now - timedelta(days=float(res.days_ago))
        db.add(
            TaskAssignment(
                employer_task_id=task.id,
                candidate_id=user.id,
                status="auto_scored" if kind == "catalog" else "reviewed",
                score=float(res.q),
                kind=kind,
                module=res.module,
                is_correct=bool(res.q >= 0.5),
                offered_at=done - timedelta(days=1),
                submitted_at=done,
                finished_at=done,
                source="offer",
            )
        )
        n += 1
    db.flush()
    return n


def fsp_assignment(n: int, exclude: set[str] = frozenset()) -> dict[int, str]:  # type: ignore[assignment]
    """индекс кандидата → sub спортсмена мок-реестра. Реестр небольшой (60), поэтому привязаны не все кандидаты с ФСП:
    сильнейшие по VSS получают сильнейших спортсменов (порядок сохраняется — связь VSS с уровнем остаётся)."""
    from app.fsp import vss
    from app.fsp.provider import load_profile

    now = clock.now()
    reg = fsp.demo_registry()
    min_age = int(section("population")["fsp_athlete_min_age"])
    athletes: list[tuple[float, str]] = []
    for a in reg.snapshot.athletes:
        if a.sub in exclude:
            continue
        age = (now.date() - a.birth_date).days / 365.25
        if age < min_age:
            continue
        prof = load_profile(reg, a.sub, now=now)
        feats = vss.build_features(prof, now)
        strength = max(feats.vss_by_discipline.values(), default=0.0)
        if strength > 0:
            athletes.append((strength, a.sub))
    athletes.sort(key=lambda x: (-x[0], x[1]))
    cfg = population_config(n)
    cands = [generate_candidate(i, cfg) for i in range(n)]
    with_fsp = sorted((c for c in cands if c.has_fsp), key=lambda c: (-max(c.vss_by_discipline.values()), c.index))
    return {c.index: sub for c, (_, sub) in zip(with_fsp, athletes, strict=False)}


def decline_reason(index: int) -> str:
    reasons = list(section("autoresponder")["decline_reasons"])
    return reasons[index % len(reasons)]


def seed_candidate(
    db: Session, settings: Settings, index: int, cand: Candidate, *, fsp_sub: str | None = None
) -> uuid.UUID | None:
    """Создать одного кандидата целиком. None — уже создан раньше (идемпотентность)."""
    pop = section("population")
    email = candidate_email(index)
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None and db.get(DemoPersona, existing.id) is not None:
        return None
    if existing is not None:  # прошлый прогон оборвался внутри кандидата: убираем полуготового целиком
        db.delete(existing)
        db.flush()
    rng = random.Random(f"{pop['seed']}:demo-profile:{cand.id}")
    full_name, _ = accounts.fictional_name(rng)
    start = clock.now() - timedelta(days=float(cand.test_days_ago), hours=2)
    with clock_at(start):
        user = accounts.ensure_user(db, settings, email=email, role=UserRole.candidate.value, password=None)
        contact_email, phone, telegram = contacts_for(index, full_name)
        mode = _pick(rng, pop["search_mode_mix"])
        limits = list(pop["weekly_invite_limit_mix"])
        apply_profile(
            db,
            user,
            full_name=full_name,
            contact_email=contact_email,
            phone=phone,
            telegram=telegram,
            salary_min=cand.salary_min,
            formats=sorted(cand.formats),
            city_code=cand.city,
            relocation=cand.relocation,
            industries=sorted(cand.industries),
            declared=sorted(cand.declared_skills),
            experience_months=cand.experience_months,
            declared_grade=cand.claimed_grade,
            search_mode=mode,
            salary_visibility="exact" if rng.random() < 0.2 else "signal",
            weekly_invite_limit=limits[index % len(limits)],
            self_description=cand.self_description,
        )
        ensure_consents(db, user, blind_card=rng.random() < float(pop["blind_card_share"]))
        survey_spec = "fullstack" if cand.truth.spec == "fullstack" else cand.claimed_spec
        fill_survey(db, user, survey_spec, sorted(cand.industries), cand.experience_months, set(cand.declared_skills))
        specs = [cand.claimed_spec]
        if cand.truth.spec == "fullstack":
            specs.append(next(s for s in TESTED_SPECS if s != cand.claimed_spec))
        scale = load_scale()
        examinee = SimulatedExaminee(theta=cand.truth.theta, module_theta=dict(cand.truth.module_theta))
        for spec in specs:
            claimed = cand.claimed_grade if cand.claimed_grade in scale.grades else scale.grades[0]
            run_session(
                db,
                user,
                spec=spec,
                claimed_grade=claimed,
                examinee=examinee,
                rng=random.Random(f"{pop['seed']}:demo-cat:{cand.id}:{spec}"),
            )
            clock.advance(3600)
    if fsp_sub:
        link_fsp(db, user, fsp_sub)
    platform_tasks(db, user, cand)
    accounts.mark(
        db,
        user,
        "synthetic",
        cand.id,
        decline_reason=decline_reason(index),
        true_spec=cand.truth.spec,
        true_grade=cand.truth.grade,
        fsp_sub=fsp_sub,
    )
    db.commit()
    return user.id


def synthetic_ids(db: Session) -> dict[int, uuid.UUID]:
    """индекс кандидата → id аккаунта (ключ c0007 → 6)."""
    out: dict[int, uuid.UUID] = {}
    for uid, key in db.execute(select(DemoPersona.user_id, DemoPersona.key).where(DemoPersona.kind == "synthetic")):
        if key and key[0] == "c" and key[1:].isdigit():
            out[int(key[1:]) - 1] = uid
    return out


def worker_chunk(args: tuple[list[int], int, dict[int, str]] | tuple[list[int], int, dict[int, str], str | None]) -> dict[str, Any]:
    """Точка входа процесса-воркера: свой движок БД, кандидаты своего куска. Возвращает счётчики.

    Четвёртый элемент args (необязателен) — специализация добавочных кандидатов (extra_config); без него — основная
    популяция."""
    indices, n_total, fsp_map = args[:3]
    extra_spec = args[3] if len(args) > 3 else None
    from app.core.settings import get_settings
    from app.db.session import get_sessionmaker

    settings = get_settings()
    cfg = extra_config(extra_spec) if extra_spec else population_config(n_total)
    done = skipped = failed = 0
    errors: list[str] = []
    for i in indices:
        db = get_sessionmaker()()
        try:
            cand = generate_candidate(i, cfg)
            if seed_candidate(db, settings, i, cand, fsp_sub=fsp_map.get(i)) is None:
                skipped += 1
            else:
                done += 1
            # Пул не должен иссякнуть посреди seed (быстрый режим — 40 экземпляров на семейство): доливаем, как сервер
            try:
                testing_pool.top_up_low_families(db)
            except Exception:  # noqa: BLE001 — долив не должен ронять кандидата: его сессия уже сохранена
                db.rollback()
                log.exception("seed: долив пула не удался")
        except Exception as e:  # noqa: BLE001 — один кандидат не должен ронять весь seed
            db.rollback()
            failed += 1
            errors.append(f"c{i + 1:04d}: {type(e).__name__}: {e}")
            log.exception("seed: кандидат %s не создан", i)
        finally:
            db.close()
    return {"done": done, "skipped": skipped, "failed": failed, "errors": errors}
