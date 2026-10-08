"""ФСП ID (экран К14, концепция §6.4–6.5, DECISIONS п. 12): демо-вход → привязка → предпросмотр без
сохранения → скрыть результат → согласие на обогащение → синхронизация → отзыв согласия → отвязка;
первый вход через ФСП ID создаёт аккаунт в pending_email и шлёт код; настоящий OIDC + PKCE против мока
Keycloak (подпись id_token по JWKS, внутренний и публичный адрес realm, одноразовый state, привязка к браузеру)."""

from __future__ import annotations

import base64
import hashlib
import json
import time
import uuid
from urllib.parse import parse_qs, urlparse

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import select

from app.fsp.provider import InProcessFSPProvider
from app.fsp.settings import FSPSettings
from app.models.candidate import CandidateProfile
from app.models.fsp import FspAchievementCache, FspAuthState, FspLink
from app.models.users import Consent, User
from app.services.fsp_link import service as fsp
from app.services.fsp_link.oidc import OIDCClient
from tests.conftest import code_from_mail

CHAMPION = "54ac9bcc-d3ac-4244-a71b-656ddbaa7a8f"  # fsp-demo-01: 3 подтверждённых результата, МС
WITH_PENDING = "f65bb71f-8ac3-4af0-8ead-29daf95d439a"  # fsp-demo-02: один результат ждёт протокола
NO_RESULTS = "4b9d66eb-d468-464d-b17e-396e6af1749d"  # fsp-demo-11: в реестре ничего нет
PUBLIC = "http://localhost:8080/realms/fsp"
INTERNAL = "http://keycloak:8080/realms/fsp"
SECRET = "vzaimno-demo-secret"


@pytest.fixture
def registry(app):
    provider = InProcessFSPProvider.from_file()
    app.dependency_overrides[fsp.get_provider] = lambda: provider
    return provider


# --------------------------------------------------------------------------- мок Keycloak


