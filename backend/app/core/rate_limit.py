"""Ограничение частоты запросов: токен-корзина в памяти процесса.

Ключ корзины = (область, ключ), например ("login_email", "a@b.ru") или ("login_ip", "10.0.0.1").
Корзина вмещает `capacity` токенов и равномерно пополняется до полной за `period_s`.
Для одного процесса uvicorn этого достаточно (MVP); при нескольких воркерах лимит станет
«на воркер» — для продакшена корзины переезжают в Redis/PostgreSQL (см. SECURITY в документации).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from fastapi import Request

from app.core.errors import too_many_requests
from app.core.netutil import resolve_client_ip
from app.core.settings import RateRule


@dataclass
class _Bucket:
    tokens: float
    updated: float


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    retry_after_s: float  # 0, если разрешено
    remaining: int


class RateLimiter:
    """Потокобезопасная токен-корзина. time_fn подменяется в тестах."""

    # Сколько корзин держать, прежде чем выкинуть заполненные (простаивающие) — защита памяти.
    _MAX_BUCKETS = 50_000

    def __init__(self, time_fn: Callable[[], float] = time.monotonic) -> None:
        self._time = time_fn
        self._lock = threading.Lock()
        self._buckets: dict[tuple[str, str], _Bucket] = {}
        self._rules: dict[str, RateRule] = {}

    def hit(self, scope: str, key: str, rule: RateRule, cost: float = 1.0) -> RateDecision:
        """Списать `cost` токенов. Если не хватает — отказ с временем ожидания."""
        now = self._time()
        with self._lock:
            self._rules[scope] = rule
            b = self._buckets.get((scope, key))
            if b is None:
                if len(self._buckets) >= self._MAX_BUCKETS:
                    self._prune(now)
                b = _Bucket(tokens=float(rule.capacity), updated=now)
                self._buckets[(scope, key)] = b
            else:
                b.tokens = min(float(rule.capacity), b.tokens + (now - b.updated) * rule.refill_per_s)
                b.updated = now
            if b.tokens >= cost:
                b.tokens -= cost
                return RateDecision(True, 0.0, int(b.tokens))
            return RateDecision(False, (cost - b.tokens) / rule.refill_per_s, 0)

    def check(self, scope: str, key: str, rule: RateRule, cost: float = 1.0) -> None:
        """hit() + ApiError 429 с заголовком Retry-After при отказе."""
        decision = self.hit(scope, key, rule, cost)
        if not decision.allowed:
            raise too_many_requests(decision.retry_after_s)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()

    def _prune(self, now: float) -> None:
        full = [
            k
            for k, b in self._buckets.items()
            if b.tokens + (now - b.updated) * self._rules[k[0]].refill_per_s >= self._rules[k[0]].capacity
        ]
        for k in full:
            del self._buckets[k]


def client_ip(request: Request) -> str:
    """IP клиента для лимитов и журналов. X-Forwarded-For учитывается только от доверенных прокси
    (VZ_FORWARDED_ALLOW_IPS, см. app/core/netutil.py); uvicorn запускается с --no-proxy-headers, чтобы
    правило было одно и проверялось тестами, а не зависело от флагов сервера."""
    settings = getattr(request.app.state, "settings", None)
    spec = settings.forwarded_allow_ips if settings is not None else "127.0.0.1"
    peer = request.client.host if request.client else None
    return resolve_client_ip(peer, request.headers.get("x-forwarded-for"), spec)


def get_rate_limiter(request: Request) -> RateLimiter:
    """FastAPI-зависимость: лимитер живёт в app.state (свой у каждого экземпляра приложения)."""
    return request.app.state.rate_limiter
