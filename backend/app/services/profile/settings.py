"""Лимиты запросов раздела «Профиль кандидата» и страницы сверки из окружения VZ_PROFILE_* (см. .env.example)."""

from __future__ import annotations

import re
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings import RateRule

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class ProfileSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZ_PROFILE_", extra="ignore")

    rl_pdf: str = "30/600"  # выдача PDF-профиля — на аккаунт (каждая выдача пишет снимок для сверки)
    rl_preview: str = "120/60"  # живое превью «так вас видят» — на аккаунт
    rl_search: str = "60/60"  # поиск компаний для блок-листа — на аккаунт
    rl_verify: str = "120/60"  # публичная страница сверки /v/{код} — с одного IP

    @field_validator("rl_pdf", "rl_preview", "rl_search", "rl_verify")
    @classmethod
    def _rate_format(cls, v: str) -> str:
        if not _RATE_RE.match(v):
            raise ValueError("ожидается формат 'N/секунд', например '30/600'")
        return v

    def rate(self, name: str) -> RateRule:
        m = _RATE_RE.match(getattr(self, f"rl_{name}"))
        assert m is not None
        return RateRule(int(m.group(1)), int(m.group(2)))


@lru_cache(maxsize=1)
def get_profile_settings() -> ProfileSettings:
    return ProfileSettings()
