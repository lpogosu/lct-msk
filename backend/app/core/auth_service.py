"""Сценарии аутентификации (концепция §3.1 шаги 1–2, §6.5, экраны О2–О4; аудит R-73, G9).

Регистрация → письмо с 6-значным кодом (15 мин) → подтверждение кодом И паролем → активный аккаунт + сессия.
Повторная отправка: не раньше чем через 60 с, не больше 5 писем в час (по данным БД, а не памяти:
лимит переживает перезапуск). Неверных вводов на один код — не больше 5, потом код сгорает.
Восстановление пароля (О4) — тот же механизм кодов с целью password_reset.

Почему подтверждение требует пароль. Повторная регистрация неподтверждённого email перезаписывает
пароль (иначе забывший пароль не смог бы зарегистрироваться снова, а чужая «заготовка» аккаунта
навсегда заняла бы email). Если бы для активации хватало кода, злоумышленник мог бы перерегистрировать
чужой email со своим паролем, а владелец ящика ввёл бы пришедший код и активировал аккаунт с чужим
паролем. Код доказывает владение ящиком, пароль — что активирует тот же человек, что его задал.

Роутеры (app/api/auth.py) тонкие: вся логика и проверки — здесь.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import NoReturn

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.clock import clock
from app.core.consents import grant_consent
from app.core.domains import email_domain, is_personal_domain, normalize_email
from app.core.errors import (
    ApiError,
    bad_request,
    conflict,
    forbidden,
    not_found,
    too_many_requests,
    unauthorized,
)
from app.core.security import (
    codes_equal,
    create_access_token,
    generate_email_code,
    hash_email_code,
    hash_password,
    hash_token,
    new_refresh_token,
    password_needs_rehash,
    password_problems,
    verify_password,
)
from app.core.settings import Settings
from app.mail.sender import Mailer, MailError, OutgoingMail
from app.mail.templates import (
    RenderedMail,
    password_reset_link,
    render_password_reset_code,
    render_verification_code,
    verification_link,
)
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.enums import ConsentPurpose, EmailCodePurpose, UserRole, UserStatus
from app.models.users import EmailCode, RefreshToken, User

log = logging.getLogger("vzaimno.auth")


@dataclass(frozen=True)
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None


@dataclass(frozen=True)
class SendStatus:
    sent: bool
    resend_available_in_s: int
    sends_left_this_hour: int


@dataclass(frozen=True)
class IssuedSession:
    """Выданная сессия: access-JWT + сырой refresh для cookie."""

    user: User
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


# --- Пользователи ---


def get_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == normalize_email(email)))


def _check_password_policy(password: str, settings: Settings) -> None:
    problems = password_problems(password, settings)
    if problems:
        raise ApiError(
            422,
            "validation_error",
            "Пароль не соответствует требованиям",
            [{"field": "password", "message": p, "type": "password_policy"} for p in problems],
        )


# --- Коды подтверждения ---


_VERIFY = EmailCodePurpose.verify_email
_RESET = EmailCodePurpose.password_reset


def _send_window(
    db: Session, user: User, settings: Settings, now: datetime, purpose: EmailCodePurpose = _VERIFY
) -> tuple[int, int]:
    """(сколько секунд до разрешённой повторной отправки, сколько отправок осталось в этом часе).
    Лимиты считаются отдельно для каждой цели (подтверждение email, восстановление пароля)."""
    hour_ago = now - timedelta(hours=1)
    sent = list(
        db.scalars(
            select(EmailCode.sent_at)
            .where(
                EmailCode.user_id == user.id,
                EmailCode.purpose == purpose.value,
                EmailCode.sent_at.is_not(None),
                EmailCode.sent_at > hour_ago,
            )
            .order_by(EmailCode.sent_at)
        )
    )
    left = max(0, settings.email_code_max_sends_per_hour - len(sent))
    wait = 0.0
    if sent:
        wait = max(wait, settings.email_code_resend_cooldown_s - (now - sent[-1]).total_seconds())
    if left == 0:
        # Ждём, пока самая старая отправка выйдет из часового окна
        wait = max(wait, (sent[0] + timedelta(hours=1) - now).total_seconds())
    return max(0, int(-(-wait // 1))), left  # округление вверх


def _ensure_can_send(
    db: Session, user: User, settings: Settings, now: datetime, purpose: EmailCodePurpose = _VERIFY
) -> None:
    wait, left = _send_window(db, user, settings, now, purpose)
    if left == 0:
        raise too_many_requests(
            wait,
            "resend_hourly_limit",
            f"Не больше {settings.email_code_max_sends_per_hour} писем в час. Повторите через {_ru_seconds(wait)}",
        )
    if wait > 0:
        raise too_many_requests(wait, "resend_too_soon", f"Отправить код ещё раз можно через {_ru_seconds(wait)}")


def _ru_seconds(seconds: int) -> str:
    if seconds >= 120:
        minutes = -(-seconds // 60)
        return f"{minutes} мин"
    return f"{seconds} с"


def _invalidate_live_codes(db: Session, user_id: uuid.UUID, purpose: EmailCodePurpose, now: datetime) -> None:
    db.execute(
        update(EmailCode)
        .where(
            EmailCode.user_id == user_id,
            EmailCode.purpose == purpose.value,
            EmailCode.consumed_at.is_(None),
            EmailCode.invalidated_at.is_(None),
        )
        .values(invalidated_at=now)
    )


def _render_code_mail(purpose: EmailCodePurpose, code: str, email: str, settings: Settings) -> RenderedMail:
    if purpose == _RESET:
        link = password_reset_link(settings.public_base_url, email, code)
        return render_password_reset_code(code, link, settings.email_code_ttl_s, settings.app_name)
    link = verification_link(settings.public_base_url, email, code)
    return render_verification_code(code, link, settings.email_code_ttl_s, settings.app_name)


def issue_code(
    db: Session,
    user: User,
    settings: Settings,
    mailer: Mailer,
    purpose: EmailCodePurpose,
    *,
    enforce_limits: bool = True,
) -> SendStatus:
    """Создать новый код цели `purpose` (старые коды этой цели гасятся), сохранить, отправить письмо.

    Код фиксируется в БД до отправки: письмо не должно содержать код, которого нет в базе.
    Если SMTP упал — sent_at остаётся пустым, такая попытка не съедает лимит отправок.
    """
    now = clock.now()
    if enforce_limits:
        _ensure_can_send(db, user, settings, now, purpose)
    _invalidate_live_codes(db, user.id, purpose, now)
    code = generate_email_code()
    row = EmailCode(
        user_id=user.id,
        purpose=purpose.value,
        email=user.email,
        code_hash=hash_email_code(code, user.id, settings),
        created_at=now,
        expires_at=now + timedelta(seconds=settings.email_code_ttl_s),
    )
    db.add(row)
    db.commit()

    rendered = _render_code_mail(purpose, code, user.email, settings)
    try:
        mailer.send(OutgoingMail.from_rendered(user.email, rendered, purpose=purpose.value))
    except MailError:
        log.warning("Письмо с кодом (%s) для пользователя %s не отправлено", purpose.value, user.id)
        wait, left = _send_window(db, user, settings, clock.now(), purpose)
        return SendStatus(False, wait, left)
    row.sent_at = clock.now()
    db.commit()
    wait, left = _send_window(db, user, settings, clock.now(), purpose)
    return SendStatus(True, wait, left)


def issue_verification_code(
    db: Session, user: User, settings: Settings, mailer: Mailer, *, enforce_limits: bool = True
) -> SendStatus:
    """Код подтверждения email (концепция §3.1 шаг 2)."""
    return issue_code(db, user, settings, mailer, _VERIFY, enforce_limits=enforce_limits)


def _code_attempt_failed(
    db: Session,
    settings: Settings,
    user: User,
    row: EmailCode,
    now: datetime,
    meta: RequestMeta,
    *,
    action: str,
    code: str,
    message: str,
) -> NoReturn:
    """Неверный ввод (код или пароль при подтверждении): +1 попытка, после лимита код сгорает."""
    row.attempts += 1
    left = settings.email_code_max_attempts - row.attempts
    if left <= 0:
        row.invalidated_at = now
    audit(db, action, actor_id=user.id, entity_type="user", entity_id=user.id, ip=meta.ip, attempts=row.attempts, reason=code)
    db.commit()  # счётчик попыток должен сохраниться, хотя ответ — ошибка
    if left <= 0:
        raise bad_request("code_attempts_exceeded", "Слишком много неверных попыток. Запросите новый код")
    raise bad_request(code, message, {"attempts_left": left})


def _check_code(
    db: Session,
    settings: Settings,
    user: User,
    purpose: EmailCodePurpose,
    code: str,
    now: datetime,
    meta: RequestMeta,
    *,
    action: str,
) -> EmailCode:
    """Найти действующий код цели `purpose` (с блокировкой строки) и сверить с введённым."""
    row = db.scalar(
        select(EmailCode)
        .where(
            EmailCode.user_id == user.id,
            EmailCode.purpose == purpose.value,
            EmailCode.consumed_at.is_(None),
            EmailCode.invalidated_at.is_(None),
        )
        .order_by(EmailCode.created_at.desc())
        .limit(1)
        .with_for_update()
    )
    if row is None:
        raise bad_request("code_not_found", "Действующего кода нет. Запросите новый код")
    if row.expires_at <= now:
        raise bad_request("code_expired", "Срок действия кода истёк. Запросите новый код")
    if row.attempts >= settings.email_code_max_attempts:
        raise bad_request("code_attempts_exceeded", "Слишком много неверных попыток. Запросите новый код")
    if not codes_equal(row.code_hash, hash_email_code(code.strip(), user.id, settings)):
        _code_attempt_failed(
            db,
            settings,
            user,
            row,
            now,
            meta,
            action=action,
            code="code_invalid",
            message="Неверный код. Проверьте письмо или запросите новый код",
        )
    return row


def _activate_email(db: Session, settings: Settings, user: User, now: datetime) -> None:
    """Email подтверждён (кодом из письма): аккаунт активен, создаются профиль/компания."""
    user.email_verified = True
    user.email_verified_at = now
    if user.status == UserStatus.pending_email.value:
        user.status = UserStatus.active.value
    _provision_role_records(db, settings, user)


# --- Регистрация и подтверждение ---


def register(
    db: Session,
    settings: Settings,
    mailer: Mailer,
    *,
    email: str,
    password: str,
    role: str,
    consent_text_version: str | None,
    meta: RequestMeta,
) -> tuple[User, SendStatus]:
    email = normalize_email(email)
    if role not in (UserRole.candidate.value, UserRole.employer.value):
        raise bad_request("role_not_allowed", "Регистрация доступна только соискателям и работодателям")
    _check_password_policy(password, settings)

    user = get_user_by_email(db, email)
    if user is not None and (user.email_verified or user.status != UserStatus.pending_email.value):
        raise conflict("email_taken", "Этот email уже зарегистрирован. Войдите в аккаунт", {"email": email})
    if user is not None:
        # Повторная регистрация неподтверждённого email: активирует аккаунт только тот, у кого есть
        # и код из ящика, и этот пароль (verify_email). Сначала лимит писем — иначе пароль можно
        # было бы перезаписывать без ограничений.
        _ensure_can_send(db, user, settings, clock.now())
        user.password_hash = hash_password(password, settings)
        user.role = role
    else:
        user = User(
            email=email,
            email_domain=email_domain(email),
            role=role,
            status=UserStatus.pending_email.value,
            email_verified=False,
            password_hash=hash_password(password, settings),
        )
        try:
            with db.begin_nested():
                db.add(user)
                db.flush()
        except IntegrityError:
            # Параллельная регистрация того же email успела раньше (UNIQUE users.email)
            raise conflict("email_taken", "Этот email уже зарегистрирован. Войдите в аккаунт", {"email": email}) from None
    # Отдельное согласие на обработку ПДн — отдельная запись журнала (152-ФЗ)
    grant_consent(
        db,
        user.id,
        ConsentPurpose.processing,
        text_version=consent_text_version,
        ip=meta.ip,
        user_agent=meta.user_agent,
        scope={"context": "registration"},
    )
    audit(db, "auth.register", actor_id=user.id, actor_role=role, entity_type="user", entity_id=user.id, ip=meta.ip)
    db.commit()
    status = issue_verification_code(db, user, settings, mailer, enforce_limits=False)
    return user, status


def resend_code(db: Session, settings: Settings, mailer: Mailer, *, email: str) -> SendStatus:
    user = get_user_by_email(db, email)
    if user is None:
        raise not_found("user_not_found", "Аккаунт с таким email не найден. Зарегистрируйтесь")
    if user.email_verified:
        raise conflict("already_verified", "Email уже подтверждён. Войдите в аккаунт")
    status = issue_verification_code(db, user, settings, mailer, enforce_limits=True)
    if not status.sent:
        raise ApiError(
            503,
            "mail_unavailable",
            "Не удалось отправить письмо. Попробуйте ещё раз через минуту",
            {"resend_available_in_s": status.resend_available_in_s},
        )
    return status


def verify_email(
    db: Session, settings: Settings, *, email: str, code: str, password: str, meta: RequestMeta
) -> User:
    """Подтверждение email: код из письма + пароль из регистрации (почему — см. docstring модуля).

    Сначала проверяется код (без него пароль не перебрать), затем пароль; неверный пароль тратит
    попытку того же кода. Аккаунт, созданный входом через ФСП ID без пароля (DECISIONS п. 12),
    получает здесь пароль — он проходит те же требования, что при регистрации.
    """
    user = get_user_by_email(db, email)
    if user is None:
        raise bad_request("code_invalid", "Неверный код. Проверьте письмо или запросите новый код")
    if user.email_verified:
        raise conflict("already_verified", "Email уже подтверждён. Войдите в аккаунт")
    if user.status == UserStatus.blocked.value:
        raise forbidden("account_blocked", "Аккаунт заблокирован. Напишите в поддержку")
    now = clock.now()
    row = _check_code(db, settings, user, _VERIFY, code, now, meta, action="auth.verify_failed")
    if user.password_hash is None:
        _check_password_policy(password, settings)
        user.password_hash = hash_password(password, settings)
    elif not verify_password(user.password_hash, password, settings):
        _code_attempt_failed(
            db,
            settings,
            user,
            row,
            now,
            meta,
            action="auth.verify_failed",
            code="password_mismatch",
            message=(
                "Пароль не совпадает с указанным при регистрации. Введите его или зарегистрируйтесь "
                "с этим email заново"
            ),
        )
    row.consumed_at = now
    _activate_email(db, settings, user, now)
    audit(db, "auth.email_verified", actor_id=user.id, actor_role=user.role, entity_type="user", entity_id=user.id, ip=meta.ip)
    db.commit()
    return user


def _provision_role_records(db: Session, settings: Settings, user: User) -> None:
    """После подтверждения email: кандидату — профиль, работодателю — компания по домену."""
    if user.role == UserRole.candidate.value:
        if db.get(CandidateProfile, user.id) is None:
            # Лимит приглашений по умолчанию — из config/contact.yaml (ставит конструктор модели)
            db.add(CandidateProfile(user_id=user.id))
    elif user.role == UserRole.employer.value and user.company_id is None:
        user.company_id = ensure_company_for_domain(db, settings, user.email_domain).id


def ensure_company_for_domain(db: Session, settings: Settings, domain: str) -> Company:
    """Корпоративный домен → общая компания домена; личный (gmail, mail.ru…) → новая компания на аккаунт.
    Компания создаётся только после подтверждения email: иначе домен можно «занять» чужим адресом."""
    personal = is_personal_domain(domain)
    if not personal:
        existing = db.scalar(select(Company).where(Company.email_domain == domain, Company.personal_domain.is_(False)))
        if existing is not None:
            return existing
    company = Company(email_domain=domain, personal_domain=personal)
    try:
        with db.begin_nested():
            db.add(company)
            db.flush()
    except IntegrityError:
        # Параллельная регистрация с того же домена успела создать компанию
        existing = db.scalar(select(Company).where(Company.email_domain == domain, Company.personal_domain.is_(False)))
        if existing is None:
            raise
        return existing
    return company


# --- Вход и сессии ---


def authenticate(db: Session, settings: Settings, *, email: str, password: str, meta: RequestMeta) -> User:
    user = get_user_by_email(db, email)
    ok = verify_password(user.password_hash if user else None, password, settings)
    if not ok or user is None:
        audit(db, "auth.login_failed", actor_id=user.id if user else None, ip=meta.ip, reason="bad_credentials")
        db.commit()
        raise unauthorized("invalid_credentials", "Неверный email или пароль")
    # Статусы проверяем после пароля: иначе ответ раскрывал бы состояние чужого аккаунта
    if user.status == UserStatus.blocked.value:
        raise forbidden("account_blocked", "Аккаунт заблокирован. Напишите в поддержку")
    if not user.email_verified:
        wait, _ = _send_window(db, user, settings, clock.now())
        raise forbidden(
            "email_not_verified",
            "Подтвердите email: мы отправили код на вашу почту",
            {"email": user.email, "resend_available_in_s": wait},
        )
    if user.password_hash and password_needs_rehash(user.password_hash, settings):
        user.password_hash = hash_password(password, settings)
    user.last_login_at = clock.now()
    audit(db, "auth.login", actor_id=user.id, actor_role=user.role, ip=meta.ip)
    return user


def _new_session(
    db: Session, settings: Settings, user: User, meta: RequestMeta, family_id: uuid.UUID | None
) -> tuple[IssuedSession, RefreshToken]:
    now = clock.now()
    raw, token_hash = new_refresh_token()
    refresh = RefreshToken(
        user_id=user.id,
        family_id=family_id or uuid.uuid4(),
        token_hash=token_hash,
        created_at=now,
        expires_at=now + timedelta(seconds=settings.refresh_token_ttl_s),
        user_agent=(meta.user_agent or "")[:512] or None,
        ip=meta.ip,
    )
    db.add(refresh)
    db.flush()
    access, access_exp = create_access_token(user.id, user.role, settings)
    return IssuedSession(user, access, access_exp, raw, refresh.expires_at), refresh


def start_session(db: Session, settings: Settings, user: User, meta: RequestMeta) -> IssuedSession:
    """Новый вход: access-JWT + refresh новой семьи."""
    session, _ = _new_session(db, settings, user, meta, None)
    db.commit()
    return session


def _revoke_family(db: Session, family_id: uuid.UUID, reason: str, now: datetime) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.family_id == family_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )


def rotate_refresh(db: Session, settings: Settings, raw: str | None, meta: RequestMeta) -> IssuedSession:
    """Ротация refresh. Предъявлен уже ротированный токен → кража: отзываем всю семью.

    Исключение — окно `refresh_reuse_grace_s` (две вкладки обновились одновременно): отвечаем 409,
    фронтенд повторяет запрос с уже обновлённой cookie, семья не отзывается.
    """
    if not raw:
        raise unauthorized("refresh_missing", "Сессия не найдена. Войдите снова")
    now = clock.now()
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)).with_for_update())
    if token is None:
        raise unauthorized("refresh_invalid", "Сессия недействительна. Войдите снова")
    if token.revoked_at is not None:
        if token.revoked_reason == "rotated":
            if (now - token.revoked_at).total_seconds() <= settings.refresh_reuse_grace_s:
                raise conflict("refresh_superseded", "Сессия уже обновлена в другой вкладке. Повторите запрос")
            _revoke_family(db, token.family_id, "reuse_detected", now)
            audit(db, "auth.refresh_reuse_detected", actor_id=token.user_id, ip=meta.ip, family_id=token.family_id)
            db.commit()
            raise unauthorized("refresh_reused", "Сессия завершена из соображений безопасности. Войдите снова")
        raise unauthorized("refresh_invalid", "Сессия недействительна. Войдите снова")
    if token.expires_at <= now:
        raise unauthorized("refresh_expired", "Сессия истекла. Войдите снова")
    user = db.get(User, token.user_id)
    if user is None or not user.is_active:
        _revoke_family(db, token.family_id, "user_inactive", now)
        db.commit()
        raise unauthorized("refresh_invalid", "Сессия недействительна. Войдите снова")
    # Старый токен гасится в той же транзакции, где выдаётся новый: двух живых в семье не бывает
    token.revoked_at = now
    token.revoked_reason = "rotated"
    db.flush()
    session, new_token = _new_session(db, settings, user, meta, token.family_id)
    token.replaced_by_id = new_token.id
    db.commit()
    return session


def logout(db: Session, raw: str | None, meta: RequestMeta) -> None:
    """Завершить сессию этого устройства (вся семья refresh). Без cookie — ничего не делаем."""
    if not raw:
        return
    token = db.scalar(select(RefreshToken).where(RefreshToken.token_hash == hash_token(raw)))
    if token is None:
        return
    _revoke_family(db, token.family_id, "logout", clock.now())
    audit(db, "auth.logout", actor_id=token.user_id, ip=meta.ip)
    db.commit()


def _revoke_all_sessions(db: Session, user_id: uuid.UUID, reason: str, now: datetime) -> None:
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user_id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )


# --- Восстановление пароля (экран О4 «Вход и восстановление») ---


def request_password_reset(db: Session, settings: Settings, mailer: Mailer, *, email: str) -> SendStatus | None:
    """Отправить код восстановления. None — аккаунта нет или он заблокирован: письмо не уходит,
    а API отвечает так же, как при отправке (ответ не выдаёт, зарегистрирован ли email).

    Работает и для неподтверждённого аккаунта: код из письма доказывает владение ящиком, поэтому
    подтверждение сброса заодно подтверждает email (так владелец возвращает себе «заготовку»
    аккаунта, созданную кем-то другим с его адресом).
    """
    user = get_user_by_email(db, email)
    if user is None or user.status == UserStatus.blocked.value:
        return None
    status = issue_code(db, user, settings, mailer, _RESET, enforce_limits=True)
    if not status.sent:
        raise ApiError(
            503,
            "mail_unavailable",
            "Не удалось отправить письмо. Попробуйте ещё раз через минуту",
            {"resend_available_in_s": status.resend_available_in_s},
        )
    audit(db, "auth.password_reset_requested", actor_id=user.id, entity_type="user", entity_id=user.id)
    db.commit()
    return status


def confirm_password_reset(
    db: Session, settings: Settings, *, email: str, code: str, new_password: str, meta: RequestMeta
) -> User:
    """Новый пароль по коду из письма. Все сессии пользователя завершаются (если пароль меняют
    из-за утечки, старые refresh-токены не должны жить); неподтверждённый email становится подтверждённым."""
    user = get_user_by_email(db, email)
    if user is None:
        raise bad_request("code_invalid", "Неверный код. Проверьте письмо или запросите новый код")
    if user.status == UserStatus.blocked.value:
        raise forbidden("account_blocked", "Аккаунт заблокирован. Напишите в поддержку")
    # Требования к паролю — до проверки кода: слабый пароль не должен сжигать попытку
    _check_password_policy(new_password, settings)
    now = clock.now()
    row = _check_code(db, settings, user, _RESET, code, now, meta, action="auth.password_reset_failed")
    row.consumed_at = now
    user.password_hash = hash_password(new_password, settings)
    _revoke_all_sessions(db, user.id, "password_reset", now)
    if not user.email_verified:
        _invalidate_live_codes(db, user.id, _VERIFY, now)
        _activate_email(db, settings, user, now)
    audit(db, "auth.password_reset", actor_id=user.id, actor_role=user.role, entity_type="user", entity_id=user.id, ip=meta.ip)
    db.commit()
    return user
