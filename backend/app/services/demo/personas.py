"""Персоны сценария §11 и служебные аккаунты демо: Анна, Илья, ступенчатые кандидаты, жюри, методист, админ, HR компаний.

Все кандидаты проходят тот же путь, что человек: опрос (сервис опроса), настоящая сессия адаптивного теста на выданных
экземплярах (app.services.demo.sim), привязка ФСП ID (app.services.fsp_link). «Мария» заранее не создаётся — по сценарию она
регистрируется вживую (код приходит в Mailpit); reset-demo удаляет её аккаунт, если он появился.

«Сессия Анны, готовая дозавершиться» — 11 ответов, заданий в выдаче нет, и по тому же rng, каким движок выберет следующие
задания, верный 12-й ответ ещё не останавливает тест, а верный 13-й останавливает его с результатом «Middle подтверждён»
(пробный прогон на копии состояния, в SAVEPOINT: неудачные попытки откатываются вместе с выданными экземплярами).
"""

from __future__ import annotations

import logging
import random
import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.settings import Settings
from app.models.company import Company
from app.models.enums import UserRole
from app.models.testing import TestSession as SessionRow
from app.models.users import User
from app.services.contact import webhooks
from app.services.contact.company import sync_completion
from app.services.demo import accounts
from app.services.demo.cfg import demo_cfg, section
from app.services.demo.clockctl import clock_at
from app.services.demo.models import DemoPersona
from app.services.demo.profiles import apply_profile, ensure_consents, fill_survey, link_fsp
from app.services.demo.sim import Trace, run_session
from app.services.testing import sessions
from app.services.testing.catalog import family_metas, seen_history
from app.services.testing.qualification import get_row
from app.testing.cat import load_blueprint
from app.testing.session import SessionStatus
from app.testing.simulate import SimulatedExaminee

log = logging.getLogger("vzaimno.demo.personas")


# --------------------------------------------------------------------------- компании и сотрудники


def demo_host() -> str:
    return str(demo_cfg()["company_webhook_host"])


def webhook_url() -> str:
    return f"https://{demo_host()}{section('webhook_sink')['path']}"


def ensure_company(db: Session, settings: Settings, cfg: dict[str, Any]) -> tuple[Company, User, str]:
    """Компания и её HR-сотрудник. Повторный вызов возвращает существующих. → (компания, HR, пароль)."""
    domain = cfg["domain"]
    company = db.scalar(select(Company).where(Company.email_domain == domain, Company.personal_domain.is_(False)))
    if company is None:
        company = Company(
            id=accounts.stable_id("company", cfg["key"]),
            email_domain=domain,
            personal_domain=False,
            name=cfg["name"],
            field=cfg["field"],
            description=cfg["description"],
            contact_methods=dict(cfg["contact"]),
            website=cfg.get("website"),
            is_demo=True,
        )
        if cfg.get("webhook"):
            company.webhook_url = webhook_url()
            company.webhook_secret = webhooks.new_secret()
        db.add(company)
        db.flush()
    sync_completion(company)
    password = accounts.password_for(settings, f"employer:{cfg['key']}")
    hr = accounts.ensure_user(
        db, settings, email=cfg["hr_email"], role=UserRole.employer.value, password=password, company=company
    )
    if hr.company_id != company.id:  # после сброса компания пересоздаётся (тот же id), а HR-аккаунт остаётся
        hr.company_id = company.id
    accounts.mark(db, hr, "employer", cfg["key"], company_id=str(company.id))
    db.flush()
    return company, hr, password


def ensure_staff(db: Session, settings: Settings) -> list[accounts.Credential]:
    out = []
    for key, c in section("staff").items():
        password = accounts.password_for(settings, f"staff:{key}")
        user = accounts.ensure_user(db, settings, email=c["email"], role=c["role"], password=password)
        accounts.mark(db, user, "staff", key, title=c["title"])
        out.append(accounts.Credential(key, c["role"], c["email"], c["title"], password, "Лаборатория (Л1–Л4)" if c["role"] == "methodist" else "/api/admin/* (Swagger: /api/docs)"))
    return out


# --------------------------------------------------------------------------- проверки результатов


def _qual_tags(db: Session, user: User, spec: str) -> dict[str, str]:
    row = get_row(db, user.id, spec)
    return {m: str((s or {}).get("tag")) for m, s in (row.subscores or {}).items()} if row is not None else {}


