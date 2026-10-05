"""Аутентификация: регистрация с кодом из письма, вход, обновление и завершение сессии.

Access-JWT — в теле ответа (держать в памяти фронтенда), refresh — в httpOnly-cookie с путём /api/auth.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Request, Response, status

from app.api.deps import CurrentUser, DbSession, LimiterDep, MailerDep, MetaDep, SettingsDep
from app.api.schemas import (
    LoginRequest,
    PasswordResetConfirmRequest,
    PasswordResetRequest,
    PasswordResetRequestResponse,
    RegisterRequest,
    RegisterResponse,
    ResendCodeRequest,
    ResendCodeResponse,
    TokenResponse,
    UserOut,
    VerifyEmailRequest,
)
from app.core import auth_service
from app.core.auth_service import IssuedSession
from app.core.clock import clock
from app.core.domains import normalize_email
from app.core.errors import error_responses
from app.core.settings import Settings

router = APIRouter(prefix="/auth", tags=["Аутентификация"], responses=error_responses())

REFRESH_COOKIE_DOC = (
    "Refresh-токен передаётся в httpOnly-cookie `vz_refresh` (путь /api/auth, SameSite=Lax). "
    "Каждое обновление выдаёт новый токен, старый перестаёт действовать; повторное предъявление "
    "старого токена завершает все сессии этой цепочки."
)


def _set_refresh_cookie(response: Response, raw: str, expires_at: datetime, settings: Settings) -> None:
    max_age = max(0, int((expires_at - clock.now()).total_seconds()))
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=raw,
        max_age=max_age,
        path=settings.refresh_cookie_path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _clear_refresh_cookie(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )


def _token_response(session: IssuedSession, response: Response, settings: Settings) -> TokenResponse:
    _set_refresh_cookie(response, session.refresh_token, session.refresh_expires_at, settings)
    response.headers["Cache-Control"] = "no-store"
    return TokenResponse(
        access_token=session.access_token,
        expires_in=settings.access_token_ttl_s,
        user=UserOut.model_validate(session.user),
    )


def _refresh_cookie(request: Request, settings: Settings) -> str | None:
    return request.cookies.get(settings.refresh_cookie_name)


@router.post(
    "/register",
    status_code=status.HTTP_201_CREATED,
    response_model=RegisterResponse,
    summary="Регистрация по email",
    description=(
        "Создаёт аккаунт в статусе `pending_email` и отправляет письмо с 6-значным кодом (действует 15 минут). "
        "Роль: `candidate` («Ищу работу») или `employer` («Нанимаю»). Согласие на обработку ПДн обязательно и "
        "записывается в журнал отдельной записью с версией текста. Повторная регистрация неподтверждённого email "
        "обновляет пароль и роль и отправляет новый код (с учётом лимита писем); подтвердить email можно только "
        "с паролем из последней регистрации. "
        "Ошибки: 409 `email_taken` — email уже подтверждён другим аккаунтом; 409 `consent_outdated` — устарела "
        "версия текста согласия; 422 — поля или требования к паролю; 429 — слишком частые запросы."
    ),
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, extra={409: "Email уже зарегистрирован или устарел текст согласия"}),
)
def register(
    body: RegisterRequest,
    db: DbSession,
    settings: SettingsDep,
    mailer: MailerDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> RegisterResponse:
    limiter.check("register_ip", meta.ip or "unknown", settings.rate("register_ip"))
    user, sent = auth_service.register(
        db,
        settings,
        mailer,
        email=body.email,
        password=body.password,
        role=body.role,
        consent_text_version=body.consent_text_version,
        meta=meta,
    )
    return RegisterResponse(
        user_id=user.id,
        email=user.email,
        role=user.role,
        status=user.status,
        code_sent=sent.sent,
        code_ttl_s=settings.email_code_ttl_s,
        resend_available_in_s=sent.resend_available_in_s,
    )


@router.post(
    "/verify-email",
    response_model=TokenResponse,
    summary="Подтверждение email кодом",
    description=(
        "Проверяет 6-значный код из письма и пароль из регистрации (код доказывает владение ящиком, пароль — "
        "что аккаунт активирует тот, кто его задал: иначе чужая повторная регистрация с этим email могла бы "
        "подменить пароль). При успехе аккаунт становится `active`, создаётся профиль кандидата или компания "
        "работодателя (по домену почты; личные домены — своя компания с пометкой «личный email»), выдаются "
        "access-токен и refresh-cookie. На один код — не больше 5 неверных попыток (неверный пароль тоже "
        "тратит попытку). Ошибки 400: `code_invalid` (в details — attempts_left), `password_mismatch` "
        "(в details — attempts_left), `code_expired`, `code_attempts_exceeded`, `code_not_found`; "
        "409 `already_verified`; 422 — формат кода или пароля; 429 — слишком частые попытки. " + REFRESH_COOKIE_DOC
    ),
)
def verify_email(
    body: VerifyEmailRequest,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> TokenResponse:
    limiter.check("verify_email", normalize_email(body.email), settings.rate("verify_email"))
    user = auth_service.verify_email(
        db, settings, email=body.email, code=body.code, password=body.password, meta=meta
    )
    session = auth_service.start_session(db, settings, user, meta)
    return _token_response(session, response, settings)


@router.post(
    "/resend-code",
    response_model=ResendCodeResponse,
    summary="Отправить код ещё раз",
    description=(
        "Отправляет новый код (предыдущие перестают действовать). Не раньше чем через 60 секунд после "
        "прошлой отправки и не больше 5 писем в час — иначе 429 с `retry_after_s` и заголовком Retry-After "
        "(коды `resend_too_soon`, `resend_hourly_limit`). 404 `user_not_found` — такого аккаунта нет; "
        "409 `already_verified` — email уже подтверждён; 503 `mail_unavailable` — почтовый сервер недоступен."
    ),
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 503),
)
def resend_code(
    body: ResendCodeRequest,
    db: DbSession,
    settings: SettingsDep,
    mailer: MailerDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> ResendCodeResponse:
    limiter.check("resend_ip", meta.ip or "unknown", settings.rate("resend_ip"))
    sent = auth_service.resend_code(db, settings, mailer, email=body.email)
    return ResendCodeResponse(
        sent=sent.sent,
        code_ttl_s=settings.email_code_ttl_s,
        resend_available_in_s=sent.resend_available_in_s,
        sends_left_this_hour=sent.sends_left_this_hour,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Вход по email и паролю",
    description=(
        "Проверяет пароль (argon2id) и выдаёт access-токен и refresh-cookie. Ошибки: 401 `invalid_credentials` "
        "(одинаково для неизвестного email и неверного пароля); 403 `email_not_verified` — нужно ввести код "
        "(экран О3, в details — email и resend_available_in_s); 403 `account_blocked`; 429 — слишком много "
        "попыток входа с этого email или IP. " + REFRESH_COOKIE_DOC
    ),
)
def login(
    body: LoginRequest,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> TokenResponse:
    # Лимит до проверки пароля: перебор упирается в 429 независимо от результата
    limiter.check("login_ip", meta.ip or "unknown", settings.rate("login_ip"))
    limiter.check("login_email", normalize_email(body.email), settings.rate("login_email"))
    user = auth_service.authenticate(db, settings, email=body.email, password=body.password, meta=meta)
    session = auth_service.start_session(db, settings, user, meta)
    return _token_response(session, response, settings)


@router.post(
    "/password-reset/request",
    response_model=PasswordResetRequestResponse,
    summary="Восстановление пароля: отправить код",
    description=(
        "Экран О4. Отправляет на email 6-значный код восстановления (действует 15 минут) со ссылкой на экран "
        "нового пароля. Ответ одинаковый, есть такой аккаунт или нет: по нему нельзя проверить, зарегистрирован "
        "ли email. Те же лимиты, что у кода подтверждения: не чаще раза в 60 секунд и не больше 5 писем в час "
        "(429 `resend_too_soon` / `resend_hourly_limit` с `retry_after_s`); 503 `mail_unavailable` — почтовый "
        "сервер недоступен."
    ),
    responses=error_responses(400, 401, 403, 404, 409, 422, 429, 503),
)
def password_reset_request(
    body: PasswordResetRequest,
    db: DbSession,
    settings: SettingsDep,
    mailer: MailerDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> PasswordResetRequestResponse:
    limiter.check("password_reset_ip", meta.ip or "unknown", settings.rate("resend_ip"))
    sent = auth_service.request_password_reset(db, settings, mailer, email=body.email)
    # Для неизвестного email — те же числа, что после настоящей отправки
    wait = sent.resend_available_in_s if sent else settings.email_code_resend_cooldown_s
    return PasswordResetRequestResponse(code_ttl_s=settings.email_code_ttl_s, resend_available_in_s=wait)


@router.post(
    "/password-reset/confirm",
    response_model=TokenResponse,
    summary="Восстановление пароля: новый пароль по коду",
    description=(
        "Проверяет код из письма восстановления и задаёт новый пароль. Все прежние сессии пользователя "
        "завершаются, выдаются новые access-токен и refresh-cookie. Если email ещё не был подтверждён, он "
        "подтверждается (код доказывает владение ящиком). Ошибки 400: `code_invalid` (в details — attempts_left), "
        "`code_expired`, `code_attempts_exceeded`, `code_not_found`; 403 `account_blocked`; 422 — формат кода "
        "или требования к паролю; 429 — слишком частые попытки. " + REFRESH_COOKIE_DOC
    ),
)
def password_reset_confirm(
    body: PasswordResetConfirmRequest,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> TokenResponse:
    limiter.check("password_reset_confirm", normalize_email(body.email), settings.rate("verify_email"))
    user = auth_service.confirm_password_reset(
        db, settings, email=body.email, code=body.code, new_password=body.new_password, meta=meta
    )
    session = auth_service.start_session(db, settings, user, meta)
    return _token_response(session, response, settings)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Обновить сессию",
    description=(
        "По refresh-cookie выдаёт новый access-токен и новую refresh-cookie (ротация). "
        "401: `refresh_missing`, `refresh_invalid`, `refresh_expired`, `refresh_reused` (предъявлен старый "
        "токен — все сессии цепочки завершены). 409 `refresh_superseded` — токен только что обновлён в другой "
        "вкладке: повторите запрос. " + REFRESH_COOKIE_DOC
    ),
)
def refresh(
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    meta: MetaDep,
) -> TokenResponse:
    limiter.check("refresh_ip", meta.ip or "unknown", settings.rate("refresh_ip"))
    session = auth_service.rotate_refresh(db, settings, _refresh_cookie(request, settings), meta)
    return _token_response(session, response, settings)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Выйти",
    description="Завершает сессию этого устройства (отзывает цепочку refresh-токенов) и удаляет cookie. Идемпотентно.",
)
def logout(
    request: Request,
    db: DbSession,
    settings: SettingsDep,
    meta: MetaDep,
) -> Response:
    auth_service.logout(db, _refresh_cookie(request, settings), meta)
    out = Response(status_code=status.HTTP_204_NO_CONTENT)
    _clear_refresh_cookie(out, settings)
    return out


@router.get(
    "/me",
    response_model=UserOut,
    summary="Текущий пользователь",
    description="Аккаунт по access-токену. 401 — токена нет, он истёк или недействителен.",
)
def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)
