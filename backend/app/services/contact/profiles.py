"""Проекции «кто что видит» (концепция §4.5): карточка кандидата для работодателя и карточка компании для
кандидата. Матрица видимости — app/contact/visibility.py и config/contact.yaml; здесь только сбор фактов.

Работодатель до взаимного «да» получает CandidateBlind: без внутренних id, ЕКП, команд, протоколов и ПДн.
CandidateDisclosed — только при действующей ContactDisclosure этой пары (или отклике кандидата).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.disclosure import ContactDisclosure as DomainDisclosure
from app.contact.visibility import employer_view, pii_keys_in
from app.models.candidate import CandidateFeatures, CandidateProfile
from app.models.company import Company
from app.models.contact import ContactDisclosure
from app.models.users import User
from app.trust.verification import trust_of

from .common import cfg
from .reliability import company_reliability, reliability_dict


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _features_rows(db: Session, candidate_id: uuid.UUID) -> list[CandidateFeatures]:
    rows = list(db.scalars(select(CandidateFeatures).where(CandidateFeatures.candidate_id == candidate_id)))
    # Сначала видимые в банке и подтверждённые тестом, затем по свежести теста
    rows.sort(key=lambda r: (not r.visible, r.theta is None, -(r.test_at.timestamp() if r.test_at else 0)))
    return rows


def _skills(skill_levels: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for skill, info in sorted((skill_levels or {}).items()):
        if not isinstance(info, dict):
            continue
        # Ссылки на доказательства (ref_id: сессия теста, запись ФСП) работодателю до раскрытия не отдаём
        out.append({"skill": skill, "source": info.get("source"), "v": info.get("v")})
    return out


def _tasks(best_tasks: Any) -> list[dict[str, Any]]:
    """Лента задач без внутренних id (DECISIONS 20: доказательства — только непрозрачные ссылки подбора)."""
    out = []
    for t in best_tasks or []:
        if isinstance(t, dict):
            out.append({k: v for k, v in t.items() if not (k in ("id", "ref", "ref_id") or k.endswith("_id"))})
    return out


def candidate_facts(db: Session, candidate_id: uuid.UUID) -> dict[str, Any]:
    """Полный набор фактов о кандидате в словаре проекции (ключи — как в visibility.fields)."""
    user = db.get(User, candidate_id)
    profile = db.get(CandidateProfile, candidate_id)
    rows = _features_rows(db, candidate_id)
    main = rows[0] if rows else None
    facts: dict[str, Any] = {"candidate_id": str(candidate_id)}
    if main is not None:
        facts["category"] = main.category or (f"{main.specialization}:{main.grade}" if main.grade else None)
        facts["test_date"] = _iso(main.test_at)
        facts["has_fsp"] = bool(main.has_fsp)
        facts["fsp"] = list(main.fsp_public or [])
        facts["skills"] = _skills(main.skill_levels)
        facts["tasks"] = _tasks(main.best_tasks)
        if main.theta is not None:
            score = 500 + 100 * main.theta
            se = main.se or 0.0
            facts["test"] = {
                "percentile": main.t_pct,
                # 80%-интервал на линейке 300–600, округлённый до 10 (точный балл — после раскрытия)
                "interval": [int(round((score - 128 * se) / 10) * 10), int(round((score + 128 * se) / 10) * 10)],
                "score": int(round(score)),
            }
        else:
            facts["test"] = None
    else:
        facts["has_fsp"] = False
    if profile is not None:
        facts.update(
            {
                "search_mode": profile.search_mode,
                "salary_min": profile.salary_min,
                "salary_visibility": profile.salary_visibility,
                "formats": list(profile.formats or []),
                "region": profile.region_code,
                "industries": list(profile.industries or []),
                "declared_skills": [
                    s.get("skill") for s in (profile.declared_skills or []) if isinstance(s, dict) and s.get("skill")
                ],
                "city": profile.city,
                "full_name": profile.full_name,
                "email": profile.contact_email or (user.email if user else None),
                "phone": profile.phone,
                "telegram": profile.telegram,
                "resume": profile.resume_text,
                "current_employer": profile.current_employer,
                "university": profile.education,
            }
        )
    elif user is not None:
        facts["email"] = user.email
    return facts


def domain_disclosure(row: ContactDisclosure | None) -> DomainDisclosure | None:
    if row is None:
        return None
    return DomainDisclosure(
        id=f"{row.basis}:{row.basis_id}",
        candidate_id=str(row.candidate_id),
        company_id=str(row.company_id),
        basis=row.basis,  # type: ignore[arg-type]
        basis_id=str(row.basis_id),
        fields=tuple(row.fields or ()),
        consent_text_ver=row.consent_text_ver,
        granted_at=row.granted_at,
        revoked_at=row.revoked_at,
    )


def active_disclosure_row(
    db: Session, candidate_id: uuid.UUID, company_id: uuid.UUID
) -> ContactDisclosure | None:
    """Действующее согласие пары (любое основание: приглашение или отклик) — самое свежее."""
    return db.scalar(
        select(ContactDisclosure)
        .where(
            ContactDisclosure.candidate_id == candidate_id,
            ContactDisclosure.company_id == company_id,
            ContactDisclosure.revoked_at.is_(None),
        )
        .order_by(ContactDisclosure.granted_at.desc())
        .limit(1)
    )


def candidate_for_employer(
    db: Session,
    *,
    candidate_id: uuid.UUID,
    company_id: uuid.UUID,
    pseudonym: str,
    at: datetime,
    offer_salary_to: int | None = None,
    need_id: uuid.UUID | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """CandidateBlind или CandidateDisclosed — решает действующее согласие пары, а не вызывающий код.

    Форма результата одна — EmployerCandidateOut (app/services/contact/schemas.py): карточка панели подбора
    (CandidateCardOut) плюс плоские контакты после раскрытия. ref — непрозрачная ссылка кандидата в рамках компании.

    Карточку строит сервис подбора (та же, что в панели Р6), если он доступен; иначе — проекция по матрице
    видимости config/contact.yaml из фактов профиля. В обоих случаях слепая карточка проверяется на ПДн.
    """
    from .identity import _matching

    disclosure = domain_disclosure(active_disclosure_row(db, candidate_id, company_id))
    facts = candidate_facts(db, candidate_id)
    own = employer_view(
        facts,
        pseudonym=pseudonym,
        company_id=str(company_id),
        now=at,
        disclosure=disclosure,
        offer_salary_to=offer_salary_to,
        cfg=cfg(),
    )
    if own.get("view") == "blind" and pii_keys_in(own, cfg()):
        # Защита в глубину: контракт «0 полей ПДн до согласия» (концепция §4.5)
        raise RuntimeError("ПДн в слепой карточке кандидата")
    # DECISIONS 20: внутренний id кандидата наружу не уходит ни в какой проекции
    own.pop("candidate_id", None)

    m = _matching()
    raw = None
    if m is not None and hasattr(m, "candidate_profile_view"):
        try:
            raw = m.candidate_profile_view(
                db,
                company_id=company_id,
                candidate_id=candidate_id,
                need_id=need_id,
                offer_salary_to=offer_salary_to,
                require_visible=False,
            )
        except Exception:  # noqa: BLE001 — карточка подбора не собралась (нет признаков): своя проекция
            raw = None
    if raw is None:
        return _minimal_card(own, pseudonym=pseudonym, ref=ref)
    view = dict(raw)
    view.pop("candidate_id", None)
    if disclosure is None:
        if pii_keys_in(view, cfg()):
            raise RuntimeError("ПДн в слепой карточке кандидата")
        return view
    # Контакты после взаимного «да» гарантирует модуль контакта: разрешённые кандидатом поля берём из своей
    # проекции, даже если у подбора нет признаков кандидата (тогда его карточка минимальная).
    for name, rule in cfg().visibility.fields.items():
        if _opens_with_disclosure(rule) and name in own and name not in view:
            view[name] = own[name]
    view["view"] = "disclosed"
    view.setdefault("disclosure", own.get("disclosure"))
    return view


_MINIMAL_PUBLIC_KEYS = ("industries", "formats", "search_mode", "has_fsp", "region")


def _opens_with_disclosure(rule: Any) -> bool:
    """Поле контакта по выбору кандидата (if_allowed) или ПДн, которое открывается вместе с ними (город: blind=hide, disclosed=show)."""
    return rule.disclosed == "if_allowed" or (rule.disclosed == "show" and rule.blind == "hide")


def _minimal_card(own: dict[str, Any], *, pseudonym: str, ref: str | None) -> dict[str, Any]:
    """Карточка, когда у подбора нет признаков кандидата (не подтверждён тестом, данные сброшены): форма та же,
    что у панели подбора, но без категории и подробностей — только то, что есть в проекции по матрице видимости."""
    out: dict[str, Any] = {
        "view": own.get("view", "blind"),
        "pseudonym": pseudonym,
        "ref": ref or "",
        "available": False,
        "note": "Подробной карточки нет: кандидат ещё не подтверждён тестом или его данные обновляются",
        "category": None,
    }
    for key in _MINIMAL_PUBLIC_KEYS:
        if key in own:
            out[key] = own[key]
    if own.get("view") == "disclosed":
        out["disclosure"] = own.get("disclosure")
        for name, rule in cfg().visibility.fields.items():
            if _opens_with_disclosure(rule) and name in own:
                out[name] = own[name]
    return out


# --- компания глазами кандидата -----------------------------------------------------------------


def contact_methods_list(company: Company) -> list[dict[str, str]]:
    labels = cfg().invitation.contact_methods
    out = []
    for key, label in labels.items():
        value = (company.contact_methods or {}).get(key)
        if value:
            out.append({"method": key, "label": label, "value": str(value)})
    return out


def company_card(db: Session, company: Company, at: datetime, events: list[Any] | None = None) -> dict[str, Any]:
    """Что кандидат видит о компании всегда, до решения: название, описание, R с разложением, способ связи.

    events — заранее загруженные события надёжности (списки грузят их пакетом)."""
    rel = company_reliability(db, company.id, at, events)
    return {
        "id": company.id,
        "name": company.name or "Компания без названия",
        "field": company.field,
        "description": company.description,
        "website": company.website,
        "personal_email": bool(company.personal_domain),
        "personal_email_label": cfg().quotas.personal_email_label if company.personal_domain else None,
        "contact_methods": contact_methods_list(company),
        "reliability": reliability_dict(rel),
        "trust": trust_of(db, company.id),
    }
