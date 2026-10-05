"""Выдача стандартизированного PDF-профиля и страница сверки /v/{код} (концепция §3.1.2, §4.5).

Контракт для других модулей (приглашения, отклики):

    employer_profile_pdf(db, company_id=…, candidate_id=…, settings=…, now=…) -> IssuedPdf

отдаёт работодателю ПОЛНУЮ версию только при действующем согласии кандидата на раскрытие этой компании
(ContactDisclosure) и только с теми полями, что кандидат разрешил (ФИО, контакты, текущий работодатель,
вуз). Без согласия — 404 (запись неотличима от несуществующей).

Каждая выдача записывает ProfileSnapshot: код сверки → что было в документе (без ПДн). Страница сверки
показывает категорию, дату подтверждения тестом, номер сессии и дату данных реестра ФСП — и предупреждает,
если категория с тех пор изменилась.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.contact.pseudonym import pseudonym as make_pseudonym
from app.contact.settings import get_contact_config
from app.core.errors import not_found
from app.core.settings import Settings
from app.fsp import vss
from app.matching.skills import get_catalog
from app.models.candidate import CandidateProfile, ProfileSnapshot
from app.models.contact import ContactDisclosure
from app.models.fsp import FspLink
from app.models.users import User
from app.services.fsp_link.service import source_label
from app.services.pdf.render import render_pdf
from app.services.pdf.sheet import (
    FspBlock,
    ModuleTag,
    ProfileSheet,
    RulerCut,
    RulerData,
    TaskLine,
    Variant,
    sheet_hash,
    verify_url,
)
from app.services.profile.regions import region_name
from app.services.profile.service import (
    city_out,
    experience_label,
    freshness_label,
    fsp_items,
    grade_label,
    main_qualification,
    qualification_snapshots,
    recent_tasks,
)

CODE_RE = re.compile(r"^[0-9a-f]{32}$")
ALL_FIELDS = frozenset({"full_name", "email", "phone", "telegram", "resume", "current_employer", "university", "photo_url"})


@dataclass(frozen=True)
class IssuedPdf:
    code: str
    url: str
    variant: Variant
    pdf: bytes
    sheet: ProfileSheet

    @property
    def filename(self) -> str:
        return f"vzaimno-profile-{self.variant}-{self.code[:8]}.pdf"


def _msk_date(now: datetime) -> date:
    offset = timedelta(hours=get_contact_config().timezone_offset_hours)
    return now.astimezone(timezone(offset)).date()


def _fmt(d: date | datetime | None) -> str | None:
    if d is None:
        return None
    if isinstance(d, datetime):
        d = _msk_date(d)
    return d.strftime("%d.%m.%Y")


def _session_ref(db: Session, user_id: uuid.UUID, spec: str) -> str | None:
    """Номер сессии теста, на которой держится грейд: «#7F3A2C91» (первые 8 символов id)."""
    try:
        from app.services.testing.qualification import get_row
    except Exception:  # noqa: BLE001
        return None
    specs = ("backend_python", "frontend_js") if spec == "fullstack" else (spec,)
    for s in specs:
        row = get_row(db, user_id, s)
        if row is not None and row.source_session_id is not None:
            return "#" + row.source_session_id.hex[:8].upper()
    return None


def _initials(full_name: str | None) -> str | None:
    if not full_name:
        return None
    parts = [p for p in re.split(r"\s+", full_name.strip()) if p]
    return " ".join(f"{p[0].upper()}." for p in parts[:3]) or None


def _extract_without_header(text: str) -> str | None:
    """Выписка без шапки «источник, данные получены N назад»: в документе дата данных — абсолютная."""
    blocks = [b for b in (text or "").split("\n\n") if b.strip()]
    body = [b for b in blocks if not b.startswith("ВЫПИСКА ИЗ ПРОТОКОЛА")]
    return "\n\n".join(body) or None


