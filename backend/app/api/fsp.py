"""ФСП ID и достижения (экран К14, концепция §3.1 шаг 9, §6.2, §6.4–6.5, DECISIONS п. 12).

Вход через ФСП ID — OIDC authorization code + PKCE (Keycloak realm `fsp`):
1. `GET /api/fsp/login` → адрес страницы ФСП ID (+ httpOnly-cookie с state: вход привязан к браузеру);
2. ФСП ID возвращает браузер на фронтенд `/auth/fsp/callback?code&state`;
3. фронтенд вызывает `GET /api/fsp/callback?code&state` (с Bearer, если это привязка к аккаунту).

Если Keycloak не поднят — «демо-вход ФСП» (`POST /api/fsp/demo-login`): подписанный мок-токен, привязка
помечается как демо. Данные реестра без согласия на обогащение только показываются (предпросмотр), а не
сохраняются; скрытый результат не участвует в оценке. ФСП не влияет на грейд, отсутствие ФСП — не штраф.
"""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from app.api.auth import _set_refresh_cookie
from app.api.deps import (
    DbSession,
    LimiterDep,
    MailerDep,
    MetaDep,
    SettingsDep,
    bearer_scheme,
    get_current_user,
    get_settings_dep,
    require_roles,
)
from app.api.schemas import TokenResponse, UserOut
from app.core.clock import clock
from app.core.errors import ErrorResponse, error_responses, forbidden
from app.core.settings import Settings
from app.db.session import get_db
from app.fsp.provider import FSPProvider
from app.models.enums import UserRole
from app.models.users import User
from app.services.fsp_link import service as fsp
from app.services.fsp_link.oidc import OIDCClient
from app.services.fsp_link.schemas import (
    DemoAthleteOut,
    DemoLoginIn,
    FspAuthResultOut,
    FspAvailabilityOut,
    FspEnrichmentIn,
    FspHideIn,
    FspLoginStartOut,
    FspResultOut,
    FspStatusOut,
    VerificationPendingOut,
)

router = APIRouter(prefix="/fsp", tags=["ФСП"], responses=error_responses())

CandidateUser = Annotated[User, Depends(require_roles(UserRole.candidate))]
OidcDep = Annotated[OIDCClient, Depends(fsp.get_oidc_client)]
ProviderDep = Annotated[FSPProvider, Depends(fsp.get_provider)]

_ERR_503 = {
    503: {
        "description": "ФСП ID или реестр ФСП недоступен (details.demo_login — доступен ли демо-вход)",
        "model": ErrorResponse,
    }
}


def optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User | None:
    """Пользователь, если передан Bearer (недействительный токен — 401), иначе None."""
    if credentials is None:
        return None
    return get_current_user(credentials, db, settings)


OptionalUser = Annotated[User | None, Depends(optional_user)]


def _auth_result(
    result: fsp.CallbackResult,
    response: Response,
    db: Session,
    settings: Settings,
    provider: FSPProvider,
) -> FspAuthResultOut:
    if result.mode == "link":
        assert result.user is not None
        return FspAuthResultOut(
            result="linked", demo=result.demo, fsp=fsp.status_view(db, result.user, provider, clock.now())
        )
    outcome = result.login
    assert outcome is not None
    if outcome.kind == "session" and outcome.session is not None:
        s = outcome.session
        _set_refresh_cookie(response, s.refresh_token, s.refresh_expires_at, settings)
        response.headers["Cache-Control"] = "no-store"
        return FspAuthResultOut(
            result="logged_in",
            demo=result.demo,
            token=TokenResponse(
                access_token=s.access_token, expires_in=settings.access_token_ttl_s, user=UserOut.model_validate(s.user)
            ),
        )
    send = outcome.send
    return FspAuthResultOut(
        result="email_verification_required",
        demo=result.demo,
        verification=VerificationPendingOut(
            user_id=outcome.user.id,
            email=outcome.user.email,
            code_sent=bool(send and send.sent),
            code_ttl_s=settings.email_code_ttl_s,
            resend_available_in_s=send.resend_available_in_s if send else 0,
            next_step=(
                "Мы отправили 6-значный код на email из ФСП ID. Введите его и придумайте пароль "
                "(POST /api/auth/verify-email) — аккаунт станет активным"
            ),
        ),
    )


