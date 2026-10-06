"""Сетевые мелочи: список доверенных прокси и клиентский IP за обратным прокси (Caddy).

Почему не «верим X-Forwarded-For всегда». Любой клиент может прислать этот заголовок сам: если брать из него
первый адрес, лимит входа/регистрации обходится подстановкой случайных IP. Поэтому заголовку верим только когда
TCP-сосед (request.client.host) — доверенный прокси из VZ_FORWARDED_ALLOW_IPS (то же имя, что у uvicorn), и читаем
цепочку справа налево: каждый доверенный прокси дописывает справа адрес, от которого получил запрос, первый
недоверенный адрес справа — настоящий клиент. Всё левее него подделывается клиентом и игнорируется.
"""

from __future__ import annotations

import ipaddress
from functools import lru_cache

IPNetwork = ipaddress.IPv4Network | ipaddress.IPv6Network


@lru_cache(maxsize=32)
def parse_trusted_proxies(spec: str) -> tuple[bool, tuple[IPNetwork, ...]]:
    """'127.0.0.1, 172.29.80.0/24' → (False, (сети…)); '*' → (True, ()). Ошибка формата → ValueError."""
    spec = (spec or "").strip()
    if spec == "*":
        return True, ()
    nets: list[IPNetwork] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            nets.append(ipaddress.ip_network(part, strict=False))
        except ValueError as e:
            raise ValueError(f"VZ_FORWARDED_ALLOW_IPS: «{part}» — не IP-адрес и не подсеть") from e
    return False, tuple(nets)


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def is_trusted_proxy(host: str | None, spec: str) -> bool:
    if not host:
        return False
    allow_all, nets = parse_trusted_proxies(spec)
    if allow_all:
        return True
    addr = _ip(host)
    return addr is not None and any(addr in n for n in nets if n.version == addr.version)


def resolve_client_ip(peer: str | None, forwarded_for: str | None, spec: str) -> str:
    """IP клиента по TCP-соседу и заголовку X-Forwarded-For (см. docstring модуля).

    Сосед не доверенный, заголовка нет или в цепочке мусор вместо адреса → возвращается сосед: безопасно
    ошибиться в сторону «все за прокси делят один лимит», а не «любой выбирает себе IP».
    """
    if not peer:
        return "unknown"
    if not forwarded_for or not is_trusted_proxy(peer, spec):
        return peer
    hops = [h.strip() for h in forwarded_for.split(",") if h.strip()]
    for hop in reversed(hops):
        addr = _ip(hop)
        if addr is None:
            return peer
        if not is_trusted_proxy(str(addr), spec):
            return str(addr)
    # вся цепочка — доверенные прокси (запрос из внутренней сети): клиент — самый левый адрес
    first = _ip(hops[0]) if hops else None
    return str(first) if first is not None else peer
