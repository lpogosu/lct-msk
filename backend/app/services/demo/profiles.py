"""Заполнение профиля кандидата демо-мира: опрос, настройки К13, согласия, привязка ФСП ID.

Общие шаги для синтетических кандидатов (population.py, зависит от eval/) и персон (personas.py): модуль не импортирует
eval/, поэтому POST /api/demo/reset работает и в образе бэкенда, где каталога eval/ нет. Всё — настоящими сервисами:
сервис опроса, журнал согласий, привязка ФСП ID (app.services.fsp_link).
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.core.auth_service import RequestMeta
from app.core.clock import clock
from app.core.consents import grant_consent, has_active_consent
from app.matching.skills import get_catalog
from app.models.candidate import CandidateProfile
from app.models.enums import ConsentPurpose
from app.models.users import User
from app.services.demo.cfg import section
from app.services.fsp_link import service as fsp
from app.services.profile.regions import region_of_city
from app.services.testing import labels, survey


def survey_answers(spec: str, declared: set[str]) -> tuple[dict[str, str], dict[str, str]]:
    """(действия, технологии) опроса по специализации и заявленным навыкам — только для серых чипов."""
    cfg = section("survey")
    actions = dict(cfg["actions"][spec if spec in cfg["actions"] else "backend_python"])
    tech_map = cfg["technology_by_skill"]
    tech = {tech_map[s]: str(cfg["recency"]) for s in sorted(declared) if s in tech_map}
    return actions, tech


def fill_survey(db: Session, user: User, spec: str, industries: list[str], months: int, declared: set[str]) -> None:
    """Четыре шага опроса настоящими сервисами; специализация «fullstack» открывает обе составляющие."""
    known = {i["code"] for i in labels.taxonomy()["industries"]}
    industries = [i for i in industries if i in known] or ["other"]
    if spec == "fullstack":
        actions = {**survey_answers("backend_python", declared)[0], **survey_answers("frontend_js", declared)[0]}
        tech = survey_answers("backend_python", declared)[1] | survey_answers("frontend_js", declared)[1]
    else:
        actions, tech = survey_answers(spec, declared)
    survey.save_industries(db, user, industries)
    survey.save_actions(db, user, actions)
    survey.save_stack(db, user, technologies=tech, experience_months=int(months), roles=[], self_assessment={})
    survey.complete(db, user, spec)


def apply_profile(
    db: Session,
    user: User,
    *,
    full_name: str,
    contact_email: str,
    phone: str,
    telegram: str,
    salary_min: int | None,
    formats: list[str],
    city_code: str | None,
    relocation: bool,
    industries: list[str],
    declared: list[str],
    experience_months: int | None,
    declared_grade: str | None,
    search_mode: str = "open",
    salary_visibility: str = "signal",
    weekly_invite_limit: int | None = None,
    self_description: str | None = None,
    city_name: str | None = None,
) -> CandidateProfile:
    """Настройки кандидата (К13), контакты для раскрытия и «заявлено». Контакты вымышленные (зона .example)."""
    prof = db.get(CandidateProfile, user.id)
    assert prof is not None
    cat = get_catalog()
    city = cat.cities.get(city_code) if city_code else None
    prof.full_name = full_name
    prof.contact_email = contact_email
    prof.phone = phone
    prof.telegram = telegram
    prof.salary_min = salary_min
    prof.salary_type = "net"
    prof.salary_visibility = salary_visibility
    prof.formats = list(formats)
    prof.city = city_name or (city.name if city is not None else None)
    prof.region_code = region_of_city(city_code) if city_code else None
    prof.relocation = bool(relocation)
    prof.industries = list(industries)
    prof.search_mode = search_mode
    if weekly_invite_limit is not None:
        prof.weekly_invite_limit = weekly_invite_limit
    prof.declared_skills = [{"skill": s, "source": "manual", "quote": ""} for s in declared]
    prof.experience_months = experience_months
    prof.declared_grade = declared_grade
    if self_description:
        prof.resume_text = self_description
    db.flush()
    return prof


def ensure_consents(db: Session, user: User, *, blind_card: bool = True, calibration: bool = True) -> None:
    if blind_card and not has_active_consent(db, user.id, ConsentPurpose.blind_card):
        grant_consent(db, user.id, ConsentPurpose.blind_card, scope={"context": "demo_seed"})
    if calibration and not has_active_consent(db, user.id, ConsentPurpose.calibration):
        grant_consent(db, user.id, ConsentPurpose.calibration, scope={"context": "demo_seed"})


def link_fsp(db: Session, user: User, sub: str) -> None:
    """Привязка ФСП ID настоящим сервисом (демо-реестр): согласие на обогащение, синхронизация достижений в кэш."""
    fsp.link_account(
        db, user, {"sub": sub, "demo": True}, is_demo=True, consent_fsp_enrichment=True,
        provider=fsp.demo_registry(), meta=RequestMeta(), now=clock.now(),
    )
