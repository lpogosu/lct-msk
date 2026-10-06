"""Настройки приложения из переменных окружения VZ_* (pydantic-settings).

Здесь — инфраструктура и безопасность (БД, секреты, SMTP, TTL токенов, лимиты запросов).
Продуктовые веса, пороги и квоты живут в config/*.yaml (см. app/core/configs.py).
Полный список переменных с пояснениями — в .env.example в корне репозитория.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")

# Значения по умолчанию для разработки. В VZ_ENV=prod они запрещены (см. _check_prod_secrets).
_DEV_SECRETS = {
    "jwt_secret": "dev-insecure-jwt-secret-change-me",
    "app_secret": "dev-insecure-app-secret-change-me",
    "pseudonym_secret": "dev-insecure-pseudonym-secret-change-me",
}
_MIN_SECRET_LEN = 32


@dataclass(frozen=True)
class RateRule:
    """Правило токен-корзины: не больше `capacity` запросов за `period_s` секунд (пополнение равномерное)."""

    capacity: int
    period_s: int

    @property
    def refill_per_s(self) -> float:
        return self.capacity / self.period_s


def parse_rate(spec: str) -> RateRule:
    """'5/300' → RateRule(5, 300) (то же правило формата, что у VZ_RL_*; для лимитов из YAML-конфигов)."""
    m = _RATE_RE.match(spec)
    if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
        raise ValueError("ожидается формат 'N/секунд', например '5/300'")
    return RateRule(int(m.group(1)), int(m.group(2)))


def _split_csv(value: object) -> object:
    """'a, b' → ['a', 'b']; JSON-массив тоже принимаем (для совместимости)."""
    if isinstance(value, str):
        v = value.strip()
        if v.startswith("["):
            import json

            return json.loads(v)
        return [x.strip() for x in v.split(",") if x.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZ_", env_file=".env", extra="ignore", populate_by_name=True)

    # --- Общее ---
    env: Literal["dev", "test", "prod"] = "dev"
    app_name: str = "Взаимно"
    version: str = "0.1.0"
    log_level: str = "INFO"
    demo_mode: bool = False
    # Кто может «глобально» управлять демо (часы, сброс мира, автоответчик, переключатель «режима жюри»), когда
    # DEMO_MODE включён. None — по среде: в VZ_ENV=prod (публичный стенд) только роль «администратор» или заголовок
    # X-Demo-Key; вне prod (локальный стенд жюри) — любой вошедший. Подсказка кандидату в режиме жюри — всегда владельцу сессии.
    demo_controls_admin_only: bool | None = None
    demo_control_key: SecretStr | None = None  # ключ для заголовка X-Demo-Key (curl/скрипты жюри); пусто — только админ
    # Встроенный планировщик (app/services/scheduler, config/scheduler.yaml): None — как в конфиге, False — выключить
    # (например, когда те же задачи гоняет внешний cron), True — включить, даже если в конфиге выключен.
    scheduler_enabled: bool | None = None

    # --- База данных ---
    database_url: str = "postgresql+psycopg://vzaimno:vzaimno@localhost:5432/vzaimno"
    db_echo: bool = False
    db_pool_size: int = 10
    db_max_overflow: int = 10

    # --- Адреса ---
    public_base_url: str = "http://localhost:5173"  # фронтенд; используется в ссылках из писем
    cors_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:5173"])

    # --- Секреты ---
    jwt_secret: SecretStr = SecretStr(_DEV_SECRETS["jwt_secret"])
    app_secret: SecretStr = SecretStr(_DEV_SECRETS["app_secret"])  # HMAC кодов подтверждения и т.п.
    pseudonym_secret: SecretStr = SecretStr(_DEV_SECRETS["pseudonym_secret"])  # «К-4821», концепция §4.5

    # --- JWT и сессии (концепция §6.5: своя аутентификация) ---
    jwt_algorithm: Literal["HS256", "HS384", "HS512"] = "HS256"
    jwt_issuer: str = "vzaimno"
    access_token_ttl_s: int = 15 * 60
    refresh_token_ttl_s: int = 30 * 24 * 3600
    # Повтор ротированного refresh в пределах окна — гонка двух вкладок, а не кража (409, семья живёт)
    refresh_reuse_grace_s: int = 10
    refresh_cookie_name: str = "vz_refresh"
    refresh_cookie_path: str = "/api/auth"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cookie_domain: str | None = None

    # --- Обратный прокси (Caddy) и клиентский IP для лимитов запросов ---
    # Заголовку X-Forwarded-For верим только от этих адресов/подсетей (через запятую) — как у uvicorn
    # (переменная FORWARDED_ALLOW_IPS читается и без префикса VZ_). Иначе любой клиент подставил бы
    # чужой IP и обошёл лимит входа, а за прокси без настройки все пользователи делили бы один лимит.
    # "*" — доверять любому соседу (только dev/test: в prod это подмена IP для всех, кто достучится до порта).
    forwarded_allow_ips: str = Field(
        default="127.0.0.1",
        validation_alias=AliasChoices("VZ_FORWARDED_ALLOW_IPS", "FORWARDED_ALLOW_IPS", "forwarded_allow_ips"),
    )

    # --- Пароли (argon2id) ---
    password_min_length: int = 8
    password_max_length: int = 128
    argon2_time_cost: int = 3
    argon2_memory_kib: int = 65536
    argon2_parallelism: int = 4
    # Одновременных хеширований на процесс (каждое — argon2_memory_kib памяти): защита от OOM при пачке входов
    argon2_max_concurrent: int = Field(default=4, ge=1)

    # --- Код подтверждения email (концепция §3.1, шаги 1–2) ---
    email_code_ttl_s: int = 15 * 60
    email_code_resend_cooldown_s: int = 60
    email_code_max_sends_per_hour: int = 5
    email_code_max_attempts: int = 5  # неверных вводов на один код, затем код сгорает

    # --- Ограничение частоты запросов: "N/секунд" ---
    rl_login_email: str = "5/300"
    rl_login_ip: str = "30/300"
    rl_verify_email: str = "10/900"
    rl_register_ip: str = "10/3600"
    rl_resend_ip: str = "20/3600"
    rl_refresh_ip: str = "60/60"

    # --- Почта ---
    mail_backend: Literal["smtp", "console"] = "smtp"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    smtp_security: Literal["none", "starttls", "ssl"] = "none"
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_timeout_s: float = 10.0
    mail_from: str = "Взаимно <no-reply@vzaimno.local>"

    # Личные почтовые домены (DECISIONS п. 8) и лимит приглашений кандидата по умолчанию — продуктовые
    # правила: живут в config/contact.yaml (см. app/core/domains.py, app/models/candidate.py), не здесь.

    # --- ФСП ID и реестр (используются модулем app/fsp) ---
    fsp_oidc_issuer: str = "http://localhost:8080/realms/fsp"  # как его видит браузер
    fsp_oidc_internal_url: str | None = None  # как его видит бэкенд внутри compose (JWKS, token)
    fsp_oidc_client_id: str = "vzaimno"
    fsp_oidc_client_secret: SecretStr | None = None
    fsp_oidc_redirect_uri: str = "http://localhost:5173/candidate/fsp/callback"  # страница из routes.tsx, см. app/api/fsp.py
    fsp_registry_url: str = "http://localhost:8001"
    fsp_demo_login_enabled: bool = True

    # --- Каталог YAML-конфигов (читается в app/core/configs.py напрямую из окружения) ---
    config_dir: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _csv(cls, v: object) -> object:
        return _split_csv(v)

    @field_validator("demo_control_key", mode="before")
    @classmethod
    def _empty_key_is_none(cls, v: object) -> object:
        """Пустая переменная в compose (VZ_DEMO_CONTROL_KEY=) — «ключа нет», а не ключ из пустой строки."""
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("forwarded_allow_ips")
    @classmethod
    def _forwarded_format(cls, v: str) -> str:
        from app.core.netutil import parse_trusted_proxies

        parse_trusted_proxies(v)  # ValueError → понятная ошибка запуска
        return v.strip()

    @field_validator("rl_login_email", "rl_login_ip", "rl_verify_email", "rl_register_ip", "rl_resend_ip", "rl_refresh_ip")
    @classmethod
    def _rate_format(cls, v: str) -> str:
        m = _RATE_RE.match(v)
        if not m or int(m.group(1)) <= 0 or int(m.group(2)) <= 0:
            raise ValueError("ожидается формат 'N/секунд', например '5/300'")
        return v

    @model_validator(mode="after")
    def _check_prod_secrets(self) -> Settings:
        if self.env == "prod":
            for name, dev_value in _DEV_SECRETS.items():
                value = getattr(self, name).get_secret_value()
                if value == dev_value:
                    raise ValueError(f"VZ_{name.upper()} обязательно задать в prod")
                # HS256 и HMAC кодов: короткий секрет перебирается офлайн по любому выданному токену
                if len(value) < _MIN_SECRET_LEN:
                    raise ValueError(f"VZ_{name.upper()} в prod — не короче {_MIN_SECRET_LEN} символов")
            if not self.cookie_secure:
                raise ValueError("В prod refresh-cookie должна быть Secure (VZ_COOKIE_SECURE=true)")
            if self.forwarded_allow_ips.strip() == "*":
                raise ValueError(
                    "В prod VZ_FORWARDED_ALLOW_IPS не может быть «*»: укажите адреса или подсеть обратного прокси"
                )
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("SameSite=None требует Secure-cookie")
        return self

    @property
    def demo_controls_restricted(self) -> bool:
        """Глобальные демо-кнопки закрыты для не-админов: явная настройка, а по умолчанию — в prod."""
        return self.env == "prod" if self.demo_controls_admin_only is None else self.demo_controls_admin_only

    def rate(self, name: str) -> RateRule:
        """settings.rate("login_email") → RateRule(5, 300)."""
        m = _RATE_RE.match(getattr(self, f"rl_{name}"))
        assert m is not None  # формат проверен валидатором
        return RateRule(int(m.group(1)), int(m.group(2)))


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
