"""Настройки загрузки резюме из окружения VZ_RESUME_* (см. .env.example)."""

from __future__ import annotations

import re

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.settings import RateRule

_RATE_RE = re.compile(r"^\s*(\d+)\s*/\s*(\d+)\s*$")


class ResumeSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZ_RESUME_", extra="ignore")

    max_bytes: int = Field(default=5 * 1024 * 1024, gt=0)  # PDF до 5 МБ (экран К7)
    max_pages: int = Field(default=10, gt=0)  # дальше 10-й страницы не читаем
    min_text_chars: int = Field(default=40, ge=1)  # меньше — считаем сканом без текстового слоя
    rl_upload: str = "10/3600"  # загрузок резюме на аккаунт

    @field_validator("rl_upload")
    @classmethod
    def _rate_format(cls, v: str) -> str:
        if not _RATE_RE.match(v):
            raise ValueError("ожидается формат 'N/секунд', например '10/3600'")
        return v

    def rate(self, name: str) -> RateRule:
        m = _RATE_RE.match(getattr(self, f"rl_{name}"))
        assert m is not None
        return RateRule(int(m.group(1)), int(m.group(2)))
