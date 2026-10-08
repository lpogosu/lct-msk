"""Паспорт навыков, ядро без БД: JCS (RFC 8785), base58/multikey, подпись eddsa-jcs-2022, список отзыва, did.json."""

from __future__ import annotations

import copy
import re
import uuid
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.core.settings import Settings
from app.credentials import builders, di, keys
from app.credentials.config import get_credentials_config
from app.credentials.jcs import canonicalize
from app.credentials.multiformats import b58decode, b58encode, public_key_multibase, raw_public_from_multibase
from app.credentials.status_list import encode_list, is_revoked

NOW = datetime(2026, 10, 8, 12, 0, tzinfo=UTC)


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, env="test", public_base_url="http://localhost:5173", **kw)


# --- JCS: контрольные примеры RFC 8785 ---------------------------------------------------------


def test_jcs_rfc8785_numbers_and_ordering():
    # RFC 8785, п. 3.2.2 и Appendix B: сериализация чисел как в ECMAScript
    doc = {"numbers": [333333333.33333329, 1e30, 4.50, 2e-3, 1e-27], "literals": [None, True, False]}
    assert canonicalize(doc).decode() == '{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27]}'
    # строки: управляющие символы — \u00xx / короткие формы, не-ASCII без экранирования
    assert canonicalize({"s": "€$\u000f\nA\"\\"}).decode() == '{"s":"€$\\u000f\\nA\\"\\\\"}'
    # п. 3.2.3: порядок ключей по кодовым единицам UTF-16 (суррогатная пара U+1F600 раньше U+FB33)
    assert canonicalize({"דּ": 1, "\U0001f600": 2, "a": 3}).decode() == '{"a":3,"\U0001f600":2,"דּ":1}'
    assert canonicalize({"b": 1, "a": {"d": 1, "c": 2}}) == b'{"a":{"c":2,"d":1},"b":1}'
    assert canonicalize([1.0, -0.0, 100.0, 1e21, 1.5e-7, 1e-6]) == b"[1,0,100,1e+21,1.5e-7,0.000001]"


def test_jcs_rejects_nan():
    with pytest.raises(ValueError):
        canonicalize({"x": float("nan")})


# --- base58 / multikey ---------------------------------------------------------------------------


def test_base58_roundtrip_and_multikey_prefix():
    for raw in (b"", b"\x00", b"\x00\x00ab", bytes(range(40))):
        assert b58decode(b58encode(raw)) == raw
    assert b58encode(b"Hello World!") == "2NEpo7TZRRrLZSi2U"  # известный вектор base58btc
    pub = Ed25519PrivateKey.generate().public_key()
    mb = public_key_multibase(keys.raw_public_bytes(pub))
    assert mb.startswith("z6Mk")  # так выглядят Ed25519 Multikey
    assert raw_public_from_multibase(mb) == keys.raw_public_bytes(pub)


# --- подпись -----------------------------------------------------------------------------------------


def _sample_doc() -> dict:
    s = _settings()
    cfg = get_credentials_config()
    doc, _ = builders.category_credential(
        s,
        cfg,
        cred_id="urn:uuid:11111111-1111-1111-1111-111111111111",
        status_index=7,
        now=NOW,
        subject="urn:vzaimno:subject:abc",
        name=None,
        category_code="backend_python:middle",
        category_name="Backend (Python) · Middle",
        score=542,
        sem=22.0,
        tested_at=NOW,
        method_url="http://localhost:5173/api/docs",
    )
    return doc


def test_sign_verify_roundtrip_and_tamper():
    key = Ed25519PrivateKey.generate()
    signed = di.sign_document(_sample_doc(), key, "did:web:localhost%3A5173#key-1", NOW)
    assert signed["proof"]["cryptosuite"] == "eddsa-jcs-2022" and signed["proof"]["proofValue"].startswith("z")
    assert di.verify_proof(signed, key.public_key()).ok
    # порядок ключей не важен: каноникализация
    shuffled = dict(reversed(list(signed.items())))
    assert di.verify_proof(shuffled, key.public_key()).ok
    # изменение любого значения
    bad = copy.deepcopy(signed)
    bad["credentialSubject"]["result"][0]["value"] = "943"
    assert di.verify_proof(bad, key.public_key()) == di.ProofCheck(False, "bad_signature")
    # чужой ключ
    assert not di.verify_proof(signed, Ed25519PrivateKey.generate().public_key()).ok
    # нет подписи / иной криптосьют
    assert di.verify_proof({k: v for k, v in signed.items() if k != "proof"}, key.public_key()).reason == "no_proof"
    alt = copy.deepcopy(signed)
    alt["proof"]["cryptosuite"] = "eddsa-rdfc-2022"
    assert di.verify_proof(alt, key.public_key()).reason == "bad_suite"


