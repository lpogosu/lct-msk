"""Настройки интеграции с ФСП: ФСП ID (OIDC, Keycloak realm fsp) и реестр достижений.

Переход на настоящую ФСП — смена значений, не кода (deploy/keycloak/README.md, концепция §6.5).

Параметры ФСП ID читаются из тех же переменных, что и в app/core/settings.py (VZ_FSP_OIDC_*), с теми же
значениями по умолчанию: у платформы один client в realm `fsp`, и два объекта настроек не должны
расходиться. Параметры реестра — VZ_FSP_REGISTRY_URL (как в compose) и VZ_FSP_* ниже.

Публичный и внутренний адрес issuer. Браузер ходит в Keycloak по `issuer` (http://localhost:8080/realms/fsp),
а бэкенд внутри docker-сети такого адреса не видит: для discovery, обмена кода на токен и JWKS нужен
`internal_url` (http://keycloak:8080/realms/fsp). Адрес страницы входа для браузера всегда строится
от публичного issuer, серверные вызовы — от внутреннего (to_public / to_backchannel).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings import RateRule

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class FSPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZ_FSP_", extra="ignore", populate_by_name=True)

    # --- ФСП ID (OIDC, authorization code + PKCE). Обработчик callback — app/api/fsp.py.
    issuer: str = Field(  # прод: https://sso.fsp-russia.ru/realms/fsp
        "http://localhost:8080/realms/fsp", validation_alias=AliasChoices("VZ_FSP_OIDC_ISSUER")
    )
    # Тот же realm изнутри docker-сети (discovery, token, JWKS). Пусто — как issuer.
    internal_url: str | None = Field(None, validation_alias=AliasChoices("VZ_FSP_OIDC_INTERNAL_URL"))
    client_id: str = Field("vzaimno", validation_alias=AliasChoices("VZ_FSP_OIDC_CLIENT_ID"))
    # Секрет конфиденциального client. Демо-realm: vzaimno-demo-secret (deploy/keycloak/README.md).
    client_secret: str | None = Field(None, validation_alias=AliasChoices("VZ_FSP_OIDC_CLIENT_SECRET"))
    redirect_uri: str = Field(
        "http://localhost:5173/candidate/fsp/callback", validation_alias=AliasChoices("VZ_FSP_OIDC_REDIRECT_URI")
    )
    scopes: str = "openid profile email"
    oidc_timeout_s: float = 5.0  # discovery, обмен кода, JWKS
    discovery_cache_s: int = 300
    # «ФСП ID недоступен» помним столько секунд: публичная GET /api/fsp/availability не ждёт таймаут Keycloak
    # на каждом открытии страницы входа
    unavailable_cache_s: int = 30
    state_ttl_s: int = 600  # сколько живёт начатый вход (state + PKCE)
    # «Демо-вход ФСП»: подписанный мок-токен вместо Keycloak (концепция §6.5) — демо не зависит от Keycloak
    demo_login_enabled: bool = True  # VZ_FSP_DEMO_LOGIN_ENABLED
    demo_token_ttl_s: int = 300
    demo_email_domain: str = "demo.example.org"  # email демо-спортсмена, если он не указал свой

    # --- Ограничение частоты запросов: "N/секунд"
    rl_login: str = "30/300"  # начать вход — с одного IP
    rl_callback: str = "30/300"  # завершить вход — с одного IP
    rl_demo_login: str = "20/300"  # демо-вход — с одного IP
    rl_refresh: str = "6/600"  # «обновить» достижения — на аккаунт (каждый раз запрос в реестр)

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

    @field_validator("rl_login", "rl_callback", "rl_demo_login", "rl_refresh")
    @classmethod
    def _rate_format(cls, v: str) -> str:
        m = _RATE_RE.match(v)
        if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
            raise ValueError("ожидается формат 'N/секунд', например '30/300'")
        return v

    def rate(self, name: str) -> RateRule:
        """settings.rate("login") → RateRule(30, 300)."""
        m = _RATE_RE.match(getattr(self, f"rl_{name}"))
        assert m is not None  # формат проверен валидатором
        return RateRule(int(m.group(1)), int(m.group(2)))

    # --- Публичный и внутренний адрес realm

    @property
    def public_issuer(self) -> str:
        return self.issuer.rstrip("/")

    @property
    def backchannel_issuer(self) -> str:
        return (self.internal_url or self.issuer).rstrip("/")

    def to_public(self, url: str) -> str:
        """Адрес из discovery (получен по внутреннему адресу) → адрес для браузера."""
        back, pub = self.backchannel_issuer, self.public_issuer
        return pub + url[len(back):] if url.startswith(back) else url

    def to_backchannel(self, url: str) -> str:
        """Адрес из discovery → адрес для запроса бэкенда (Keycloak без KC_HOSTNAME отдаёт публичный)."""
        back, pub = self.backchannel_issuer, self.public_issuer
        return back + url[len(pub):] if url.startswith(pub) else url

    def accepted_issuers(self) -> frozenset[str]:
        """`iss` в id_token: публичный (KC_HOSTNAME задан) или внутренний (запрос шёл по нему)."""
        return frozenset({self.public_issuer, self.backchannel_issuer})
