"""Вакансии [доп.] (концепция §3.2 шаг 11, §3.1 шаг 13; spec-contact §2.2 п. 7).

«Опубликовать бриф как вакансию»: тот же текст и вилка, статус published. Кандидат видит список, отсортированный
тем же Score, что и подборка работодателя (кандидат против брифа вакансии): признаки — контракт подбора
app.services.matching.build_candidate_features, формула — движок app.matching.scoring (здесь её нет).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contact.rules import validate_salary_range
from app.contact.texts import salary_range
from app.core.errors import bad_request, conflict, not_found
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.contact import Application, Vacancy
from app.models.enums import NeedStatus, VacancyStatus
from app.models.matching import Need
from app.models.users import User

from .common import cfg, now, rel
from .company import company_of, require_complete
from .profiles import company_card

log = logging.getLogger("vzaimno.contact.vacancies")


# --- публикация (работодатель) ------------------------------------------------------------------


@dataclass
class VacancySpec:
    need_id: uuid.UUID
    title: str | None = None
    description: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    salary_type: str | None = None
    format: str | None = None
    city: str | None = None


def _owned_need(db: Session, company: Company, need_id: uuid.UUID) -> Need:
    need = db.get(Need, need_id)
    if need is None or need.company_id != company.id:
        raise not_found("need_not_found", "Бриф не найден")
    if need.status == NeedStatus.closed.value:
        raise conflict("need_closed", "Бриф закрыт — опубликовать его как вакансию нельзя")
    return need


def _salary_check(salary_from: int, salary_to: int, salary_type: str) -> list[str]:
    """Вилка вакансии — по тем же правилам, что у приглашения (DECISIONS 6). Возвращает предупреждения."""
    check = validate_salary_range(salary_from, salary_to, salary_type, cfg())
    if not check.ok:
        raise bad_request(
            "salary_invalid",
            check.errors[0].message,
            {"errors": [{"code": i.code, "message": i.message} for i in check.errors]},
        )
    return [i.message for i in check.warnings]


def _how_we_check(need: Need) -> dict[str, Any]:
    """«Как мы проверим» (концепция §5.5) — из сервиса подбора; без него вакансия публикуется без блока."""
    try:
        from app.services.matching.needs import check_view

        return dict(check_view(need))
    except Exception as e:  # noqa: BLE001 — сервис подбора собирается параллельно; блок необязательный
        log.info("Блок «как мы проверим» недоступен: %s", e)
        return {}


def publish(db: Session, user: User, spec: VacancySpec) -> tuple[Vacancy, list[str]]:
    company = company_of(db, user)
    require_complete(company)
    need = _owned_need(db, company, spec.need_id)
    existing = db.scalar(
        select(Vacancy).where(Vacancy.need_id == need.id, Vacancy.status == VacancyStatus.published.value)
    )
    if existing is not None:
        raise conflict(
            "vacancy_exists",
            "Этот бриф уже опубликован как вакансия — измените её или снимите с публикации",
            {"vacancy_id": str(existing.id)},
        )
    salary_from = spec.salary_from if spec.salary_from is not None else need.salary_from
    salary_to = spec.salary_to if spec.salary_to is not None else need.salary_to
    if salary_from is None or salary_to is None:
        raise bad_request("salary_required", "Укажите вилку «от–до»: без неё вакансия не публикуется")
    salary_type = spec.salary_type or ("net" if need.salary_net else "gross")
    warnings = _salary_check(int(salary_from), int(salary_to), salary_type)
    fmt = spec.format if spec.format is not None else need.format
    if fmt is not None and fmt not in cfg().invitation.formats:
        raise bad_request("format_invalid", "Выберите формат работы: " + ", ".join(cfg().invitation.formats.values()).lower())
    title = (spec.title or need.title or "").strip()
    if not title:
        raise bad_request("title_required", "Укажите название вакансии")
    description = (spec.description or need.raw_text or "").strip()
    at = now()
    vac = Vacancy(
        company_id=company.id,
        need_id=need.id,
        title=title[:200],
        description=description,
        specialization=need.specialization,
        grade=need.grade,
        salary_from=int(salary_from),
        salary_to=int(salary_to),
        salary_net=salary_type == "net",
        format=fmt,
        city=spec.city if spec.city is not None else need.city,
        status=VacancyStatus.published.value,
        published_at=at,
        test_profile=_how_we_check(need),
        created_at=at,
        updated_at=at,
    )
    db.add(vac)
    db.flush()
    return vac, warnings


@dataclass
class VacancyPatch:
    title: str | None = None
    description: str | None = None
    salary_from: int | None = None
    salary_to: int | None = None
    salary_type: str | None = None
    format: str | None = None
    city: str | None = None


def owned_vacancy(db: Session, user: User, vacancy_id: uuid.UUID) -> Vacancy:
    vac = db.get(Vacancy, vacancy_id)
    if vac is None or user.company_id is None or vac.company_id != user.company_id:
        raise not_found("vacancy_not_found", "Вакансия не найдена")
    return vac


def update(db: Session, vac: Vacancy, patch: VacancyPatch) -> list[str]:
    if vac.status == VacancyStatus.closed.value:
        raise conflict("vacancy_closed", "Вакансия снята с публикации — изменить её нельзя")
    salary_from = patch.salary_from if patch.salary_from is not None else vac.salary_from
    salary_to = patch.salary_to if patch.salary_to is not None else vac.salary_to
    salary_type = patch.salary_type or ("net" if vac.salary_net else "gross")
    warnings = _salary_check(int(salary_from), int(salary_to), salary_type)
    if patch.format is not None and patch.format not in cfg().invitation.formats:
        raise bad_request("format_invalid", "Выберите формат работы: " + ", ".join(cfg().invitation.formats.values()).lower())
    if patch.title is not None:
        if not patch.title.strip():
            raise bad_request("title_required", "Укажите название вакансии")
        vac.title = patch.title.strip()[:200]
    if patch.description is not None:
        vac.description = patch.description.strip()
    vac.salary_from, vac.salary_to, vac.salary_net = int(salary_from), int(salary_to), salary_type == "net"
    if patch.format is not None:
        vac.format = patch.format
    if patch.city is not None:
        vac.city = patch.city or None
    vac.updated_at = now()
    return warnings


def close(db: Session, vac: Vacancy) -> None:
    if vac.status != VacancyStatus.closed.value:
        vac.status = VacancyStatus.closed.value
        vac.updated_at = now()


# --- Score кандидата против брифа вакансии -----------------------------------------------------


@dataclass(frozen=True)
class VacancyScore:
    score: float
    parts: dict[str, float]
    spec: str
    grade: str
    category: str | None


def _scoring() -> Any | None:
    try:
        from app.matching import scoring

        return scoring
    except Exception:  # noqa: BLE001 — движок подбора недоступен: список без Score
        return None


def _brief(scoring: Any, vac: Vacancy, need: Need | None) -> Any | None:
    """BriefSpec вакансии: разобранный бриф (как в подборке), иначе — по полям вакансии."""
    if need is not None and (need.parsed or {}).get("brief"):
        try:
            from app.services.matching.needs import need_brief

            return need_brief(need)
        except Exception:  # noqa: BLE001 — формат брифа другого модуля: строим по полям вакансии
            pass
    if not vac.specialization or not vac.grade:
        return None
    return scoring.BriefSpec(
        need_id=str(vac.need_id or vac.id),
        spec=vac.specialization,
        grade=vac.grade,
        salary_from=vac.salary_from,
        salary_to=vac.salary_to,
        salary_net=vac.salary_net,
        formats=frozenset({vac.format}) if vac.format else frozenset(),
    )


def candidate_features(db: Session, user_id: uuid.UUID) -> list[Any]:
    """Контракт подбора: признаки кандидата по всем его категориям (без записи в кэш)."""
    try:
        from app.services.matching.features import build_candidate_features

        return list(build_candidate_features(db, user_id))
    except Exception as e:  # noqa: BLE001 — сервис подбора недоступен: список вакансий без Score
        log.info("Признаки кандидата недоступны: %s", e)
        return []


def _category_pool(db: Session, spec: str, grade: str) -> list[Any]:
    try:
        from app.services.matching.features import load_pool

        return [r.features for r in load_pool(db, [(spec, grade)])]
    except Exception:  # noqa: BLE001 — без пула T считается по калибровке банка (n < 30)
        return []


def scores_for(db: Session, user_id: uuid.UUID, vacancies: Sequence[Vacancy]) -> dict[uuid.UUID, VacancyScore]:
    """Score кандидата против брифа каждой вакансии — лучший по его категориям (Fullstack — в обеих)."""
    scoring = _scoring()
    feats = candidate_features(db, user_id)
    if scoring is None or not feats:
        return {}
    contexts: dict[tuple[str, str], Any] = {}
    for f in feats:
        key = (f.spec, f.grade)
        if key not in contexts:
            pool = [p for p in _category_pool(db, f.spec, f.grade) if p.candidate_id != f.candidate_id]
            contexts[key] = scoring.ScoringContext.build([*pool, f])
    needs = {n.id: n for n in db.scalars(select(Need).where(Need.id.in_([v.need_id for v in vacancies if v.need_id])))}
    out: dict[uuid.UUID, VacancyScore] = {}
    for vac in vacancies:
        brief = _brief(scoring, vac, needs.get(vac.need_id) if vac.need_id else None)
        if brief is None:
            continue
        best = None
        for f in feats:
            r = scoring.score_candidate(f, brief, contexts[(f.spec, f.grade)])
            if best is None or r.score > best.score:
                best = r
        if best is not None:
            try:
                category = scoring.ScoringConfig.load().category_name(best.spec, best.grade)
            except Exception:  # noqa: BLE001
                category = None
            out[vac.id] = VacancyScore(
                score=best.score_display,
                parts=dict(best.parts_display),
                spec=best.spec,
                grade=best.grade,
                category=category,
            )
    return out


# --- DTO ---------------------------------------------------------------------------------------


def _salary(vac: Vacancy) -> dict[str, Any]:
    stype = "net" if vac.salary_net else "gross"
    label = cfg().salary.types.get(stype, "")
    return {
        "salary_from": vac.salary_from,
        "salary_to": vac.salary_to,
        "salary_type": stype,
        "salary_type_label": label,
        "text": f"{salary_range(vac.salary_from, vac.salary_to)} {label}".strip(),
    }


def city_label(city: str | None) -> str | None:
    """Код города справочника (moscow) → название («Москва»); свободный текст остаётся как есть."""
    if not city:
        return None
    from app.matching.skills import get_catalog

    known = get_catalog().cities.get(city)
    return known.name if known is not None else city


def vacancy_base(vac: Vacancy, at: datetime | None = None) -> dict[str, Any]:
    at = at or now()
    return {
        "id": vac.id,
        "need_id": vac.need_id,
        "title": vac.title,
        "description": vac.description,
        "specialization": vac.specialization,
        "grade": vac.grade,
        "salary": _salary(vac),
        "format": vac.format,
        "format_label": cfg().invitation.formats.get(vac.format) if vac.format else None,
        "city": vac.city,
        "city_name": city_label(vac.city),
        "status": vac.status,
        "published_text": rel(vac.published_at, at),
    }


def application_counts(db: Session, vacancy_ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, dict[str, int]]:
    ids = list(vacancy_ids)
    out: dict[uuid.UUID, dict[str, int]] = {i: {} for i in ids}
    if not ids:
        return out
    for vid, status, n in db.execute(
        select(Application.vacancy_id, Application.status, func.count())
        .where(Application.vacancy_id.in_(ids))
        .group_by(Application.vacancy_id, Application.status)
    ):
        out[vid][status] = int(n)
    return out


def employer_list(db: Session, user: User) -> list[dict[str, Any]]:
    company = company_of(db, user)
    rows = list(db.scalars(select(Vacancy).where(Vacancy.company_id == company.id).order_by(Vacancy.created_at.desc())))
    counts = application_counts(db, [v.id for v in rows])
    at = now()
    out = []
    for v in rows:
        c = counts.get(v.id, {})
        out.append(
            {
                **vacancy_base(v, at),
                "applications_total": sum(c.values()),
                "applications_new": c.get("submitted", 0),
                "how_we_check": v.test_profile or None,
            }
        )
    return out


def published_for_candidate(db: Session, user: User) -> list[Vacancy]:
    profile = db.get(CandidateProfile, user.id)
    blocked = set(profile.blocked_company_ids or []) if profile else set()
    rows = db.scalars(select(Vacancy).where(Vacancy.status == VacancyStatus.published.value))
    return [v for v in rows if v.company_id not in blocked]


def candidate_list(db: Session, user: User, *, only_matching: bool) -> list[dict[str, Any]]:
    """К12: вакансии по Score (тот же, что у работодателя); без признаков — по свежести публикации."""
    at = now()
    rows = published_for_candidate(db, user)
    scores = scores_for(db, user.id, rows)
    mine = {
        a.vacancy_id: a
        for a in db.scalars(select(Application).where(Application.candidate_id == user.id))
    }
    companies = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_({v.company_id for v in rows})))}
    items = []
    for v in rows:
        s = scores.get(v.id)
        if only_matching and scores and (s is None or s.score <= 0):
            continue
        comp = companies.get(v.company_id)
        card = company_card(db, comp, at) if comp else None
        app = mine.get(v.id)
        items.append(
            {
                **vacancy_base(v, at),
                "company": {
                    "id": card["id"],
                    "name": card["name"],
                    "reliability": card["reliability"]["display"],
                    "r": card["reliability"]["r"],
                    "personal_email_label": card["personal_email_label"],
                }
                if card
                else None,
                "score": s.score if s else None,
                "parts": s.parts if s else None,
                "category": s.category if s else None,
                "weak_match": bool(s is not None and s.score < cfg().matching.weak_score_below),
                "application_status": app.status if app else None,
                "application_id": app.id if app else None,
                "_published_at": v.published_at,
            }
        )
    # Score ↓, затем свежие публикации, затем id — детерминированно
    items.sort(
        key=lambda d: (
            -(d["score"] if d["score"] is not None else -1.0),
            -(d["_published_at"].timestamp() if d["_published_at"] else 0.0),
            str(d["id"]),
        )
    )
    for d in items:
        d.pop("_published_at", None)
    return items


def candidate_detail(db: Session, user: User, vacancy_id: uuid.UUID) -> dict[str, Any]:
    vac = db.get(Vacancy, vacancy_id)
    if vac is None or vac.status != VacancyStatus.published.value:
        # Снятая с публикации вакансия видна только тем, кто на неё откликнулся
        applied = (
            vac is not None
            and db.scalar(
                select(Application.id).where(Application.vacancy_id == vac.id, Application.candidate_id == user.id)
            )
            is not None
        )
        if not applied:
            raise not_found("vacancy_not_found", "Вакансия не найдена")
    assert vac is not None
    at = now()
    comp = db.get(Company, vac.company_id)
    s = scores_for(db, user.id, [vac]).get(vac.id)
    app = db.scalar(select(Application).where(Application.vacancy_id == vac.id, Application.candidate_id == user.id))
    c = cfg()
    return {
        **vacancy_base(vac, at),
        "company": company_card(db, comp, at) if comp else None,
        "score": s.score if s else None,
        "parts": s.parts if s else None,
        "category": s.category if s else None,
        "how_we_check": vac.test_profile or None,
        "application_status": app.status if app else None,
        "application_id": app.id if app else None,
        "consent": {
            "text_version": c.disclosure.consent_text_version,
            "default_fields": list(c.disclosure.default_fields),
            "contact_fields": dict(c.disclosure.contact_fields),
            "text": "Откликаясь, вы передаёте этой компании контакты и поля профиля, которые отметите. "
            "Согласие отзывается в журнале согласий",
        },
        "response_days": c.application.response_days,
    }