def _b64(n: int) -> str:
    return base64.urlsafe_b64encode(n.to_bytes((n.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()


class FakeKeycloak:
    """Realm fsp: discovery по внутреннему адресу отдаёт публичные адреса (как Keycloak с KC_HOSTNAME),
    токен-эндпоинт проверяет PKCE, секрет client и redirect_uri; id_token подписан RS256."""

    def __init__(self) -> None:
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = self.key.public_key().public_numbers()
        self.jwks = {"keys": [{"kty": "RSA", "kid": "k1", "use": "sig", "alg": "RS256", "n": _b64(pub.n), "e": _b64(pub.e)}]}
        self.codes: dict[str, dict] = {}
        self.up = True
        self.override_nonce: str | None = None
        self.iss = PUBLIC

    def authorize(self, url: str, *, sub: str, email: str | None) -> tuple[str, str]:
        """Пользователь вошёл на странице ФСП ID → (code, state) из адреса возврата."""
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert url.startswith(f"{PUBLIC}/protocol/openid-connect/auth?")
        assert q["code_challenge_method"] == "S256" and q["response_type"] == "code"
        code = uuid.uuid4().hex
        self.codes[code] = {**q, "sub": sub, "email": email}
        return code, q["state"]

    def handler(self, request: httpx.Request) -> httpx.Response:
        if not self.up:
            raise httpx.ConnectError("keycloak down", request=request)
        url = str(request.url)
        assert url.startswith(INTERNAL), url  # бэкенд ходит только по внутреннему адресу
        if url.endswith("/.well-known/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": PUBLIC,
                    "authorization_endpoint": f"{PUBLIC}/protocol/openid-connect/auth",
                    "token_endpoint": f"{PUBLIC}/protocol/openid-connect/token",
                    "jwks_uri": f"{PUBLIC}/protocol/openid-connect/certs",
                },
            )
        if url.endswith("/certs"):
            return httpx.Response(200, json=self.jwks)
        if url.endswith("/token"):
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            if form.get("client_secret") != SECRET:
                return httpx.Response(401, json={"error": "invalid_client"})
            data = self.codes.pop(form.get("code", ""), None)
            if data is None:
                return httpx.Response(400, json={"error": "invalid_grant"})
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"].encode()).digest()).rstrip(b"=").decode()
            if challenge != data["code_challenge"] or form["redirect_uri"] != data["redirect_uri"]:
                return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
            now = int(time.time())
            claims = {
                "iss": self.iss,
                "aud": data["client_id"],
                "azp": data["client_id"],
                "sub": data["sub"],
                "iat": now,
                "exp": now + 300,
                "nonce": self.override_nonce or data["nonce"],
                "name": "Роман Дебагов",
                "preferred_username": "fsp-demo-01",
            }
            if data["email"]:
                claims["email"] = data["email"]
                claims["email_verified"] = True
            token = jwt.encode(claims, self.key, algorithm="RS256", headers={"kid": "k1"})
            return httpx.Response(200, json={"access_token": "at", "id_token": token, "token_type": "Bearer"})
        return httpx.Response(404)


@pytest.fixture
def keycloak(app):
    kc = FakeKeycloak()
    settings = FSPSettings(issuer=PUBLIC, internal_url=INTERNAL, client_id="vzaimno", client_secret=SECRET)
    client = OIDCClient(settings, httpx.Client(transport=httpx.MockTransport(kc.handler)))
    app.dependency_overrides[fsp.get_oidc_client] = lambda: client
    return kc


# --------------------------------------------------------------------------- демо-вход и К14


def test_demo_link_preview_hide_enrich_revoke_unlink(client, make_account, db_session, frozen_clock, registry):
    acc = make_account()
    st = client.get("/api/fsp/me", headers=acc.headers).json()
    assert st["state"] == "not_linked" and st["linked"] is False
    assert st["employer_line"] == "Достижения ФСП не привязаны — оценка по тесту и задачам"
    assert "не штраф" in st["note"]

    r = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "link"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "linked" and body["demo"] is True
    st = body["fsp"]
    assert st["state"] == "linked" and st["is_demo"] is True
    assert st["preview_only"] is True and st["consent_fsp_enrichment"] is False
    assert len(st["results"]) == 3 and "МС" in st["badges"]
    # Без согласия данные не сохраняются и в подбор не идут
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == acc.user_id))
    assert link.is_demo and link.achievements is None
    assert "email" not in link.claims and "name" not in link.claims  # ПДн из токена не храним
    assert db_session.scalars(select(FspAchievementCache).where(FspAchievementCache.user_id == acc.user_id)).all() == []
    assert st["employer_line"].startswith("Достижения ФСП не привязаны")

    ach = client.get("/api/fsp/me/achievements", headers=acc.headers).json()
    target = next(a for a in ach if "Чемпионат России" in a["title"])
    assert target["place"] == "1 из 103" and target["employer_text"] and "1 из 103" not in target["employer_text"]
    r = client.patch(f"/api/fsp/me/results/{target['id']}", json={"hidden": True}, headers=acc.headers)
    assert r.status_code == 200
    hidden = next(x for x in r.json()["results"] if x["id"] == target["id"])
    assert hidden["hidden"] and not hidden["counted"] and hidden["status_label"] == "Скрыт"
    assert client.patch("/api/fsp/me/results/unknown-id", json={"hidden": True}, headers=acc.headers).status_code == 404

    r = client.post("/api/fsp/me/enrichment", json={}, headers=acc.headers)
    assert r.status_code == 200, r.text
    st = r.json()
    assert st["consent_fsp_enrichment"] is True and st["preview_only"] is False
    assert st["source_label"] == "Демо-реестр ФСП" and st["sync_status"] == "ok"
    assert "ЕКП" in st["extract"]
    db_session.expire_all()
    rows = {r.external_id: r for r in db_session.scalars(select(FspAchievementCache).where(FspAchievementCache.user_id == acc.user_id))}
    assert rows[target["id"]].hidden is True and rows[target["id"]].kind == "result"
    assert {r.kind for r in rows.values()} >= {"result", "rank"}
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == acc.user_id))
    assert link.achievements["athlete_sub"] == CHAMPION and link.registry_as_of is not None
    # Скрытый результат не участвует в оценке: в выписке его ЕКП нет
    assert "2025992399617295" not in st["extract"]

    r = client.patch(f"/api/fsp/me/results/{target['id']}", json={"hidden": False}, headers=acc.headers)
    db_session.expire_all()
    assert db_session.scalar(select(FspAchievementCache).where(FspAchievementCache.external_id == target["id"])).hidden is False
    assert "2025992399617295" in r.json()["extract"]

    assert client.post("/api/fsp/me/refresh", headers=acc.headers).status_code == 200

    # Отзыв согласия из журнала: данные реестра удаляются, привязка остаётся
    consent = db_session.scalar(select(Consent).where(Consent.user_id == acc.user_id, Consent.purpose == "fsp_enrichment"))
    assert client.post(f"/api/consents/{consent.id}/revoke", headers=acc.headers).status_code == 200
    db_session.expire_all()
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == acc.user_id))
    assert link is not None and link.achievements is None
    assert db_session.scalars(select(FspAchievementCache).where(FspAchievementCache.user_id == acc.user_id)).all() == []
    r = client.post("/api/fsp/me/refresh", headers=acc.headers)
    assert r.status_code == 409 and r.json()["code"] == "fsp_consent_required"

    # Отвязка: привязка и данные удалены, согласие отозвано (запись в журнале)
    client.post("/api/fsp/me/enrichment", json={}, headers=acc.headers)
    r = client.delete("/api/fsp/me", headers=acc.headers)
    assert r.status_code == 200 and r.json()["state"] == "not_linked"
    db_session.expire_all()
    assert db_session.scalar(select(FspLink).where(FspLink.user_id == acc.user_id)) is None
    consents = db_session.scalars(select(Consent).where(Consent.user_id == acc.user_id, Consent.purpose == "fsp_enrichment")).all()
    assert consents and all(c.revoked_at is not None for c in consents)
    assert client.delete("/api/fsp/me", headers=acc.headers).json()["code"] == "fsp_not_linked"