def _experience_lines(prof: CandidateProfile, allow_company: bool) -> tuple[str, ...]:
    out = []
    for p in prof.experience or ():
        if not isinstance(p, Mapping) or not p.get("start"):
            continue
        y, m = str(p["start"]).split("-")
        start = f"{m}.{y}"
        if p.get("end"):
            ey, em = str(p["end"]).split("-")
            end = f"{em}.{ey}"
        else:
            end = "н. в."
        parts = [f"{start} — {end}", p.get("title")]
        if allow_company:
            parts.append(p.get("company"))
        out.append(" · ".join(str(x) for x in parts if x))
    return tuple(out)


def build_sheet(
    db: Session,
    user: User,
    variant: Variant,
    now: datetime,
    settings: Settings,
    *,
    allowed_fields: frozenset[str] = ALL_FIELDS,
) -> ProfileSheet:
    """Содержимое листа. allowed_fields — что кандидат разрешил раскрыть (для работодателя), себе — всё."""
    from app.testing.irt import load_scale

    cat = get_catalog()
    prof = db.get(CandidateProfile, user.id) or CandidateProfile(user_id=user.id)
    full = variant == "full"
    snaps = qualification_snapshots(db, user.id, now)
    main = main_qualification(snaps)
    scale = load_scale()
    cuts = tuple(RulerCut(g, scale.grade_name(g), int(round(scale.score(c)))) for g, c in scale.cuts)

    ruler = None
    category_name = category_note = None
    borderline = False
    modules: tuple[ModuleTag, ...] = ()
    session_ref = None
    freshness = None
    claims_cat: dict[str, Any] | None = None
    interval: tuple[int, int] | None = None
    score: int | None = None
    if main is not None:
        category_name = (main.category or {}).get("name")
        borderline = bool(main.borderline)
        bits = []
        if main.confirmed_at:
            bits.append(f"подтверждено тестом {_fmt(main.confirmed_at)}")
        if main.p_pass is not None:
            bits.append(f"уверенность {round(main.p_pass * 100)}%")
        category_note = " · ".join(bits) or None
        if main.theta is not None:
            se = main.se_t if main.se_t is not None else (main.se or 0.0)
            s = scale.score(main.theta)
            half = scale.interval_z * scale.score_per_theta * se
            interval = (int(round((s - half) / 10) * 10), int(round((s + half) / 10) * 10))
            score = int(round(s)) if full else None
        ruler = RulerData(lo=cuts[0].score, hi=cuts[-1].score, cuts=cuts, interval=interval, score=score)
        modules = tuple(
            ModuleTag(m.name, m.symbol or "~", m.tag if m.tag in ("strong", "confirmed") else "insufficient")
            for m in sorted(main.modules.values(), key=lambda m: (m.tag not in ("strong",), m.tag not in ("confirmed",), m.name))
            if m.tag in ("strong", "confirmed", "insufficient")
        )
        session_ref = _session_ref(db, user.id, main.specialization)
        if main.relevance is not None:
            freshness = f"Актуальность профиля: {main.relevance} из 100 · {freshness_label(main.freshness_band)}"
        claims_cat = {
            "code": (main.category or {}).get("code"),
            "name": category_name,
            "borderline": borderline,
        }

    # --- ФСП
    has_fsp, items, fsp_prof = fsp_items(db, user.id, now)
    link = db.scalar(select(FspLink).where(FspLink.user_id == user.id))
    registry_on = _fmt(link.registry_as_of) if link is not None and link.registry_as_of else None
    if fsp_prof is not None:
        source = source_label(fsp_prof)
        badges = tuple(vss.badges(fsp_prof, now))
        if full:
            extract = _extract_without_header(vss.protocol_extract(fsp_prof, now))
            lines: tuple[str, ...] = () if extract else (vss.employer_line(fsp_prof, now),)
        else:
            extract = None
            lines = tuple(i["text"] for i in items[:4]) or (vss.employer_line(fsp_prof, now),)
        fsp = FspBlock(True, lines, badges, extract, source, registry_on, None)
    else:
        fsp = FspBlock(False, neutral=vss.NOT_LINKED_LINE)

    # --- кто и заявленное
    pseudo = make_pseudonym(settings.pseudonym_secret.get_secret_value(), "pdf", str(user.id))
    if full:
        if not prof.full_name:
            title = "Кандидат (ФИО не указано)"
        elif "full_name" in allowed_fields:
            title = prof.full_name
        else:
            title = "Кандидат (ФИО кандидат не раскрыл)"
        contacts = []
        if "email" in allowed_fields:
            contacts.append(prof.contact_email or user.email)
        if prof.phone and "phone" in allowed_fields:
            contacts.append(prof.phone)
        if prof.telegram and "telegram" in allowed_fields:
            contacts.append(prof.telegram)
        city, city_text = city_out(cat, prof.city)
        location = city.name if city else city_text
        if location and prof.region_code and region_name(prof.region_code) not in (location, None):
            location = f"{location}, {region_name(prof.region_code)}"
        education = prof.education if "university" in allowed_fields else None
        # Периоды работы — из резюме: только если кандидат разрешил раскрыть «Резюме» (компании — ещё и
        # «Текущий работодатель»); стаж словами остаётся в любом случае
        experience = _experience_lines(prof, "current_employer" in allowed_fields) if "resume" in allowed_fields else ()
        holder = {"initials": _initials(prof.full_name) if "full_name" in allowed_fields else None}
    else:
        title = f"Кандидат {pseudo}"
        contacts = []
        location = region_name(prof.region_code)
        education = None
        experience = ()
        holder = {"pseudonym": pseudo}
    roles = tuple(
        cat.roles[x["role"]].name for x in prof.declared_roles or () if isinstance(x, Mapping) and x.get("role") in cat.roles
    )
    grade = grade_label(prof.declared_grade)
    subtitle_bits = [roles[0] if roles else None, f"заявлен грейд {grade}" if grade else None]
    subtitle = " · ".join(b for b in subtitle_bits if b) or None
    declared = tuple(
        cat.name(x["skill"]) for x in prof.declared_skills or () if isinstance(x, Mapping) and x.get("skill") in cat.skills
    )
    soft = tuple(
        cat.soft_skills[x["id"]] for x in prof.soft_skills or () if isinstance(x, Mapping) and x.get("id") in cat.soft_skills
    )
    tasks = tuple(
        TaskLine(t["title"], t["result"], _fmt(t["finished_at"]) or "") for t in recent_tasks(db, user.id, now)
    )
    issued_on = _msk_date(now)
    claims: dict[str, Any] = {
        "variant": variant,
        "issued_on": issued_on.isoformat(),
        "holder": holder,
        "category": claims_cat,
        "confirmed_on": (_msk_date(main.confirmed_at).isoformat() if main is not None and main.confirmed_at else None),
        "confidence": round(main.p_pass, 2) if main is not None and main.p_pass is not None else None,
        "session_ref": session_ref,
        "interval": list(interval) if interval else None,
        "score": score,
        "modules": [{"name": m.name, "symbol": m.symbol} for m in modules if m.state != "insufficient"],
        "fsp": {
            "linked": fsp.linked,
            "source": fsp.source_label,
            "registry_on": (
                _msk_date(link.registry_as_of).isoformat() if link is not None and link.registry_as_of and fsp.linked else None
            ),
            "results": len(items),
        },
        "freshness": {"relevance": main.relevance, "band": freshness_label(main.freshness_band)} if main is not None else None,
    }
    return ProfileSheet(
        variant=variant,
        issued_on=issued_on,
        title=title,
        subtitle=subtitle,
        contacts=tuple(contacts),
        location=location,
        category_name=category_name,
        category_note=category_note,
        borderline=borderline,
        ruler=ruler,
        modules=modules,
        declared_skills=declared,
        roles=roles,
        soft_skills=soft,
        experience_label=experience_label(prof.experience_months),
        experience=experience,
        education=education,
        fsp=fsp,
        tasks=tasks,
        freshness=freshness,
        session_ref=session_ref,
        claims=claims,
    )


