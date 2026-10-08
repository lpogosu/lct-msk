"""Конфиг паспорта навыков (config/credentials.yaml): сроки, лимиты, минимизация полей ФСП."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from app.core.configs import load_config


@dataclass(frozen=True)
class CredentialsConfig:
    raw: dict[str, Any]

    @property
    def issuer_name(self) -> str:
        return str(self.raw["issuer"]["name"])

    @property
    def issuer_description(self) -> str:
        return str(self.raw["issuer"]["description"])

    @property
    def disclaimer(self) -> str:
        return str(self.raw["issuer"]["disclaimer"])

    @property
    def validity_days(self) -> int:
        return int(self.raw["validity_days"])

    @property
    def max_active(self) -> int:
        return int(self.raw["max_active_per_user"])

    @property
    def status_size_bits(self) -> int:
        return int(self.raw["status_list"]["size_bits"])

    @property
    def status_list_id(self) -> str:
        return str(self.raw["status_list"]["id"])

    @property
    def status_ttl_ms(self) -> int:
        return int(self.raw["status_list"]["ttl_ms"])

    @property
    def code_prefix(self) -> str:
        return str(self.raw["verify"]["code_prefix"])

    @property
    def max_body_bytes(self) -> int:
        return int(self.raw["verify"]["max_body_bytes"])

    @property
    def fsp_allowed_fields(self) -> tuple[str, ...]:
        return tuple(self.raw["fsp"]["allowed_fields"])


@lru_cache(maxsize=1)
def get_credentials_config() -> CredentialsConfig:
    return CredentialsConfig(load_config("credentials"))
