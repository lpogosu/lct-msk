"""ФСП ID: OpenID Connect, authorization code + PKCE (S256) против Keycloak realm `fsp` (концепция §6.5).

Браузер ↔ Keycloak — по публичному адресу issuer (http://localhost:8080/realms/fsp), бэкенд ↔ Keycloak —
по внутреннему (http://keycloak:8080/realms/fsp внутри compose): discovery, обмен кода на токен и JWKS.
Адреса из discovery переводятся FSPSettings.to_public / to_backchannel, поэтому один и тот же код работает
и с локальным Keycloak, и с sso.fsp-russia.ru (там оба адреса совпадают).

Проверка id_token: подпись по JWKS (RS*/PS*/ES*, «none» и HS* запрещены), aud = наш client, iss — публичный
или внутренний адрес realm, nonce из начатого входа, срок действия. При неизвестном kid JWKS
перечитывается один раз (ротация ключей Keycloak).

«Демо-вход ФСП» (Keycloak не поднят): платформа сама выпускает мок-токен с теми же claims, подписанный
HMAC-ключом, производным от VZ_APP_SECRET. Его issuer — urn:vzaimno:demo-fsp-id: настоящим id_token он не
притворяется, а привязка помечается is_demo (в UI — «Демо-вход ФСП»).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

from app.fsp.settings import FSPSettings

log = logging.getLogger("vzaimno.fsp.oidc")

DEMO_ISSUER = "urn:vzaimno:demo-fsp-id"
_ALLOWED_ALGS = frozenset({"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512"})
_LEEWAY_S = 30


class OIDCError(Exception):
    """Ошибка входа через ФСП ID: машинный код + русское сообщение для человека."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OIDCUnavailable(OIDCError):
    """Keycloak (ФСП ID) не отвечает — можно предложить «демо-вход ФСП»."""


# --------------------------------------------------------------------------- PKCE, state, nonce


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def pkce_pair() -> tuple[str, str]:
    """(code_verifier, code_challenge S256). RFC 7636: verifier 43–128 символов [A-Za-z0-9-._~]."""
    verifier = b64url(secrets.token_bytes(64))  # 86 символов
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return verifier, challenge


def new_state() -> str:
    return b64url(secrets.token_bytes(32))


def new_nonce() -> str:
    return b64url(secrets.token_bytes(24))


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- клиент


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str  # для браузера (публичный адрес)
    token_endpoint: str  # для бэкенда (внутренний адрес)
    jwks_uri: str  # для бэкенда


