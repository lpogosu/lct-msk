"""Загрузка резюме и подтверждение распознанных полей (экран К7, концепция §3.1.1).

1. Загрузка PDF (≤ 5 МБ) → разбор правилами (+ необязательная LLM) → черновик в resume_fields.draft
   вместе с извлечённым текстом: фронтенд подсвечивает цитаты по смещениям text[start:end].
2. Кандидат подтверждает или правит каждое поле → значения ложатся в профиль: совпавшее с распознанным —
   с источником «резюме» и цитатой, исправленное — «вручную». Всё это «заявлено» (DECISIONS п. 1).
3. Текст резюме сохраняется (если кандидат не против) и раскрывается компании только после «да» и только
   если разрешено поле «Резюме».
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.auth_service import RequestMeta
from app.core.clock import relative_ru
from app.core.errors import ApiError, not_found
from app.llm.gateway import LLMGateway
from app.matching.skills import get_catalog
from app.models.users import User
from app.services.profile import service as profile_service
from app.services.resume.extract import ResumeError
from app.services.resume.parser import parse_resume
from app.services.resume.schemas import ResumeConfirmIn, ResumeDraftOut, ResumeParse
from app.services.resume.settings import ResumeSettings

NEXT_STEP = (
    "Проверьте поля: наведите на значение, чтобы увидеть цитату из резюме, исправьте ошибки и подтвердите "
    "(POST /api/candidates/me/resume/confirm)"
)
_ERROR_STATUS = {"resume_not_pdf": 415, "resume_too_large": 413}


def resume_error(e: ResumeError) -> ApiError:
    return ApiError(_ERROR_STATUS.get(e.code, 422), e.code, e.message)


def _draft_out(draft: Mapping[str, Any], now: datetime) -> ResumeDraftOut:
    uploaded = datetime.fromisoformat(str(draft["uploaded_at"]))
    return ResumeDraftOut(
        parse=ResumeParse.model_validate(draft["parse"]),
        text=str(draft.get("text") or ""),
        sha256=str(draft.get("sha256") or ""),
        uploaded_ago=relative_ru(uploaded, now),
        next_step=NEXT_STEP,
        note=profile_service.DECLARED_NOTE,
    )


def upload(
    db: Session,
    user: User,
    data: bytes,
    *,
    now: datetime,
    meta: RequestMeta,
    gateway: LLMGateway | None = None,
    settings: ResumeSettings | None = None,
) -> ResumeDraftOut:
    s = settings or ResumeSettings()
    if len(data) > s.max_bytes:
        raise ApiError(413, "resume_too_large", f"Файл больше {s.max_bytes // (1024 * 1024)} МБ. Сожмите PDF или загрузите версию без картинок")
    if not data:
        raise ApiError(422, "resume_empty", "Файл пустой. Выберите PDF с резюме")
    prof = profile_service.get_profile_row(db, user)
    try:
        ext, parse = parse_resume(data, now=now, settings=s, gateway=gateway)
    except ResumeError as e:
        raise resume_error(e) from e
    draft = {
        "parse": parse.model_dump(mode="json"),
        "text": ext.text,
        "sha256": hashlib.sha256(data).hexdigest(),
        "uploaded_at": now.isoformat(),
    }
    fields = dict(prof.resume_fields or {})
    fields["status"] = "draft"
    fields["draft"] = draft
    prof.resume_fields = fields
    audit(
        db,
        "resume.uploaded",
        actor_id=user.id,
        actor_role=user.role,
        entity_type="candidate_profile",
        entity_id=user.id,
        ip=meta.ip,
        pages=parse.pages,
        llm=parse.llm.used,
    )
    db.commit()
    return _draft_out(draft, now)


def current_draft(db: Session, user: User, now: datetime) -> ResumeDraftOut:
    prof = profile_service.get_profile_row(db, user)
    draft = (prof.resume_fields or {}).get("draft")
    if not draft:
        raise not_found("resume_draft_not_found", "Нет резюме, ожидающего подтверждения. Загрузите PDF")
    return _draft_out(draft, now)


def _same(a: Any, b: Any) -> bool:
    return str(a or "").strip().lower() == str(b or "").strip().lower()


def confirm(db: Session, user: User, body: ResumeConfirmIn, *, now: datetime, meta: RequestMeta) -> None:
    prof = profile_service.get_profile_row(db, user)
    fields_store = dict(prof.resume_fields or {})
    draft = fields_store.get("draft")
    if not draft:
        raise not_found("resume_draft_not_found", "Нет резюме, ожидающего подтверждения. Загрузите PDF")
    parse = ResumeParse.model_validate(draft["parse"])
    cat = get_catalog()
    given = body.model_fields_set
    sources: dict[str, Any] = {}

    def mark(name: str, value: Any, parsed: Any) -> None:
        if parsed is not None and _same(value, parsed.value if hasattr(parsed, "value") else parsed):
            sources[name] = {"source": "resume", "quote": parsed.quote.model_dump() if getattr(parsed, "quote", None) else None}
        else:
            sources[name] = {"source": "manual", "quote": None}

    if "full_name" in given:
        prof.full_name = (body.full_name or "").strip() or None
        mark("full_name", body.full_name, parse.full_name)
    if "email" in given:
        prof.contact_email = str(body.email).lower() if body.email else None
        mark("email", body.email, parse.email)
    if "phone" in given:
        prof.phone = profile_service.normalize_phone(body.phone) if body.phone else None
        mark("phone", prof.phone, parse.phone)
    if "telegram" in given:
        prof.telegram = profile_service.normalize_telegram(body.telegram) if body.telegram else None
        mark("telegram", prof.telegram, parse.telegram)
    if "city" in given:
        code, text = profile_service.resolve_city(cat, body.city)
        prof.city = code or text
        region = profile_service.region_of_city(code)
        if region:
            prof.region_code = region
        mark("city", code or text, parse.city)
    if "education" in given:
        prof.education = (body.education or "").strip() or None
        mark("education", body.education, parse.education)
    if "declared_grade" in given:
        prof.declared_grade = body.declared_grade
        mark("declared_grade", body.declared_grade, parse.declared_grade)
    if "experience_months" in given:
        prof.experience_months = body.experience_months
        mark("experience_months", body.experience_months, parse.experience)
    if body.use_experience_periods and parse.experience.periods:
        prof.experience = [
            {"start": p.start, "end": p.end, "title": p.title, "company": p.company} for p in parse.experience.periods
        ]
        if "experience_months" not in given:
            prof.experience_months = parse.experience.value
            sources["experience_months"] = {"source": "resume", "quote": None}
    quotes = {
        "skill": {i.id: i for i in parse.skills},
        "role": {i.id: i for i in parse.roles},
        "id": {i.id: i for i in parse.soft_skills},
    }

    def declared(ids: list[str], key: str, *, with_name: bool = False) -> list[dict[str, Any]]:
        out = []
        for i in ids:
            item = quotes[key].get(i)
            entry: dict[str, Any] = {
                key: i,
                "source": "resume" if item is not None else "manual",
                "quote": item.quote.text if item is not None else None,
            }
            if with_name:
                entry["name"] = cat.soft_skills.get(i, i)
            out.append(entry)
        return out

    if "skills" in given:
        ids, unknown = profile_service.normalize_skills(cat, body.skills or ())
        if unknown:
            raise profile_service.unknown_error("skills", unknown, "Навыков")
        prof.declared_skills = declared(ids, "skill")
    if "roles" in given:
        ids = list(dict.fromkeys(body.roles or ()))
        bad = [r for r in ids if r not in cat.roles]
        if bad:
            raise profile_service.unknown_error("roles", bad, "Ролей")
        prof.declared_roles = declared(ids, "role")
    if "soft_skills" in given:
        ids = list(dict.fromkeys(body.soft_skills or ()))
        bad = [x for x in ids if x not in cat.soft_skills]
        if bad:
            raise profile_service.unknown_error("soft_skills", bad, "Софт-скиллов")
        prof.soft_skills = declared(ids, "id", with_name=True)

    prof.resume_text = str(draft.get("text") or "") if body.keep_resume_text else None
    prof.resume_file_sha256 = draft.get("sha256")
    prof.resume_uploaded_at = datetime.fromisoformat(str(draft["uploaded_at"]))
    fields_store.pop("draft", None)
    fields_store.update(
        {
            "status": "confirmed",
            "confirmed_at": now.isoformat(),
            "parser_version": parse.parser_version,
            "pages": parse.pages,
            "fields": sources,
        }
    )
    prof.resume_fields = fields_store
    prof.updated_at = now
    audit(
        db,
        "resume.confirmed",
        actor_id=user.id,
        actor_role=user.role,
        entity_type="candidate_profile",
        entity_id=user.id,
        ip=meta.ip,
        fields=sorted(sources),
    )
    profile_service.invalidate(db, user.id)
    db.commit()


def delete(db: Session, user: User, *, meta: RequestMeta) -> None:
    """Удалить резюме: текст, черновик и цитаты. Подтверждённые поля профиля остаются (их можно править)."""
    prof = profile_service.get_profile_row(db, user)
    had = bool(prof.resume_text or prof.resume_fields)
    prof.resume_text = None
    prof.resume_file_sha256 = None
    prof.resume_uploaded_at = None
    prof.resume_fields = {}
    for col in ("declared_skills", "declared_roles", "soft_skills"):
        items = []
        for x in getattr(prof, col) or ():
            if isinstance(x, Mapping):
                x = dict(x)
                x["quote"] = None
            items.append(x)
        setattr(prof, col, items)
    if had:
        audit(db, "resume.deleted", actor_id=user.id, actor_role=user.role, entity_type="candidate_profile", entity_id=user.id, ip=meta.ip)
    db.commit()


__all__ = ["confirm", "current_draft", "delete", "resume_error", "upload"]
