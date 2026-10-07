"""Несколько подтверждённых категорий у одного кандидата (Backend + QA): банк находит его по каждой
специализации, карточка работодателя показывает все категории (DECISIONS п. 29, п. 6 Q&A)."""

from __future__ import annotations

import pytest

from app.models.users import User
from app.services.matching import resolve_ref
from tests.test_api_needs_support import _qualification, seed_candidate

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def employer(make_account):
    return make_account("hr@severny-kod.ru", role="employer")


def test_candidate_with_backend_and_qa_found_by_both_and_card_lists_both(client, db_session, employer):
    uid = seed_candidate(db_session, theta=0.6)
    _qualification(
        db_session, uid, "qa", "junior", -0.4, se=0.3, trust=1.0, borderline=False, days_ago=10,
        modules={"qa_testing": (-0.4, 0.35, "confirmed")}, with_session=True,
    )
    company = db_session.get(User, employer.user_id).company_id
    by_spec = {}
    for spec in ("backend_python", "qa"):
        items = client.get("/api/bank", params={"spec": spec}, headers=employer.headers).json()["items"]
        assert len(items) == 1, spec
        assert resolve_ref(db_session, company, items[0]["ref"]) == uid and items[0]["confirmed"] is True
        by_spec[spec] = items[0]
    assert by_spec["backend_python"]["grade"] == "middle" and by_spec["qa"]["grade"] == "junior"
    card = client.get(f"/api/bank/candidates/{by_spec['qa']['ref']}", headers=employer.headers).json()
    names = set(card["category"]["all"])
    assert names == {"Backend (Python) · Middle", "QA / тестирование · Junior"} and card["category"]["unconfirmed"] == []