def test_states_awaiting_protocol_and_no_results(client, make_account, frozen_clock, registry):
    a = make_account()
    st = client.post("/api/fsp/demo-login", json={"sub": WITH_PENDING, "mode": "link", "consent_fsp_enrichment": True}, headers=a.headers).json()["fsp"]
    assert st["state"] == "linked" and st["pending_count"] == 1
    assert "ожидает протокол: 1" in st["status_line"]
    pending = next(r for r in st["results"] if not r["confirmed"])
    assert pending["status_label"] == "Ожидает протокол" and pending["counted"] is False and pending["employer_text"] is None
    b = make_account()
    st = client.post("/api/fsp/demo-login", json={"sub": NO_RESULTS, "mode": "link", "consent_fsp_enrichment": True}, headers=b.headers).json()["fsp"]
    assert st["state"] == "linked_no_results" and st["employer_line"] == "Участник ФСП, результатов пока нет"


def test_first_fsp_login_creates_pending_account_and_sends_code(client, make_account, db_session, mailer, frozen_clock, registry):
    r = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login"})
    assert r.status_code == 400 and r.json()["code"] == "consent_required"
    r = client.post(
        "/api/fsp/demo-login",
        json={"sub": CHAMPION, "mode": "login", "consent_personal_data": True, "consent_fsp_enrichment": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "email_verification_required" and body["demo"] is True
    email = body["verification"]["email"]
    assert email == f"fsp-{CHAMPION[:8]}@demo.example.org" and body["verification"]["code_sent"] is True
    user = db_session.scalar(select(User).where(User.email == email))
    assert user.status == "pending_email" and user.password_hash is None
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == user.id))
    assert link.fsp_id == CHAMPION and link.claims["origin"] == "fsp_signup"
    # Согласие на обогащение отложено до подтверждения email: данные реестра к неподтверждённому
    # аккаунту не привязываем (он может оказаться чужим — защита от предзахвата)
    assert link.claims["enrichment_requested"] is True and link.achievements is None
    purposes = {c.purpose for c in db_session.scalars(select(Consent).where(Consent.user_id == user.id))}
    assert purposes == {"processing"}

    # Повторный вход до подтверждения: снова просим код (не раньше минуты — письмо не дублируется)
    again = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login"}).json()
    assert again["result"] == "email_verification_required" and again["verification"]["code_sent"] is False

    r = client.post("/api/auth/verify-email", json={"email": email, "code": code_from_mail(mailer, email), "password": "Parol-2026"})
    assert r.status_code == 200, r.text
    db_session.expire_all()
    assert db_session.get(CandidateProfile, user.id) is not None

    client.cookies.clear()
    r = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login"})
    body = r.json()
    assert body["result"] == "logged_in" and body["token"]["user"]["id"] == str(user.id)
    assert r.cookies.get("vz_refresh")
    me = client.get("/api/fsp/me", headers={"Authorization": f"Bearer {body['token']['access_token']}"}).json()
    assert me["state"] == "linked" and me["consent_fsp_enrichment"] is True and me["enrichment_requested"] is False
    db_session.expire_all()
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == user.id))
    assert "enrichment_requested" not in link.claims and link.claims["origin"] == "fsp_signup"
    assert link.achievements is not None  # согласие применено при входе подтверждённого аккаунта

    # Email из ФСП ID не даёт права на чужой аккаунт
    other = make_account(email="taken@example.com")
    r = client.post(
        "/api/fsp/demo-login",
        json={"sub": WITH_PENDING, "mode": "login", "consent_personal_data": True, "email": other.email},
    )
    assert r.status_code == 409 and r.json()["code"] == "email_taken"


