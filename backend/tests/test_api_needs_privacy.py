"""API подбора: роли, IDOR, приватность (DECISIONS п. 20, концепция §4.5).

- только «Нанимаю», только свои брифы (чужой — 404, неотличим от несуществующего);
- кандидат — псевдоним HMAC(компания‖кандидат) и ссылка ref, свои у каждой компании;
- до «да» 0 полей ПДн, ни одного внутреннего id кандидата, номера ЕКП и ссылки на протокол;
- после ContactDisclosure — CandidateDisclosed с разрешёнными полями и выпиской ФСП;
- скрытые (режим «не ищу», без согласия, младше 16, на проверке методистом) не видны никогда.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.clock import clock
from app.models.contact import ContactDisclosure
from app.models.matching import CandidateAlias
from app.models.users import User
from tests.test_api_needs_support import (
    DEMO_BRIEF,
    FSP_MEDIUM,
    FSP_STRONG,
    assert_no_pii,
    ekp_numbers,
    seed_demo_pool,
)

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def pool(db_session):
    return seed_demo_pool(db_session)


@pytest.fixture
def two_employers(make_account):
    return make_account("hr@severny-kod.ru", role="employer"), make_account("lead@yuzhny-bereg.ru", role="employer")


def _need_with_version(client, emp) -> tuple[str, dict]:
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=emp.headers).json()["id"]
    v = client.post(f"/api/needs/{nid}/versions", json={}, headers=emp.headers)
    assert v.status_code == 201, v.text
    return nid, v.json()


def _company(db, account) -> uuid.UUID:
    return db.get(User, account.user_id).company_id


def test_roles_and_auth(client, make_account):
    cand = make_account(role="candidate")
    for method, path in (
        ("POST", "/api/needs/parse"),
        ("POST", "/api/needs"),
        ("GET", "/api/needs"),
        ("GET", "/api/bank"),
        ("GET", f"/api/needs/{uuid.uuid4()}/selection"),
    ):
        body = {"text": DEMO_BRIEF} if method == "POST" else None
        r = client.request(method, path, json=body, headers=cand.headers)
        assert r.status_code == 403, (method, path, r.text)
        r = client.request(method, path, json=body)
        assert r.status_code == 401, (method, path)
        assert r.json()["code"] == "not_authenticated"


def test_idor_foreign_need_and_refs(client, db_session, pool, two_employers):
    a, b = two_employers
    nid_a, v_a = _need_with_version(client, a)
    nid_b, v_b = _need_with_version(client, b)
    ref_a = v_a["items"][0]["ref"]
    for method, path in (
        ("GET", f"/api/needs/{nid_a}"),
        ("PATCH", f"/api/needs/{nid_a}"),
        ("GET", f"/api/needs/{nid_a}/selection"),
        ("POST", f"/api/needs/{nid_a}/versions"),
        ("GET", f"/api/needs/{nid_a}/versions/1"),
        ("GET", f"/api/needs/{nid_a}/categories"),
        ("GET", f"/api/needs/{nid_a}/check"),
        ("GET", f"/api/needs/{nid_a}/shortlist"),
        ("PUT", f"/api/needs/{nid_a}/shortlist/{ref_a}"),
        ("GET", f"/api/needs/{nid_a}/candidates/{ref_a}"),
        ("POST", f"/api/needs/{nid_a}/rollback"),
    ):
        body = {} if method in ("PATCH", "POST") else None
        if path.endswith("rollback"):
            body = {"n": 1}
        r = client.request(method, path, json=body, headers=b.headers)
        assert r.status_code == 404, (method, path, r.text)
    # Ссылка компании A для компании B ничего не значит
    assert client.put(f"/api/needs/{nid_b}/shortlist/{ref_a}", headers=b.headers).status_code == 404
    assert client.get(f"/api/needs/{nid_b}/candidates/{ref_a}", headers=b.headers).status_code == 404
    assert client.get(f"/api/bank/candidates/{ref_a}", headers=b.headers).status_code == 404
    assert client.get("/api/bank", params={"need_id": nid_a}, headers=b.headers).status_code == 404
    # Один и тот же кандидат у двух компаний — разные ref и (почти всегда) разные псевдонимы
    rows = db_session.execute(select(CandidateAlias.candidate_id, CandidateAlias.company_id, CandidateAlias.ref, CandidateAlias.pseudonym)).all()
    by_cand: dict = {}
    for cid, comp, ref, ps in rows:
        by_cand.setdefault(cid, {})[comp] = (ref, ps)
    both = [v for v in by_cand.values() if len(v) == 2]
    assert len(both) >= 10
    assert all(len({x[0] for x in v.values()}) == 2 for v in both)
    assert sum(len({x[1] for x in v.values()}) == 2 for v in both) >= len(both) - 1


def test_blind_dto_has_no_pii_ids_or_ekp(client, db_session, pool, two_employers):
    a, _ = two_employers
    nid, v1 = _need_with_version(client, a)
    forbidden = ekp_numbers(FSP_STRONG) | ekp_numbers(FSP_MEDIUM) | {"protocol", "fsp.example", "/protocols/"}
    page2 = client.get(f"/api/needs/{nid}/selection", params={"page": 2}, headers=a.headers).json()
    refs = [i["ref"] for i in v1["items"] + page2["items"]]
    client.put(f"/api/needs/{nid}/shortlist/{refs[0]}", headers=a.headers)
    payloads = [
        v1,
        page2,
        client.get(f"/api/needs/{nid}/shortlist", headers=a.headers).json(),
        client.get("/api/bank", headers=a.headers).json(),
        client.get("/api/bank", params={"page": 2}, headers=a.headers).json(),
    ]
    for ref in refs:
        card = client.get(f"/api/needs/{nid}/candidates/{ref}", headers=a.headers)
        assert card.status_code == 200, card.text
        assert card.json()["view"] == "blind" and card.json()["disclosed"] is None
        payloads.append(card.json())
    for p in payloads:
        assert_no_pii(p, pool.values(), forbidden)
    # Ни фамилии, ни контактов из профиля
    text = str(payloads)
    assert "Иванова" not in text and "@mail.example" not in text and "+7 900" not in text


def test_hidden_candidates_never_reach_employer(client, db_session, pool, two_employers):
    a, _ = two_employers
    nid, _ = _need_with_version(client, a)
    client.get("/api/bank", headers=a.headers)
    client.get("/api/bank", params={"page": 2}, headers=a.headers)
    client.get(f"/api/needs/{nid}/selection", params={"page": 2}, headers=a.headers)
    seen = set(db_session.scalars(select(CandidateAlias.candidate_id).where(CandidateAlias.company_id == _company(db_session, a))))
    for key in ("hidden_mode", "no_consent", "minor", "under_review"):
        assert pool[key] not in seen, key
    assert {pool["anna"], pool["boris"], pool["senior0"], pool["fullstack"]} <= seen


def test_disclosed_card_after_mutual_yes(client, db_session, pool, two_employers):
    a, b = two_employers
    nid_a, v_a = _need_with_version(client, a)
    anna_ref = next(i["ref"] for i in v_a["items"] if "Призёр" in (i["fsp_line"] or ""))
    company_a = _company(db_session, a)
    from app.services.matching.privacy import resolve_ref

    anna = resolve_ref(db_session, company_a, anna_ref)
    assert anna == pool["anna"]
    db_session.add(
        ContactDisclosure(
            candidate_id=anna,
            company_id=company_a,
            basis="invitation",
            basis_id=uuid.uuid4(),
            fields=["full_name", "email", "telegram"],
            consent_text_ver="contact-consent-v1",
            granted_at=clock.now(),
        )
    )
    db_session.flush()
    r = client.get(f"/api/needs/{nid_a}/candidates/{anna_ref}", headers=a.headers)
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["view"] == "disclosed"
    d = card["disclosed"]
    assert d["basis"] == "invitation" and d["full_name"] == "Иванова Мария Сергеевна"
    assert d["email"] and d["email"].endswith("@mail.example") and d["telegram"] == "@cand"
    assert d["phone"] is None  # телефон кандидат не разрешил
    assert d["city"] == "Москва"
    assert card["test"]["score"] is not None  # точный балл — после раскрытия
    assert d["fsp_extract"] and any(e in d["fsp_extract"] for e in ekp_numbers(FSP_STRONG))
    # Компания B по-прежнему видит слепую карточку
    nid_b, v_b = _need_with_version(client, b)
    company_b = _company(db_session, b)
    ref_b = next(i["ref"] for i in v_b["items"] if resolve_ref(db_session, company_b, i["ref"]) == anna)
    card_b = client.get(f"/api/needs/{nid_b}/candidates/{ref_b}", headers=b.headers).json()
    assert card_b["view"] == "blind" and card_b["disclosed"] is None
    assert_no_pii(card_b, pool.values(), ekp_numbers(FSP_STRONG))
    # Отзыв согласия — снова слепая
    row = db_session.scalars(select(ContactDisclosure).where(ContactDisclosure.candidate_id == anna)).one()
    row.revoked_at = clock.now()
    db_session.flush()
    assert client.get(f"/api/needs/{nid_a}/candidates/{anna_ref}", headers=a.headers).json()["view"] == "blind"


def test_contract_for_invitations(db_session, pool, two_employers, client):
    """candidate_profile_view (require_visible=False) — всегда словарь, даже для скрытого кандидата."""
    from app.services.matching import candidate_profile_view, get_alias, score_snapshot

    a, _ = two_employers
    nid, v1 = _need_with_version(client, a)
    company = _company(db_session, a)
    hidden = candidate_profile_view(db_session, company_id=company, candidate_id=pool["hidden_mode"])
    assert hidden["view"] == "blind" and hidden["available"] is False and hidden["category"] is None
    assert hidden["pseudonym"] == get_alias(db_session, company, pool["hidden_mode"]).pseudonym
    full = candidate_profile_view(db_session, company_id=company, candidate_id=pool["boris"], need_id=uuid.UUID(nid))
    assert full["view"] == "blind" and full["available"] is True and full["category"]["grade"] == "middle"
    snap = score_snapshot(db_session, uuid.UUID(nid), pool["boris"])
    assert snap and snap["rank"] >= 1 and snap["why_you"] and "candidate_id" not in snap
    assert score_snapshot(db_session, uuid.UUID(nid), pool["hidden_mode"]) is None
