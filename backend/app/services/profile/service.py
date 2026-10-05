"""Профиль кандидата: ручная форма (К7), приватность и поиск (К13), живое превью «так вас видят»,
журнал согласий, «Как меня видят» (К15). Концепция §3.1 шаги 7, 10, 16; §4.5; DECISIONS п. 1, 20.

Правила:
- Всё, что кандидат пишет о себе, и всё из резюме — «заявлено» (серые чипы). Категорию и Score это не
  меняет (DECISIONS п. 1); стаж — только подсказка при выборе грейда.
- Превью строится той же матрицей видимости, что и ответы работодателю (app/contact/visibility.py):
  слепая карточка без ПДн, раскрытая — с полями, которые кандидат разрешил.
- Изменения профиля и настроек помечают признаки подбора устаревшими (invalidate_features).
"""

from __future__ import annotations

import re
import statistics
import uuid
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.contact.disclosure import ContactDisclosure as DomainDisclosure
from app.contact.disclosure import chosen_fields, disclosure_fields_problem
from app.contact.pseudonym import pseudonym as make_pseudonym
from app.contact.settings import get_contact_config
from app.contact.visibility import blind_view, disclosed_view, pii_keys_in
from app.core.access import ensure_role
from app.core.audit import audit
from app.core.auth_service import RequestMeta
from app.core.clock import relative_ru
from app.core.configs import load_config
from app.core.consents import CONSENT_DOCUMENTS, has_active_consent, revocable_in_journal
from app.core.errors import ApiError, bad_request
from app.core.settings import Settings
from app.llm.masking import PHONE_RE, TELEGRAM_HANDLE_RE, TELEGRAM_LINK_RE
from app.matching.skills import SkillCatalog, get_catalog
from app.models.candidate import DEFAULT_FIELD_VISIBILITY, CandidateProfile
from app.models.company import Company
from app.models.contact import Application, Feedback, Invitation
from app.models.enums import ConsentPurpose, SalaryType, SalaryVisibility, SearchMode, UserRole
from app.models.matching import ImpressionLog, NeedVersion, ProfileView
from app.models.users import Consent, User
from app.services.profile.regions import REGIONS, region_name, region_of_city
from app.services.profile.schemas import (
    BankStatusOut,
    BlockedCompanyOut,
    CodeName,
    CompanySearchOut,
    ConsentJournalOut,
    DeclaredItemOut,
    ExperiencePeriodIO,
    FieldVisibilityOut,
    MissingSkillOut,
    PreviewIn,
    PreviewOut,
    PrivacyOptionsOut,
    PrivacyOut,
    PrivacyUpdateIn,
    ProfileOut,
    ProfileUpdateIn,
    ReasonCount,
    ResumeStateOut,
    VisibilityStatsOut,
)

DECLARED_NOTE = (
    "Всё, что вы пишете о себе и что распознано из резюме, — «заявлено»: работодатель видит это серыми "
    "чипами, но категорию и место в подборке оно не меняет. Их подтверждают тест, задачи и ФСП."
)
FIELD_LABELS = {
    "full_name": "ФИО",
    "photo_url": "Фото",
    "email": "Email",
    "phone": "Телефон",
    "telegram": "Telegram",
    "resume": "Резюме",
    "current_employer": "Текущий работодатель",
    "university": "Вуз и образование",
}
SEARCH_MODE_LABELS = {
    SearchMode.active.value: "Активно ищу",
    SearchMode.open.value: "Открыт к предложениям",
    SearchMode.hidden.value: "Не ищу — скрыт из банка",
}
_TG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{4,31}$")
_INTL_PHONE_RE = re.compile(r"\+[\d\s\-().]{9,24}")
_GRADE_ORDER = ("intern", "junior", "middle", "senior")
VISIBILITY_WINDOW_DAYS = 7
MIN_IMPRESSIONS = 5


# --------------------------------------------------------------------------- общее


def invalidate(db: Session, user_id: uuid.UUID) -> None:
    try:
        from app.services.matching import invalidate_features
    except Exception:  # noqa: BLE001 — подбор может быть ещё не собран
        return
    invalidate_features(db, user_id)


def get_profile_row(db: Session, user: User) -> CandidateProfile:
    """Профиль кандидата (создаётся при подтверждении email; если его нет — создаём)."""
    ensure_role(user, UserRole.candidate.value)
    prof = db.get(CandidateProfile, user.id)
    if prof is None:
        prof = CandidateProfile(user_id=user.id)
        db.add(prof)
        db.flush()
    return prof


def _taxonomy() -> dict[str, Any]:
    return load_config("taxonomy")


def grade_label(code: str | None) -> str | None:
    if not code:
        return None
    return next((g["name"] for g in _taxonomy()["grades"] if g["code"] == code), code)


def industry_names() -> dict[str, str]:
    raw = _taxonomy().get("industries") or []
    if isinstance(raw, Mapping):
        return {k: (v.get("name", k) if isinstance(v, Mapping) else str(v)) for k, v in raw.items()}
    return {str(i["code"]): str(i["name"]) for i in raw if isinstance(i, Mapping) and "code" in i}


def freshness_label(code: str | None) -> str | None:
    """Полоса актуальности (fresh/actual/aging/stale) → подпись из config/testing.yaml («свежий»)."""
    if not code:
        return None
    try:
        from app.testing.session import load_rules

        return next((label for c, label, _ in load_rules().freshness_bands if c == code), code)
    except Exception:  # noqa: BLE001
        return code