def test_signup_with_foreign_email_cannot_hijack_account(client, db_session, mailer, frozen_clock, registry):
    """Предзахват: кто-то входит «через ФСП ID» с чужим email → аккаунт в pending_email; владелец email позже
    регистрируется сам (регистрация перезаписывает пароль неподтверждённого аккаунта) и подтверждает email.
    Чужой ФСП ID не должен открывать этот аккаунт, а его данные реестра — попадать в профиль."""
    victim = "victim.owner@example.com"
    r = client.post(
        "/api/fsp/demo-login",
        json={"sub": CHAMPION, "mode": "login", "consent_personal_data": True, "consent_fsp_enrichment": True, "email": victim},
    )
    assert r.status_code == 200 and r.json()["result"] == "email_verification_required", r.text
    user_id = uuid.UUID(r.json()["verification"]["user_id"])
    frozen_clock.advance(61)  # повторное письмо — не раньше минуты
    r = client.post(
        "/api/auth/register",
        json={"email": victim, "password": "Svoi-Parol-2026", "role": "candidate", "consent_personal_data": True},
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/api/auth/verify-email", json={"email": victim, "code": code_from_mail(mailer, victim), "password": "Svoi-Parol-2026"}
    )
    assert r.status_code == 200, r.text
    victim_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    # Отложенное согласие: данных реестра и согласия на обогащение у аккаунта нет
    assert db_session.scalars(select(FspAchievementCache).where(FspAchievementCache.user_id == user_id)).all() == []
    assert not db_session.scalars(select(Consent).where(Consent.user_id == user_id, Consent.purpose == "fsp_enrichment")).all()

    # Вход по тому же ФСП ID сессию не даёт: привязка недействительна и удаляется
    r = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login"})
    assert r.status_code == 400 and r.json()["code"] == "consent_required" and "token" not in r.text
    r = client.post(
        "/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login", "consent_personal_data": True, "email": victim}
    )
    assert r.status_code == 409 and r.json()["code"] == "email_taken"
    db_session.expire_all()
    assert db_session.scalar(select(FspLink).where(FspLink.fsp_id == CHAMPION)) is None
    me = client.get("/api/fsp/me", headers=victim_headers).json()
    assert me["state"] == "not_linked"
    # Владелец ФСП ID может привязать его к своему аккаунту — ФСП ID больше не «занят»
    owner = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login", "consent_personal_data": True})
    assert owner.status_code == 200 and owner.json()["result"] == "email_verification_required"