def _qual_grade(db: Session, user: User, spec: str) -> str | None:
    row = get_row(db, user.id, spec)
    return row.grade if row is not None and row.status == "CONFIRMED" else None


def next_two_correct_decide(db: Session, user: User, main_id: uuid.UUID, p_low: float, p_high: float) -> bool:
    """Пробный прогон на копии: после 11-го ответа P(пройдёт) в окне, и два верных ответа подряд (12-й и 13-й, задания
    выберет движок по тому же rng) останавливают тест с «пройдено». Копия — та же сессия, загруженная из БД заново."""
    row = db.get(SessionRow, main_id)
    ld = sessions.wrap(db, row)
    s = ld.sess
    if s.status != SessionStatus.IN_PROGRESS or s.pending is not None or not s.cat.responses:
        return False
    p = s.cat.responses[-1].p_after
    if p is None or not p_low <= float(p) <= p_high:
        return False
    now = clock.now()
    metas = family_metas(db, s.spec, now)
    history = seen_history(db, user.id, now)
    for _ in range(2):
        sel = s.next_item(metas, sessions.rng_for(row, s.cat.next_seq, 0), clock, seen_history=history)
        if sel is None:
            return False
        s.answer(sel.seq, True, clock)
        if s.status != SessionStatus.IN_PROGRESS:
            break
    d = s.decision
    return s.status != SessionStatus.IN_PROGRESS and d is not None and d.passed


def ready_session(
    db: Session, user: User, *, spec: str, grade: str, theta: float, answered: int, key: str, tries: int,
    p_low: float, p_high: float, strict: bool = True,
) -> Trace | None:
    """Сессия на `answered` ответах, без висящего задания, у которой верные ответы 12 и 13 ведут к стопу «пройдено»."""
    examinee = SimulatedExaminee(theta=theta)
    for attempt in range(tries):
        sp = db.begin_nested()
        try:
            start = clock.now() - timedelta(minutes=answered * 2 + 5)
            with clock_at(start):
                tr = run_session(
                    db, user, spec=spec, claimed_grade=grade, examinee=examinee,
                    rng=random.Random(f"{key}:{attempt}"), stop_after=answered, issue_pending=False, extensions="none",
                )
            good = tr.answered == answered and tr.status == str(SessionStatus.IN_PROGRESS)
            if good and (not strict or next_two_correct_decide(db, user, tr.main_id, p_low, p_high)):
                sp.commit()
                return tr
        except Exception:  # noqa: BLE001 — попытка не удалась: откат и следующий seed
            log.exception("ready_session: попытка %s не удалась", attempt)
        sp.rollback()
        db.expire_all()  # объекты сессии помнят состояние отката: перечитать из БД
    if strict:
        log.warning("ready_session(%s): подходящий seed не найден за %s попыток — берём сессию без проверки", key, tries)
        return ready_session(db, user, spec=spec, grade=grade, theta=theta, answered=answered, key=key, tries=3,
                             p_low=p_low, p_high=p_high, strict=False)
    return None


def escalating_examinee(base_theta: float, modules: dict[str, float], weak_module: str | None, step: float, after: int):
    """Фабрика «абитуриента по номеру попытки»: после `after` неудач целевой модуль занижается ещё на `step` за попытку.

    Оценка модуля из ~4–6 заданий шумная (разброс ≈ ±0,8), и при фиксированном «истинном» уровне нужный исход бывает
    лишь у трети попыток; сдвиг истины делает последние попытки заведомо подходящими (без него сид «Ильи» флакал)."""

    def make(attempt: int) -> SimulatedExaminee:
        shift = step * max(0, attempt - after)
        mt = dict(modules)
        if weak_module is not None and shift:
            mt[weak_module] = mt[weak_module] - shift
        return SimulatedExaminee(theta=base_theta, module_theta=mt)

    return make