class OIDCClient:
    """Клиент ФСП ID. http — подменяемый httpx.Client (в тестах MockTransport)."""

    def __init__(
        self,
        settings: FSPSettings,
        http: httpx.Client | None = None,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.settings = settings
        self._http = http or httpx.Client(timeout=settings.oidc_timeout_s, follow_redirects=False)
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._discovery: Discovery | None = None
        self._discovery_at = 0.0
        self._down_until = 0.0
        self._jwks: dict[str, Any] | None = None

    # --- discovery ---

    def _get_json(self, url: str) -> dict[str, Any]:
        try:
            r = self._http.get(url, timeout=self.settings.oidc_timeout_s)
        except httpx.HTTPError as e:
            raise OIDCUnavailable("fsp_id_unavailable", "ФСП ID сейчас недоступен. Попробуйте позже или воспользуйтесь демо-входом") from e
        if r.status_code >= 500:
            raise OIDCUnavailable("fsp_id_unavailable", "ФСП ID сейчас недоступен. Попробуйте позже или воспользуйтесь демо-входом")
        if r.status_code != 200:
            raise OIDCError("fsp_id_misconfigured", "ФСП ID ответил ошибкой. Сообщите в поддержку")
        try:
            data = r.json()
        except ValueError as e:
            raise OIDCError("fsp_id_misconfigured", "ФСП ID ответил не в формате OpenID Connect") from e
        if not isinstance(data, dict):
            raise OIDCError("fsp_id_misconfigured", "ФСП ID ответил не в формате OpenID Connect")
        return data

    def discovery(self) -> Discovery:
        """/.well-known/openid-configuration по внутреннему адресу realm; кэш на discovery_cache_s."""
        s = self.settings
        with self._lock:
            if self._discovery is not None and self._monotonic() - self._discovery_at < s.discovery_cache_s:
                return self._discovery
        data = self._get_json(f"{s.backchannel_issuer}/.well-known/openid-configuration")
        try:
            disc = Discovery(
                issuer=str(data["issuer"]).rstrip("/"),
                authorization_endpoint=s.to_public(str(data["authorization_endpoint"])),
                token_endpoint=s.to_backchannel(str(data["token_endpoint"])),
                jwks_uri=s.to_backchannel(str(data["jwks_uri"])),
            )
        except KeyError as e:
            raise OIDCError("fsp_id_misconfigured", "В настройках ФСП ID нет нужных адресов") from e
        with self._lock:
            self._discovery, self._discovery_at = disc, self._monotonic()
            self._down_until = 0.0  # ФСП ID снова отвечает
        return disc

    def available(self) -> bool:
        """Отвечает ли ФСП ID. Отказ запоминается на unavailable_cache_s: публичная проверка не ждёт таймаут
        Keycloak на каждом запросе и не превращается в способ нагружать его через платформу."""
        with self._lock:
            if self._monotonic() < self._down_until:
                return False
        try:
            self.discovery()
            return True
        except OIDCError:
            with self._lock:
                self._down_until = self._monotonic() + self.settings.unavailable_cache_s
            return False

    # --- шаг 1: адрес страницы входа ---

    def authorization_url(self, *, state: str, nonce: str, code_challenge: str, redirect_uri: str) -> str:
        disc = self.discovery()
        params = {
            "response_type": "code",
            "client_id": self.settings.client_id,
            "redirect_uri": redirect_uri,
            "scope": self.settings.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "ui_locales": "ru",
        }
        sep = "&" if "?" in disc.authorization_endpoint else "?"
        return f"{disc.authorization_endpoint}{sep}{urlencode(params)}"

    # --- шаг 2: обмен кода на токены ---

    def exchange_code(self, code: str, *, code_verifier: str, redirect_uri: str) -> dict[str, Any]:
        disc = self.discovery()
        s = self.settings
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": s.client_id,
            "code_verifier": code_verifier,
        }
        if s.client_secret:
            data["client_secret"] = s.client_secret  # client_secret_post (конфиденциальный client realm fsp)
        try:
            r = self._http.post(disc.token_endpoint, data=data, timeout=s.oidc_timeout_s)
        except httpx.HTTPError as e:
            raise OIDCUnavailable("fsp_id_unavailable", "ФСП ID не ответил при завершении входа. Попробуйте ещё раз") from e
        if r.status_code >= 500:
            raise OIDCUnavailable("fsp_id_unavailable", "ФСП ID не ответил при завершении входа. Попробуйте ещё раз")
        if r.status_code in (400, 401):
            # invalid_grant — код уже использован, истёк или подменён; invalid_client — неверный секрет
            err = ""
            try:
                err = str(r.json().get("error", ""))
            except ValueError:
                pass
            if err == "invalid_client" or r.status_code == 401:
                log.error("ФСП ID отклонил client: проверьте VZ_FSP_OIDC_CLIENT_SECRET")
                raise OIDCError("fsp_id_misconfigured", "Платформа не смогла подтвердить себя в ФСП ID. Сообщите в поддержку")
            raise OIDCError("fsp_code_invalid", "Код входа ФСП ID устарел или уже использован. Начните вход заново")
        if r.status_code != 200:
            raise OIDCError("fsp_id_misconfigured", "ФСП ID ответил ошибкой при обмене кода")
        try:
            tokens = r.json()
        except ValueError as e:
            raise OIDCError("fsp_id_misconfigured", "ФСП ID вернул ответ не в формате JSON") from e
        if not isinstance(tokens, dict) or not tokens.get("id_token"):
            raise OIDCError("fsp_id_misconfigured", "ФСП ID не вернул id_token — проверьте scope openid")
        return tokens

    # --- шаг 3: проверка id_token ---

    def _load_jwks(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            if self._jwks is not None and not force:
                return self._jwks
        jwks = self._get_json(self.discovery().jwks_uri)
        if not isinstance(jwks.get("keys"), list):
            raise OIDCError("fsp_id_misconfigured", "ФСП ID отдал некорректный набор ключей")
        with self._lock:
            self._jwks = jwks
        return jwks

    def _key_for(self, kid: str | None, alg: str) -> Any:
        for force in (False, True):
            keys = self._load_jwks(force=force)["keys"]
            for k in keys:
                if not isinstance(k, dict) or k.get("use", "sig") != "sig":
                    continue
                if kid is not None and k.get("kid") != kid:
                    continue
                if k.get("alg") not in (None, alg):
                    continue
                try:
                    return jwt.PyJWK(k).key
                except jwt.PyJWKError:
                    continue
        raise OIDCError("fsp_token_invalid", "Подпись ФСП ID не прошла проверку. Начните вход заново")

    def verify_id_token(self, id_token: str, *, nonce: str) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(id_token)
        except jwt.PyJWTError as e:
            raise OIDCError("fsp_token_invalid", "ФСП ID вернул некорректный токен") from e
        alg = str(header.get("alg", ""))
        if alg not in _ALLOWED_ALGS:
            raise OIDCError("fsp_token_invalid", "ФСП ID вернул токен с недопустимой подписью")
        key = self._key_for(header.get("kid"), alg)
        try:
            claims = jwt.decode(
                id_token,
                key,
                algorithms=[alg],
                audience=self.settings.client_id,
                options={"require": ["exp", "iat", "iss", "sub", "aud"], "verify_iss": False},
                leeway=_LEEWAY_S,
            )
        except jwt.ExpiredSignatureError as e:
            raise OIDCError("fsp_token_expired", "Вход через ФСП ID устарел. Начните заново") from e
        except jwt.PyJWTError as e:
            raise OIDCError("fsp_token_invalid", "Токен ФСП ID не прошёл проверку. Начните вход заново") from e
        _check_common(claims, nonce=nonce, client_id=self.settings.client_id, issuers=self.settings.accepted_issuers())
        return claims


def _check_common(claims: dict[str, Any], *, nonce: str, client_id: str, issuers: frozenset[str]) -> None:
    if str(claims.get("iss", "")).rstrip("/") not in issuers:
        raise OIDCError("fsp_token_invalid", "Токен выдан не тем ФСП ID. Начните вход заново")
    aud = claims.get("aud")
    if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != client_id:
        raise OIDCError("fsp_token_invalid", "Токен ФСП ID выдан другому приложению")
    if not hmac.compare_digest(str(claims.get("nonce", "")), nonce):
        raise OIDCError("fsp_token_invalid", "Токен ФСП ID не относится к этому входу. Начните заново")
    sub = claims.get("sub")
    if not isinstance(sub, str) or not sub or len(sub) > 128:
        raise OIDCError("fsp_token_invalid", "В токене ФСП ID нет идентификатора участника")


# --------------------------------------------------------------------------- «демо-вход ФСП»


def _demo_key(app_secret: str) -> bytes:
    # Отдельный ключ, производный от секрета платформы: мок-токен нельзя выдать за access-JWT и наоборот
    return hmac.new(app_secret.encode("utf-8"), b"vzaimno/fsp-demo-id-token/v1", hashlib.sha256).digest()


def mint_demo_token(
    *,
    app_secret: str,
    client_id: str,
    sub: str,
    nonce: str,
    now: datetime,
    ttl_s: int,
    email: str | None,
    given_name: str | None = None,
    family_name: str | None = None,
) -> str:
    """Подписанный мок-токен с теми же claims, что у id_token ФСП ID, + demo: true."""
    iat = int(now.timestamp())
    payload: dict[str, Any] = {
        "iss": DEMO_ISSUER,
        "aud": client_id,
        "sub": sub,
        "iat": iat,
        "exp": iat + ttl_s,
        "nonce": nonce,
        "demo": True,
        "email_verified": False,
    }
    if email:
        payload["email"] = email
    if given_name or family_name:
        payload["given_name"] = given_name
        payload["family_name"] = family_name
        payload["name"] = " ".join(p for p in (given_name, family_name) if p)
    return jwt.encode(payload, _demo_key(app_secret), algorithm="HS256", headers={"typ": "JWT", "kid": "vzaimno-demo"})


def verify_demo_token(token: str, *, app_secret: str, client_id: str, nonce: str, now: datetime) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            _demo_key(app_secret),
            algorithms=["HS256"],
            audience=client_id,
            issuer=DEMO_ISSUER,
            options={"require": ["exp", "iat", "iss", "sub", "aud"], "verify_iat": False, "verify_exp": False},
        )
    except jwt.PyJWTError as e:
        raise OIDCError("fsp_token_invalid", "Демо-токен ФСП не прошёл проверку") from e
    # Срок — по часам платформы (DEMO_MODE двигает время), а не по системным
    if int(claims["exp"]) + _LEEWAY_S < int(now.timestamp()):
        raise OIDCError("fsp_token_expired", "Демо-вход устарел. Начните заново")
    if claims.get("demo") is not True:
        raise OIDCError("fsp_token_invalid", "Это не демо-токен ФСП")
    _check_common(claims, nonce=nonce, client_id=client_id, issuers=frozenset({DEMO_ISSUER}))
    return claims
