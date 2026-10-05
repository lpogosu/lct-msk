"""Общие помощники тестов API подбора и банка: синтетические кандидаты в БД, работодатели, обход JSON.

Кандидаты создаются прямо в БД (квалификация — через сервис модуля тестирования save_domain, как после теста),
работодатели — через API (make_account), чтобы проверялись настоящие роли и компании.
Тестов здесь нет: модуль импортируют test_api_needs_*.py и test_api_bank_*.py.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from datetime import timedelta
from functools import lru_cache
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.consents import grant_consent
from app.models.candidate import CandidateProfile
from app.models.fsp import FspLink
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.testing import TestSession
from app.models.users import User
from app.services.testing.qualification import get_or_create_row, save_domain
from app.testing.session import Qualification as DomainQualification
from app.testing.session import QualStatus

DEMO_BRIEF = (
    "Бэкенд в команду платёжного шлюза. Уверенный Python, FastAPI, PostgreSQL, опыт от 2 лет. "
    "Kafka и Docker будут плюсом. Гибрид, Москва. 220–280 тыс. на руки"
)
SPEC_MODULES = {
    "backend_python": ("python_core", "sql", "http_api", "algo", "git_linux"),
    "frontend_js": ("js_core", "react", "html_css", "http_api", "algo"),
}
# Демо-реестр ФСП (app/fsp/data/demo_registry.json): призёр всероссийских по продуктовому программированию
# и участник всероссийских — у обоих VSS > 0 по дисциплине «продуктовое».
FSP_STRONG = "54ac9bcc-d3ac-4244-a71b-656ddbaa7a8f"
FSP_MEDIUM = "43d2def2-48bb-4deb-ba70-5ffa4d4f7369"
# Ключи ПДн (config/contact.yaml visibility.pii_fields + внутренние id) — в ответах работодателю до «да» их нет.
PII_KEYS = frozenset(
    {
        "full_name", "photo_url", "email", "phone", "telegram", "resume", "current_employer", "university",
        "city", "candidate_id", "birth_year", "birth_date", "age", "gender", "ekp", "team", "protocol_url",
        "place", "first_name", "last_name", "middle_name", "contact_email", "education", "resume_text",
        "user_id", "fsp_id", "snils", "inn", "passport", "address", "is_minor",
    }
)


@lru_cache(maxsize=1)
def _demo_provider():
    from app.fsp.provider import InProcessFSPProvider
    from app.fsp.settings import FSPSettings

    return InProcessFSPProvider.from_file(FSPSettings().snapshot_path)


def fsp_registry_data(sub: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Ответ демо-реестра (как его сохраняет синхронизация ФСП в FspLink): достижения и спортсмен."""
    p = _demo_provider()
    ach = p.get_achievements(sub)
    ath = p.get_athlete(sub)
    assert ach is not None, sub
    return ach.model_dump(mode="json"), (ath.model_dump(mode="json") if ath else None)


def ekp_numbers(sub: str) -> set[str]:
    ach, _ = fsp_registry_data(sub)
    return {r["ekp"] for r in ach["results"]} | {c["ekp"] for c in ach["competitions"]}


def _qualification(
    db: Session,
    uid: uuid.UUID,
    spec: str,
    grade: str,
    theta: float,
    *,
    se: float,
    trust: float,
    borderline: bool,
    days_ago: float,
    modules: Mapping[str, tuple[float, float, str]] | None,
    with_session: bool,
    hidden: bool = False,
) -> None:
    now = clock.now()
    at = now - timedelta(days=days_ago)
    session_id = None
    if with_session:
        s = TestSession(
            candidate_id=uid,
            specialization=spec,
            kind="initial",
            claimed_grade=grade,
            status="FINAL",
            seed=1,
            theta=theta,
            se=se,
            started_at=at,
            finished_at=at,
        )
        db.add(s)
        db.flush()
        session_id = str(s.id)
    mods = dict(modules) if modules is not None else {m: (theta, 0.35, "confirmed") for m in SPEC_MODULES[spec]}
    q = DomainQualification(account_id=str(uid), spec=spec)
    q.status = QualStatus.CONFIRMED
    q.grade = grade
    q.theta = theta
    q.se = se
    q.p_pass = 0.95
    q.trust = trust
    q.borderline = borderline
    q.hidden = hidden
    q.confirmed_at = at
    q.first_confirmed_at = at
    q.last_activity_at = at
    q.source_session_id = session_id
    q.subscores = {
        m: {"theta": th, "se": s_, "tag": tag, "n": 3, "name": m, "symbol": "✓✓" if tag == "strong" else "✓"}
        for m, (th, s_, tag) in mods.items()
    }
    q.module_tags = {m: tag for m, (_, _, tag) in mods.items() if tag in ("strong", "confirmed")}
    q.module_theta = {m: th for m, (th, _, _) in mods.items()}
    row = get_or_create_row(db, uid, spec, lock=False)
    save_domain(db, row, q, notify=True)