def issue(
    db: Session,
    user: User,
    variant: Variant,
    now: datetime,
    settings: Settings,
    *,
    allowed_fields: frozenset[str] = ALL_FIELDS,
    render: bool = True,
) -> IssuedPdf:
    """Собрать лист, записать снимок для сверки (идемпотентно) и отрисовать PDF."""
    sheet = build_sheet(db, user, variant, now, settings, allowed_fields=allowed_fields)
    code = sheet_hash(sheet, settings.app_secret.get_secret_value(), subject=str(user.id))
    url = verify_url(settings.public_base_url, code)
    stmt = pg_insert(ProfileSnapshot).values(
        hash=code, user_id=user.id, variant=variant, issued_on=sheet.issued_on, claims=sheet.claims
    )
    db.execute(stmt.on_conflict_do_nothing(index_elements=[ProfileSnapshot.hash]))
    db.commit()
    pdf = render_pdf(sheet, url, code) if render else b""
    return IssuedPdf(code=code, url=url, variant=variant, pdf=pdf, sheet=sheet)


def employer_profile_pdf(
    db: Session, *, company_id: uuid.UUID, candidate_id: uuid.UUID, settings: Settings, now: datetime
) -> IssuedPdf:
    """Полная версия для работодателя — только при действующем ContactDisclosure этой пары."""
    row = db.scalar(
        select(ContactDisclosure)
        .where(
            ContactDisclosure.candidate_id == candidate_id,
            ContactDisclosure.company_id == company_id,
            ContactDisclosure.revoked_at.is_(None),
        )
        .order_by(ContactDisclosure.granted_at.desc())
        .limit(1)
    )
    user = db.get(User, candidate_id)
    if row is None or user is None:
        raise not_found("profile_not_disclosed", "Профиль недоступен: кандидат не раскрывал контакты вашей компании")
    allowed = frozenset(row.fields or ()) & ALL_FIELDS
    return issue(db, user, "full", now, settings, allowed_fields=allowed)


