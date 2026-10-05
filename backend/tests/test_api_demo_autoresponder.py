"""Автоответчик синтетических кандидатов (G2, DECISIONS п. 4): правила «Да» / «Да, если: от min» / «Нет», задержка 30–90 с,
ответ через тот же слой, что HTTP-роуты, персоны-люди автоответчик не трогает."""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.core.clock import clock
from app.models.candidate import CandidateProfile
from app.models.contact import Invitation
from app.models.users import User
from app.services.demo import accounts, autoresponder
from app.services.demo import models as _demo_models  # noqa: F401
from app.services.demo.responder import Terms
from app.services.demo.state import get_state
from tests.test_api_contact_support import invite, make_candidate, make_employer, open_card

pytestmark = pytest.mark.usefixtures("frozen_clock")

NET = Terms(salary_from=220_000, salary_to=280_000, salary_net=True, format="hybrid")


def decide(terms: Terms = NET, **kw):
    base = {"salary_min": 230_000, "formats": [], "persona_reason": "stack"}
    return autoresponder.decide(terms, **{**base, **kw})


def test_yes_when_salary_to_covers_minimum_and_format_ok():
    assert decide().kind == "accept"
    assert decide(salary_min=280_000).kind == "accept"  # to ≥ min, граница включена
    assert decide(salary_min=None).kind == "accept"  # минимума нет
    assert decide(formats=["hybrid", "remote"]).kind == "accept"


def test_counter_when_minimum_above_range_but_within_1_2x():
    d = decide(salary_min=300_000)  # 280 000 < 300 000 ≤ 336 000
    assert d.kind == "counter" and d.salary_from == 300_000
    d = decide(salary_min=301_000)  # шаг встречного — 5000, округляем вверх
    assert d.kind == "counter" and d.salary_from == 305_000
    edge = decide(salary_min=336_000)  # ровно 1,2·to
    assert edge.kind == "counter" and edge.salary_from == 340_000
    assert edge.salary_from <= int(1.3 * NET.salary_to)  # потолок встречного 1,3·to (config/contact.yaml)


def test_no_with_persona_reason_when_minimum_far_above():
    d = decide(salary_min=336_001, persona_reason="other_process")
    assert d.kind == "decline" and d.reason == "other_process"
    assert decide(salary_min=500_000, persona_reason="stack").reason == "stack"


def test_format_mismatch_with_fitting_salary_is_honest_format_decline():
    d = decide(formats=["remote"], persona_reason="stack")
    assert d.kind == "decline" and d.reason == "format"


def test_gross_range_is_compared_net_of_ndfl():
    gross = Terms(salary_from=250_000, salary_to=300_000, salary_net=False, format=None)  # на руки ≈ 261 000
    assert decide(gross, salary_min=260_000).kind == "accept"
    d = decide(gross, salary_min=270_000)
    assert d.kind == "counter" and d.salary_from is not None and d.salary_from > 300_000  # в валюте вилки (до НДФЛ)
    assert d.salary_from <= int(1.3 * 300_000)


def test_delay_is_deterministic_and_within_30_90_seconds():
    ids = [uuid.uuid4() for _ in range(200)]
    delays = [autoresponder.delay_s(i) for i in ids]
    assert all(30 <= d <= 90 for d in delays)
    assert delays == [autoresponder.delay_s(i) for i in ids]
    assert len(set(delays)) > 20  # разброс есть


def _world(client, make_account, db):
    emp = make_employer(client, make_account, db)
    cand = make_candidate(make_account, db, salary_min=230_000)
    accounts.mark(db, db.get(User, cand.user_id), "synthetic", "c9999", decline_reason="stack")
    alias = open_card(db, emp, cand)
    get_state(db).scenario_built_at = clock.now() - timedelta(hours=1)  # приглашение отправлено ПОСЛЕ сборки мира
    db.flush()
    return emp, cand, alias


def test_tick_waits_for_delay_then_answers_yes(client, make_account, db_session):
    emp, cand, alias = _world(client, make_account, db_session)
    r = invite(client, emp, alias.ref)
    assert r.status_code == 201, r.text
    inv_id = uuid.UUID(r.json()["sent"][0]["id"])

    first = autoresponder.tick(db_session)
    assert first.answered == 0  # задержка 30–90 с ещё не прошла
    clock.advance(91)
    res = autoresponder.tick(db_session)
    db_session.flush()
    assert res.answered == 1 and res.accepted == 1 and not res.errors
    inv = db_session.get(Invitation, inv_id)
    db_session.refresh(inv)
    assert inv.status == "accepted"  # вилка 220–280 ≥ минимума 230, формат не ограничен
    assert autoresponder.tick(db_session).answered == 0  # повторно не отвечает


def test_tick_counter_and_decline_paths(client, make_account, db_session):
    emp, cand, alias = _world(client, make_account, db_session)
    prof = db_session.get(CandidateProfile, cand.user_id)
    prof.salary_min = 300_000  # выше вилки, но в пределах 1,2×
    db_session.flush()
    r = invite(client, emp, alias.ref)
    inv_id = uuid.UUID(r.json()["sent"][0]["id"])
    res = autoresponder.tick(db_session, force=True)
    assert res.countered == 1, res.as_dict()
    inv = db_session.get(Invitation, inv_id)
    db_session.refresh(inv)
    assert inv.status == "countered" and inv.counter["salary_from"] == 300_000

    emp2, cand2, alias2 = _world(client, make_account, db_session)
    db_session.get(CandidateProfile, cand2.user_id).salary_min = 500_000
    db_session.flush()
    r2 = invite(client, emp2, alias2.ref)
    inv2_id = uuid.UUID(r2.json()["sent"][0]["id"])
    res2 = autoresponder.tick(db_session, force=True)
    assert res2.declined == 1, res2.as_dict()
    inv2 = db_session.get(Invitation, inv2_id)
    db_session.refresh(inv2)
    assert inv2.status == "declined" and inv2.decline_reason == "stack"  # причина из персоны


def test_human_personas_and_seeded_invitations_are_left_alone(client, make_account, db_session):
    emp = make_employer(client, make_account, db_session)
    human = make_candidate(make_account, db_session, salary_min=230_000)  # не отмечен как synthetic
    alias = open_card(db_session, emp, human)
    r = invite(client, emp, alias.ref)
    assert r.status_code == 201
    clock.advance(3600)
    assert autoresponder.tick(db_session, force=True).answered == 0

    emp2, cand2, alias2 = _world(client, make_account, db_session)
    get_state(db_session).scenario_built_at = clock.now() + timedelta(hours=1)  # приглашение «до сборки» — заготовка
    db_session.flush()
    invite(client, emp2, alias2.ref)
    assert autoresponder.tick(db_session, force=True).answered == 0


def test_autoresponder_can_be_switched_off(client, make_account, db_session):
    emp, cand, alias = _world(client, make_account, db_session)
    invite(client, emp, alias.ref)
    get_state(db_session).autoresponder = False
    db_session.flush()
    clock.advance(300)
    assert autoresponder.tick(db_session).answered == 0
    assert autoresponder.tick(db_session, force=True).answered == 1  # «ответить сейчас» работает и при выключенном автоответе
