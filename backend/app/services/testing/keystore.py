"""Хранение ключей экземпляров: ключ шифруется (Fernet, AES-128-CBC + HMAC), в БД лежит только шифртекст.

Ключ выдаётся наружу лишь в двух местах: разбор ошибок кандидату ПОСЛЕ решения по его собственному
экземпляру и проверка ответа на сервере. Ключ шифрования выводится из VZ_APP_SECRET (HKDF-подобно,
с отдельной меткой), поэтому утечка дампа БД без секрета не раскрывает ключи пула.
"""

from __future__ import annotations

import base64
import hashlib
import json
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from app.core.settings import get_settings

_LABEL = b"vzaimno/item-instance-keys/v1"


@lru_cache(maxsize=4)
def _fernet(secret: str) -> Fernet:
    digest = hashlib.sha256(_LABEL + b"\x00" + secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def _cipher() -> Fernet:
    return _fernet(get_settings().app_secret.get_secret_value())


def encrypt_key(key: Any) -> str:
    """Ключ (str | dict | list) → шифртекст. JSON сохраняет тип ключа (строка «3» ≠ число 3)."""
    payload = json.dumps(key, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return _cipher().encrypt(payload.encode("utf-8")).decode("ascii")


def decrypt_key(token: str) -> Any:
    try:
        raw = _cipher().decrypt(token.encode("ascii"))
    except InvalidToken as e:  # другой VZ_APP_SECRET, чем при генерации пула
        raise RuntimeError("Не удалось расшифровать ключ экземпляра: пул создан с другим секретом") from e
    return json.loads(raw.decode("utf-8"))