def seed_candidate(
    db: Session,
    *,
    spec: str = "backend_python",
    grade: str = "middle",
    theta: float = 0.5,
    se: float = 0.3,
    modules: Mapping[str, tuple[float, float, str]] | None = None,
    salary_min: int | None = 230_000,
    salary_net: bool = True,
    salary_visibility: str = "signal",
    formats: Sequence[str] = ("hybrid", "remote"),
    city: str | None = "Москва",
    region_code: str | None = "77",
    relocation: bool = False,
    declared: Sequence[str] = (),
    industries: Sequence[str] = ("fintech",),
    search_mode: str = "open",
    blind: bool = True,
    fsp_sub: str | None = None,
    tasks: Sequence[tuple[str, float, float, Sequence[str], str | None]] = (),
    birth_year: int | None = None,
    days_ago: float = 20,
    borderline: bool = False,
    trust: float = 1.0,
    extra: Sequence[tuple[str, str, float]] = (),
    with_session: bool = True,
    hidden: bool = False,
    full_name: str = "Иванова Мария Сергеевна",
    blocked: Sequence[uuid.UUID] = (),
) -> uuid.UUID:
    """Кандидат с подтверждённой категорией. tasks: (название, качество 0–1, дней назад, теги, модуль)."""
    uid = uuid.uuid4()
    db.add(
        User(
            id=uid,
            email=f"cand-{uid.hex[:12]}@example.com",
            email_domain="example.com",
            role="candidate",
            status="active",
            email_verified=True,
        )
    )
    db.flush()
    db.add(
        CandidateProfile(
            user_id=uid,
            search_mode=search_mode,
            salary_min=salary_min,
            salary_type="net" if salary_net else "gross",
            salary_visibility=salary_visibility,
            formats=list(formats),
            region_code=region_code,
            city=city,
            relocation=relocation,
            industries=list(industries),
            declared_skills=[{"skill": s, "source": "resume"} for s in declared],
            birth_year=birth_year,
            blocked_company_ids=list(blocked),
            full_name=full_name,
            contact_email=f"real-{uid.hex[:6]}@mail.example",
            phone="+7 900 000-00-00",
            telegram="@cand",
        )
    )
    db.flush()
    if blind:
        grant_consent(db, uid, "blind_card")
    _qualification(
        db, uid, spec, grade, theta, se=se, trust=trust, borderline=borderline, days_ago=days_ago,
        modules=modules, with_session=with_session, hidden=hidden,
    )
    for sp, gr, th in extra:
        _qualification(
            db, uid, sp, gr, th, se=se, trust=trust, borderline=False, days_ago=days_ago, modules=None,
            with_session=with_session,
        )
    if fsp_sub is not None:
        grant_consent(db, uid, "fsp_enrichment")
        ach, ath = fsp_registry_data(fsp_sub)
        db.add(
            FspLink(
                user_id=uid,
                fsp_id=fsp_sub,
                achievements=ach,
                athlete=ath,
                last_sync_at=clock.now() - timedelta(days=1),
                sync_status="ok",
                data_source="fsp_mock",
            )
        )
    for title, q, days, tags, module in tasks:
        t = EmployerTask(
            kind="catalog",
            title=title,
            tags=list(tags),
            time_limit_min=10,
            status="active",
            is_platform=True,
            module=module,
        )
        db.add(t)
        db.flush()
        done = clock.now() - timedelta(days=days)
        db.add(
            TaskAssignment(
                employer_task_id=t.id,
                candidate_id=uid,
                status="auto_scored",
                score=q,
                kind="catalog",
                module=module,
                submitted_at=done,
                finished_at=done,
            )
        )
    db.flush()
    return uid


