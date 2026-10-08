"""Метка «Проверенная компания»: контрольные суммы ИНН/ОГРН, учебный реестр, уровни, отзыв (docs/TRUST_AND_SAFETY.md)."""

from __future__ import annotations

import pytest

from app.models.users import User
from app.trust.checksums import inn_valid, ogrn_valid
from app.trust.registry import mock_registry
from tests.test_api_contact_support import COMPANY_PROFILE, make_candidate, make_employer

SEVERNY_INN, SEVERNY_OGRN = "7720987658", "1057720987653"


@pytest.mark.parametrize(
    ("inn", "ok"),
    [
        ("7701123451", True),
        ("502501337740", True),  # 12 цифр, обе контрольные верны
        ("7701123452", False),  # контрольная цифра
        ("502501337741", False),
        ("770112345", False),  # длина
        ("77011234510", False),
        ("77011234ab", False),
        ("", False),
        (None, False),
    ],
)
def test_inn_checksum(inn, ok):
    assert inn_valid(inn) is ok


@pytest.mark.parametrize(
    ("ogrn", "ok"),
    [
        ("1027701123450", True),
        ("1027701123451", False),
        ("313500001234561", True),  # ОГРНИП
        ("313500001234562", False),
        ("10277011234", False),
        ("102770112345x", False),
        (None, False),
    ],
)
def test_ogrn_checksum(ogrn, ok):
    assert ogrn_valid(ogrn) is ok


def test_every_mock_registry_entry_has_valid_requisites():
    """Учебный реестр не должен содержать «битых» реквизитов: иначе демо-компании не пройдут проверку."""
    import json

    from app.trust.registry import _DATA

    for c in json.loads(_DATA.read_text(encoding="utf-8"))["companies"]:
        assert inn_valid(c["inn"]) and ogrn_valid(c["ogrn"]), c["name"]
    assert mock_registry().lookup(SEVERNY_INN).short_name == "Северный код"


def _post(client, emp, inn, ogrn):
    return client.post("/api/companies/me/verification", json={"inn": inn, "ogrn": ogrn}, headers=emp.headers)