@router.get(
    "/availability",
    response_model=FspAvailabilityOut,
    summary="Доступен ли ФСП ID",
    description=(
        "Отвечает ли ФСП ID (Keycloak realm `fsp`) и включён ли «демо-вход ФСП». Если ФСП ID недоступен, "
        "фронтенд показывает кнопку демо-входа. Доступно без входа."
    ),
)
def availability(oidc: OidcDep, settings: SettingsDep) -> FspAvailabilityOut:
    s = oidc.settings
    return FspAvailabilityOut(
        fsp_id_available=oidc.available(),
        demo_login_available=fsp.demo_allowed(settings),
        issuer=s.public_issuer,
        registry_label="Демо-реестр ФСП" if s.provider != "http" else "Реестр ФСП",
    )


@router.get(
    "/login",
    response_model=FspLoginStartOut,
    summary="Начать вход через ФСП ID",
    description=(
        "Создаёт одноразовый state, nonce и PKCE (S256) и возвращает адрес страницы ФСП ID. "
        "`mode=link` — привязать ФСП ID к текущему аккаунту кандидата (нужен Bearer); `mode=login` — вход "
        "с лендинга: найдём аккаунт по ФСП ID или создадим новый в статусе pending_email и отправим код "
        "(нужно `consent_personal_data=true`). Ответ ставит httpOnly-cookie `vz_fsp_flow` — завершить вход "
        "можно только из того же браузера. 503 `fsp_id_unavailable` — ФСП ID не отвечает "
        "(details.demo_login: доступен ли демо-вход). 429 — слишком частые попытки."
    ),
    responses={**error_responses(), **_ERR_503},
)
def login_start(
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    limiter: LimiterDep,
    meta: MetaDep,
    oidc: OidcDep,
    user: OptionalUser,
    mode: Annotated[Literal["link", "login"], Query(description="link — привязка, login — вход с лендинга")] = "link",
    consent_personal_data: Annotated[bool, Query(description="Согласие на обработку ПДн (для mode=login)")] = False,
    consent_fsp_enrichment: Annotated[bool, Query(description="Сразу согласиться на обогащение профиля данными ФСП")] = False,
) -> FspLoginStartOut:
    limiter.check("fsp_login_ip", meta.ip or "unknown", oidc.settings.rate("login"))
    start = fsp.start_flow(
        db,
        oidc,
        mode=mode,
        user=user,
        consent_personal_data=consent_personal_data,
        consent_fsp_enrichment=consent_fsp_enrichment,
        now=clock.now(),
    )
    response.set_cookie(
        fsp.FLOW_COOKIE,
        start.state,
        max_age=start.expires_in,
        path=fsp.FLOW_COOKIE_PATH,
        domain=settings.cookie_domain,
        secure=settings.cookie_secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    response.headers["Cache-Control"] = "no-store"
    return FspLoginStartOut(
        authorization_url=start.authorization_url,
        state=start.state,
        expires_in=start.expires_in,
        mode=start.mode,
        redirect_uri=start.redirect_uri,
    )


@router.get(
    "/callback",
    response_model=FspAuthResultOut,
    summary="Завершить вход через ФСП ID",
    description=(
        "Фронтенд передаёт `code` и `state`, которые ФСП ID вернул в адрес возврата. Бэкенд меняет код на токены "
        "(PKCE), проверяет подпись id_token по JWKS, issuer, audience и nonce. Привязка (`mode=link`) завершается "
        "только тем же аккаунтом, что её начал (Bearer). Результат: `linked`, `logged_in` (сессия, refresh — "
        "в cookie) или `email_verification_required` (первый вход: аккаунт создан, код отправлен на email из "
        "ФСП ID — DECISIONS п. 12; согласие на обогащение применится после подтверждения email). "
        "Ошибки: 400 `fsp_state_invalid` / `fsp_state_mismatch` / `fsp_code_invalid` / "
        "`fsp_token_invalid` / `consent_required` / `fsp_email_missing`; 403 `fsp_state_foreign`; 409 "
        "`fsp_id_taken` / `fsp_already_linked` / `email_taken` / `email_pending`; 503 — ФСП ID недоступен."
    ),
    responses={**error_responses(), **_ERR_503},
)
def callback(
    request: Request,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    mailer: MailerDep,
    limiter: LimiterDep,
    meta: MetaDep,
    oidc: OidcDep,
    provider: ProviderDep,
    user: OptionalUser,
    code: Annotated[str, Query(min_length=1, max_length=2048, description="Код авторизации из адреса возврата")],
    state: Annotated[str, Query(min_length=16, max_length=256, description="state из адреса возврата")],
) -> FspAuthResultOut:
    limiter.check("fsp_callback_ip", meta.ip or "unknown", oidc.settings.rate("callback"))
    result = fsp.complete_callback(
        db,
        oidc,
        code=code,
        state=state,
        browser_state=request.cookies.get(fsp.FLOW_COOKIE),
        current_user=user,
        settings=settings,
        mailer=mailer,
        provider=provider,
        meta=meta,
        now=clock.now(),
    )
    response.delete_cookie(fsp.FLOW_COOKIE, path=fsp.FLOW_COOKIE_PATH, domain=settings.cookie_domain)
    return _auth_result(result, response, db, settings, provider)


@router.get(
    "/demo/athletes",
    response_model=list[DemoAthleteOut],
    summary="Демо-спортсмены для «демо-входа ФСП»",
    description=(
        "Вымышленные спортсмены встроенного демо-реестра ФСП (реальных ФИО нет) — для кнопки «демо-вход ФСП», "
        "когда Keycloak не поднят. 403 `fsp_demo_disabled` — демо-вход выключен (VZ_FSP_DEMO_LOGIN_ENABLED); "
        "429 — слишком частые запросы с одного адреса."
    ),
)
def demo_athletes_list(settings: SettingsDep, limiter: LimiterDep, meta: MetaDep) -> list[DemoAthleteOut]:
    if not fsp.demo_allowed(settings):
        raise forbidden("fsp_demo_disabled", "Демо-вход ФСП выключен на этом стенде")
    limiter.check("fsp_demo_athletes_ip", meta.ip or "unknown", fsp.get_fsp_settings().rate("login"))
    return [DemoAthleteOut(**a) for a in fsp.demo_athletes(clock.now())]


@router.post(
    "/demo-login",
    response_model=FspAuthResultOut,
    summary="Демо-вход ФСП",
    description=(
        "Запасной вход для демо, когда ФСП ID (Keycloak) не поднят: платформа выпускает подписанный мок-токен "
        "с теми же claims, что у ФСП ID (issuer `urn:vzaimno:demo-fsp-id`), проверяет его и дальше идёт тем же "
        "путём, что и настоящий вход. Привязка помечается «Демо-вход ФСП». `mode=link` — нужен Bearer кандидата; "
        "`mode=login` — вход или создание аккаунта (нужно `consent_personal_data`); входит только в аккаунты, "
        "созданные через ФСП ID (аккаунт с паролем — 409 `fsp_demo_password_account`, войдите по паролю). "
        "Согласие на обогащение при создании аккаунта применяется после подтверждения email. Ошибки: 403 "
        "`fsp_demo_disabled`; 404 `fsp_demo_unknown`; 409 как у callback; 429 — лимит попыток."
    ),
)
def demo_login(
    body: DemoLoginIn,
    response: Response,
    db: DbSession,
    settings: SettingsDep,
    mailer: MailerDep,
    limiter: LimiterDep,
    meta: MetaDep,
    provider: ProviderDep,
    user: OptionalUser,
) -> FspAuthResultOut:
    limiter.check("fsp_demo_login_ip", meta.ip or "unknown", fsp.get_fsp_settings().rate("demo_login"))
    result = fsp.demo_login(
        db,
        sub=body.sub,
        mode=body.mode,
        email=str(body.email) if body.email else None,
        current_user=user,
        consent_personal_data=body.consent_personal_data,
        consent_fsp_enrichment=body.consent_fsp_enrichment,
        settings=settings,
        mailer=mailer,
        provider=provider,
        meta=meta,
        now=clock.now(),
    )
    return _auth_result(result, response, db, settings, provider)


@router.get(
    "/me",
    response_model=FspStatusOut,
    summary="Мои данные ФСП",
    description=(
        "Состояние (не привязан / привязан, результатов нет / ожидает протокол / привязан), результаты с "
        "переключателями «показывать», бейджи разрядов и ролей, строка для работодателя, выписка из протокола. "
        "Без согласия на обогащение данные показываются для предпросмотра и не сохраняются. Только кандидат."
    ),
)
def my_fsp(user: CandidateUser, db: DbSession, provider: ProviderDep) -> FspStatusOut:
    return fsp.status_view(db, user, provider, clock.now())


@router.get(
    "/me/achievements",
    response_model=list[FspResultOut],
    summary="Предпросмотр достижений ФСП",
    description=(
        "Результаты из реестра ФСП с отметками «подтверждён / ожидает протокол / скрыт» и тем, как результат "
        "увидит работодатель до раскрытия. 404 `fsp_not_linked` — ФСП ID не привязан. Только кандидат."
    ),
)
def my_achievements(user: CandidateUser, db: DbSession, provider: ProviderDep) -> list[FspResultOut]:
    fsp.require_link(db, user)
    return fsp.status_view(db, user, provider, clock.now()).results


@router.patch(
    "/me/results/{result_id}",
    response_model=FspStatusOut,
    summary="Показывать или скрыть результат",
    description=(
        "Скрытый результат не участвует в оценке ФСП и не виден работодателям (иначе была бы скрытая накрутка). "
        "Работает и до согласия на обогащение: выбор применится при синхронизации. 404 — результата нет "
        "в ваших данных или ФСП ID не привязан."
    ),
)
def hide_result(
    body: FspHideIn,
    user: CandidateUser,
    db: DbSession,
    provider: ProviderDep,
    meta: MetaDep,
    result_id: Annotated[str, Path(min_length=1, max_length=128, description="id результата из GET /api/fsp/me")],
) -> FspStatusOut:
    fsp.set_hidden(db, user, result_id, body.hidden, provider, meta, clock.now())
    return fsp.status_view(db, user, provider, clock.now())


@router.post(
    "/me/enrichment",
    response_model=FspStatusOut,
    summary="Согласие на обогащение профиля данными ФСП",
    description=(
        "Записывает согласие `fsp_enrichment` в журнал (отдельной записью с версией текста) и забирает данные "
        "из реестра. Без этого согласия данные ФСП не сохраняются и в подбор не идут. 404 `fsp_not_linked`; "
        "409 `consent_outdated` — устарела версия текста; 429 — не чаще 6 раз за 10 минут (каждый раз запрос в реестр)."
    ),
)
def grant_enrichment(
    body: FspEnrichmentIn,
    user: CandidateUser,
    db: DbSession,
    provider: ProviderDep,
    meta: MetaDep,
    limiter: LimiterDep,
) -> FspStatusOut:
    limiter.check("fsp_enrichment_user", str(user.id), fsp.get_fsp_settings().rate("refresh"))
    fsp.grant_enrichment(db, user, body.text_version, provider, meta, clock.now())
    return fsp.status_view(db, user, provider, clock.now())


@router.post(
    "/me/refresh",
    response_model=FspStatusOut,
    summary="Обновить данные из реестра ФСП",
    description=(
        "Повторно забирает достижения из реестра (кнопка «обновить»). Нужны привязка и согласие на обогащение "
        "(409 `fsp_consent_required`). 503 `fsp_registry_unavailable` — реестр недоступен, прежние данные "
        "сохранены. 429 — не чаще 6 раз за 10 минут."
    ),
    responses={**error_responses(), **_ERR_503},
)
def refresh(user: CandidateUser, db: DbSession, provider: ProviderDep, limiter: LimiterDep) -> FspStatusOut:
    limiter.check("fsp_refresh_user", str(user.id), fsp.get_fsp_settings().rate("refresh"))
    fsp.refresh(db, user, provider, clock.now())
    return fsp.status_view(db, user, provider, clock.now())


@router.delete(
    "/me",
    response_model=FspStatusOut,
    summary="Отвязать ФСП ID",
    description=(
        "Удаляет привязку ФСП ID и все сохранённые данные реестра, отзывает согласие на обогащение (запись "
        "остаётся в журнале согласий с датой отзыва). Подбор пересчитает оценку без ФСП — это не штраф. "
        "404 `fsp_not_linked`."
    ),
)
def unlink(user: CandidateUser, db: DbSession, provider: ProviderDep, meta: MetaDep) -> FspStatusOut:
    fsp.unlink(db, user, meta)
    return fsp.status_view(db, user, provider, clock.now())


__all__ = ["optional_user", "router"]