def test_signup_link_dropped_when_victim_opens_profile_first(client, db_session, mailer, frozen_clock, registry):
    victim = "victim.first@example.com"
    client.post("/api/fsp/demo-login", json={"sub": WITH_PENDING, "mode": "login", "consent_personal_data": True, "email": victim})
    frozen_clock.advance(61)
    client.post("/api/auth/register", json={"email": victim, "password": "Svoi-Parol-2026", "role": "candidate", "consent_personal_data": True})
    r = client.post(
        "/api/auth/verify-email", json={"email": victim, "code": code_from_mail(mailer, victim), "password": "Svoi-Parol-2026"}
    )
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    client.cookies.clear()
    assert client.get("/api/fsp/me", headers=headers).json()["state"] == "not_linked"
    db_session.expire_all()
    assert db_session.scalar(select(FspLink).where(FspLink.fsp_id == WITH_PENDING)) is None
    # Свой ФСП ID владелец email привязывает как обычно
    r = client.post("/api/fsp/demo-login", json={"sub": WITH_PENDING, "mode": "link"}, headers=headers)
    assert r.status_code == 200 and r.json()["result"] == "linked"


def test_demo_login_does_not_open_password_accounts(client, make_account, frozen_clock, registry):
    acc = make_account()
    assert client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "link"}, headers=acc.headers).json()["result"] == "linked"
    r = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login"})
    assert r.status_code == 409 and r.json()["code"] == "fsp_demo_password_account"
    assert "vz_refresh" not in r.cookies and "token" not in r.text


def test_registry_birth_year_wins_for_minor_policy(client, make_account, db_session, frozen_clock, registry):
    acc = make_account()
    assert client.put("/api/candidates/me", json={"birth_year": 1950}, headers=acc.headers).status_code == 200
    client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "consent_fsp_enrichment": True}, headers=acc.headers)
    year = registry.get_athlete(CHAMPION).birth_date.year
    db_session.expire_all()
    assert db_session.get(CandidateProfile, acc.user_id).birth_year == year
    r = client.put("/api/candidates/me", json={"birth_year": year - 10}, headers=acc.headers)
    assert r.status_code == 422 and "реестром ФСП" in r.text
    assert client.put("/api/candidates/me", json={"birth_year": year}, headers=acc.headers).status_code == 200
    # Без данных реестра (отзыв согласия) год снова можно править
    consent = db_session.scalar(select(Consent).where(Consent.user_id == acc.user_id, Consent.purpose == "fsp_enrichment"))
    client.post(f"/api/consents/{consent.id}/revoke", headers=acc.headers)
    assert client.put("/api/candidates/me", json={"birth_year": year - 1}, headers=acc.headers).status_code == 200


def test_link_conflicts_and_roles(client, make_account, frozen_clock, registry):
    a, b = make_account(), make_account()
    assert client.post("/api/fsp/demo-login", json={"sub": CHAMPION}, headers=a.headers).status_code == 200
    r = client.post("/api/fsp/demo-login", json={"sub": CHAMPION}, headers=b.headers)
    assert r.status_code == 409 and r.json()["code"] == "fsp_id_taken"
    r = client.post("/api/fsp/demo-login", json={"sub": WITH_PENDING}, headers=a.headers)
    assert r.status_code == 409 and r.json()["code"] == "fsp_already_linked"
    emp = make_account(role="employer")
    assert client.post("/api/fsp/demo-login", json={"sub": NO_RESULTS}, headers=emp.headers).status_code == 403
    assert client.get("/api/fsp/me", headers=emp.headers).status_code == 403
    assert client.post("/api/fsp/demo-login", json={"sub": NO_RESULTS}).status_code == 401
    assert client.post("/api/fsp/demo-login", json={"sub": "nobody"}, headers=b.headers).json()["code"] == "fsp_demo_unknown"
    athletes = client.get("/api/fsp/demo/athletes").json()
    assert len(athletes) >= 30 and athletes[0]["state"] == "linked"


