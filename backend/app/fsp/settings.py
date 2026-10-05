"""Настройки интеграции с ФСП: ФСП ID (OIDC, Keycloak realm fsp) и реестр достижений.

Переход на настоящую ФСП — смена значений, не кода (deploy/keycloak/README.md, концепция §6.5).

Параметры ФСП ID читаются из тех же переменных, что и в app/core/settings.py (VZ_FSP_OIDC_*), с теми же
значениями по умолчанию: у платформы один client в realm `fsp`, и два объекта настроек не должны
расходиться. Параметры реестра — VZ_FSP_REGISTRY_URL (как в compose) и VZ_FSP_* ниже.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FSPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZ_FSP_", extra="ignore", populate_by_name=True)

    # --- ФСП ID (OIDC, authorization code + PKCE). Сам обработчик callback — в app/api.
    issuer: str = Field(  # прод: https://sso.fsp-russia.ru/realms/fsp
        "http://localhost:8080/realms/fsp", validation_alias=AliasChoices("VZ_FSP_OIDC_ISSUER")
    )
    client_id: str = Field("vzaimno", validation_alias=AliasChoices("VZ_FSP_OIDC_CLIENT_ID"))
    # Секрет конфиденциального client. Демо-realm: vzaimno-demo-secret (deploy/keycloak/README.md).
    client_secret: str | None = Field(None, validation_alias=AliasChoices("VZ_FSP_OIDC_CLIENT_SECRET"))
    redirect_uri: str = Field(
        "http://localhost:5173/auth/fsp/callback", validation_alias=AliasChoices("VZ_FSP_OIDC_REDIRECT_URI")
    )
    scopes: str = "openid profile email"

    # --- Реестр достижений
    # http — только сеть; inprocess — встроенный снимок демо-реестра; auto — сеть, при недоступности снимок.
    provider: Literal["http", "inprocess", "auto"] = "auto"
    registry_url: str = "http://localhost:8001"  # сервис fsp-mock (порт из docker-compose.yml)
    registry_token: str | None = "demo-fsp-registry-token"  # статический токен мока
    # client credentials (сервисный аккаунт того же client): если задан token_url, статический токен не используется.
    token_url: str | None = None
    registry_scope: str | None = None
    timeout_s: float = 5.0
    snapshot_path: str | None = None  # None → app/fsp/data/demo_registry.json