def confirmed_session(
    db: Session, user: User, *, spec: str, grade: str, examinee: SimulatedExaminee | Callable[[int], SimulatedExaminee], key: str, tries: int,
    want_grade: str, weak_module: str | None = None, weak_theta_max: float | None = None, extensions: str = "auto",
) -> Trace | None:
    """Полная сессия, после которой категория подтверждена на want_grade (и, если задано, оценка уровня по модулю
    weak_module не выше weak_theta_max: навык «не подтверждён» для брифа этого грейда)."""
    for attempt in range(tries):
        sp = db.begin_nested()
        try:
            start = clock.now() - timedelta(days=1)
            with clock_at(start):
                tr = run_session(db, user, spec=spec, claimed_grade=grade,
                                 examinee=examinee(attempt) if callable(examinee) else examinee,
                                 rng=random.Random(f"{key}:{attempt}"), extensions=extensions)
            ok = _qual_grade(db, user, spec) == want_grade
            if ok and weak_module is not None:
                row = get_row(db, user.id, spec)
                theta = ((row.subscores or {}).get(weak_module) or {}).get("theta") if row is not None else None
                ok = theta is not None and float(theta) <= float(weak_theta_max if weak_theta_max is not None else 0.0)
            if ok:
                sp.commit()
                return tr
            sp.rollback()
        except Exception:  # noqa: BLE001
            log.exception("confirmed_session: попытка %s не удалась", attempt)
            sp.rollback()
        db.expire_all()
    return None


# --------------------------------------------------------------------------- кандидаты-персоны


def _candidate_base(
    db: Session, settings: Settings, *, key: str, cfg: dict[str, Any], password: str | None, search_mode: str = "active"
) -> User:
    user = accounts.ensure_user(db, settings, email=cfg["email"], role=UserRole.candidate.value, password=password)
    full_name = cfg["full_name"]
    first, last = full_name.split(" ", 1)
    apply_profile(
        db, user,
        full_name=full_name,
        contact_email=f"{accounts.translit(first)}.{accounts.translit(last)}@mail.example.org",
        phone=f"{section('names')['phone_prefix']}{len(key) % 10:01d}0-0{len(key) % 10}",
        telegram=f"@{section('names')['telegram_prefix']}{key}",
        salary_min=cfg.get("salary_min"),
        formats=list(cfg.get("formats", [])),
        city_code=_city_code(cfg.get("city")),
        relocation=False,
        industries=list(cfg.get("industries", [])),
        declared=list(cfg.get("declared_skills", [])),
        experience_months=cfg.get("experience_months"),
        declared_grade=cfg.get("grade"),
        search_mode=search_mode,
        city_name=cfg.get("city"),
    )
    return user


def _city_code(name: str | None) -> str | None:
    if not name:
        return None
    from app.matching.skills import get_catalog

    for c in get_catalog().cities.values():
        if c.name == name:
            return c.id
    return None


def build_anna(db: Session, settings: Settings) -> tuple[User, accounts.Credential]:
    cfg = section("personas")["anna"]
    password = accounts.password_for(settings, "persona:anna")
    user = _candidate_base(db, settings, key="anna", cfg=cfg, password=password)
    ensure_consents(db, user)
    fill_survey(db, user, cfg["spec"], list(cfg["industries"]), int(cfg["experience_months"]), set(cfg["declared_skills"]))
    db.commit()
    link_fsp(db, user, cfg["fsp_sub"])
    tr = ready_session(
        db, user, spec=cfg["spec"], grade=cfg["grade"], theta=float(cfg["theta"]), answered=int(cfg["session_answered"]),
        key="anna", tries=int(cfg["session_theta_search_seeds"]), p_low=float(cfg["session_pending_p_low"]),
        p_high=float(cfg["session_pending_p_high"]),
    )
    accounts.mark(db, user, "persona", "anna", session_id=str(tr.main_id) if tr else None, keep_alive=True)
    db.commit()
    return user, accounts.Credential(
        "anna", "candidate", cfg["email"], "Анна — Backend Middle, призёр ФСП, тест на 12-м задании", password,
        "Телефон кандидата (сценарий §11, 0:50): ответить на 12-е задание — на 13-м стоп",
    )