def test_refresh_rate_limited(client, make_account, frozen_clock, registry):
    acc = make_account()
    client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "consent_fsp_enrichment": True}, headers=acc.headers)
    codes = [client.post("/api/fsp/me/refresh", headers=acc.headers).status_code for _ in range(7)]
    assert codes[:6] == [200] * 6 and codes[6] == 429


# --------------------------------------------------------------------------- OIDC + PKCE против мока Keycloak


def test_oidc_link_flow_pkce_jwks_and_single_use_state(client, make_account, db_session, frozen_clock, registry, keycloak):
    acc = make_account()
    r = client.get("/api/fsp/login", params={"mode": "link", "consent_fsp_enrichment": True}, headers=acc.headers)
    assert r.status_code == 200, r.text
    start = r.json()
    assert start["authorization_url"].startswith(f"{PUBLIC}/protocol/openid-connect/auth?")  # браузеру — публичный адрес
    assert client.cookies.get("vz_fsp_flow") == start["state"]
    row = db_session.scalar(select(FspAuthState).where(FspAuthState.user_id == acc.user_id))
    assert row.code_verifier not in start["authorization_url"]  # verifier остаётся на сервере

    code, state = keycloak.authorize(start["authorization_url"], sub=CHAMPION, email="fsp-demo-01@demo.example.org")
    r = client.get("/api/fsp/callback", params={"code": code, "state": state}, headers=acc.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "linked" and body["demo"] is False
    assert body["fsp"]["state"] == "linked" and body["fsp"]["consent_fsp_enrichment"] is True
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == acc.user_id))
    assert link.fsp_id == CHAMPION and not link.is_demo and link.claims["iss"] == PUBLIC
    # state одноразовый
    r = client.get("/api/fsp/callback", params={"code": code, "state": state}, headers=acc.headers)
    assert r.status_code == 400 and r.json()["code"] == "fsp_state_invalid"


def test_oidc_rejects_foreign_browser_user_nonce_and_pkce(client, make_account, db_session, frozen_clock, registry, keycloak):
    a, b = make_account(), make_account()
    # Другой браузер (нет cookie начатого входа)
    start = client.get("/api/fsp/login", params={"mode": "link"}, headers=a.headers).json()
    code, state = keycloak.authorize(start["authorization_url"], sub=CHAMPION, email=None)
    client.cookies.clear()
    r = client.get("/api/fsp/callback", params={"code": code, "state": state}, headers=a.headers)
    assert r.status_code == 400 and r.json()["code"] == "fsp_state_mismatch"
    # Привязку, начатую аккаунтом A, нельзя завершить аккаунтом B
    start = client.get("/api/fsp/login", params={"mode": "link"}, headers=a.headers).json()
    code, state = keycloak.authorize(start["authorization_url"], sub=CHAMPION, email=None)
    r = client.get("/api/fsp/callback", params={"code": code, "state": state}, headers=b.headers)
    assert r.status_code == 403 and r.json()["code"] == "fsp_state_foreign"
    # Подменённый nonce в id_token
    start = client.get("/api/fsp/login", params={"mode": "link"}, headers=a.headers).json()
    code, state = keycloak.authorize(start["authorization_url"], sub=CHAMPION, email=None)
    keycloak.override_nonce = "evil"
    r = client.get("/api/fsp/callback", params={"code": code, "state": state}, headers=a.headers)
    assert r.status_code == 400 and r.json()["code"] == "fsp_token_invalid"
    keycloak.override_nonce = None
    # PKCE: подменённый code_verifier не пройдёт обмен кода
    start = client.get("/api/fsp/login", params={"mode": "link"}, headers=a.headers).json()
    code, state = keycloak.authorize(start["authorization_url"], sub=CHAMPION, email=None)
    row = db_session.scalar(select(FspAuthState).where(FspAuthState.state_hash == hashlib.sha256(state.encode()).hexdigest()))
    row.code_verifier = "x" * 64
    db_session.commit()
    r = client.get("/api/fsp/callback", params={"code": code, "state": state}, headers=a.headers)
    assert r.status_code == 400 and r.json()["code"] == "fsp_code_invalid"
    assert db_session.scalar(select(FspLink).where(FspLink.user_id == a.user_id)) is None