def seed_demo_pool(db: Session) -> dict[str, uuid.UUID]:
    """Демо-пул под бриф §11: 12 Backend·Middle с разным тестом, ФСП, задачами и условиями + 3 Senior +
    Fullstack + кандидаты, которых работодатель видеть не должен (скрыт, без согласия, младше 16, на проверке)."""
    ids: dict[str, uuid.UUID] = {}
    strong = {"python_core": (1.0, 0.3, "strong"), "sql": (0.9, 0.3, "strong"), "http_api": (0.8, 0.3, "confirmed")}
    ids["anna"] = seed_candidate(
        db, theta=0.95, modules=strong, fsp_sub=FSP_STRONG, declared=("kafka",),
        tasks=[("Идемпотентность платежей", 0.8, 6, ("python", "fastapi"), "python_core")],
    )
    ids["boris"] = seed_candidate(
        db, theta=1.0, modules=strong, tasks=[("Очередь задач", 0.9, 3, ("python",), "python_core")]
    )
    ids["vera"] = seed_candidate(db, theta=0.7, fsp_sub=FSP_MEDIUM, formats=("remote",))
    ids["gleb"] = seed_candidate(db, theta=0.6, salary_min=320_000, salary_visibility="exact")
    ids["dina"] = seed_candidate(db, theta=0.55, borderline=True)
    ids["egor"] = seed_candidate(db, theta=0.5, modules={"python_core": (0.6, 0.3, "confirmed"), "sql": (-0.6, 0.5, "insufficient")})
    ids["zoya"] = seed_candidate(db, theta=0.45, city="Казань", region_code="16", formats=("office",))
    ids["ilya"] = seed_candidate(db, theta=0.4, trust=0.75)
    ids["kira"] = seed_candidate(db, theta=0.35, salary_min=None, formats=())
    ids["lev"] = seed_candidate(db, theta=0.3, days_ago=400)
    ids["mila"] = seed_candidate(db, theta=0.25, industries=("gov",))
    ids["nina"] = seed_candidate(db, theta=0.2, search_mode="active")
    for i, th in enumerate((1.4, 1.6, 1.2)):
        ids[f"senior{i}"] = seed_candidate(db, grade="senior", theta=th)
    ids["fullstack"] = seed_candidate(db, theta=0.65, extra=[("frontend_js", "junior", -0.4)])
    # Не должны попадать к работодателю никогда:
    ids["hidden_mode"] = seed_candidate(db, theta=1.5, search_mode="hidden")
    ids["no_consent"] = seed_candidate(db, theta=1.5, blind=False)
    ids["minor"] = seed_candidate(db, theta=1.5, birth_year=clock.now().year - 15)
    ids["under_review"] = seed_candidate(db, theta=1.5, hidden=True)
    return ids


def walk(obj: Any) -> Iterator[tuple[str, Any]]:
    """Все пары (ключ, значение) JSON на любой глубине."""
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            yield str(k), v
            yield from walk(v)
    elif isinstance(obj, list | tuple):
        for v in obj:
            yield from walk(v)


def assert_no_pii(payload: Any, candidate_ids: Iterable[uuid.UUID] = (), forbidden: Iterable[str] = ()) -> None:
    """0 полей ПДн, ни одного внутреннего id кандидата и номера ЕКП в ответе работодателю."""
    keys = {k for k, _ in walk(payload)}
    assert not (keys & PII_KEYS), sorted(keys & PII_KEYS)
    import json

    text = json.dumps(payload, ensure_ascii=False)
    for cid in candidate_ids:
        assert str(cid) not in text and cid.hex not in text, cid
    for s in forbidden:
        assert s not in text, s
    assert "θ" not in text
