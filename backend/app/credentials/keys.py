"""Ключ издателя (Ed25519) и идентификатор did:web.

Приватный ключ НИКОГДА не лежит в git: VZ_CREDENTIALS_KEY_PEM (PEM PKCS#8) либо файл VZ_CREDENTIALS_KEY_FILE.
dev: если ничего не задано — генерируется при первом запуске и пишется в backend/var/ (каталог вне git).
test: эфемерный ключ на процесс. prod: без ключа выдача и проверка не работают (явная ошибка), ключ не подставляется.
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from app.core.settings import Settings
from app.credentials.multiformats import public_key_multibase

DEV_KEY_PATH = Path(__file__).resolve().parents[2] / "var" / "credentials-ed25519.pem"
KEY_FRAGMENT = "key-1"
_lock = threading.Lock()
_cache: dict[tuple[str, str], Ed25519PrivateKey] = {}


class SigningKeyUnavailable(RuntimeError):
    """Ключ издателя не настроен (prod без VZ_CREDENTIALS_KEY_*)."""


def _load_pem(pem: bytes) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SigningKeyUnavailable("Ключ издателя должен быть Ed25519")
    return key


def _save_pem(key: Ed25519PrivateKey, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(pem)


def signing_key(settings: Settings) -> Ed25519PrivateKey:
    pem = settings.credentials_key_pem.get_secret_value() if settings.credentials_key_pem else ""
    if pem:
        return _load_pem(pem.replace("\\n", "\n").encode())
    ck = (settings.env, str(settings.credentials_key_file or ""))
    with _lock:
        if ck in _cache:
            return _cache[ck]
        if settings.credentials_key_file:
            p = Path(settings.credentials_key_file)
            if not p.is_file():
                raise SigningKeyUnavailable(f"Файл ключа издателя не найден: {p}")
            key = _load_pem(p.read_bytes())
        elif settings.env == "test":
            key = Ed25519PrivateKey.generate()
        elif settings.env == "dev":
            if DEV_KEY_PATH.is_file():
                key = _load_pem(DEV_KEY_PATH.read_bytes())
            else:
                key = Ed25519PrivateKey.generate()
                try:
                    _save_pem(key, DEV_KEY_PATH)
                except FileExistsError:
                    key = _load_pem(DEV_KEY_PATH.read_bytes())
        else:
            raise SigningKeyUnavailable("Ключ издателя не задан: укажите VZ_CREDENTIALS_KEY_PEM или VZ_CREDENTIALS_KEY_FILE")
        _cache[ck] = key
        return key


def public_key(settings: Settings) -> Ed25519PublicKey:
    return signing_key(settings).public_key()


def raw_public_bytes(key: Ed25519PublicKey) -> bytes:
    return key.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def issuer_domain(settings: Settings) -> str:
    """Домен платформы для did:web: порт кодируется как %3A (спецификация did:web)."""
    host = settings.credentials_did_domain or urlparse(settings.public_base_url).netloc
    return host.replace(":", "%3A")


def issuer_did(settings: Settings) -> str:
    return f"did:web:{issuer_domain(settings)}"


def verification_method_id(settings: Settings) -> str:
    return f"{issuer_did(settings)}#{KEY_FRAGMENT}"


def did_document(settings: Settings) -> dict[str, object]:
    did = issuer_did(settings)
    vm = f"{did}#{KEY_FRAGMENT}"
    return {
        "@context": ["https://www.w3.org/ns/did/v1", "https://w3id.org/security/multikey/v1"],
        "id": did,
        "verificationMethod": [
            {
                "id": vm,
                "type": "Multikey",
                "controller": did,
                "publicKeyMultibase": public_key_multibase(raw_public_bytes(public_key(settings))),
            }
        ],
        "authentication": [vm],
        "assertionMethod": [vm],
    }
