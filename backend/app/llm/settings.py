"""Настройки шлюза LLM из окружения VZ_LLM_* (см. .env.example). По умолчанию LLM выключена."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="VZ_LLM_", extra="ignore")

    # none — LLM не вызывается (умолчание, демо); auto — YandexGPT, при сбое GigaChat, иначе без LLM;
    # yandexgpt / gigachat — только этот провайдер.
    provider: Literal["none", "auto", "yandexgpt", "gigachat", "openai_compat"] = "none"
    # Режим шлюза (DECISIONS п. 23): none — выключена; live — провайдеры; record — провайдеры + запись ответов
    # в кассету; replay — только кассета, без сети и ключей (демо воспроизводимо). Пусто — none при
    # provider=none, иначе live.
    mode: Literal["none", "live", "record", "replay"] | None = None
    cassette_path: str | None = None  # JSON-кассета для record / replay (VZ_LLM_CASSETTE_PATH)
    # Общий бюджет времени на один разбор (все провайдеры цепочки вместе), секунд — концепция §3.1.1
    timeout_s: float = Field(default=20.0, gt=0)
    # Цитата поля засчитывается при fuzz.partial_ratio ≥ порога (поле без цитаты отбрасывается)
    min_quote_ratio: float = Field(default=90.0, ge=0, le=100)
    min_quote_len: int = Field(default=3, ge=1)
    cache_size: int = Field(default=256, ge=0)  # кэш ответов по sha256 замаскированного текста
    max_input_chars: int = Field(default=12000, gt=0)

    # YandexGPT (Foundation Models API)
    yandex_api_key: SecretStr | None = None
    yandex_folder_id: str | None = None
    yandex_model: str = "yandexgpt-lite/latest"
    yandex_url: str = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

    # GigaChat (OAuth-ключ авторизации из личного кабинета)
    gigachat_auth_key: SecretStr | None = None
    gigachat_scope: str = "GIGACHAT_API_PERS"
    gigachat_model: str = "GigaChat"
    gigachat_oauth_url: str = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    gigachat_url: str = "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    # TLS у GigaChat: bundled — корневой сертификат НУЦ Минцифры из поставки (app/llm/certs); путь к PEM;
    # true — системное хранилище; false — без проверки (только разработка)
    gigachat_verify: str = "bundled"

    # Любой OpenAI-совместимый /chat/completions: открытые модели (Qwen, Llama, DeepSeek, gpt-oss) у облачных
    # хостингов (OpenRouter, Together, Fireworks, Nebius), собственный vLLM/Ollama, а также зарубежные API.
    # ВАЖНО (152-ФЗ, ст. 12): зарубежный адрес получает только задачи из openai_tasks. По умолчанию это разбор текста
    # брифа работодателя — в нём нет ПДн кандидатов. Резюме («resume») уходит на зарубежный адрес лишь по явному
    # решению оператора (VZ_LLM_OPENAI_TASKS=brief_skills,resume) и после маскирования ПДн.
    openai_base_url: str | None = None  # например https://openrouter.ai/api/v1
    openai_api_key: SecretStr | None = None
    openai_model: str = "qwen/qwen3-235b-a22b-2507"
    openai_tasks: str = "brief_skills"

    @property
    def openai_task_set(self) -> frozenset[str]:
        return frozenset(t.strip() for t in self.openai_tasks.split(",") if t.strip())

    @property
    def openai_configured(self) -> bool:
        return bool(self.openai_base_url and self.openai_api_key)

    @property
    def effective_mode(self) -> str:
        if self.mode is not None:
            return self.mode
        return "none" if self.provider == "none" else "live"

    @property
    def yandex_configured(self) -> bool:
        return bool(self.yandex_api_key and self.yandex_folder_id)

    @property
    def gigachat_configured(self) -> bool:
        return bool(self.gigachat_auth_key)
