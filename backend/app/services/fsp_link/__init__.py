"""ФСП ID: вход и привязка (OIDC + PKCE, «демо-вход ФСП»), данные реестра у кандидата (экран К14).

Контракт для других модулей:
- ``stored_profile(link, now) -> FSPProfile | None`` — профиль ФСП из сохранённого ответа реестра (без сети);
- ``get_provider()`` — реестр по настройкам VZ_FSP_PROVIDER;
- FspLink / FspAchievementCache (app/models/fsp.py) заполняются только при согласии fsp_enrichment:
  ``fsp_achievements`` — нормализованные записи (kind result|rank|role, payload по контракту app/fsp),
  ``fsp_links.achievements`` / ``athlete`` — сырой ответ реестра, ``hidden_result_ids`` — скрытые кандидатом.
"""

from app.services.fsp_link.service import (
    FLOW_COOKIE,
    FLOW_COOKIE_PATH,
    get_fsp_settings,
    get_oidc_client,
    get_provider,
    stored_profile,
)

__all__ = ["FLOW_COOKIE", "FLOW_COOKIE_PATH", "get_fsp_settings", "get_oidc_client", "get_provider", "stored_profile"]
