"""Кандидат с неподтверждённым грейдом (ответы организаторов, DECISIONS п. 29): показываем со статусом
«грейд не подтверждён» и ниже подтверждённых, а не прячем. Банк и подборка; охват и счётчики — только по
подтверждённым; слепые правила (согласие, нет ПДн) прежние."""

from __future__ import annotations

import uuid

import pytest

from app.core.clock import clock
from app.core.consents import grant_consent
from app.models.candidate import CandidateProfile, SurveyResponse
from app.models.testing import TestSession
from app.models.users import User
from tests.test_api_needs_support import DEMO_BRIEF, assert_no_pii, seed_demo_pool

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def employer(make_account):
    return make_account("hr@severny-kod.ru", role="employer")


def _untested(db, *, spec="backend_python", grade="middle", blind=True, session=False, status="active") -> uuid.UUID:
    """Кандидат прошёл опрос (и, возможно, провалил тест), категория заявлена, подтверждения нет."""
    uid = uuid.uuid4()
    db.add(User(id=uid, email=f"u-{uid.hex[:10]}@example.com", email_domain="example.com", role="candidate",
                status=status, email_verified=True))
    db.flush()
    db.add(CandidateProfile(user_id=uid, salary_min=200_000, formats=["hybrid"], region_code="77",
                            industries=["fintech"], full_name="Петров Пётр Петрович", contact_email="p@mail.example"))
    db.flush()
    if blind:
        grant_consent(db, uid, "blind_card")
    db.add(SurveyResponse(candidate_id=uid, step=4, chosen_spec=spec, suggested_grade=grade, completed_at=clock.now()))
    if session:
        db.add(TestSession(candidate_id=uid, specialization=spec, kind="initial", claimed_grade=grade,
                           status="FINAL", seed=1))
    db.flush()
    return uid


def _bank(client, emp, **params):
    body = client.get("/api/bank", params=params, headers=emp.headers).json()
    items = list(body["items"])
    for p in range(2, body["pages"] + 1):
        items += client.get("/api/bank", params={**params, "page": p}, headers=emp.headers).json()["items"]
    return items


def test_bank_shows_unconfirmed_below_all_confirmed_with_status(client, db_session, employer):
    seed_demo_pool(db_session)
    survey_only = _untested(db_session, spec="data_ml", grade="junior")
    claimed = _untested(db_session, grade="middle", session=True)
    no_consent = _untested(db_session, blind=False)
    items = _bank(client, employer)
    flags = [i["confirmed"] for i in items]
    # все подтверждённые выше всех неподтверждённых (хвост)
    assert flags == sorted(flags, reverse=True) and flags.count(False) == 2
    tail = [i for i in items if not i["confirmed"]]
    assert {i["spec"] for i in tail} == {"backend_python", "data_ml"}
    assert all(i["test"]["interval"] is None or not i["test"]["interval"] for i in tail)
    mid = _bank(client, employer, category="backend_python:middle")
    assert [i["confirmed"] for i in mid].count(False) == 1
    only_ml = _bank(client, employer, spec="data_ml")
    assert len(only_ml) == 1 and only_ml[0]["confirmed"] is False and only_ml[0]["grade"] == "junior"
    # слепота: без согласия не виден, ПДн не утекают
    from app.models.users import User as U
    from app.services.matching import resolve_ref

    company = db_session.get(U, employer.user_id).company_id
    seen = {resolve_ref(db_session, company, i["ref"]) for i in items}
    assert no_consent not in seen and {survey_only, claimed} <= seen
    assert_no_pii(items, [survey_only, claimed, no_consent], ["Петров", "p@mail.example"])


def test_selection_ranks_unconfirmed_after_confirmed_and_explains(client, db_session, employer):
    seed_demo_pool(db_session)
    _untested(db_session, grade="middle", session=True)
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    items = list(v1["items"])
    for p in range(2, v1["pages"] + 1):
        items += client.get(f"/api/needs/{nid}/versions/1", params={"page": p}, headers=employer.headers).json()["items"]
    flags = [i["confirmed"] for i in items]
    assert flags.count(False) == 1 and flags[-1] is False and all(flags[:-1])
    tail = items[-1]
    assert "unconfirmed" in tail["flags"]
    assert tail["con"]["text"] == "Категория заявлена, тестом не подтверждена"
    # охват и рекомендации по подтверждённым: неподтверждённый в «N кандидатов» категории не входит
    cats = client.get(f"/api/needs/{nid}", headers=employer.headers).json()["categories"]
    primary = next(c for c in cats if (c["spec"], c["grade"]) == ("backend_python", "middle"))
    assert primary["n"] == 13
    # фильтр «только подтверждённые тестом» убирает хвост в «Скрыто фильтрами»
    v2 = client.post(f"/api/needs/{nid}/versions", json={"filters": [{"code": "confirmed_only"}]},
                     headers=employer.headers).json()
    assert all(i["confirmed"] for i in v2["items"]) and len(v2["hidden"]["items"]) >= 1
