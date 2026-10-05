"""Сервисный слой механики контакта: приглашения, вакансии и отклики, профиль компании, вебхуки ATS.

Связывает чистые движки app/contact/* с БД и HTTP. Правила (таблица переходов, guards, сроки, квоты, паузы,
видимость) живут в движке и config/contact.yaml — здесь только перенос состояния и сборка DTO.
Источники: 01-concept.md §3.1 шаги 11–14, §3.2 шаги 2, 8–12, §4.3, §4.5, §8; spec-contact; DECISIONS 3, 6–9,
16, 18–20, 22.

Контракты для других модулей (импортируйте отсюда):

- ``withdraw_pending_for_need(ctx, need_id, actor_id)`` — бриф закрыт → ждущие ответа приглашения
  отзываются «позиция закрыта» (spec-contact §2.3). ``Ctx(db=…)`` — контекст запроса.
- ``sweep(db)`` — фоновый проход (DEMO_MODE «+1 день», периодическая задача): ленивые сроки приглашений
  и откликов, напоминания, повторы вебхуков. Сроки и без него применяются при каждом чтении.
- ``company_reliability(db, company_id, at)`` / ``reliability_dict`` — надёжность R с разложением.
- ``notify_user`` / ``notify_company`` — из app.services.notify.
- ``webhooks.set_transport(fn)`` — встроенный приёмник демо (/demo/webhook-sink) подключается подменой
  транспорта: адреса внутренней сети (localhost, docker-имена, частные сети) как URL вебхука запрещены (SSRF;
  config/contact.yaml → webhooks.allow_private_hosts — только для локальной разработки).

Импорт пакета регистрирует обработчик отзыва согласия contact_disclosure (app.core.consents): приглашение
переходит в consent_revoked, отклик — в «Отозван» (концепция §3.1 п. 10).
"""

from __future__ import annotations

from . import hooks as _hooks  # noqa: F401 — регистрация обработчика отзыва согласия
from .invitations import withdraw_pending_for_need
from .reliability import company_reliability, reliability_dict
from .store import Ctx
from .sweep import sweep

__all__ = ["Ctx", "company_reliability", "reliability_dict", "sweep", "withdraw_pending_for_need"]
