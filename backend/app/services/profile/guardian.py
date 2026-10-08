"""Кандидаты 15–17 лет: возрастные правила и согласие законного представителя (docs/SECURITY.md п. 1.4).

Пороги — config/contact.yaml -> minors. Год рождения даёт «минимально возможный возраст» (год − рождение − 1): при сомнении
считаем моложе (защита несовершеннолетних важнее). Младше `registration_min_age` — отказ; 15–17 — профиль скрыт от
работодателей, пока представитель не подтвердит согласие по ссылке из письма (одноразовый токен, в БД только хеш).
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.settings import get_contact_config
from app.core.audit import audit
from app.core.errors import ApiError, bad_request, conflict, not_found
from app.core.settings import Settings
from app.mail.sender import Mailer, MailError, OutgoingMail
from app.mail.templates import guardian_confirm_link, render_guardian_consent
from app.models.candidate import CandidateProfile
from app.models.users import User


def min_age(birth_year: int, now: datetime) -> int:
    return now.year - birth_year - 1


def too_young(birth_year: int | None, now: datetime) -> bool:
    cfg = get_contact_config().minors
    return birth_year is not None and min_age(birth_year, now) < cfg.registration_min_age


def too_young_error() -> ApiError:
    cfg = get_contact_config().minors
    return ApiError(
        422,
        "validation_error",
        "Проверьте заполнение полей",
        [
            {
                "field": "birth_year",
                "message": f"Платформа доступна с {cfg.registration_min_age} лет. Вернитесь, когда вам исполнится "
                f"{cfg.registration_min_age} — до этого мы не регистрируем и не храним профиль",
                "type": "age_too_young",
            }
        ],
    )


def is_minor(birth_year: int | None, now: datetime) -> bool:
    cfg = get_contact_config().minors
    return birth_year is not None and min_age(birth_year, now) < cfg.guardian_consent_below_age


def sync_flags(prof: CandidateProfile, now: datetime) -> None:
    """is_minor следует за годом рождения; у взрослого данные представителя не нужны — стираем."""
    prof.is_minor = is_minor(prof.birth_year, now)
    if not prof.is_minor and (prof.guardian_email or prof.guardian_name or prof.guardian_consent):
        prof.guardian_name = prof.guardian_email = prof.guardian_token_hash = None
        prof.guardian_requested_at = prof.guardian_confirmed_at = None
        prof.guardian_consent = False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def status(prof: CandidateProfile, now: datetime) -> dict[str, Any]:
    """Состояние для кабинета кандидата. Для взрослого — required=False."""
    cfg = get_contact_config().minors
    required = is_minor(prof.birth_year, now)
    if not required:
        return {"required": False, "state": "not_required", "state_label": "", "guardian_name": None, "guardian_email_masked": None}
    if prof.guardian_consent:
        state, label = "confirmed", "Согласие представителя подтверждено"
    elif prof.guardian_requested_at is not None:
        ttl = timedelta(hours=cfg.guardian.token_ttl_hours)
        expired = prof.guardian_requested_at + ttl < now
        state = "expired" if expired else "pending"
        label = "Ссылка из письма устарела — отправьте письмо ещё раз" if expired else "Ждём подтверждения от представителя"
    else:
        state, label = "missing", "Нужно согласие законного представителя: профиль пока не виден работодателям"
    return {
        "required": True,
        "state": state,
        "state_label": label,
        "guardian_name": prof.guardian_name,
        "guardian_email_masked": _mask(prof.guardian_email),
        "employer_rule": cfg.employer_notice,
    }


def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def request_consent(
    db: Session,
    settings: Settings,
    mailer: Mailer,
    user: User,
    prof: CandidateProfile,
    *,
    name: str,
    email: str,
    now: datetime,
    ip: str | None = None,
) -> dict[str, Any]:
    cfg = get_contact_config().minors
    if not is_minor(prof.birth_year, now):
        raise conflict("guardian_not_required", "Согласие представителя нужно только кандидатам 15–17 лет")
    name = " ".join((name or "").split())
    if not cfg.guardian.name_min_len <= len(name) <= cfg.guardian.name_max_len:
        raise bad_request("guardian_name", "Укажите ФИО представителя полностью")
    email = (email or "").strip().lower()
    if email == user.email.lower() or email == (prof.contact_email or "").lower():
        raise bad_request("guardian_email_same", "Email представителя должен отличаться от вашего")
    if prof.guardian_consent and prof.guardian_email == email:
        raise conflict("guardian_confirmed", "Согласие этого представителя уже подтверждено")
    if prof.guardian_requested_at is not None and not prof.guardian_consent:
        wait = timedelta(minutes=cfg.guardian.resend_cooldown_minutes)
        if prof.guardian_requested_at + wait > now and prof.guardian_email == email:
            left = int((prof.guardian_requested_at + wait - now).total_seconds() // 60) + 1
            raise ApiError(429, "guardian_resend_wait", f"Письмо уже отправлено. Повторить можно через {left} мин.")
    token = secrets.token_urlsafe(32)
    prof.guardian_name, prof.guardian_email = name, email
    prof.guardian_token_hash = _hash(token)
    prof.guardian_requested_at = now
    prof.guardian_confirmed_at = None
    prof.guardian_consent = False  # другой представитель или повторная отправка: прежнее согласие не переносится
    link = guardian_confirm_link(settings.public_base_url, token)
    hint = f"с адресом {_mask(user.email)}"
    rendered = render_guardian_consent(name, hint, link, cfg.guardian.token_ttl_hours)
    audit(db, "guardian.requested", actor_id=user.id, actor_role=user.role, entity_type="user", entity_id=user.id, ip=ip)
    db.commit()
    try:
        mailer.send(OutgoingMail.from_rendered(email, rendered, purpose="guardian_consent"))
    except MailError as e:
        raise ApiError(502, "mail_failed", "Не удалось отправить письмо представителю. Попробуйте позже") from e
    return status(prof, now)


def confirm(db: Session, token: str, now: datetime, ip: str | None = None) -> dict[str, Any]:
    """Публичное подтверждение по ссылке из письма. Токен одноразовый: после подтверждения хеш стирается."""
    cfg = get_contact_config().minors
    prof = db.scalar(select(CandidateProfile).where(CandidateProfile.guardian_token_hash == _hash(token or "")))
    if prof is None:
        raise not_found("guardian_token_invalid", "Ссылка недействительна или уже использована")
    ttl = timedelta(hours=cfg.guardian.token_ttl_hours)
    if prof.guardian_requested_at is None or prof.guardian_requested_at + ttl < now:
        raise ApiError(410, "guardian_token_expired", "Срок ссылки истёк. Попросите кандидата отправить письмо ещё раз")
    prof.guardian_consent = True
    prof.guardian_confirmed_at = now
    prof.guardian_token_hash = None
    audit(db, "guardian.confirmed", actor_id=None, actor_role=None, entity_type="user", entity_id=prof.user_id, ip=ip)
    db.commit()
    return {"confirmed": True, "message": "Спасибо! Согласие записано. Профиль станет виден работодателям для стажировок и лёгкого труда."}
