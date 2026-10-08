"""Bitstring Status List (W3C): список отзыва для statusPurpose = revocation.

encodedList = multibase base64url ('u…') от GZIP-сжатой битовой строки; бит с номером statusListIndex = 1 — отозван.
Размер по умолчанию 131 072 бита (минимум спецификации — не даёт вычислить число выданных паспортов).
"""

from __future__ import annotations

import base64
import gzip
from collections.abc import Iterable
from datetime import datetime
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.settings import Settings
from app.credentials.builders import status_list_url
from app.credentials.config import CredentialsConfig
from app.credentials.di import iso_z, sign_document
from app.credentials.keys import issuer_did, verification_method_id


def encode_list(revoked: Iterable[int], size_bits: int) -> str:
    bits = bytearray(size_bits // 8)
    for i in revoked:
        if not 0 <= i < size_bits:
            raise ValueError("индекс статуса вне списка")
        bits[i // 8] |= 0x80 >> (i % 8)  # старший бит байта — меньший индекс (по спецификации)
    raw = gzip.compress(bytes(bits), mtime=0)
    return "u" + base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def is_revoked(encoded: str, index: int) -> bool:
    if not encoded.startswith("u"):
        raise ValueError("ожидается multibase base64url")
    body = encoded[1:]
    raw = gzip.decompress(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    if not 0 <= index < len(raw) * 8:
        raise ValueError("индекс статуса вне списка")
    return bool(raw[index // 8] & (0x80 >> (index % 8)))


def status_list_credential(
    settings: Settings, cfg: CredentialsConfig, key: Ed25519PrivateKey, revoked: Iterable[int], now: datetime
) -> dict[str, Any]:
    url = status_list_url(settings, cfg)
    doc = {
        "@context": ["https://www.w3.org/ns/credentials/v2"],
        "id": url,
        "type": ["VerifiableCredential", "BitstringStatusListCredential"],
        "issuer": issuer_did(settings),
        "validFrom": iso_z(now),
        "credentialSubject": {
            "id": f"{url}#list",
            "type": "BitstringStatusList",
            "statusPurpose": "revocation",
            "encodedList": encode_list(revoked, cfg.status_size_bits),
            "ttl": cfg.status_ttl_ms,
        },
    }
    return sign_document(doc, key, verification_method_id(settings), now)
