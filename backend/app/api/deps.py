"""Общие зависимости FastAPI: настройки, почта, текущий пользователь, проверка ролей, метаданные запроса."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.core.access import ensure_role
from app.core.auth_service import RequestMeta
from app.core.errors import unauthorized
from app.core.rate_limit import RateLimiter, client_ip
from app.core.security import TokenError, decode_access_token
from app.core.settings import Settings
from app.db.session import get_db
from app.mail.sender import Mailer
from app.models.enums import UserRole, UserStatus
from app.models.users import User

bearer_scheme = HTTPBearer(
    auto_error=False,
    scheme_name="Bearer",
    description="Access-токен из ответа /api/auth/login, /verify-email или /refresh (живёт 15 минут)",
)


def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


def get_mailer(request: Request) -> Mailer:
    return request.app.state.mailer


def get_limiter(request: Request) -> RateLimiter:
    return request.app.state.rate_limiter


def get_request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


_TOKEN_MESSAGES = {
    "token_expired": "Срок действия входа истёк. Обновите сессию",
    "token_invalid": "Недействительный токен. Войдите снова",
}


def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings_dep)],
) -> User:
    """Пользователь по access-JWT. Роль и статус берутся из БД (а не из токена): блокировка
    и смена роли действуют сразу, не дожидаясь истечения токена."""
    if credentials is None or credentials.scheme.lower() != "bearer" or not credentials.credentials:
        raise unauthorized("not_authenticated", "Требуется вход в систему")
    try:
        claims = decode_access_token(credentials.credentials, settings)
    except TokenError as e:
        raise unauthorized(e.code, _TOKEN_MESSAGES.get(e.code, "Недействительный токен")) from e
    user = db.get(User, claims.user_id)
    if user is None or user.status != UserStatus.active.value:
        raise unauthorized("token_invalid", "Недействительный токен. Войдите снова")
    return user


def require_roles(*roles: UserRole | str) -> Callable[..., User]:
    """Зависимость «только для ролей»: `user: User = Depends(require_roles(UserRole.employer))` → 403 иначе."""
    allowed = tuple(str(r.value if isinstance(r, UserRole) else r) for r in roles)

    def _dep(user: Annotated[User, Depends(get_current_user)]) -> User:
        ensure_role(user, *allowed)
        return user

    _dep.__name__ = f"require_roles_{'_'.join(allowed)}"
    return _dep


# Аннотированные зависимости: `def route(db: DbSession, user: CurrentUser): ...`
DbSession = Annotated[Session, Depends(get_db)]
SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
MailerDep = Annotated[Mailer, Depends(get_mailer)]
LimiterDep = Annotated[RateLimiter, Depends(get_limiter)]
MetaDep = Annotated[RequestMeta, Depends(get_request_meta)]
CurrentUser = Annotated[User, Depends(get_current_user)]
