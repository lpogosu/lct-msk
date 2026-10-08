"""Паспорт навыков через API: выпуск → скачивание → проверка (код и файл) → подделка → отзыв → карточка работодателя."""

from __future__ import annotations

import json
import uuid
from datetime import timedelta

from app.credentials import service
from app.credentials.status_list import is_revoked
from app.models.contact import ContactDisclosure
from app.models.users import User
from tests.fixtures.profile_support import confirm_qualification


def _candidate(client, make_account, db_session, now, name="Дебагова Мария Олеговна"):
    acc = make_account()
    r = client.put("/api/candidates/me", json={"full_name": name, "contact_email": "m@example.com"}, headers=acc.headers)
    assert r.status_code == 200, r.text
    confirm_qualification(db_session, acc.user_id, now=now)
    db_session.commit()
    return acc


def _issue(client, acc, **extra):
    r = client.post(
        "/api/credentials/me",
        json={"kind": "category", "ref": "backend_python", **extra},
        headers=acc.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


def _file(client, acc, cid) -> bytes:
    r = client.get(f"/api/credentials/me/{cid}/file", headers=acc.headers)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"]
    return r.content


def _verify_file(client, raw: bytes):
    r = client.post("/api/credentials/verify", content=raw, headers={"content-type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()


def test_well_known_did_json(client):
    r = client.get("/.well-known/did.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/did+json")
    d = r.json()
    assert d["id"] == "did:web:localhost%3A5173"
    assert d["verificationMethod"][0]["publicKeyMultibase"].startswith("z6Mk")
    assert client.get("/api/credentials/did.json").json() == d


def test_offers_issue_download_verify_roundtrip(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    lst = client.get("/api/credentials/me", headers=acc.headers).json()
    assert [(o["kind"], o["ref"]) for o in lst["offers"]] == [("category", "backend_python")]
    assert "демонстрационный" in lst["disclaimer"].lower()

    cred = _issue(client, acc)
    assert cred["status"] == "active" and cred["code"].startswith("vc") and len(cred["code"]) == 32
    assert cred["qr"].startswith("data:image/png;base64,") and cred["verify_url"] == f"/v/{cred['code']}"
    raw = _file(client, acc, cred["id"])
    doc = json.loads(raw)

    # PII: по умолчанию ни имени, ни почты, ни id пользователя
    text = raw.decode()
    for secret in ("Дебагова", "Мария", "m@example.com", str(acc.user_id), acc.email):
        assert secret not in text
    assert doc["credentialSubject"]["id"].startswith("urn:vzaimno:subject:") and "name" not in doc["credentialSubject"]
    assert doc["credentialSubject"]["achievement"]["name"] == "Backend (Python) · Middle"

    # проверка файла: всё зелёное
    v = _verify_file(client, raw)
    assert v["valid"] is True and all(c["ok"] for c in v["checks"]) and v["reasons"] == []
    assert {c["id"] for c in v["checks"]} >= {"format", "issuer", "signature", "validity", "registry", "revocation"}
    assert v["summary"]["achievement"] == "Backend (Python) · Middle" and v["summary"]["holder_name"] is None
    # проверка по коду из QR: публично, без входа и без ПДн
    vc = client.get(f"/api/credentials/verify/{cred['code']}")
    assert vc.status_code == 200 and vc.json()["valid"] is True
    assert "Дебагова" not in vc.text and "holder_name" in vc.text and vc.json()["summary"]["holder_name"] is None
    assert client.get("/api/credentials/verify/vc0000").status_code == 404


def test_tamper_one_byte_turns_red(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    cred = _issue(client, acc)
    raw = _file(client, acc, cred["id"])
    # меняем ровно один символ значения балла: 542 → 543
    assert b'"542"' in raw
    tampered = raw.replace(b'"542"', b'"543"', 1)
    assert len(tampered) == len(raw) and tampered != raw
    v = _verify_file(client, tampered)
    assert v["valid"] is False
    assert "Подпись не совпадает: содержимое файла изменено после выдачи" in v["reasons"]
    sig = next(c for c in v["checks"] if c["id"] == "signature")
    assert sig["ok"] is False and v["summary"] is None
    # битый JSON и посторонний JSON
    broken = _verify_file(client, raw[:-5])
    assert broken["valid"] is False and "не корректный JSON" in broken["reasons"][0]
    other = _verify_file(client, b'{"hello": "world"}')
    assert other["valid"] is False and other["summary"] is None
    # подмена срока действия тоже ломает подпись
    doc = json.loads(raw)
    doc["validUntil"] = "2099-01-01T00:00:00Z"
    assert _verify_file(client, json.dumps(doc).encode())["valid"] is False


def test_foreign_issuer_and_expiry(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    cred = _issue(client, acc)
    doc = json.loads(_file(client, acc, cred["id"]))
    foreign = {**doc, "issuer": {**doc["issuer"], "id": "did:web:evil.example"}}
    v = _verify_file(client, json.dumps(foreign).encode())
    assert v["valid"] is False and any("Издатель неизвестен" in r for r in v["reasons"])
    # срок: сдвигаем часы за пределы validity_days
    frozen_clock.advance(400 * 86400)
    v = client.get(f"/api/credentials/verify/{cred['code']}").json()
    assert v["valid"] is False and "Срок действия истёк" in v["reasons"]
    assert client.get("/api/credentials/me", headers=acc.headers).json()["items"][0]["status"] == "expired"


def test_revoke_flow_and_status_list(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    cred = _issue(client, acc)
    raw = _file(client, acc, cred["id"])
    doc = json.loads(raw)
    idx = int(doc["credentialStatus"]["statusListIndex"])

    sl = client.get("/api/credentials/status/1").json()
    assert "BitstringStatusListCredential" in sl["type"] and sl["proof"]["cryptosuite"] == "eddsa-jcs-2022"
    assert sl["credentialSubject"]["statusPurpose"] == "revocation"
    assert not is_revoked(sl["credentialSubject"]["encodedList"], idx)
    assert client.get("/api/credentials/status/zzz").status_code == 404

    r = client.post(f"/api/credentials/me/{cred['id']}/revoke", headers=acc.headers)
    assert r.status_code == 200 and r.json()["status"] == "revoked"
    assert client.post(f"/api/credentials/me/{cred['id']}/revoke", headers=acc.headers).status_code == 200  # идемпотентно

    v = _verify_file(client, raw)  # сам файл не изменился — подпись верна, но отозван
    assert v["valid"] is False and "Отозван владельцем: паспорт больше не действует" in v["reasons"]
    assert next(c for c in v["checks"] if c["id"] == "signature")["ok"] is True
    assert client.get(f"/api/credentials/verify/{cred['code']}").json()["valid"] is False
    sl = client.get("/api/credentials/status/1").json()
    assert is_revoked(sl["credentialSubject"]["encodedList"], idx)


def test_erased_registry_record_invalidates(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    cred = _issue(client, acc)
    raw = _file(client, acc, cred["id"])
    from app.credentials.models import IssuedCredential

    db_session.query(IssuedCredential).delete()
    db_session.flush()
    v = _verify_file(client, raw)
    assert v["valid"] is False and any("В реестре выдачи записи нет" in r for r in v["reasons"])


def test_name_only_when_candidate_chooses(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    cred = _issue(client, acc, include_name=True)
    raw = _file(client, acc, cred["id"])
    assert json.loads(raw)["credentialSubject"]["name"] == "Дебагова Мария Олеговна"
    v = _verify_file(client, raw)  # владелец сам загрузил файл — имя показываем
    assert v["valid"] and v["summary"]["holder_name"] == "Дебагова Мария Олеговна"
    pub = client.get(f"/api/credentials/verify/{cred['code']}")  # по коду — никогда
    assert "Дебагова" not in pub.text
    # без ФИО в профиле имя включить нельзя
    nameless = make_account()
    confirm_qualification(db_session, nameless.user_id, now=frozen_clock.now())
    db_session.commit()
    r = client.post("/api/credentials/me", json={"kind": "category", "ref": "backend_python", "include_name": True}, headers=nameless.headers)
    assert r.status_code == 400 and r.json()["code"] == "name_missing"


def test_access_control_and_validation(client, make_account, db_session, frozen_clock):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    other = make_account()
    emp = make_account(role="employer")
    cred = _issue(client, acc)
    cid = cred["id"]
    assert client.get(f"/api/credentials/me/{cid}/file", headers=other.headers).status_code == 404  # чужой паспорт
    assert client.post(f"/api/credentials/me/{cid}/revoke", headers=other.headers).status_code == 404
    assert client.get("/api/credentials/me", headers=emp.headers).status_code == 403
    assert client.get("/api/credentials/me").status_code == 401
    # без подтверждённой категории выпустить нельзя
    r = client.post("/api/credentials/me", json={"kind": "category", "ref": "backend_python"}, headers=other.headers)
    assert r.status_code == 404 and r.json()["code"] == "category_not_confirmed"
    assert client.post("/api/credentials/me", json={"kind": "bogus", "ref": "x"}, headers=acc.headers).status_code == 422
    assert client.get(f"/api/credentials/me/{uuid.uuid4()}/file", headers=acc.headers).status_code == 404


def test_fsp_credential_issue_minimized(client, make_account, db_session, frozen_clock, monkeypatch):
    acc = _candidate(client, make_account, db_session, frozen_clock.now())
    item = {
        "discipline": "prog_algo", "discipline_label": "Программирование", "level": "federal", "year": 2026,
        "outcome": "Призёр", "place_band": "топ-3", "role": None, "text": "…", "ekp": "ЕКП-9", "team": "Секретная команда",
    }
    monkeypatch.setattr(service, "_fsp_results", lambda db, uid, now: [("res-1", item)])
    offers = client.get("/api/credentials/me", headers=acc.headers).json()["offers"]
    assert ("fsp", "res-1") in [(o["kind"], o["ref"]) for o in offers]
    r = client.post("/api/credentials/me", json={"kind": "fsp", "ref": "res-1"}, headers=acc.headers)
    assert r.status_code == 201, r.text
    raw = _file(client, acc, r.json()["id"]).decode()
    assert "Призёр" in raw and "Программирование" in raw
    for secret in ("ЕКП-9", "Секретная команда", "res-1"):
        assert secret not in raw
    assert _verify_file(client, raw.encode())["valid"] is True
    assert client.post("/api/credentials/me", json={"kind": "fsp", "ref": "nope"}, headers=acc.headers).status_code == 404


def test_employer_card_shows_passport_only_after_disclosure(client, make_account, db_session, frozen_clock):
    from app.credentials.models import IssuedCredential
    from tests.test_api_needs_support import DEMO_BRIEF, seed_demo_pool

    pool = seed_demo_pool(db_session)
    anna = pool["anna"]
    emp = make_account("hr@severny-kod.ru", role="employer")
    company_id = db_session.get(User, emp.user_id).company_id
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=emp.headers).json()["id"]
    items = client.post(f"/api/needs/{nid}/versions", json={}, headers=emp.headers).json()["items"]
    from app.services.matching.privacy import resolve_ref

    ref = next(i["ref"] for i in items if resolve_ref(db_session, company_id, i["ref"]) == anna)
    now = frozen_clock.now()
    db_session.add(
        IssuedCredential(
            user_id=anna, code="vc" + "0" * 30, kind="category", title="Backend (Python) · Middle", credential_id="urn:uuid:test-1",
            status_index=900, document={}, valid_until=now + timedelta(days=30),
        )
    )
    db_session.flush()

    def card() -> dict:
        r = client.get(f"/api/needs/{nid}/candidates/{ref}", headers=emp.headers)
        assert r.status_code == 200, r.text
        return r.json()

    blind = card()
    assert blind["view"] == "blind" and blind["disclosed"] is None and "Паспорт проверен" not in json.dumps(blind, ensure_ascii=False)
    disclosure = ContactDisclosure(
        candidate_id=anna, company_id=company_id, basis="invitation", basis_id=uuid.uuid4(),
        fields=["full_name", "email"], consent_text_ver="contact-consent-v1", granted_at=now,
    )
    db_session.add(disclosure)
    db_session.flush()
    sp = card()["disclosed"]["skills_passport"]
    assert sp["verified"] is True and sp["label"] == "Паспорт проверен ✓" and sp["titles"] == ["Backend (Python) · Middle"]
    db_session.query(IssuedCredential).update({"revoked_at": now})
    db_session.flush()
    assert card()["disclosed"]["skills_passport"] is None