def test_oidc_login_mode_creates_account_and_internal_issuer_accepted(client, db_session, mailer, frozen_clock, registry, keycloak):
    r = client.get("/api/fsp/login", params={"mode": "login"})
    assert r.status_code == 400 and r.json()["code"] == "consent_required"
    start = client.get("/api/fsp/login", params={"mode": "login", "consent_personal_data": True}).json()
    code, state = keycloak.authorize(start["authorization_url"], sub=WITH_PENDING, email="Fsp-Demo-02@Demo.Example.org")
    keycloak.iss = INTERNAL  # Keycloak без KC_HOSTNAME подписывает адресом запроса — тоже принимаем
    r = client.get("/api/fsp/callback", params={"code": code, "state": state})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "email_verification_required"
    assert body["verification"]["email"] == "fsp-demo-02@demo.example.org"
    assert mailer.last_to("fsp-demo-02@demo.example.org") is not None


def test_keycloak_down_offers_demo_login(client, make_account, frozen_clock, registry, keycloak):
    keycloak.up = False
    acc = make_account()
    r = client.get("/api/fsp/login", params={"mode": "link"}, headers=acc.headers)
    assert r.status_code == 503 and r.json()["code"] == "fsp_id_unavailable"
    assert r.json()["details"] == {"demo_login": True}
    av = client.get("/api/fsp/availability").json()
    assert av["fsp_id_available"] is False and av["demo_login_available"] is True
    assert client.post("/api/fsp/demo-login", json={"sub": CHAMPION}, headers=acc.headers).json()["result"] == "linked"


def test_availability_remembers_outage_briefly():
    calls: list[str] = []

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ConnectError("keycloak down", request=request)

    t = [0.0]
    oidc = OIDCClient(
        FSPSettings(issuer=PUBLIC, internal_url=INTERNAL), httpx.Client(transport=httpx.MockTransport(down)), monotonic=lambda: t[0]
    )
    assert oidc.available() is False and oidc.available() is False
    assert len(calls) == 1  # публичная проверка не дёргает Keycloak на каждый запрос
    t[0] = 31.0
    assert oidc.available() is False and len(calls) == 2


def test_public_and_backchannel_addresses():
    s = FSPSettings(issuer=PUBLIC, internal_url=INTERNAL)
    assert s.to_public(f"{INTERNAL}/protocol/openid-connect/auth") == f"{PUBLIC}/protocol/openid-connect/auth"
    assert s.to_backchannel(f"{PUBLIC}/protocol/openid-connect/token") == f"{INTERNAL}/protocol/openid-connect/token"
    assert s.accepted_issuers() == {PUBLIC, INTERNAL}
    same = FSPSettings(issuer="https://sso.fsp-russia.ru/realms/fsp", internal_url=None)
    assert same.backchannel_issuer == same.public_issuer


# --------------------------------------------------------------------------- ФСП в превью и PDF