def test_full_verification_with_corporate_domain(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    r = client.get("/api/companies/me/verification", headers=emp.headers)
    assert r.status_code == 200 and r.json()["level"] == "none" and r.json()["submitted"] is False

    r = _post(client, emp, SEVERNY_INN, SEVERNY_OGRN)
    assert r.status_code == 200, r.text
    v = r.json()
    assert v["level"] == "domain" and v["label"] == "Домен подтверждён" and v["verified"] is True
    assert v["registry_name"] == "ООО «Северный код»" and all(c["ok"] for c in v["checks"])
    assert client.get("/api/companies/me", headers=emp.headers).json()["inn"] == SEVERNY_INN

    # Кандидат видит метку в карточке компании, но не реквизиты
    cand = make_candidate(make_account, db_session)
    card = client.get("/api/companies/me/preview", headers=emp.headers).json()
    assert card["trust"]["level"] == "domain"
    assert "inn" not in card["trust"] and "checks" not in card["trust"]
    assert cand  # кандидат создан — метка в его экранах проверяется в тестах жалоб/приглашений


def test_requisites_level_when_domain_differs(client, make_account, db_session, frozen_clock):
    """Реквизиты верные, но email сотрудника не на домене из реестра: «Реквизиты проверены» без домена."""
    emp = make_employer(client, make_account, db_session, domain="kedr-mail.ru", profile={**COMPANY_PROFILE, "name": "Кедр Системы"})
    r = _post(client, emp, "7701123451", "1027701123450")
    v = r.json()
    assert r.status_code == 200 and v["level"] == "requisites" and v["label"] == "Реквизиты проверены"
    failed = [c["code"] for c in v["checks"] if not c["ok"]]
    assert failed == ["domain_match"]


def test_failed_checks_never_block_and_report_reasons(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    # битая контрольная сумма
    v = _post(client, emp, "7720987659", SEVERNY_OGRN).json()
    assert v["level"] == "none" and "inn_checksum" in [c["code"] for c in v["checks"] if not c["ok"]]
    # ИНН существует, ОГРН от другой записи
    v = _post(client, emp, SEVERNY_INN, "1027701123450").json()
    assert v["level"] == "none" and "inn_ogrn_match" in [c["code"] for c in v["checks"] if not c["ok"]]
    # ликвидированная компания
    v = _post(client, emp, "5406018777", "1195406018774").json()
    failed = [c["code"] for c in v["checks"] if not c["ok"]]
    assert v["level"] == "none" and "registry_active" in failed and "name_match" in failed
    # не цифры
    assert _post(client, emp, "77a0987658", SEVERNY_OGRN).status_code == 400
    # основной сценарий не заблокирован: профиль и квота работают
    me = client.get("/api/companies/me", headers=emp.headers).json()
    assert me["profile_complete"] is True and me["quota"]["remaining"] == 60 and me["verification"]["level"] == "none"


def test_personal_email_company_gets_no_domain_level(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session, domain="gmail.com")
    v = _post(client, emp, SEVERNY_INN, SEVERNY_OGRN).json()
    assert v["level"] == "requisites"
    assert "domain_match" in [c["code"] for c in v["checks"] if not c["ok"]]


def test_inn_cannot_be_claimed_by_two_companies(client, make_account, db_session, frozen_clock):
    first = make_employer(client, make_account, db_session)
    assert _post(client, first, SEVERNY_INN, SEVERNY_OGRN).json()["level"] == "domain"
    second = make_employer(client, make_account, db_session, domain="gmail.com")
    v = _post(client, second, SEVERNY_INN, SEVERNY_OGRN).json()
    assert v["level"] == "none" and "inn_unique" in [c["code"] for c in v["checks"] if not c["ok"]]


def test_rename_after_verification_downgrades_label(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    assert _post(client, emp, SEVERNY_INN, SEVERNY_OGRN).json()["level"] == "domain"
    r = client.put("/api/companies/me", json={"name": "ООО «Совсем Другая Фирма»"}, headers=emp.headers)
    assert r.json()["verification"]["level"] == "none"


def test_company_can_withdraw_label(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    _post(client, emp, SEVERNY_INN, SEVERNY_OGRN)
    r = client.delete("/api/companies/me/verification", headers=emp.headers)
    assert r.status_code == 200 and r.json()["level"] == "none" and r.json()["submitted"] is False


def test_moderator_revoke_and_restore(client, make_account, db_session, frozen_clock):
    emp = make_employer(client, make_account, db_session)
    _post(client, emp, SEVERNY_INN, SEVERNY_OGRN)
    methodist = make_account(role="candidate")
    db_session.get(User, methodist.user_id).role = "methodist"
    db_session.flush()
    cand = make_account(role="candidate")

    path = f"/api/admin/trust/companies/{emp.company_id}"
    assert client.post(f"{path}/revoke", json={"reason": "other"}, headers=cand.headers).status_code == 403
    assert client.post(f"{path}/revoke", json={"reason": "нет такой"}, headers=methodist.headers).status_code == 400

    r = client.post(f"{path}/revoke", json={"reason": "wrong_data", "note": "реквизиты чужие"}, headers=methodist.headers)
    assert r.status_code == 200 and r.json()["revoked"] is True
    card = client.get("/api/companies/me/preview", headers=emp.headers).json()
    assert card["trust"]["level"] == "none" and card["trust"]["verified"] is False
    mine = client.get("/api/companies/me/verification", headers=emp.headers).json()
    assert mine["revoked"] is True and mine["revoke_reason"] == "Реквизиты не соответствуют компании"
    # работа компании не заблокирована, но заново подать реквизиты нельзя, пока отзыв не снят
    r = _post(client, emp, SEVERNY_INN, SEVERNY_OGRN)
    assert r.status_code == 409 and r.json()["code"] == "verification_revoked"
    assert client.put("/api/companies/me", json={"field": "Финтех"}, headers=emp.headers).status_code == 200
    # повторный отзыв — 404
    assert client.post(f"{path}/revoke", json={"reason": "other"}, headers=methodist.headers).status_code == 404

    assert client.post(f"{path}/restore", headers=methodist.headers).status_code == 200
    assert _post(client, emp, SEVERNY_INN, SEVERNY_OGRN).json()["level"] == "domain"
