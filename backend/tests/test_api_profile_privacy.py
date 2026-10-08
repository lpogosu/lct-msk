"""API профиля и приватности (экраны К7, К13, К15): ручная форма, настройки поиска, живое превью
«так вас видят» (слепая без ПДн / раскрытая с разрешёнными полями), журнал согласий, блок-лист, «как меня видят»."""

from __future__ import annotations

from sqlalchemy import select

from app.contact.visibility import pii_keys_in
from app.models.candidate import CandidateProfile
from app.models.company import Company
from app.models.users import User
from tests.fixtures.profile_support import confirm_qualification


def _fill_profile(client, acc) -> dict:
    r = client.put(
        "/api/candidates/me",
        json={
            "full_name": "Дебагова Мария Олеговна",
            "phone": "8 (915) 204-18-73",
            "telegram": "https://t.me/maria_debug",
            "city": "Казань",
            "current_employer": "ООО «Тихая гавань софт»",
            "education": "Казанский университет информационных технологий",
            "skills": ["FastAPI", "postgresql", "Docker"],
            "roles": ["backend_dev"],
            "soft_skills": ["teamwork"],
            "industries": ["fintech"],
            "experience": [{"start": "2020-09", "end": "2021-05", "title": "Стажёр"}, {"start": "2021-03"}],
        },
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    return r.json()


def test_profile_form_normalizes_and_declared_is_gray(client, make_account, frozen_clock):
    acc = make_account()
    r = client.get("/api/candidates/me", headers=acc.headers)
    assert r.status_code == 200
    assert r.json()["missing"]  # что ещё заполнить
    prof = _fill_profile(client, acc)
    assert prof["phone"] == "+79152041873"
    assert prof["telegram"] == "@maria_debug"
    assert prof["city"]["code"] == "kazan" and prof["region"] == {"code": "16", "name": "Республика Татарстан"}
    assert [s["id"] for s in prof["skills"]] == ["fastapi", "postgresql", "docker"]
    assert all(s["source"] == "manual" for s in prof["skills"])
    # 2020-09..2026-10 без двойного счёта пересечения 2021-03..05
    assert prof["experience_months"] == 74
    assert prof["industries"] == [{"code": "fintech", "name": "Финтех"}]
    # Частичное обновление: остальное не трогается; null очищает
    r = client.put("/api/candidates/me", json={"telegram": None}, headers=acc.headers)
    assert r.json()["telegram"] is None and r.json()["phone"] == "+79152041873"
    bad = client.put("/api/candidates/me", json={"skills": ["Квантовая магия"]}, headers=acc.headers)
    assert bad.status_code == 422 and "Квантовая магия" in bad.json()["details"][0]["message"]
    bad = client.put("/api/candidates/me", json={"telegram": "@ab"}, headers=acc.headers)
    assert bad.status_code == 422
    bad = client.put("/api/candidates/me", json={"phone": "позвоните мне"}, headers=acc.headers)
    assert bad.status_code == 422


def test_phone_normalization_rejects_extensions_and_wrong_length():
    import pytest

    from app.core.errors import ApiError
    from app.services.profile.service import normalize_phone

    assert normalize_phone("+7 915 204-18-73") == "+79152041873"
    assert normalize_phone("8 (915) 204-18-73") == "+79152041873"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    # Добавочный номер исказил бы основной; +7 — ровно 11 цифр
    for bad in ("+7 915 204 18 73 доб. 5", "+7 915 204 18 7", "+7 915 204 18 73 1", "12345"):
        with pytest.raises(ApiError):
            normalize_phone(bad)


def test_privacy_defaults_and_validation(client, make_account, frozen_clock):
    acc = make_account()
    r = client.get("/api/candidates/me/privacy", headers=acc.headers)
    assert r.status_code == 200
    p = r.json()
    assert p["weekly_invite_limit"] == 5 and p["search_mode"] == "open" and p["salary_visibility"] == "signal"
    assert p["options"]["weekly_limits"] == [3, 5, 10, None]
    assert any(f["field"] == "phone" and f["contact"] for f in p["field_visibility"])
    assert p["bank"]["visible"] is False and p["bank"]["reasons"]

    r = client.put(
        "/api/candidates/me/privacy",
        json={
            "search_mode": "active",
            "salary_min": 250000,
            "salary_visibility": "exact",
            "weekly_invite_limit": None,
            "formats": ["remote", "hybrid"],
            "city": "Санкт-Петербург",
            "relocation": True,
        },
        headers=acc.headers,
    )
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["weekly_invite_limit"] is None  # ∞ — только явным выбором
    assert p["formats"] == ["hybrid", "remote"] and p["region"]["code"] == "78" and p["city"]["code"] == "spb"
    assert p["salary_min"] == 250000

    for body, code in (
        ({"weekly_invite_limit": 7}, 422),
        ({"salary_min": 250500}, 422),
        ({"region_code": "99"}, 422),
        ({"field_visibility": {"passport": True}}, 422),
        ({"field_visibility": {"email": False, "phone": False, "telegram": False}}, 400),
        ({"blocked_company_ids": ["00000000-0000-0000-0000-000000000001"]}, 422),
    ):
        r = client.put("/api/candidates/me/privacy", json=body, headers=acc.headers)
        assert r.status_code == code, (body, r.text)
    assert r.json()["details"][0]["field"] == "blocked_company_ids"
    r = client.put("/api/candidates/me/privacy", json={"field_visibility": {"email": False, "phone": False, "telegram": False}}, headers=acc.headers)
    assert r.json()["code"] == "contact_field_required"


def test_blocklist_search_and_block(client, make_account, frozen_clock, db_session):
    emp = make_account(email="hr@severny-kod.ru", role="employer")
    company = db_session.get(Company, db_session.get(User, emp.user_id).company_id)
    company.name = "Северный код"
    db_session.commit()
    acc = make_account()
    r = client.get("/api/candidates/me/blocklist/search", params={"q": "север"}, headers=acc.headers)
    assert r.status_code == 200
    found = r.json()
    assert found and found[0]["name"] == "Северный код" and found[0]["blocked"] is False
    r = client.put("/api/candidates/me/privacy", json={"blocked_company_ids": [found[0]["id"]]}, headers=acc.headers)
    assert r.json()["blocked_companies"] == [{"id": found[0]["id"], "name": "Северный код"}]
    r = client.get("/api/candidates/me/blocklist/search", params={"q": "код"}, headers=acc.headers)
    assert r.json()[0]["blocked"] is True
    assert client.get("/api/candidates/me/blocklist/search", params={"q": "к"}, headers=acc.headers).status_code == 422


def test_preview_blind_has_no_pii_and_disclosed_respects_field_visibility(client, make_account, frozen_clock, db_session):
    acc = make_account()
    _fill_profile(client, acc)
    confirm_qualification(db_session, acc.user_id, now=frozen_clock.now())
    client.put("/api/candidates/me/privacy", json={"salary_min": 200000}, headers=acc.headers)

    r = client.get("/api/candidates/me/preview", headers=acc.headers)
    assert r.status_code == 200, r.text
    pv = r.json()
    blind, disclosed = pv["blind"], pv["disclosed"]
    assert pv["pii_in_blind"] == [] and pii_keys_in(blind) == set()
    assert blind["view"] == "blind" and blind["pseudonym"].startswith("К-")
    assert blind["category"]["name"] == "Backend (Python) · Middle"
    assert "score" not in blind["test"] and blind["test"]["interval"]  # до раскрытия — полоса и интервал
    assert blind["region"] == "Республика Татарстан"
    flat = str(blind)
    for secret in ("Дебагова", "+79152041873", "maria_debug", "Казань", "Тихая гавань", "университет"):
        assert secret not in flat
    assert disclosed["full_name"] == "Дебагова Мария Олеговна"
    assert disclosed["phone"] == "+79152041873" and disclosed["city"] == "Казань"
    assert disclosed["test"]["score"] == 542
    assert any("Псевдоним" in n for n in pv["notes"])

    # Черновик настроек: телефон не раскрывать, вилка до 180 000 — превью меняется, профиль нет
    r = client.post(
        "/api/candidates/me/preview",
        json={"privacy": {"field_visibility": {"phone": False}}, "offer_salary_to": 180000},
        headers=acc.headers,
    )
    pv = r.json()
    assert "phone" not in pv["disclosed"] and pv["disclosed"]["full_name"]
    assert pv["blind"]["salary_min"] == {"signal": "выше вилки"}
    saved = client.get("/api/candidates/me/privacy", headers=acc.headers).json()
    assert next(f for f in saved["field_visibility"] if f["field"] == "phone")["allowed"] is True

    # Сохранили — раскрытая карточка без телефона; режим «не ищу» убирает из банка
    client.put("/api/candidates/me/privacy", json={"field_visibility": {"phone": False}, "search_mode": "hidden"}, headers=acc.headers)
    pv = client.get("/api/candidates/me/preview", headers=acc.headers).json()
    assert "phone" not in pv["disclosed"]
    assert pv["in_bank"]["visible"] is False
    assert any("Не ищу" in x for x in pv["in_bank"]["reasons"])


def test_bank_status_follows_consent_and_qualification(client, make_account, frozen_clock, db_session):
    acc = make_account()
    reasons = client.get("/api/candidates/me/privacy", headers=acc.headers).json()["bank"]["reasons"]
    assert any("Пройдите тест" in x for x in reasons) and any("согласие" in x for x in reasons)
    confirm_qualification(db_session, acc.user_id, now=frozen_clock.now())
    r = client.post("/api/consents", json={"purpose": "blind_card"}, headers=acc.headers)
    assert r.status_code == 201
    bank = client.get("/api/candidates/me/privacy", headers=acc.headers).json()["bank"]
    assert bank == {"visible": True, "reasons": []}


def test_consent_journal_with_titles_and_revoke_via_consents_api(client, make_account, frozen_clock):
    acc = make_account()
    client.post("/api/consents", json={"purpose": "blind_card"}, headers=acc.headers)
    journal = client.get("/api/candidates/me/consents", headers=acc.headers).json()
    by_purpose = {c["purpose"]: c for c in journal}
    assert by_purpose["processing"]["revocable"] is False
    blind = by_purpose["blind_card"]
    assert blind["active"] and blind["revocable"] and "банк" in blind["effect_of_revoke"]
    assert blind["title"].startswith("Согласие на показ слепой карточки")
    r = client.post(f"/api/consents/{blind['id']}/revoke", headers=acc.headers)
    assert r.status_code == 200
    journal = client.get("/api/candidates/me/consents", headers=acc.headers).json()
    revoked = next(c for c in journal if c["id"] == blind["id"])
    assert revoked["active"] is False and revoked["revoked_ago"] is not None


def test_visibility_stats_little_data(client, make_account, frozen_clock):
    acc = make_account()
    r = client.get("/api/candidates/me/visibility-stats", headers=acc.headers)
    assert r.status_code == 200
    s = r.json()
    assert s["enough_data"] is False and "мало" in s["message"]
    assert s["impressions"] == 0 and s["missing_skill"] is None


def test_profile_routes_are_candidate_only_and_own_data(client, make_account, frozen_clock, db_session):
    emp = make_account(role="employer")
    for method, path in (
        ("GET", "/api/candidates/me"),
        ("GET", "/api/candidates/me/privacy"),
        ("GET", "/api/candidates/me/preview"),
        ("GET", "/api/candidates/me/profile.pdf"),
    ):
        r = client.request(method, path, headers=emp.headers)
        assert r.status_code == 403, path
    assert client.get("/api/candidates/me").status_code == 401
    a = make_account()
    b = make_account()
    client.put("/api/candidates/me", json={"full_name": "Кэшев Лев"}, headers=a.headers)
    assert client.get("/api/candidates/me", headers=b.headers).json()["full_name"] is None
    rows = db_session.scalars(select(CandidateProfile).where(CandidateProfile.full_name == "Кэшев Лев")).all()
    assert [r.user_id for r in rows] == [a.user_id]


def test_visibility_stats_with_impressions_and_missing_skill(client, make_account, frozen_clock, db_session):
    from datetime import timedelta

    from app.models.matching import ImpressionLog, Need, NeedVersion, ProfileView

    acc = make_account()
    emp = make_account(email="hr@severny-kod.ru", role="employer")
    company_id = db_session.get(User, emp.user_id).company_id
    now = frozen_clock.now()
    other = "00000000-0000-0000-0000-000000000077"
    for i in range(3):
        need = Need(company_id=company_id, raw_text=f"Бриф {i}", salary_from=200000, salary_to=280000)
        db_session.add(need)
        db_session.flush()
        items = [
            {"candidate_id": other, "chips": [{"skill_id": "pytorch", "kind": "must", "state": "declared"}]},
            {
                "candidate_id": str(acc.user_id),
                "chips": [
                    {"skill_id": "pytorch", "kind": "must", "state": "no_data"},
                    {"skill_id": "python", "kind": "must", "state": "confirmed"},
                    {"skill_id": "docker", "kind": "nice", "state": "declared"},
                ],
            },
        ]
        nv = NeedVersion(need_id=need.id, n=1, scoring_config_version="t", items=items)
        db_session.add(nv)
        db_session.flush()
        for rank, days in ((3 + i * 9, 1), (5 + i * 9, 2)):
            db_session.add(ImpressionLog(need_id=need.id, version_id=nv.id, candidate_id=acc.user_id, rank=rank, shown_at=now - timedelta(days=days)))
    db_session.add(ImpressionLog(need_id=need.id, version_id=nv.id, candidate_id=acc.user_id, rank=1, shown_at=now - timedelta(days=30)))
    db_session.add(ProfileView(company_id=company_id, candidate_id=acc.user_id, at=now - timedelta(days=1)))
    db_session.commit()
    s = client.get("/api/candidates/me/visibility-stats", headers=acc.headers).json()
    assert s["impressions"] == 6 and s["selections"] == 3 and s["enough_data"] is True
    assert s["top10"] == 1  # лучшие позиции в подборках: 3, 12, 21 (показ месячной давности не считается)
    assert s["median_position"] == 12.0
    assert s["profile_views"] == 1
    assert s["missing_skill"]["skill"] == "pytorch" and s["missing_skill"]["count"] == 3
    assert "не проверяет" in s["missing_skill"]["action"]  # PyTorch тестом не проверяется — честная подсказка
    assert "в 3 подборках, в 1 — в топ-10" in s["message"]
