"""Встроенный приёмник вебхуков ATS для демо (/api/demo/webhook-sink): показывает последние 20 событий.

Адреса внутренней сети как URL вебхука запрещены (SSRF), поэтому демо-компании указывают вымышленный публичный хост
(config/demo.yaml → company_webhook_host), а доставку на него перехватывает подменённый транспорт
(app.services.contact.webhooks.set_transport): запрос в сеть не уходит, ответ — 200. События видны двумя путями:
доставки платформы (журнал webhook_deliveries с хостом приёмника) и прямые POST на /api/demo/webhook-sink
(кольцевой буфер процесса — для внешней ATS, которой указали адрес стенда).
"""

from __future__ import annotations

import hmac
import json
import threading
from collections import deque
from collections.abc import Mapping
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.models.company import Company, WebhookDelivery
from app.services.contact import webhooks
from app.services.demo.cfg import demo_cfg, section

_LOCK = threading.Lock()
_DIRECT: deque[dict[str, Any]] = deque()


def sink_host() -> str:
    return str(demo_cfg()["company_webhook_host"])


def keep() -> int:
    return int(section("webhook_sink")["keep"])


def clear() -> None:
    with _LOCK:
        _DIRECT.clear()


def make_transport(previous: webhooks.Transport) -> webhooks.Transport:
    """Транспорт: запросы на хост приёмника «принимаются» на месте, остальные идут прежним путём."""

    def transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, str]:
        if (urlparse(url).hostname or "").lower() == sink_host().lower():
            return 200, str(section("webhook_sink")["response_body"])
        return previous(url, body, headers, timeout)

    return transport


def install() -> webhooks.Transport:
    """Подключить приёмник. Возвращает прежний транспорт — его возвращает uninstall (в тестах обязательно)."""
    previous = webhooks.set_transport(None)  # узнать текущий, не меняя
    webhooks.set_transport(previous)
    webhooks.set_transport(make_transport(previous))
    return previous


def uninstall(previous: webhooks.Transport) -> None:
    webhooks.set_transport(previous)


def record_direct(db: Session, headers: Mapping[str, str], body: bytes) -> dict[str, Any]:
    """Прямой POST на приёмник: разобрать, проверить подпись компании (если событие известно) и запомнить."""
    wc = webhooks.cfg().webhooks
    event_id = headers.get(wc.event_id_header)
    event_type = headers.get(wc.event_header)
    signature = headers.get(wc.signature_header)
    try:
        payload: Any = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        payload = None
    verified: bool | None = None
    company_name: str | None = None
    if event_id:
        row = db.scalar(select(WebhookDelivery).where(WebhookDelivery.event_id == event_id)) if _is_uuid(event_id) else None
        company = db.get(Company, row.company_id) if row is not None else None
        if company is not None:
            company_name = company.name
            if company.webhook_secret and signature:
                verified = hmac.compare_digest(signature, webhooks.sign(company.webhook_secret, body))
    entry = {
        "received_at": clock.now(),
        "source": "direct",
        "event_id": event_id,
        "event_type": event_type or (payload.get("type") if isinstance(payload, dict) else None),
        "company": company_name,
        "signature": signature,
        "signature_valid": verified,
        "attempts": 1,
        "status": "delivered",
        "payload": payload,
    }
    with _LOCK:
        _DIRECT.appendleft(entry)
        while len(_DIRECT) > keep():
            _DIRECT.pop()
    return entry


def _is_uuid(v: str) -> bool:
    import uuid

    try:
        uuid.UUID(v)
    except ValueError:
        return False
    return True


def recent(db: Session) -> list[dict[str, Any]]:
    """Последние события приёмника: доставки платформы на хост приёмника + прямые POST, новые сверху."""
    host = sink_host()
    rows = db.scalars(
        select(WebhookDelivery)
        .where(WebhookDelivery.target_url.like(f"%://{host}/%"))
        .order_by(WebhookDelivery.created_at.desc())
        .limit(keep())
    ).all()
    names = {c.id: c.name for c in db.scalars(select(Company).where(Company.id.in_({r.company_id for r in rows})))} if rows else {}
    items: list[dict[str, Any]] = []
    for r in rows:
        company = db.get(Company, r.company_id)
        sig = None
        if company is not None and company.webhook_secret:
            sig = webhooks.sign(company.webhook_secret, webhooks.body_bytes(r.payload))
        items.append(
            {
                "received_at": r.last_attempt_at or r.created_at,
                "source": "platform",
                "event_id": str(r.event_id),
                "event_type": r.event_type,
                "company": names.get(r.company_id),
                "signature": sig,
                "signature_valid": True if sig else None,  # подпись считает сама платформа тем же ключом компании
                "attempts": r.attempts,
                "status": r.status,
                "payload": r.payload,
            }
        )
    with _LOCK:
        items.extend(dict(x) for x in _DIRECT)
    items.sort(key=lambda x: _ts(x["received_at"]), reverse=True)
    return items[: keep()]


def _ts(v: Any) -> float:
    return v.timestamp() if isinstance(v, datetime) else 0.0
