"""Платформа: защита от IDOR («владелец записи»), проверка ролей, журнал согласий 152-ФЗ."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

import pytest
from fastapi import Depends
from sqlalchemy import select

from app.api.deps import require_roles
from app.core import consents as consents_mod
from app.core.access import ensure_owner, ensure_role, get_owned_or_404, is_owner
from app.core.errors import ApiError
from app.models import Company, Consent, ContactDisclosure, User
from app.models.enums import UserRole

# --- Хелпер IDOR без HTTP ---


def _user(role="candidate", company_id=None):
    return SimpleNamespace(id=uuid.uuid4(), role=role, company_id=company_id)


def test_ensure_owner_hides_foreign_records_as_404():
    alice, bob = _user(), _user()
    record = SimpleNamespace(user_id=alice.id)
    assert ensure_owner(record, alice) is record
    with pytest.raises(ApiError) as e:
        ensure_owner(record, bob)
    assert e.value.status_code == 404 and e.value.code == "not_found"  # не 403: не раскрываем существование
    with pytest.raises(ApiError) as e:
        ensure_owner(None, alice)
    assert e.value.status_code == 404
    admin = _user("admin")
    assert ensure_owner(record, admin, allow_roles=("admin",)) is record
    with pytest.raises(ApiError):
        ensure_owner(record, admin)  # без явного разрешения админ тоже не владелец


def test_ensure_owner_by_company():
    company = uuid.uuid4()
    hr, stranger, candidate = _user("employer", company), _user("employer", uuid.uuid4()), _user()
    need = SimpleNamespace(company_id=company)
    assert is_owner(need, hr, "company_id")
    assert not is_owner(need, stranger, "company_id")
    assert not is_owner(need, candidate, "company_id")  # company_id None ≠ company


def test_ensure_role():
    ensure_role(_user("employer"), "employer", "admin")
    with pytest.raises(ApiError) as e:
        ensure_role(_user("candidate"), "employer")
    assert e.value.status_code == 403 and e.value.code == "role_forbidden"
    assert e.value.details == {"required": ["employer"], "role": "candidate"}


def test_get_owned_or_404_with_db(db_session, make_account):
    a, b = make_account(), make_account()
    user_a, user_b = db_session.get(User, a.user_id), db_session.get(User, b.user_id)
    consent_a = db_session.scalar(select(Consent).where(Consent.user_id == a.user_id))
    assert get_owned_or_404(db_session, Consent, consent_a.id, user_a).id == consent_a.id
    assert get_owned_or_404(db_session, Consent, str(consent_a.id), user_a).id == consent_a.id
    for bad in (consent_a.id, uuid.uuid4(), "не-uuid"):
        with pytest.raises(ApiError) as e:
            get_owned_or_404(db_session, Consent, bad, user_b)
        assert e.value.status_code == 404


# --- IDOR через API: два токена ---


def test_cannot_revoke_someone_elses_consent(client, make_account, db_session):
    alice, bob = make_account(), make_account()
    r = client.post("/api/consents", json={"purpose": "blind_card"}, headers=alice.headers)
    alice_consent = db_session.get(Consent, uuid.UUID(r.json()["id"]))

    r = client.post(f"/api/consents/{alice_consent.id}/revoke", headers=bob.headers)
    assert r.status_code == 404
    assert r.json() == {"code": "not_found", "message": "Согласие не найдено", "details": None}
    db_session.refresh(alice_consent)
    assert alice_consent.revoked_at is None

    # Чужое согласие не видно и в списке
    listed = client.get("/api/consents", headers=bob.headers).json()
    assert alice_consent.id not in {uuid.UUID(c["id"]) for c in listed}

    r = client.post(f"/api/consents/{alice_consent.id}/revoke", headers=alice.headers)
    assert r.status_code == 200 and r.json()["active"] is False and r.json()["revoked_at"]
    r = client.post(f"/api/consents/{alice_consent.id}/revoke", headers=alice.headers)
    assert r.status_code == 409 and r.json()["code"] == "consent_already_revoked"
    # Некорректный id → 422 (валидация пути), без утечки
    assert client.post("/api/consents/123/revoke", headers=alice.headers).status_code == 422


def test_require_roles_dependency(app, client, make_account):
    @app.get("/api/_test/employers-only")
    def _only_employers(user: User = Depends(require_roles(UserRole.employer, UserRole.admin))):  # noqa: B008
        return {"id": str(user.id)}

    employer, candidate = make_account(role="employer"), make_account(role="candidate")
    assert client.get("/api/_test/employers-only", headers=employer.headers).status_code == 200
    r = client.get("/api/_test/employers-only", headers=candidate.headers)
    assert r.status_code == 403 and r.json()["code"] == "role_forbidden"
    assert r.json()["message"] == "Это действие недоступно для вашей роли"
    assert client.get("/api/_test/employers-only").status_code == 401


# --- Журнал согласий ---


def test_consent_documents_public(client):
    docs = client.get("/api/consents/documents").json()
    purposes = {d["purpose"]: d for d in docs}
    assert set(purposes) == {"processing", "blind_card", "fsp_enrichment", "contact_disclosure", "test_logging", "calibration"}
    assert purposes["processing"]["required_for_registration"] is True
    assert purposes["processing"]["revocable_in_journal"] is False
    assert purposes["blind_card"]["revocable_in_journal"] is True
    assert purposes["contact_disclosure"]["grantable_in_journal"] is False
    assert all(d["title"] and d["summary"] and d["version"] for d in docs)


def test_processing_consent_not_revocable_from_journal(client, make_account, db_session):
    """Иначе запись была бы «отозвана», а обработка ПДн аккаунта продолжалась бы."""
    acc = make_account()
    processing = db_session.scalar(select(Consent).where(Consent.user_id == acc.user_id, Consent.purpose == "processing"))
    r = client.post(f"/api/consents/{processing.id}/revoke", headers=acc.headers)
    assert r.status_code == 409 and r.json()["code"] == "consent_not_revocable_here"
    assert "удалением аккаунта" in r.json()["message"]
    db_session.refresh(processing)
    assert processing.revoked_at is None


def test_repeated_grant_does_not_duplicate_and_revoke_covers_purpose(client, make_account, db_session):
    acc = make_account()
    first = client.post("/api/consents", json={"purpose": "blind_card"}, headers=acc.headers)
    again = client.post("/api/consents", json={"purpose": "blind_card", "text_version": "2026-10-05"}, headers=acc.headers)
    assert first.status_code == 201 and again.status_code == 200 and again.json()["id"] == first.json()["id"]
    # Две действующие записи одной цели (например, выданы модулем напрямую) — отзыв гасит обе
    extra = consents_mod.grant_consent(db_session, acc.user_id, "blind_card")
    db_session.commit()
    r = client.post(f"/api/consents/{first.json()['id']}/revoke", headers=acc.headers)
    assert r.status_code == 200
    db_session.refresh(extra)
    assert extra.revoked_at is not None
    assert not consents_mod.has_active_consent(db_session, acc.user_id, "blind_card")
    # После отзыва повторная выдача — новая запись (201)
    r = client.post("/api/consents", json={"purpose": "blind_card"}, headers=acc.headers)
    assert r.status_code == 201 and r.json()["id"] != first.json()["id"]


def test_revoking_one_disclosure_keeps_other_companies(db_session, make_account):
    """contact_disclosure привязано к компании: отзыв для одной компании не трогает другую."""
    cand = make_account()
    c1 = consents_mod.grant_consent(db_session, cand.user_id, "contact_disclosure", scope={"company_id": "a"})
    c2 = consents_mod.grant_consent(db_session, cand.user_id, "contact_disclosure", scope={"company_id": "b"})
    consents_mod.revoke_consent(db_session, c1)
    db_session.refresh(c2)
    assert c1.revoked_at is not None and c2.revoked_at is None


def test_grant_and_list_consents(client, make_account):
    acc = make_account()
    r = client.post("/api/consents", json={"purpose": "blind_card"}, headers=acc.headers)
    assert r.status_code == 201 and r.json()["purpose"] == "blind_card" and r.json()["active"]
    r = client.post("/api/consents", json={"purpose": "test_logging", "text_version": "2026-10-05"}, headers=acc.headers)
    assert r.status_code == 201
    listed = client.get("/api/consents", headers=acc.headers).json()
    assert [c["purpose"] for c in listed].count("processing") == 1
    assert {"blind_card", "test_logging", "processing"} == {c["purpose"] for c in listed}

    r = client.post("/api/consents", json={"purpose": "contact_disclosure"}, headers=acc.headers)
    assert r.status_code == 400 and r.json()["code"] == "consent_contextual"
    r = client.post("/api/consents", json={"purpose": "fsp_enrichment", "text_version": "1999-01-01"}, headers=acc.headers)
    assert r.status_code == 409 and r.json()["code"] == "consent_outdated"
    r = client.post("/api/consents", json={"purpose": "marketing"}, headers=acc.headers)
    assert r.status_code == 422
    assert client.get("/api/consents").status_code == 401


def test_revoking_disclosure_consent_hides_contacts_and_calls_hooks(db_session, make_account):
    cand, emp = make_account(), make_account("hr@acme-test.ru", role="employer")
    company = db_session.get(Company, db_session.get(User, emp.user_id).company_id)
    consent = consents_mod.grant_consent(
        db_session, cand.user_id, "contact_disclosure", scope={"company_id": str(company.id)}
    )
    disclosure = ContactDisclosure(
        candidate_id=cand.user_id,
        company_id=company.id,
        basis="invitation",
        basis_id=uuid.uuid4(),
        fields=["full_name", "phone"],
        consent_id=consent.id,
        consent_text_ver=consent.text_version,
    )
    db_session.add(disclosure)
    db_session.flush()

    seen: list[uuid.UUID] = []
    hook = consents_mod.on_consent_revoked(lambda db, c: seen.append(c.id))
    try:
        consents_mod.revoke_consent(db_session, consent)
    finally:
        consents_mod._revoke_hooks.remove(hook)
    db_session.refresh(disclosure)
    assert disclosure.revoked_at is not None and consent.revoked_at is not None
    assert seen == [consent.id]
    assert not consents_mod.has_active_consent(db_session, cand.user_id, "contact_disclosure")
    assert consents_mod.has_active_consent(db_session, cand.user_id, "processing")


def test_regrant_creates_new_record(db_session, make_account):
    acc = make_account()
    first = consents_mod.grant_consent(db_session, acc.user_id, "blind_card")
    consents_mod.revoke_consent(db_session, first)
    second = consents_mod.grant_consent(db_session, acc.user_id, "blind_card")
    assert second.id != first.id and first.revoked_at is not None and second.revoked_at is None
    rows = db_session.scalars(select(Consent).where(Consent.user_id == acc.user_id, Consent.purpose == "blind_card")).all()
    assert len(rows) == 2