def test_credential_shape_is_open_badges_3():
    doc = _sample_doc()
    assert doc["@context"][0] == "https://www.w3.org/ns/credentials/v2"
    assert "OpenBadgeCredential" in doc["type"] and "VerifiableCredential" in doc["type"]
    subj = doc["credentialSubject"]
    assert subj["type"] == ["AchievementSubject"] and subj["achievement"]["type"] == ["Achievement"]
    assert doc["credentialStatus"]["type"] == "BitstringStatusListEntry" and doc["credentialStatus"]["statusListIndex"] == "7"
    assert doc["issuer"]["id"] == "did:web:localhost%3A5173" and "демонстрационный" in doc["issuer"]["name"]
    assert {r["value"] for r in subj["result"]} == {"542", "22"}
    assert doc["evidence"][0]["id"].startswith("http")  # ссылка на методику


# --- PII ---------------------------------------------------------------------------------------------


def test_pii_minimization_defaults():
    s = _settings()
    cfg = get_credentials_config()
    uid = uuid.uuid4()
    sid = builders.subject_id(s, cfg, uid)
    assert re.fullmatch(r"urn:vzaimno:subject:[0-9a-f]{24}", sid) and str(uid) not in sid
    assert sid == builders.subject_id(s, cfg, uid) and sid != builders.subject_id(s, cfg, uuid.uuid4())
    doc = _sample_doc()
    assert "name" not in doc["credentialSubject"]  # имя только по выбору кандидата
    assert "email" not in str(doc).lower()


def test_fsp_credential_keeps_only_allowed_fields():
    s = _settings()
    cfg = get_credentials_config()
    item = {
        "discipline": "prog_algo",
        "discipline_label": "Программирование",
        "level": "federal",
        "year": 2026,
        "outcome": "Призёр",
        "place_band": "топ-3",
        "role": "участник",
        "text": "Призёр федерального …",
        "ekp": "ЕКП-123456",
        "team": "Команда Альфа",
        "protocol_url": "https://reg.example/protocol/1",
        "place": 2,
    }
    doc, _ = builders.fsp_credential(
        s,
        cfg,
        cred_id="urn:uuid:22222222-2222-2222-2222-222222222222",
        status_index=1,
        now=NOW,
        subject="urn:vzaimno:subject:abc",
        name=None,
        item=item,
        method_url="http://x",
    )
    dump = str(doc)
    for secret in ("ЕКП-123456", "Команда Альфа", "protocol/1"):
        assert secret not in dump
    assert "Призёр" in dump and "Программирование" in dump and "2026" in dump


# --- список отзыва -----------------------------------------------------------------------------------


def test_status_list_bits():
    enc = encode_list([0, 7, 8, 131071], 131072)
    assert enc.startswith("u")
    assert all(is_revoked(enc, i) for i in (0, 7, 8, 131071))
    assert not any(is_revoked(enc, i) for i in (1, 6, 9, 100000))
    with pytest.raises(ValueError):
        encode_list([131072], 131072)


# --- did.json ----------------------------------------------------------------------------------------


def test_did_document_format():
    s = _settings(credentials_did_domain="vzaimno.example:8443")
    assert keys.issuer_did(s) == "did:web:vzaimno.example%3A8443"
    doc = keys.did_document(s)
    assert doc["id"] == "did:web:vzaimno.example%3A8443"
    vm = doc["verificationMethod"][0]
    assert vm["id"] == doc["id"] + "#key-1" and vm["type"] == "Multikey" and vm["controller"] == doc["id"]
    assert raw_public_from_multibase(vm["publicKeyMultibase"]) == keys.raw_public_bytes(keys.public_key(s))
    assert doc["assertionMethod"] == [vm["id"]] and "https://www.w3.org/ns/did/v1" in doc["@context"]
    assert "private" not in str(doc).lower()