def experience_label(months: int | None) -> str | None:
    if months is None:
        return None
    years, rest = divmod(int(months), 12)
    parts = []
    if years:
        n = years % 100
        word = "лет" if 11 <= n <= 14 or years % 10 in (0, 5, 6, 7, 8, 9) else ("год" if years % 10 == 1 else "года")
        parts.append(f"{years} {word}")
    if rest or not years:
        parts.append(f"{rest} мес.")
    return " ".join(parts)


def normalize_phone(raw: str) -> str:
    from app.services.resume.contacts import normalize_phone as _norm

    text = raw.strip()
    m = PHONE_RE.search(text)
    if m and m.group(0).strip() == text.replace(" ", " ").strip():
        return _norm(text)
    digits = re.sub(r"\D", "", text)
    # Международный номер: только цифры и разделители (без «доб. 5» — добавочный исказил бы номер);
    # +7 — всегда 11 цифр
    if _INTL_PHONE_RE.fullmatch(text) and 10 <= len(digits) <= 15 and (digits[0] != "7" or len(digits) == 11):
        return "+" + digits
    raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "phone", "message": "Телефон в формате +7 915 204-18-73", "type": "phone"}])


def normalize_telegram(raw: str) -> str:
    text = raw.strip()
    m = TELEGRAM_LINK_RE.search(text)
    if m:
        return "@" + m.group(1)
    handle = text.removeprefix("@")
    if _TG_RE.match(handle) and (TELEGRAM_HANDLE_RE.search("@" + handle) is not None):
        return "@" + handle
    raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "telegram", "message": "Telegram: @ник из 5–32 латинских букв, цифр и _", "type": "telegram"}])


def resolve_city(cat: SkillCatalog, raw: str | None) -> tuple[str | None, str | None]:
    """(код города справочника, свободный текст) — город не из справочника сохраняем как есть."""
    if raw is None or not raw.strip():
        return None, None
    text = raw.strip()
    if text in cat.cities:
        return text, None
    found = cat.find_cities(text)
    if found:
        return found[0].id, None
    return None, text


def city_out(cat: SkillCatalog, value: str | None) -> tuple[CodeName | None, str | None]:
    if not value:
        return None, None
    city = cat.cities.get(value)
    if city is not None:
        return CodeName(code=city.id, name=city.name), None
    return None, value


def normalize_skills(cat: SkillCatalog, items: Iterable[str]) -> tuple[list[str], list[str]]:
    ids: list[str] = []
    unknown: list[str] = []
    for raw in items:
        text = str(raw).strip()
        if not text:
            continue
        if text in cat.skills:
            ids.append(text)
            continue
        found = cat.find_skills(text, fuzzy=True)
        if found:
            ids.append(found[0].id)
        else:
            unknown.append(text)
    return list(dict.fromkeys(ids)), unknown


def unknown_error(field: str, values: list[str], what: str) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        "Проверьте заполнение полей",
        [{"field": field, "message": f"{what} нет в справочнике: {', '.join(values[:10])}", "type": "unknown_code"}],
    )


