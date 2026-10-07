"""Удаление аккаунта (право субъекта на удаление ПДн, 152-ФЗ ст. 14, 21; хардненинг п. 1).

DELETE /api/candidates/me и DELETE /api/companies/me (работодатель) — с подтверждением паролем. Что происходит:

1. Проверка: пароль (argon2) или, для аккаунтов без пароля (вход через ФСП ID без заданного пароля), фраза «УДАЛИТЬ»;
   попытки считаются лимитом `erasure.rate_limit` на аккаунт (config/privacy.yaml): удаление нельзя использовать для
   подбора пароля.
2. Удаление строки users. Все таблицы, ссылающиеся на пользователя, объявлены с ON DELETE CASCADE / SET NULL, поэтому БД
   сама убирает всё персональное: профиль и резюме, ответы опроса, признаки подбора, сессии теста и ответы, согласия,
   привязку ФСП ID и сохранённые данные реестра, приглашения, отклики, раскрытия контактов, уведомления, коды и refresh-токены
   (все сессии на всех устройствах перестают работать). Ничего «мягко» не помечается и не копится в архиве.
3. Что в БД не связано внешним ключом, чистим явно: тексты обратной связи (Feedback) по этим приглашениям и откликам,
   контакты кандидата в копиях вебхуков ATS (журнал доставок компании), IP/идентификаторы в записях журнала аудита.
4. Остаётся только то, что не относится к человеку: запись журнала аудита «аккаунт удалён» (роль, дата, способ — без ПДн);
   счётчики надёжности компаний (reliability_events без ссылки на кандидата и приглашение); откалиброванные параметры
   заданий (не зависят от конкретного человека); обезличенные ИТОГОВЫЕ агрегаты нигде не хранятся отдельно.
5. Работодатель: удаляется его аккаунт. Если он был последним участником компании, её ждущие приглашения отзываются,
   брифы и вакансии закрываются, контакты связи и вебхук ATS стираются; название компании (не ПДн) остаётся для истории
   приглашений кандидатов.

Роли «методист» и «администратор» через самообслуживание не удаляются.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, func, literal_column, or_, select, update
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.configs import load_config
from app.core.errors import bad_request, forbidden
from app.core.rate_limit import RateLimiter
from app.core.security import verify_password
from app.core.settings import RateRule, Settings, parse_rate
from app.mail.sender import Mailer, MailError, OutgoingMail
from app.models.company import Company, WebhookDelivery
from app.models.contact import Application, Feedback, Invitation, Vacancy
from app.models.enums import NeedStatus, UserRole, VacancyStatus
from app.models.integration import AtsLog, ChatMessage
from app.models.matching import Need
from app.models.users import AuditLog, User

log = logging.getLogger("vzaimno.account")

_SELF_SERVICE_ROLES = (UserRole.candidate.value, UserRole.employer.value)


@dataclass
class ErasureOutcome:
    role: str
    email: str  # только для письма-подтверждения; нигде не сохраняется
    company_closed: bool = False
    invitations_withdrawn: int = 0
    scrubbed: dict[str, int] = field(default_factory=dict)


def _erasure_cfg() -> dict[str, Any]:
    return load_config("privacy")["erasure"]


def erasure_rate() -> RateRule:
    return parse_rate(str(_erasure_cfg()["rate_limit"]))


def confirm_deletion(
    user: User, *, password: str | None, confirm: str | None, settings: Settings, limiter: RateLimiter
) -> None:
    """Подтверждение удаления: пароль или (аккаунт без пароля) фраза. Неверное подтверждение → 403 и расход лимита."""
    if user.role not in _SELF_SERVICE_ROLES:
        raise forbidden("erasure_not_self_service", "Этот аккаунт удаляется через администратора")
    limiter.check("account_erasure", str(user.id), erasure_rate())
    if user.password_hash:
        if not password or not verify_password(user.password_hash, password, settings):
            raise forbidden("password_invalid", "Пароль не подошёл. Аккаунт не удалён")
        return
    phrase = str(_erasure_cfg()["confirm_phrase"])
    if (confirm or "").strip().casefold() != phrase.casefold():
        raise bad_request(
            "confirm_phrase_required",
            f"У аккаунта нет пароля (вход через ФСП ID). Для удаления введите слово «{phrase}»",
        )


# --------------------------------------------------------------------------- подготовка к удалению


def _scrub_candidate_links(db: Session, candidate_id: uuid.UUID) -> dict[str, int]:
    """Данные кандидата, которые не связаны с users внешним ключом (см. п. 3 docstring модуля)."""
    inv_ids = [str(i) for i in db.scalars(select(Invitation.id).where(Invitation.candidate_id == candidate_id))]
    app_ids = [str(i) for i in db.scalars(select(Application.id).where(Application.candidate_id == candidate_id))]
    target_ids = [uuid.UUID(i) for i in inv_ids + app_ids]
    counts = {"feedback": 0, "webhook_deliveries": 0, "chat_messages": 0, "ats_log": 0}
    if target_ids:
        res = db.execute(delete(Feedback).where(Feedback.target_id.in_(target_ids)))
        counts["feedback"] = int(res.rowcount or 0)
        # чат и «Журнал ATS» ссылаются на приглашения/отклики без внешнего ключа: текст переписки и запросы с контактами стираем
        res = db.execute(delete(ChatMessage).where(ChatMessage.ref_id.in_(target_ids)))
        counts["chat_messages"] = int(res.rowcount or 0)
        res = db.execute(delete(AtsLog).where(AtsLog.entity_id.in_(inv_ids + app_ids)))
        counts["ats_log"] = int(res.rowcount or 0)
    if inv_ids or app_ids:
        # копии событий для ATS компании: контакты кандидата (если были раскрыты) стираем, остальное — без ПДн
        res = db.execute(
            update(WebhookDelivery)
            .where(
                or_(
                    WebhookDelivery.payload["data"]["invitation_id"].astext.in_(inv_ids),
                    WebhookDelivery.payload["data"]["application_id"].astext.in_(app_ids),
                )
            )
            .values(payload=literal_column("payload #- '{data,contacts}'"))
        )
        counts["webhook_deliveries"] = int(res.rowcount or 0)
    return counts


def _scrub_audit(db: Session, user_id: uuid.UUID) -> int:
    """Записи журнала аудита про этого человека остаются как событие, но без IP и ссылок на него (actor_user_id
    обнуляется внешним ключом при удалении)."""
    uid = str(user_id)
    res = db.execute(
        update(AuditLog)
        .where(
            or_(
                AuditLog.actor_user_id == user_id,
                AuditLog.entity_id == uid,
                AuditLog.payload["user_id"].astext == uid,
            )
        )
        .values(
            ip=None,
            entity_id=func.nullif(AuditLog.entity_id, uid),
            payload=literal_column("payload - 'user_id' - 'family_id'"),
        )
    )
    return int(res.rowcount or 0)


def _close_company_if_orphaned(db: Session, user: User, secret: str, mailer: Mailer | None) -> tuple[bool, int]:
    """Работодатель — последний в компании: отозвать ждущие приглашения, закрыть брифы и вакансии, стереть контакты связи."""
    company = db.get(Company, user.company_id) if user.company_id else None
    if company is None:
        return False, 0
    others = db.scalar(
        select(func.count()).select_from(User).where(User.company_id == company.id, User.id != user.id)
    )
    if others:
        return False, 0
    from app.services.contact import Ctx
    from app.services.contact.invitations import withdraw_pending_for_company

    withdrawn = withdraw_pending_for_company(Ctx(db=db, mailer=mailer, secret=secret), company.id, user.id)
    db.execute(
        update(Need)
        .where(Need.company_id == company.id, Need.status != NeedStatus.closed.value)
        .values(status=NeedStatus.closed.value, close_reason="company_deleted")
    )
    db.execute(
        update(Vacancy)
        .where(Vacancy.company_id == company.id, Vacancy.status != VacancyStatus.closed.value)
        .values(status=VacancyStatus.closed.value)
    )
    company.contact_methods = {}
    company.webhook_url = None
    company.webhook_secret = None
    company.profile_completed_at = None
    db.flush()
    return True, withdrawn


# --------------------------------------------------------------------------- удаление


def erase_user(
    db: Session,
    user: User,
    *,
    secret: str,
    mailer: Mailer | None = None,
    reason: str = "self_service",
    ip: str | None = None,
) -> ErasureOutcome:
    """Удалить аккаунт и всё персональное (см. docstring модуля). Коммитит транзакцию. Вызывающий уже проверил подтверждение."""
    uid, role, email = user.id, user.role, user.email
    out = ErasureOutcome(role=role, email=email)
    if role == UserRole.candidate.value:
        out.scrubbed.update(_scrub_candidate_links(db, uid))
    elif role == UserRole.employer.value:
        out.company_closed, out.invitations_withdrawn = _close_company_if_orphaned(db, user, secret, mailer)
    out.scrubbed["audit_rows"] = _scrub_audit(db, uid)
    db.flush()
    # Core-DELETE: все внешние ключи на users — CASCADE/SET NULL (проверяется tests/test_account_erasure.py), ORM-удаление
    # попыталось бы обнулить первичный ключ профиля.
    db.execute(delete(User).where(User.id == uid).execution_options(synchronize_session=False))
    db.expunge(user)
    audit(
        db,
        str(_erasure_cfg()["audit_action"]),
        actor_role=role,
        entity_type="account",
        reason=reason,
        company_closed=out.company_closed,
        invitations_withdrawn=out.invitations_withdrawn,
    )
    db.commit()
    log.info("Аккаунт (%s) удалён: %s", role, reason)
    return out


def notify_erased(mailer: Mailer, outcome: ErasureOutcome, app_name: str = "Взаимно") -> bool:
    """Письмо-подтверждение на адрес удалённого аккаунта (адрес нигде не сохраняется). Сбой почты не откатывает удаление."""
    text = (
        "Здравствуйте!\n\n"
        f"Ваш аккаунт в «{app_name}» удалён по вашему запросу: профиль, ответы, результаты тестов, переписка и контакты "
        "стёрты, активные сессии завершены. Если вы не делали этого запроса, ответьте на это письмо — "
        "восстановить данные мы не сможем, но разберёмся, как это произошло.\n\n"
        f"— Команда «{app_name}»\n"
    )
    try:
        mailer.send(OutgoingMail(to=outcome.email, subject=f"Аккаунт удалён — {app_name}", text=text, tags={"purpose": "account_erased"}))
    except MailError:
        log.warning("Письмо об удалении аккаунта не отправлено")
        return False
    return True


# --------------------------------------------------------------------------- один вызов для API


_REMOVED = {
    UserRole.candidate.value: [
        "профиль, резюме и контактные данные",
        "ответы опроса, сессии и результаты тестов, категория",
        "приглашения, отклики и раскрытые компаниям контакты",
        "привязка ФСП ID и сохранённые данные реестра",
        "согласия, уведомления и все активные сессии на всех устройствах",
    ],
    UserRole.employer.value: [
        "ваш аккаунт и email",
        "все активные сессии на всех устройствах",
        "если вы были последним в компании: контакты связи и вебхук ATS компании",
    ],
}
_KEPT = [
    "запись журнала аудита «аккаунт удалён» без персональных данных (дата, роль, способ)",
    "счётчики надёжности компаний — без ссылки на вас",
    "параметры сложности заданий — они не зависят от конкретного человека",
]


def delete_account(
    db: Session,
    user: User,
    *,
    password: str | None,
    confirm: str | None,
    settings: Settings,
    limiter: RateLimiter,
    mailer: Mailer,
    ip: str | None,
) -> dict[str, Any]:
    """Подтверждение → удаление → письмо-подтверждение. Ответ API (AccountDeleteResponse) в виде словаря."""
    confirm_deletion(user, password=password, confirm=confirm, settings=settings, limiter=limiter)
    outcome = erase_user(
        db, user, secret=settings.pseudonym_secret.get_secret_value(), mailer=mailer, reason="self_service", ip=ip
    )
    notify_erased(mailer, outcome, settings.app_name)
    return {
        "deleted": True,
        "role": outcome.role,
        "message": "Аккаунт удалён безвозвратно. Мы отправили подтверждение на вашу почту.",
        "removed": _REMOVED[outcome.role],
        "kept": _KEPT,
        "company_closed": outcome.company_closed,
        "invitations_withdrawn": outcome.invitations_withdrawn,
    }