def verify(db: Session, code: str, now: datetime) -> dict[str, Any]:
    """Публичная сверка: что было в документе (без ПДн) и актуально ли это сейчас."""
    code = code.strip().lower()
    snap = db.get(ProfileSnapshot, code) if CODE_RE.match(code) else None
    if snap is None:
        return {
            "valid": False,
            "code": code[:64],
            "message": "Документ с таким кодом платформа не выдавала: профиль изменён или подделан",
        }
    c = dict(snap.claims or {})
    cat = c.get("category") or {}
    fsp = c.get("fsp") or {}
    parts = []
    if cat.get("name"):
        confirmed = c.get("confirmed_on")
        phrase = f"Категория {cat['name']} подтверждена тестом"
        if confirmed:
            phrase += f" {date.fromisoformat(confirmed).strftime('%d.%m.%Y')}"
        if c.get("session_ref"):
            phrase += f", сессия {c['session_ref']}"
        parts.append(phrase)
    else:
        parts.append("Категория в документе не указана")
    if fsp.get("linked") and fsp.get("registry_on"):
        parts.append(f"данные ФСП из реестра на {date.fromisoformat(fsp['registry_on']).strftime('%d.%m.%Y')}")
    elif not fsp.get("linked"):
        parts.append("достижения ФСП не привязаны")
    # Актуальность: та же ли категория у кандидата сейчас
    user = db.get(User, snap.user_id)
    current_note = "Данные документа совпадают с текущим профилем"
    actual = True
    if user is None:
        actual, current_note = False, "Профиль кандидата удалён"
    else:
        main = main_qualification(qualification_snapshots(db, user.id, now))
        now_code = (main.category or {}).get("code") if main is not None else None
        if (cat.get("code") or None) != now_code:
            actual = False
            current_note = "С момента выдачи категория кандидата изменилась — запросите у него свежий профиль"
    return {
        "valid": True,
        "code": code,
        "variant": snap.variant,
        "variant_label": "Слепая версия" if snap.variant == "blind" else "Полная версия",
        "issued_on": snap.issued_on,
        "holder": c.get("holder") or {},
        "category": cat or None,
        "confirmed_on": c.get("confirmed_on"),
        "confidence": c.get("confidence"),
        "session_ref": c.get("session_ref"),
        "interval": c.get("interval"),
        "score": c.get("score"),
        "modules": c.get("modules") or [],
        "fsp": fsp,
        "freshness": c.get("freshness"),
        "actual": actual,
        "actual_note": current_note,
        "message": ", ".join(parts) + ".",
    }