def build_ilya(db: Session, settings: Settings) -> tuple[User, accounts.Credential]:
    cfg = section("personas")["ilya"]
    password = accounts.password_for(settings, "persona:ilya")
    user = _candidate_base(db, settings, key="ilya", cfg=cfg, password=password)
    ensure_consents(db, user)
    fill_survey(db, user, cfg["spec"], list(cfg["industries"]), int(cfg["experience_months"]), set(cfg["declared_skills"]))
    modules = {m: float(cfg["theta"]) for m in load_blueprint(cfg["spec"])}
    for m, delta in (cfg.get("weak_modules") or {}).items():
        modules[m] = float(cfg["theta"]) + float(delta)
    examinee = escalating_examinee(
        float(cfg["theta"]), modules, cfg.get("ensure_unconfirmed_skill"),
        float(cfg["escalate_step"]), int(cfg["escalate_after"]),
    )
    tr = confirmed_session(
        db, user, spec=cfg["spec"], grade=cfg["grade"], examinee=examinee, key="ilya", tries=int(cfg["session_tries"]),
        want_grade=cfg["grade"], weak_module=cfg.get("ensure_unconfirmed_skill"),
        weak_theta_max=float(cfg["unconfirmed_theta_max"]), extensions="decline",
    )
    if tr is None:
        log.warning("ilya: нужный результат не получен за %s попыток — берём последнюю сессию как есть", cfg["session_tries"])
        with clock_at(clock.now() - timedelta(days=1)):
            run_session(db, user, spec=cfg["spec"], claimed_grade=cfg["grade"], examinee=examinee(int(cfg["session_tries"])),
                        rng=random.Random("ilya:fallback"), extensions="decline")
    row = get_row(db, user.id, cfg["spec"])
    sql_theta = ((row.subscores or {}).get("sql") or {}).get("theta") if row is not None else None
    accounts.mark(db, user, "persona", "ilya", sql_theta=sql_theta,
                  sql_unconfirmed=bool(sql_theta is not None and sql_theta <= float(cfg["unconfirmed_theta_max"])))
    db.commit()
    return user, accounts.Credential(
        "ilya", "candidate", cfg["email"], "Илья — Backend Middle без ФСП, SQL не подтверждён", password,
        "Сценарий §11, 3:55: «+3 дня», отклик на вакансию, задача по SQL",
    )


def build_staged(db: Session, settings: Settings) -> list[accounts.Credential]:
    """Четыре ступенчатых аккаунта кандидата (G2, RK2): каждый стоит на своём шаге пути, застрять негде."""
    defaults = section("staged_defaults")
    out: list[accounts.Credential] = []
    for st in section("staged"):
        password = accounts.password_for(settings, f"staged:{st['key']}")
        cfg = {
            **st,
            "grade": st.get("grade", defaults["claimed_grade"]),
            "industries": ["fintech"],
            "declared_skills": ["python", "sql"],
            "experience_months": 36,
            "formats": ["hybrid", "remote"],
            "city": "Москва",
            "salary_min": 200000,
        }
        user = _candidate_base(db, settings, key=st["key"], cfg=cfg, password=password)
        stage, spec = st["stage"], defaults["spec"]
        if stage != "registered":
            ensure_consents(db, user, blind_card=stage == "category")
            fill_survey(db, user, spec, ["fintech"], 36, {"python", "sql"})
        if stage == "midtest":
            with clock_at(clock.now() - timedelta(minutes=int(st.get("answered", 7)) * 2 + 5)):
                run_session(db, user, spec=spec, claimed_grade=cfg["grade"], examinee=SimulatedExaminee(theta=0.2),
                            rng=random.Random(f"staged:{st['key']}"), stop_after=int(st.get("answered", 7)),
                            issue_pending=False, extensions="none")
        elif stage == "category":
            confirmed_session(db, user, spec=spec, grade=cfg["grade"],
                              examinee=SimulatedExaminee(theta=float(st.get("theta", 0.7))), key=f"staged:{st['key']}",
                              tries=10, want_grade=cfg["grade"])
        accounts.mark(db, user, "staged", st["key"], stage=stage, keep_alive=st["key"] in defaults["keep_alive"])
        db.commit()
        out.append(accounts.Credential(st["key"], "candidate", st["email"], st["title"], password, "Ступенчатый аккаунт кандидата (G2)"))
    return out


# --------------------------------------------------------------------------- «живость» незавершённых сессий


def keep_alive(db: Session) -> int:
    """Сессия «на середине теста» оборвётся через 30 минут тишины (ABANDONED: попытка засчитана). Демо-цикл освежает
    отметку активности у персон с keep_alive, пока часы не сдвинуты. → число освежённых сессий."""
    ids = [
        uid for uid, prof in db.execute(select(DemoPersona.user_id, DemoPersona.profile)) if (prof or {}).get("keep_alive")
    ]
    if not ids:
        return 0
    n = 0
    rows = db.scalars(
        select(SessionRow).where(SessionRow.candidate_id.in_(ids), SessionRow.status == str(SessionStatus.IN_PROGRESS))
        .with_for_update()
    ).all()
    now = clock.now()
    for row in rows:
        if not row.state:
            continue
        ld = sessions.wrap(db, row)
        if ld.sess.pending is not None:
            continue
        ld.sess.last_activity_at = now
        sessions.save(db, ld)
        n += 1
    db.flush()
    return n