def _merge_declared(
    old: list[Any], new_ids: list[str], key: str, *, name_of: Any = None, extra: Mapping[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Новый список «заявлено»: у оставшихся элементов сохраняем источник и цитату (из резюме)."""
    prev = {str(x.get(key)): x for x in old or () if isinstance(x, Mapping) and x.get(key)}
    out: list[dict[str, Any]] = []
    for i in new_ids:
        if i in prev:
            out.append(dict(prev[i]))
        else:
            item: dict[str, Any] = {key: i, "source": "manual", "quote": None}
            if name_of is not None:
                item["name"] = name_of(i)
            out.append(item)
    return out


def _declared_out(items: list[Any], key: str, name_of: Any) -> list[DeclaredItemOut]:
    out = []
    for x in items or ():
        if not isinstance(x, Mapping) or not x.get(key):
            continue
        code = str(x[key])
        try:
            name = name_of(code)
        except KeyError:
            name = str(x.get("name") or code)
        out.append(DeclaredItemOut(id=code, name=name, source=str(x.get("source") or "manual"), quote=x.get("quote")))
    return out


# --------------------------------------------------------------------------- К7: профиль


def profile_out(db: Session, user: User, prof: CandidateProfile, now: datetime) -> ProfileOut:
    cat = get_catalog()
    city, city_text = city_out(cat, prof.city)
    region = CodeName(code=prof.region_code, name=region_name(prof.region_code) or prof.region_code) if prof.region_code else None
    inds = industry_names()
    rf = prof.resume_fields or {}
    status = rf.get("status") if rf.get("status") in ("draft", "confirmed") else ("confirmed" if prof.resume_text else "none")
    draft = rf.get("draft") or {}
    missing = []
    if not prof.full_name:
        missing.append("ФИО — его увидит только компания после взаимного «да»")
    if not (prof.phone or prof.telegram or prof.contact_email):
        missing.append("Способ связи: телефон или Telegram (иначе компания напишет на email аккаунта)")
    if not prof.declared_skills:
        missing.append("Навыки — серые чипы «заявлено» рядом с подтверждёнными тестом")
    if prof.experience_months is None:
        missing.append("Стаж — подсказка при выборе грейда")
    return ProfileOut(
        user_id=user.id,
        account_email=user.email,
        full_name=prof.full_name,
        contact_email=prof.contact_email,
        phone=prof.phone,
        telegram=prof.telegram,
        city=city,
        city_text=city_text,
        region=region,
        current_employer=prof.current_employer,
        education=prof.education,
        birth_year=prof.birth_year,
        declared_grade=CodeName(code=prof.declared_grade, name=grade_label(prof.declared_grade) or "") if prof.declared_grade else None,
        roles=_declared_out(prof.declared_roles, "role", lambda r: cat.roles[r].name),
        skills=_declared_out(prof.declared_skills, "skill", cat.name),
        soft_skills=_declared_out(prof.soft_skills, "id", lambda s: cat.soft_skills[s]),
        experience_months=prof.experience_months,
        experience_label=experience_label(prof.experience_months),
        experience=[ExperiencePeriodIO.model_validate(p) for p in prof.experience or () if isinstance(p, Mapping)],
        industries=[CodeName(code=c, name=inds.get(c, c)) for c in prof.industries or ()],
        resume=ResumeStateOut(
            status=status,  # type: ignore[arg-type]
            uploaded_ago=relative_ru(prof.resume_uploaded_at, now) if prof.resume_uploaded_at else None,
            pages=(draft.get("parse") or {}).get("pages") or rf.get("pages"),
            has_text=bool(prof.resume_text),
        ),
        missing=missing,
        note=DECLARED_NOTE,
    )


def update_profile(db: Session, user: User, body: ProfileUpdateIn, meta: RequestMeta, now: datetime) -> CandidateProfile:
    prof = get_profile_row(db, user)
    cat = get_catalog()
    fields = body.model_fields_set
    simple = {"full_name": "full_name", "current_employer": "current_employer", "education": "education"}
    for f, col in simple.items():
        if f in fields:
            value = getattr(body, f)
            setattr(prof, col, value.strip() if isinstance(value, str) and value.strip() else None)
    if "contact_email" in fields:
        prof.contact_email = str(body.contact_email).lower() if body.contact_email else None
    if "phone" in fields:
        prof.phone = normalize_phone(body.phone) if body.phone else None
    if "telegram" in fields:
        prof.telegram = normalize_telegram(body.telegram) if body.telegram else None
    if "city" in fields:
        code, text = resolve_city(cat, body.city)
        prof.city = code or text
        region = region_of_city(code)
        if region:
            prof.region_code = region
    if "birth_year" in fields:
        if body.birth_year is not None and body.birth_year > now.year - 14:
            raise ApiError(
                422,
                "validation_error",
                "Проверьте заполнение полей",
                [{"field": "birth_year", "message": "Платформа доступна с 14 лет", "type": "birth_year"}],
            )
        from app.services.fsp_link.service import registry_birth_year

        registry_year = registry_birth_year(db, user.id)
        if registry_year is not None and body.birth_year != registry_year:
            # Год из реестра ФСП защищает несовершеннолетних (концепция §4.3): заявленным его не заменить
            raise ApiError(
                422,
                "validation_error",
                "Проверьте заполнение полей",
                [{"field": "birth_year", "message": "Год рождения подтверждён реестром ФСП — изменить его нельзя", "type": "registry"}],
            )
        prof.birth_year = body.birth_year
    if "declared_grade" in fields:
        prof.declared_grade = body.declared_grade
    if "roles" in fields:
        ids = [r for r in dict.fromkeys(body.roles or ())]
        bad = [r for r in ids if r not in cat.roles]
        if bad:
            raise unknown_error("roles", bad, "Ролей")
        prof.declared_roles = _merge_declared(prof.declared_roles, ids, "role")
    if "skills" in fields:
        ids, unknown = normalize_skills(cat, body.skills or ())
        if unknown:
            raise unknown_error("skills", unknown, "Навыков")
        prof.declared_skills = _merge_declared(prof.declared_skills, ids, "skill")
    if "soft_skills" in fields:
        ids = list(dict.fromkeys(body.soft_skills or ()))
        bad = [s for s in ids if s not in cat.soft_skills]
        if bad:
            raise unknown_error("soft_skills", bad, "Софт-скиллов")
        prof.soft_skills = _merge_declared(prof.soft_skills, ids, "id", name_of=lambda s: cat.soft_skills[s])
    if "experience" in fields:
        periods = []
        for p in body.experience or ():
            if p.end is not None and p.end < p.start:
                raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "experience", "message": "Конец периода раньше начала", "type": "period"}])
            periods.append(p.model_dump())
        prof.experience = periods
        if "experience_months" not in fields:
            prof.experience_months = months_from_periods(periods, now)
    if "experience_months" in fields:
        prof.experience_months = body.experience_months
    if "industries" in fields:
        names = industry_names()
        ids = list(dict.fromkeys(body.industries or ()))
        bad = [i for i in ids if i not in names]
        if bad:
            raise unknown_error("industries", bad, "Отраслей")
        prof.industries = ids
    prof.updated_at = now
    audit(db, "profile.updated", actor_id=user.id, actor_role=user.role, entity_type="candidate_profile", entity_id=user.id, ip=meta.ip, fields=sorted(fields))
    invalidate(db, user.id)
    db.commit()
    return prof


def months_from_periods(periods: list[Mapping[str, Any]], now: datetime) -> int | None:
    """Стаж по периодам «ГГГГ-ММ»: объединение без двойного счёта пересечений (как в разборе резюме)."""
    from app.services.resume.dates import Period, merged_months, month_index

    now_idx = month_index(now.year, now.month)
    ps = []
    for p in periods:
        y, m = (int(x) for x in str(p["start"]).split("-"))
        start = month_index(y, m)
        if p.get("end"):
            ey, em = (int(x) for x in str(p["end"]).split("-"))
            end = min(month_index(ey, em), now_idx)
        else:
            end = now_idx
        if start <= end:
            ps.append(Period(start, end, not p.get("end"), (0, 0)))
    if not ps:
        return None
    return merged_months(ps)[0]


# --------------------------------------------------------------------------- К13: приватность и поиск


def _blocked_out(db: Session, ids: Iterable[uuid.UUID]) -> list[BlockedCompanyOut]:
    ids = list(ids)
    if not ids:
        return []
    rows = {c.id: c for c in db.scalars(select(Company).where(Company.id.in_(ids)))}
    return [BlockedCompanyOut(id=i, name=rows[i].name or "Компания без названия") for i in ids if i in rows]


def _field_visibility(raw: Mapping[str, Any] | None) -> dict[str, bool]:
    out = dict(DEFAULT_FIELD_VISIBILITY)
    for k, v in (raw or {}).items():
        if k in out:
            out[k] = bool(v)
    return out


def _visibility_out(fv: Mapping[str, bool]) -> list[FieldVisibilityOut]:
    contact = set(get_contact_config().disclosure.contact_fields)
    return [
        FieldVisibilityOut(field=k, label=FIELD_LABELS.get(k, k), allowed=bool(v), contact=k in contact)
        for k, v in fv.items()
    ]


def _options() -> PrivacyOptionsOut:
    cfg = get_contact_config()
    return PrivacyOptionsOut(
        weekly_limits=list(load_config("contact")["candidate_limits"]["weekly_options"]),
        formats=[CodeName(code=k, name=v) for k, v in cfg.invitation.formats.items()],
        search_modes=[CodeName(code=k, name=v) for k, v in SEARCH_MODE_LABELS.items()],
        regions=[CodeName(code=k, name=v) for k, v in REGIONS.items()],
    )


@dataclass
class _Draft:
    """Настройки К13 (сохранённые или черновик превью) в одном месте."""

    search_mode: str
    salary_min: int | None
    salary_type: str
    salary_visibility: str
    weekly_invite_limit: int | None
    formats: list[str]
    region_code: str | None
    city: str | None
    relocation: bool
    available_from: Any
    blocked_company_ids: list[uuid.UUID]
    field_visibility: dict[str, bool]

    @classmethod
    def of(cls, prof: CandidateProfile) -> _Draft:
        return cls(
            search_mode=prof.search_mode or SearchMode.open.value,
            salary_min=prof.salary_min,
            salary_type=prof.salary_type or SalaryType.net.value,
            salary_visibility=prof.salary_visibility or SalaryVisibility.signal.value,
            weekly_invite_limit=prof.weekly_invite_limit,
            formats=list(prof.formats or ()),
            region_code=prof.region_code,
            city=prof.city,
            relocation=bool(prof.relocation),
            available_from=prof.available_from,
            blocked_company_ids=list(prof.blocked_company_ids or ()),
            field_visibility=_field_visibility(prof.field_visibility),
        )


def apply_privacy(db: Session, draft: _Draft, body: PrivacyUpdateIn) -> _Draft:
    """Проверить и применить изменения К13 к черновику (без записи в БД)."""
    fields = body.model_fields_set
    cat = get_catalog()
    if "search_mode" in fields and body.search_mode is not None:
        draft.search_mode = body.search_mode
    if "salary_min" in fields:
        step = int(load_config("contact")["salary"]["step"])
        if body.salary_min is not None and body.salary_min % step:
            raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "salary_min", "message": f"Минимум — с шагом {step:,} ₽".replace(",", " "), "type": "step"}])
        draft.salary_min = body.salary_min
    if "salary_type" in fields and body.salary_type is not None:
        draft.salary_type = body.salary_type
    if "salary_visibility" in fields and body.salary_visibility is not None:
        draft.salary_visibility = body.salary_visibility
    if "weekly_invite_limit" in fields:
        options = load_config("contact")["candidate_limits"]["weekly_options"]
        if body.weekly_invite_limit not in options:
            raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "weekly_invite_limit", "message": "Лимит: 3, 5, 10 или без ограничений", "type": "choice"}])
        draft.weekly_invite_limit = body.weekly_invite_limit
    if "formats" in fields:
        draft.formats = sorted(set(body.formats or ()))
    if "city" in fields:
        code, text = resolve_city(cat, body.city)
        draft.city = code or text
        region = region_of_city(code)
        if region and "region_code" not in fields:
            draft.region_code = region
    if "region_code" in fields:
        if body.region_code is not None and body.region_code not in REGIONS:
            raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "region_code", "message": "Неизвестный код субъекта РФ", "type": "choice"}])
        draft.region_code = body.region_code
    if "relocation" in fields and body.relocation is not None:
        draft.relocation = body.relocation
    if "available_from" in fields:
        draft.available_from = body.available_from
    if "blocked_company_ids" in fields:
        ids = list(dict.fromkeys(body.blocked_company_ids or ()))
        found = set(db.scalars(select(Company.id).where(Company.id.in_(ids)))) if ids else set()
        missing = [str(i) for i in ids if i not in found]
        if missing:
            raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "blocked_company_ids", "message": "Таких компаний нет", "type": "unknown_company"}])
        draft.blocked_company_ids = ids
    if "field_visibility" in fields and body.field_visibility is not None:
        unknown = [k for k in body.field_visibility if k not in DEFAULT_FIELD_VISIBILITY]
        if unknown:
            raise ApiError(422, "validation_error", "Проверьте заполнение полей", [{"field": "field_visibility", "message": f"Неизвестные поля: {', '.join(unknown)}", "type": "unknown_field"}])
        fv = dict(draft.field_visibility)
        fv.update({k: bool(v) for k, v in body.field_visibility.items()})
        problem = disclosure_fields_problem([k for k, v in fv.items() if v])
        if problem:
            raise bad_request("contact_field_required", problem)
        draft.field_visibility = fv
    return draft


def _save_draft(prof: CandidateProfile, d: _Draft) -> None:
    prof.search_mode = d.search_mode
    prof.salary_min = d.salary_min
    prof.salary_type = d.salary_type
    prof.salary_visibility = d.salary_visibility
    prof.weekly_invite_limit = d.weekly_invite_limit
    prof.formats = d.formats
    prof.region_code = d.region_code
    prof.city = d.city
    prof.relocation = d.relocation
    prof.available_from = d.available_from
    prof.blocked_company_ids = d.blocked_company_ids
    prof.field_visibility = d.field_visibility


def privacy_out(db: Session, user: User, prof: CandidateProfile, now: datetime, draft: _Draft | None = None) -> PrivacyOut:
    d = draft or _Draft.of(prof)
    cat = get_catalog()
    city, city_text = city_out(cat, d.city)
    return PrivacyOut(
        search_mode=d.search_mode,
        salary_min=d.salary_min,
        salary_type=d.salary_type,
        salary_visibility=d.salary_visibility,
        weekly_invite_limit=d.weekly_invite_limit,
        formats=d.formats,
        region=CodeName(code=d.region_code, name=region_name(d.region_code) or d.region_code) if d.region_code else None,
        city=city or (CodeName(code=city_text, name=city_text) if city_text else None),
        relocation=d.relocation,
        available_from=d.available_from,
        blocked_companies=_blocked_out(db, d.blocked_company_ids),
        field_visibility=_visibility_out(d.field_visibility),
        bank=bank_status(db, user, prof, d, now),
        options=_options(),
    )


def update_privacy(db: Session, user: User, body: PrivacyUpdateIn, meta: RequestMeta, now: datetime) -> CandidateProfile:
    prof = get_profile_row(db, user)
    draft = apply_privacy(db, _Draft.of(prof), body)
    _save_draft(prof, draft)
    prof.updated_at = now
    audit(db, "profile.privacy_updated", actor_id=user.id, actor_role=user.role, entity_type="candidate_profile", entity_id=user.id, ip=meta.ip, fields=sorted(body.model_fields_set))
    invalidate(db, user.id)
    db.commit()
    return prof


# --------------------------------------------------------------------------- квалификация и факты для превью


def qualification_snapshots(db: Session, user_id: uuid.UUID, now: datetime) -> list[Any]:
    """Снимки квалификаций кандидата (контракт модуля тестирования). Пусто, если модуль недоступен."""
    try:
        from app.services.testing.qualification import get_qualification_snapshot, list_rows
    except Exception:  # noqa: BLE001
        return []
    snaps = []
    specs = [r.specialization for r in list_rows(db, user_id)]
    for spec in specs:
        s = get_qualification_snapshot(db, user_id, spec, now=now)
        if s is not None:
            snaps.append(s)
    if {"backend_python", "frontend_js"} <= set(specs):
        fs = get_qualification_snapshot(db, user_id, "fullstack", now=now)
        if fs is not None:
            snaps.append(fs)
    return snaps


def main_qualification(snaps: list[Any]) -> Any | None:
    """Основная категория: подтверждённая, с самым высоким грейдом; Fullstack — только если других нет."""
    confirmed = [s for s in snaps if s.confirmed and s.grade]
    if not confirmed:
        return None

    def key(s: Any) -> tuple[bool, int, float]:
        g = _GRADE_ORDER.index(s.grade) if s.grade in _GRADE_ORDER else -1
        return (not s.is_fullstack, g, s.confirmed_at.timestamp() if s.confirmed_at else 0.0)

    return max(confirmed, key=key)


def bank_status(db: Session, user: User, prof: CandidateProfile, d: _Draft, now: datetime) -> BankStatusOut:
    reasons = []
    snaps = qualification_snapshots(db, user.id, now)
    main = main_qualification(snaps)
    if main is None:
        if any(s.status == "CONFIRMED" and not s.visible for s in snaps):
            reasons.append("Результат теста на проверке у методиста (до 7 дней)")
        else:
            reasons.append("Пройдите тест: без подтверждённой категории вас нет в банке")
    elif not main.visible:
        reasons.append("Результат теста на проверке у методиста (до 7 дней)")
    if not has_active_consent(db, user.id, ConsentPurpose.blind_card):
        reasons.append("Дайте согласие на показ слепой карточки — без него работодатели вас не видят")
    if d.search_mode == SearchMode.hidden.value:
        reasons.append("Режим «Не ищу»: вы скрыты из банка и подборок")
    hidden_below = int(load_config("contact")["minors"]["hidden_below_age"])
    if prof.birth_year is not None and now.year - prof.birth_year - 1 < hidden_below:
        reasons.append(f"До {hidden_below} лет профиль не виден работодателям")
    if not user.is_active:
        reasons.append("Подтвердите email")
    return BankStatusOut(visible=not reasons, reasons=reasons)


def _task_quality(score: float, kind: str) -> float:
    """Как в подборе: ≤ 1 — доля; тип B — сумма рубрики (критерии × баллы); тип A — проценты."""
    if score <= 1.0:
        return max(0.0, score)
    if kind == "B":
        try:
            rub = get_contact_config().tasks.rubric
            total = rub.max_points * len(rub.criteria)
        except Exception:  # noqa: BLE001 — рубрика по умолчанию: 4 критерия × 0–2
            total = 8
        return min(1.0, score / total) if total else 0.0
    return min(1.0, score / 100.0)


def recent_tasks(db: Session, user_id: uuid.UUID, now: datetime, limit: int = 5) -> list[dict[str, Any]]:
    try:
        from app.models.tasks import EmployerTask, TaskAssignment
    except Exception:  # noqa: BLE001
        return []
    rows = db.execute(
        select(TaskAssignment, EmployerTask.title)
        .join(EmployerTask, EmployerTask.id == TaskAssignment.employer_task_id)
        .where(TaskAssignment.candidate_id == user_id, TaskAssignment.finished_at.is_not(None))
        .order_by(TaskAssignment.finished_at.desc())
        .limit(limit)
    ).all()
    out = []
    for a, title in rows:
        kind = "A" if (a.kind or "catalog") == "catalog" else "B"
        result = None
        if a.score is not None:
            result = f"{round(_task_quality(float(a.score), kind) * 5)}/5"
        elif a.is_correct is not None:
            result = "верно" if a.is_correct else "неверно"
        out.append({"title": title, "kind": kind, "result": result, "when": relative_ru(a.finished_at, now), "finished_at": a.finished_at})
    return out


def fsp_items(db: Session, user_id: uuid.UUID, now: datetime) -> tuple[bool, list[dict[str, Any]], Any | None]:
    """(есть ли ФСП, обобщённые записи для карточки, FSPProfile) — только при привязке и согласии."""
    from app.fsp import vss
    from app.models.fsp import FspLink
    from app.services.fsp_link import stored_profile

    if not has_active_consent(db, user_id, ConsentPurpose.fsp_enrichment):
        return False, [], None
    link = db.scalar(select(FspLink).where(FspLink.user_id == user_id))
    if link is None:
        return False, [], None
    prof = stored_profile(link, now)
    if prof is None:
        return False, [], None
    items = []
    by_id = {r.result_id: r for r in prof.counted_results}
    ordered = sorted(
        prof.counted_results, key=lambda r: (-vss.result_points(r, now).points, -r.end_date.toordinal(), r.result_id)
    )
    for r in ordered:
        pub = vss.result_public_item(r)
        items.append(
            {
                "discipline": pub.discipline_label,
                "level": pub.level.value,
                "year": pub.year,
                "role": pub.role,
                "outcome": pub.outcome,
                "place_band": pub.place_band,
                "text": pub.text,
                # Только для раскрытой карточки (ключи из списка ПДн — в слепой вычищаются)
                "place": by_id[r.result_id].worst_place,
                "ekp": r.ekp,
                "team": r.team_name,
                "protocol_url": r.protocol_url,
            }
        )
    has = bool(vss.vss_by_discipline(prof, now))
    return has, items, prof


def candidate_facts(
    db: Session, user: User, prof: CandidateProfile, d: _Draft, now: datetime
) -> dict[str, Any]:
    """Факты о кандидате в ключах матрицы видимости config/contact.yaml (visibility.fields)."""
    from app.testing.irt import load_scale

    cat = get_catalog()
    contact = get_contact_config()
    snaps = qualification_snapshots(db, user.id, now)
    main = main_qualification(snaps)
    facts: dict[str, Any] = {"candidate_id": str(user.id)}
    if main is not None:
        scale = load_scale()
        se = main.se_t if main.se_t is not None else (main.se or 0.0)
        score = scale.score(main.theta) if main.theta is not None else None
        facts["category"] = {
            "spec": main.specialization,
            "grade": main.grade,
            "name": (main.category or {}).get("name"),
            "borderline": bool(main.borderline),
            "all": [s.category["name"] for s in snaps if s.confirmed and s.category],
        }
        facts["test_date"] = relative_ru(main.confirmed_at, now) if main.confirmed_at else None
        facts["test"] = (
            {
                "percentile": None,
                "interval": [int(round((score - scale.interval_z * scale.score_per_theta * se) / 10) * 10),
                             int(round((score + scale.interval_z * scale.score_per_theta * se) / 10) * 10)],
                "score": int(round(score)),
                "confidence": main.p_pass,
            }
            if score is not None
            else None
        )
        facts["skills"] = [
            {"module": m.module, "name": m.name, "state": m.tag, "symbol": m.symbol}
            for m in sorted(main.modules.values(), key=lambda m: (m.tag != "strong", m.name))
            if m.tag in ("strong", "confirmed")
        ]
        facts["freshness"] = {"relevance": main.relevance, "band": freshness_label(main.freshness_band)}
        try:
            from app.services.testing.qualification import grade_changes

            facts["grade_history"] = [
                {"from": grade_label(h.from_grade), "to": grade_label(h.to_grade), "when": relative_ru(h.at, now)}
                for h in grade_changes(db, user.id, main.specialization)
            ]
        except Exception:  # noqa: BLE001
            facts["grade_history"] = []
    else:
        facts["category"] = None
        facts["test"] = None
    facts["declared_skills"] = [
        cat.name(x["skill"]) for x in prof.declared_skills or () if isinstance(x, Mapping) and x.get("skill") in cat.skills
    ]
    facts["tasks"] = [{k: v for k, v in t.items() if k != "finished_at"} for t in recent_tasks(db, user.id, now)]
    inds = industry_names()
    facts["industries"] = [inds.get(c, c) for c in prof.industries or ()]
    facts["formats"] = [contact.invitation.formats.get(f, f) for f in d.formats]
    facts["search_mode"] = SEARCH_MODE_LABELS.get(d.search_mode, d.search_mode)
    has_fsp, items, _ = fsp_items(db, user.id, now)
    facts["has_fsp"] = has_fsp
    facts["fsp"] = items
    facts["salary_min"] = d.salary_min
    facts["salary_visibility"] = d.salary_visibility
    facts["region"] = region_name(d.region_code)
    city, city_text = city_out(cat, d.city)
    facts["city"] = city.name if city else city_text
    facts["full_name"] = prof.full_name
    facts["email"] = prof.contact_email or user.email
    facts["phone"] = prof.phone
    facts["telegram"] = prof.telegram
    facts["resume"] = "Текст резюме" if prof.resume_text else None
    facts["current_employer"] = prof.current_employer
    facts["university"] = prof.education
    return facts


def preview(db: Session, user: User, settings: Settings, body: PreviewIn, now: datetime) -> PreviewOut:
    """Живое превью «так вас видят» (К13): та же матрица видимости, что и в ответах работодателю."""
    prof = get_profile_row(db, user)
    d = _Draft.of(prof)
    if body.privacy is not None:
        d = apply_privacy(db, d, body.privacy)
    facts = candidate_facts(db, user, prof, d, now)
    pseudo = make_pseudonym(settings.pseudonym_secret.get_secret_value(), "preview", str(user.id))
    blind = blind_view(facts, pseudonym=pseudo, offer_salary_to=body.offer_salary_to)
    allowed = chosen_fields([k for k, v in d.field_visibility.items() if v])
    disclosure = DomainDisclosure(
        id="preview",
        candidate_id=str(user.id),
        company_id="preview",
        basis="invitation",
        basis_id="preview",
        fields=allowed,
        consent_text_ver=get_contact_config().disclosure.consent_text_version,
        granted_at=now,
    )
    disclosed = disclosed_view(
        facts, pseudonym=pseudo, company_id="preview", disclosure=disclosure, now=now, offer_salary_to=body.offer_salary_to
    )
    notes = [
        "Псевдоним свой для каждой компании: две компании не могут сопоставить ваши карточки.",
        "До взаимного «да» компания не видит ФИО, контакты, город, текущего работодателя и вуз.",
        "Полоса «топ-N%» считается по категории при показе работодателю; точный балл — после раскрытия.",
    ]
    if d.salary_visibility == SalaryVisibility.signal.value and d.salary_min is not None:
        notes.append("Минимум зарплаты виден только значком «в вилке ✓ / выше вилки».")
    bank = bank_status(db, user, prof, d, now)
    return PreviewOut(blind=blind, disclosed=disclosed, pii_in_blind=sorted(pii_keys_in(blind)), in_bank=bank, notes=notes)


# --------------------------------------------------------------------------- блок-лист: поиск компаний


def company_search(db: Session, user: User, q: str, limit: int = 10) -> list[CompanySearchOut]:
    prof = get_profile_row(db, user)
    blocked = set(prof.blocked_company_ids or ())
    text = q.strip()
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    rows = list(
        db.scalars(
            select(Company)
            .where(Company.name.is_not(None), Company.name.ilike(f"%{escaped}%", escape="\\"))
            .order_by(func.length(Company.name), Company.name, Company.id)
            .limit(limit)
        )
    )
    return [CompanySearchOut(id=c.id, name=c.name or "Компания без названия", field=c.field, blocked=c.id in blocked) for c in rows]


# --------------------------------------------------------------------------- журнал согласий


_REVOKE_EFFECT = {
    ConsentPurpose.processing.value: "Отзыв — только вместе с удалением аккаунта (через поддержку)",
    ConsentPurpose.blind_card.value: "Вы сразу пропадёте из банка кандидатов и подборок",
    ConsentPurpose.fsp_enrichment.value: "Данные ФСП удалятся из профиля, привязка ФСП ID останется",
    ConsentPurpose.contact_disclosure.value: "Компания снова не увидит ваши контакты",
    ConsentPurpose.test_logging.value: "Новые тесты нельзя начать без журналирования событий",
    ConsentPurpose.calibration.value: "Ваши ответы перестанут использоваться для калибровки заданий",
}


def consent_journal(db: Session, user: User, now: datetime) -> list[ConsentJournalOut]:
    rows = db.scalars(
        select(Consent).where(Consent.user_id == user.id).order_by(Consent.granted_at.desc(), Consent.id)
    )
    companies: dict[str, str] = {}
    out = []
    for c in rows:
        doc = CONSENT_DOCUMENTS.get(ConsentPurpose(c.purpose))
        scope = dict(c.scope or {})
        cid = scope.get("company_id")
        if cid and cid not in companies:
            try:
                comp = db.get(Company, uuid.UUID(str(cid)))
            except ValueError:
                comp = None
            companies[cid] = (comp.name if comp is not None and comp.name else "Компания")
        if cid:
            scope["company_name"] = companies[cid]
        out.append(
            ConsentJournalOut(
                id=c.id,
                purpose=c.purpose,
                title=doc.title if doc else c.purpose,
                text_version=c.text_version,
                granted_ago=relative_ru(c.granted_at, now),
                revoked_ago=relative_ru(c.revoked_at, now) if c.revoked_at else None,
                active=c.revoked_at is None,
                revocable=c.revoked_at is None and revocable_in_journal(c.purpose),
                effect_of_revoke=_REVOKE_EFFECT.get(c.purpose, "Согласие перестанет действовать"),
                scope=scope,
            )
        )
    return out


# --------------------------------------------------------------------------- К15: как меня видят


def _missing_skill(db: Session, user_id: uuid.UUID, version_ids: set[uuid.UUID]) -> MissingSkillOut | None:
    cat = get_catalog()
    counter: Counter[str] = Counter()
    if version_ids:
        for items in db.scalars(select(NeedVersion.items).where(NeedVersion.id.in_(version_ids))):
            for it in items or ():
                if not isinstance(it, Mapping) or str(it.get("candidate_id")) != str(user_id):
                    continue
                for chip in it.get("chips") or ():
                    if chip.get("kind") == "must" and chip.get("state") in ("declared", "no_data", "weak"):
                        counter[str(chip.get("skill_id"))] += 1
                break
    # Причины отказов компаний с навыками («не хватило навыков из брифа»)
    for skill_ids in db.scalars(
        select(Feedback.skill_ids).where(
            Feedback.direction == "company_to_candidate",
            Feedback.target_id.in_(select(Invitation.id).where(Invitation.candidate_id == user_id)),
        )
    ):
        for s in skill_ids or ():
            counter[str(s)] += 1
    counter.pop("None", None)
    if not counter:
        return None
    skill, n = max(counter.items(), key=lambda kv: (kv[1], kv[0]))
    name = cat.name(skill) if skill in cat.skills else skill
    modules = cat.modules_of(skill) if skill in cat.skills else ()
    if modules:
        mod = cat.module_names.get(modules[0], modules[0])
        action = f"Решите задачу по модулю «{mod}» (≈ 10–15 минут) — навык станет подтверждённым"
    else:
        action = "Тест этот навык не проверяет: покажите его в задаче «предложи подход» или на интервью"
    return MissingSkillOut(skill=skill, name=name, count=n, action=action)


def visibility_stats(db: Session, user: User, now: datetime) -> VisibilityStatsOut:
    ensure_role(user, UserRole.candidate.value)
    since = now - timedelta(days=VISIBILITY_WINDOW_DAYS)
    rows = db.execute(
        select(ImpressionLog.need_id, ImpressionLog.version_id, ImpressionLog.rank).where(
            ImpressionLog.candidate_id == user.id, ImpressionLog.shown_at >= since
        )
    ).all()
    best: dict[uuid.UUID, int] = {}
    versions: set[uuid.UUID] = set()
    for need_id, version_id, rank in rows:
        versions.add(version_id)
        best[need_id] = min(rank, best.get(need_id, rank))
    views = db.scalar(
        select(func.count(func.distinct(ProfileView.company_id))).where(
            ProfileView.candidate_id == user.id, ProfileView.at >= since
        )
    ) or 0
    invitations = db.scalar(
        select(func.count()).select_from(Invitation).where(Invitation.candidate_id == user.id, Invitation.created_at >= since)
    ) or 0
    cfg = get_contact_config()
    reasons: Counter[str] = Counter()
    for (reason,) in db.execute(
        select(Application.reject_reason).where(Application.candidate_id == user.id, Application.reject_reason.is_not(None))
    ):
        reasons[cfg.reasons.application_reject.get(reason, reason)] += 1
    for (outcome_reason,) in db.execute(
        select(Invitation.outcome_reason).where(Invitation.candidate_id == user.id, Invitation.outcome == "not_matched")
    ):
        code = (outcome_reason or {}).get("reason") if isinstance(outcome_reason, Mapping) else None
        if code:
            reasons[cfg.reasons.not_matched.get(code, code)] += 1
    impressions = len(rows)
    enough = impressions >= MIN_IMPRESSIONS
    positions = sorted(best.values())
    top10 = sum(1 for p in positions if p <= 10)
    if not enough:
        message = "Данных пока мало: меньше 5 показов за неделю. Загляните позже"
    else:
        message = f"За {VISIBILITY_WINDOW_DAYS} дней вы были в {len(best)} подборках, в {top10} — в топ-10"
    return VisibilityStatsOut(
        period_days=VISIBILITY_WINDOW_DAYS,
        enough_data=enough,
        message=message,
        impressions=impressions,
        selections=len(best),
        top10=top10,
        median_position=float(statistics.median(positions)) if enough and positions else None,
        profile_views=int(views),
        invitations=int(invitations),
        missing_skill=_missing_skill(db, user.id, versions),
        decline_reasons=[ReasonCount(reason=r, count=n) for r, n in reasons.most_common(5)],
    )
