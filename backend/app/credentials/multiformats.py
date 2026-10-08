"""base58btc, multibase ('z…') и Multikey для Ed25519 (префикс мультикодека 0xED 0x01)."""

from __future__ import annotations

_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_INDEX = {c: i for i, c in enumerate(_ALPHABET)}
ED25519_PUB_PREFIX = b"\xed\x01"


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n:
        n, r = divmod(n, 58)
        out = _ALPHABET[r] + out
    pad = len(data) - len(data.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        if ch not in _INDEX:
            raise ValueError("недопустимый символ base58")
        n = n * 58 + _INDEX[ch]
    body = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    pad = len(text) - len(text.lstrip("1"))
    return b"\x00" * pad + body


def multibase_b58_encode(data: bytes) -> str:
    return "z" + b58encode(data)


def multibase_b58_decode(text: str) -> bytes:
    if not text.startswith("z"):
        raise ValueError("ожидается multibase base58btc ('z…')")
    return b58decode(text[1:])


def public_key_multibase(raw_public: bytes) -> str:
    return multibase_b58_encode(ED25519_PUB_PREFIX + raw_public)


def raw_public_from_multibase(text: str) -> bytes:
    data = multibase_b58_decode(text)
    if not data.startswith(ED25519_PUB_PREFIX) or len(data) != 34:
        raise ValueError("это не Ed25519 Multikey")
    return data[2:]