def test_fsp_in_preview_and_pdf_blind_has_no_ekp(client, make_account, frozen_clock, registry):
    import io

    import pdfplumber

    acc = make_account()
    client.put("/api/candidates/me", json={"full_name": "Логгерова Анна"}, headers=acc.headers)
    client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "consent_fsp_enrichment": True}, headers=acc.headers)
    pv = client.get("/api/candidates/me/preview", headers=acc.headers).json()
    assert pv["blind"]["has_fsp"] is True and pv["pii_in_blind"] == []
    blind_fsp = json.dumps(pv["blind"]["fsp"], ensure_ascii=False)
    assert "ekp" not in blind_fsp and "2025992399617295" not in blind_fsp and "Белый кластер" not in blind_fsp
    assert any(item.get("ekp") == "2025992399617295" for item in pv["disclosed"]["fsp"])

    texts = {}
    for variant in ("blind", "full"):
        r = client.get("/api/candidates/me/profile.pdf", params={"variant": variant}, headers=acc.headers)
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            texts[variant] = "\n".join(p.extract_text() or "" for p in pdf.pages)
    assert "Призёр всероссийских" in texts["blind"] and "Демо-реестр ФСП" in texts["blind"]
    assert "2025992399617295" not in texts["blind"] and "1 из 103" not in texts["blind"]
    assert "ЕКП: 2025992399617295" in texts["full"] and "Место: 1 из 103" in texts["full"]
    code = client.get("/api/candidates/me/profile-pdf", params={"variant": "full"}, headers=acc.headers).json()["code"]
    v = client.get(f"/api/v/{code}").json()
    assert v["fsp"]["linked"] is True and "данные ФСП из реестра на" in v["message"]


def test_registry_breaker_skips_network_after_fallback():
    from app.fsp.provider import FallbackFSPProvider, HttpFSPProvider

    calls: list[str] = []

    def down(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        raise httpx.ConnectError("реестр не отвечает", request=request)

    http = HttpFSPProvider("http://registry", client=httpx.Client(base_url="http://registry", transport=httpx.MockTransport(down)))
    clock = [0.0]
    breaker = fsp.BreakerProvider(FallbackFSPProvider(http, InProcessFSPProvider.from_file()), cooldown_s=60, monotonic=lambda: clock[0])
    assert breaker.get_achievements(CHAMPION).athlete_sub == CHAMPION
    assert breaker.get_athlete(CHAMPION).sub == CHAMPION
    assert len(calls) == 1  # после первого отказа сеть не трогаем
    clock[0] = 61.0
    breaker.get_athlete(CHAMPION)
    assert len(calls) == 2  # предохранитель остыл — снова пробуем сеть


def test_demo_login_never_in_prod():
    from app.core.settings import Settings

    secret = "x" * 40
    prod = Settings(
        _env_file=None, env="prod", jwt_secret=secret, app_secret=secret + "a", pseudonym_secret=secret + "b", cookie_secure=True
    )
    assert fsp.demo_allowed(prod) is False
    assert fsp.demo_allowed(Settings(_env_file=None, env="dev")) is True


def test_demo_mode_fsp_signup_logs_in_without_email_code(client, db_session, test_settings, frozen_clock, registry):
    """Q-13: «Войти через ФСП ID» без Keycloak в DEMO — аккаунт создаётся и сессия открывается сразу, достижения импортируются."""
    test_settings.demo_mode = True
    r = client.post(
        "/api/fsp/demo-login",
        json={"sub": CHAMPION, "mode": "login", "consent_personal_data": True, "consent_fsp_enrichment": True},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"] == "logged_in" and body["demo"] is True
    assert body["token"]["user"]["role"] == "candidate" and r.cookies.get("vz_refresh")
    user = db_session.get(User, uuid.UUID(body["token"]["user"]["id"]))
    assert user.status == "active" and user.email_verified and user.password_hash is None
    me = client.get("/api/fsp/me", headers={"Authorization": f"Bearer {body['token']['access_token']}"}).json()
    assert me["state"] == "linked" and me["consent_fsp_enrichment"] is True
    link = db_session.scalar(select(FspLink).where(FspLink.user_id == user.id))
    assert link.achievements is not None
    # повторный вход тем же ФСП ID — в тот же аккаунт
    client.cookies.clear()
    again = client.post("/api/fsp/demo-login", json={"sub": CHAMPION, "mode": "login"}).json()
    assert again["result"] == "logged_in" and again["token"]["user"]["id"] == body["token"]["user"]["id"]
