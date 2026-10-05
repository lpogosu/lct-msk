"""Исходящие вебхуки ATS [доп.] (концепция §8.2): подписанное событие при смене статуса приглашения и отклика.

- Подпись: заголовок `X-Vzaimno-Signature: sha256=<hex HMAC-SHA256(секрет компании, тело)>`.
- Идемпотентность: event_id = uuid5 от «компания + {invitation_id}-{seq}» — повтор события не создаёт новую
  доставку, а приёмник может отбрасывать дубли по заголовку `X-Vzaimno-Event-Id`.
- Повторы: 30 с, 60 с, 120 с, … (config/contact.yaml → webhooks), не больше max_attempts.
- Контакты кандидата в событии — только при действующем согласии (собирает вызывающий код).
- Журнал: последние 20 доставок с кодом ответа (экран Р1).
- SSRF: адрес ATS не может вести во внутреннюю сеть платформы (БД, почта, метаданные облака) — иначе
  работодатель читал бы ответы внутренних сервисов в журнале доставок. Проверка при сохранении URL
  (validate_url) и перед каждой отправкой по всем адресам, в которые резолвится имя (http_transport).
  Остаточный риск — смена DNS-ответа между проверкой и соединением (TOCTOU); для MVP принят.

Доставка выполняется после фиксации транзакции запроса (deliver_due); неуспешные повторяются при следующих
событиях компании и при открытии журнала. Встроенный приёмник /demo/webhook-sink — в разделе демо.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import re
import secrets
import socket
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company, WebhookDelivery
from app.models.enums import WebhookStatus

from .common import cfg, now

_NAMESPACE = uuid.UUID("6f1c7d2e-8a51-4c1e-9d0b-5a4e3f2b1c0d")

# (url, тело, заголовки, таймаут) → (HTTP-код, начало ответа). Подменяется в тестах (set_transport).
Transport = Callable[[str, bytes, Mapping[str, str], float], tuple[int, str]]

INTERNAL_HOST_MESSAGE = "Адрес вебхука указывает во внутреннюю сеть — укажите публичный адрес вашей ATS"
_LEGACY_IPV4 = re.compile(r"^[0-9a-fx.]+$")  # 2130706433, 0x7f.1, 017700000001 — тоже адреса


class WebhookBlocked(Exception):
    """Доставка запрещена: имя резолвится во внутренний адрес (SSRF)."""


def _ip_internal(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return not ip.is_global or ip.is_multicast or ip.is_reserved or ip.is_unspecified


def _literal_ip(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """IP-адрес, записанный в URL, в т. ч. «старыми» формами IPv4 (целое число, hex, octal)."""
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    if _LEGACY_IPV4.match(host):
        try:
            return ipaddress.IPv4Address(socket.inet_aton(host))
        except OSError:
            return None
    return None


def host_problem(host: str | None) -> str | None:
    """Причина запрета хоста (SSRF) или None. Без обращения к DNS: проверка при сохранении детерминирована."""
    wc = cfg().webhooks
    if wc.allow_private_hosts:
        return None
    h = (host or "").strip().lower().rstrip(".")
    if not h:
        return INTERNAL_HOST_MESSAGE
    ip = _literal_ip(h)
    if ip is not None:
        return INTERNAL_HOST_MESSAGE if _ip_internal(ip) else None
    # Имя без точки — сервис docker или машина интранета (db, mailpit, backend); служебные зоны
    if "." not in h or any(h == s or h.endswith("." + s) for s in wc.blocked_host_suffixes):
        return INTERNAL_HOST_MESSAGE
    return None


def _check_resolved(host: str, port: int) -> None:
    """Перед соединением: все адреса, в которые резолвится имя, должны быть публичными."""
    if cfg().webhooks.allow_private_hosts:
        return
    if host_problem(host):
        raise WebhookBlocked(INTERNAL_HOST_MESSAGE)
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(str(addr).split("%", 1)[0])
        except ValueError:
            raise WebhookBlocked(INTERNAL_HOST_MESSAGE) from None
        if _ip_internal(ip):
            raise WebhookBlocked(INTERNAL_HOST_MESSAGE)


def http_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> tuple[int, str]:
    p = urlparse(url)
    _check_resolved(p.hostname or "", p.port or (443 if p.scheme == "https" else 80))
    r = httpx.post(url, content=body, headers=dict(headers), timeout=timeout, follow_redirects=False)
    return r.status_code, r.text[:500]


_transport: Transport = http_transport


def set_transport(transport: Transport | None) -> Transport:
    """Подменить отправку (тесты, демо). None — вернуть HTTP. Возвращает прежнюю."""
    global _transport
    prev = _transport
    _transport = transport or http_transport
    return prev


def new_secret() -> str:
    return secrets.token_urlsafe(cfg().webhooks.secret_bytes)


def validate_url(url: str) -> str | None:
    """Причина отказа или None. Только http(s) с хостом; учётные данные в URL запрещены."""
    try:
        p = urlparse(url)
    except ValueError:
        return "Некорректный адрес вебхука"
    if p.scheme not in cfg().webhooks.allowed_schemes or not p.hostname:
        return "Адрес вебхука должен начинаться с http:// или https:// и содержать хост"
    if p.username or p.password:
        return "Не указывайте логин и пароль в адресе вебхука — запросы подписываются секретом"
    try:
        p.port  # noqa: B018 — порт вне 0–65535 → ValueError
    except ValueError:
        return "Некорректный порт в адресе вебхука"
    return host_problem(p.hostname)


def event_uuid(company_id: uuid.UUID, event_key: str) -> uuid.UUID:
    return uuid.uuid5(_NAMESPACE, f"{company_id}:{event_key}")


def body_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()


def enqueue(
    db: Session,
    company: Company,
    event_type: str,
    data: Mapping[str, Any],
    event_key: str,
    at: datetime | None = None,
) -> WebhookDelivery | None:
    """Поставить событие в очередь доставки (если у компании задан URL). Повтор того же event_key — та же запись."""
    if not company.webhook_url:
        return None
    at = at or now()
    event_id = event_uuid(company.id, event_key)
    existing = db.scalar(select(WebhookDelivery).where(WebhookDelivery.event_id == event_id))
    if existing is not None:
        return existing
    payload = {
        "event_id": str(event_id),
        "type": event_type,
        "created_at": at.isoformat(),
        "data": dict(data),
    }
    row = WebhookDelivery(
        company_id=company.id,
        event_id=event_id,
        event_type=event_type,
        target_url=company.webhook_url,
        payload=payload,
        status=WebhookStatus.pending.value,
        attempts=0,
        next_attempt_at=at,
        created_at=at,
    )
    db.add(row)
    return row


def _attempt(row: WebhookDelivery, company: Company | None, at: datetime) -> bool:
    """Одна попытка доставки. False — приёмник недоступен (сеть/таймаут/5xx): остальное ему в этом проходе не шлём."""
    wc = cfg().webhooks
    body = body_bytes(row.payload)
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        wc.event_header: row.event_type,
        wc.event_id_header: str(row.event_id),
    }
    if company is not None and company.webhook_secret:
        headers[wc.signature_header] = sign(company.webhook_secret, body)
    code: int | None
    blocked = False
    try:
        code, excerpt = _transport(row.target_url, body, headers, float(wc.timeout_s))
    except WebhookBlocked as e:
        code, excerpt, blocked = None, str(e), True
    except Exception as e:  # noqa: BLE001 — любая сетевая ошибка = неудачная попытка, а не 500 у пользователя
        code, excerpt = None, f"Ошибка соединения: {type(e).__name__}"
    row.attempts = (row.attempts or 0) + 1
    row.last_attempt_at = at
    row.response_code = code
    row.response_excerpt = (excerpt or "")[:500] or None
    if code is not None and 200 <= code < 300:
        row.status = WebhookStatus.delivered.value
        row.next_attempt_at = None
        return True
    if blocked or row.attempts >= wc.max_attempts:
        row.status = WebhookStatus.failed.value
        row.next_attempt_at = None
    else:
        delay = wc.backoff_base_s * (wc.backoff_factor ** (row.attempts - 1))
        row.status = WebhookStatus.pending.value
        row.next_attempt_at = at + timedelta(seconds=delay)
    # 4xx — приёмник жив, но событие не принял; сеть, таймаут и 5xx — приёмник лежит
    return code is not None and code < 500


def deliver_due(db: Session, company_id: uuid.UUID | None = None, *, limit: int = 20) -> int:
    """Отправить созревшие доставки (новые и повторы по расписанию) в порядке событий. Возвращает число попыток.

    Если приёмник не отвечает, остальные события ему в этом проходе не шлём (они ждут следующего повтора):
    действие пользователя не должно висеть по таймауту на каждое событие.
    """
    at = now()
    q = select(WebhookDelivery).where(
        WebhookDelivery.status == WebhookStatus.pending.value,
        WebhookDelivery.next_attempt_at <= at,
    )
    if company_id is not None:
        q = q.where(WebhookDelivery.company_id == company_id)
    rows = list(db.scalars(q.order_by(WebhookDelivery.created_at, WebhookDelivery.event_id).limit(limit)))
    companies: dict[uuid.UUID, Company | None] = {}
    down: set[str] = set()
    attempts = 0
    for row in rows:
        if row.target_url in down:
            continue
        if row.company_id not in companies:
            companies[row.company_id] = db.get(Company, row.company_id)
        attempts += 1
        if not _attempt(row, companies[row.company_id], at):
            down.add(row.target_url)
    return attempts


def send_test(db: Session, company: Company) -> WebhookDelivery | None:
    """Тестовое событие «ping» (кнопка в Р1): доставляется сразу, в журнал попадает как обычное."""
    at = now()
    row = enqueue(
        db,
        company,
        "ping",
        {"message": "Проверка вебхука «Взаимно»", "company_id": str(company.id)},
        f"ping:{uuid.uuid4()}",
        at,
    )
    if row is None:
        return None
    db.flush()
    _attempt(row, company, at)
    return row


def deliveries_log(db: Session, company_id: uuid.UUID) -> list[WebhookDelivery]:
    return list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.company_id == company_id)
            .order_by(WebhookDelivery.created_at.desc(), WebhookDelivery.id)
            .limit(cfg().webhooks.log_size)
        )
    )
