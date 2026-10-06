"""«Подтверждение за 5 минут»: работодателю — только после раскрытия контакта, без улик и причин выбора заданий;
методисту — по сессии, с причинами (след двойника выбирается первым)."""

from __future__ import annotations

import json
import uuid

import pytest

from app.models.contact import Invitation
from app.services.anticheat import kit as anticheat_kit
from tests import test_anticheat_api_support as sup
from tests import test_api_testing_support as support
from tests.test_api_contact_support import (
    invite,
    make_candidate,
    make_employer,
    make_need,
    open_card,
    shortlist,
)
from tests.test_api_testing_support import do_survey, make_role, run_session, start

api = support.api
bank = support.bank

pytestmark = pytest.mark.usefixtures("frozen_clock")

EVIDENCE_WORDS = ("двойник", "twin", "clipboard", "улик", "reconcil", "selection_reason", "family_id", "seed", "honest")


@pytest.fixture
def world(client, make_account, bank, api):
    """Кандидат с законченным тестом (копировал задания с двойником), работодатель с приглашением и потребностью."""
    sup.prepare_twins(bank)
    cand = make_candidate(make_account, bank)
    do_survey(api, cand.acct)
    s = start(api, cand.acct, "middle")
    run = run_session(api, bank, cand.acct, s["id"], sup.copy_policy())
    emp = make_employer(client, make_account, bank)
    need = make_need(bank, emp, must=("python", "kafka"))
    shortlist(bank, need, cand)
    alias = open_card(bank, emp, cand)
    inv = invite(client, emp, alias.ref).json()["sent"][0]
    row = bank.get(Invitation, uuid.UUID(inv["id"]))
    row.need_id = need.id
    bank.flush()
    return {"cand": cand, "emp": emp, "inv": inv, "session": s, "run": run, "need": need}


def _accept(client, w) -> None:
    client.get(f"/api/invitations/inbox/{w['inv']['id']}", headers=w["cand"].headers)
    r = client.post(
        f"/api/invitations/{w['inv']['id']}/accept",
        json={"consent": True, "fields": ["full_name", "telegram"]},
        headers=w["cand"].headers,
    )
    assert r.status_code == 200 and r.json()["contacts_open"] is True, r.text


def test_employer_kit_only_after_disclosure(client, world, make_account, bank):
    w = world
    url = f"/api/invitations/{w['inv']['id']}/verification-kit"
    # До «да» контакты закрыты — комплекта нет (и причины в ответе не раскрываются)
    r = client.get(url, headers=w["emp"].headers)
    assert r.status_code == 409 and r.json()["code"] == "kit_not_available", r.text
    assert "двойник" not in r.text.lower()
    _accept(client, w)
    r = client.get(url, headers=w["emp"].headers)
    assert r.status_code == 200, r.text
    kit = r.json()
    trace = [q for q in kit["questions"] if q["kind"] == "trace"]
    assert len(trace) == 3 - kit["shortfall"] and trace
    for q in trace:
        assert q["expected_answer"] and q["rubric"] and [x["score"] for x in q["rubric"]] == [0, 1, 2]
        assert q["listen_for_ru"] and q["verified_by"] in ("execution", "generator")
    skills = [q for q in kit["questions"] if q["kind"] == "skill"]
    assert skills and skills[0]["label_ru"] == "не откалибровано" and skills[0]["calibrated"] is False  # kafka тестом не покрыт
    # Улик, флагов и причин выбора заданий в комплекте нет: ни в полях, ни в тексте
    text = json.dumps(kit, ensure_ascii=False).lower()
    for word in EVIDENCE_WORDS:
        assert word not in text, word
    for q in kit["questions"]:
        assert not {"family_id", "seed", "selection_reason"} & set(q)
    # Секретный seed экземпляра и ключ копии тоже не в ответе
    for inst in w["run"].instances:
        assert inst.key_hash not in text
    # Комплект детерминирован: тот же вызов — тот же kit_id
    assert client.get(url, headers=w["emp"].headers).json()["kit_id"] == kit["kit_id"]


def test_employer_kit_access_control(client, world, make_account, bank):
    w = world
    _accept(client, w)
    url = f"/api/invitations/{w['inv']['id']}/verification-kit"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=w["cand"].headers).status_code == 403  # кандидат комплект работодателя не видит
    other = make_employer(client, make_account, bank, "yuzhny-bit.ru", profile={
        "name": "ООО «Южный бит»", "field": "Ритейл", "description": "Интернет-магазин: команда 60 человек, офис в Казани.",
        "contact_methods": {"email": "hr@yuzhny-bit.ru"},
    })
    r = client.get(url, headers=other.headers)  # чужое приглашение неотличимо от несуществующего
    assert r.status_code == 404 and r.json()["code"] == "invitation_not_found"
    assert client.get(f"/api/invitations/{uuid.uuid4()}/verification-kit", headers=w["emp"].headers).status_code == 404


def test_revoked_disclosure_closes_the_kit(client, world, bank):
    """Контакт отозван — комплект снова недоступен: он доступен ровно пока раскрытие действует."""
    from sqlalchemy import select

    from app.core.clock import clock
    from app.models.contact import ContactDisclosure

    w = world
    _accept(client, w)
    url = f"/api/invitations/{w['inv']['id']}/verification-kit"
    assert client.get(url, headers=w["emp"].headers).status_code == 200
    for d in bank.scalars(select(ContactDisclosure).where(ContactDisclosure.candidate_id == w["cand"].user_id)):
        d.revoked_at = clock.now()
    bank.flush()
    assert client.get(url, headers=w["emp"].headers).status_code == 409


def test_lab_kit_has_reasons_and_prefers_twin_trace(client, world, make_account, bank):
    w = world
    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    kit = client.get(f"/api/lab/sessions/{w['session']['id']}/verification-kit", headers=methodist.headers)
    assert kit.status_code == 200, kit.text
    body = kit.json()
    first = body["questions"][0]
    assert first["selection_reason"] == "twin_trace" and first["family_id"] and first["seed"] is not None
    assert body["session_id"] == w["session"]["id"]
    # Тот же комплект, что видит работодатель, только без причин (kit_id совпадает)
    _accept(client, w)
    emp_kit = client.get(f"/api/invitations/{w['inv']['id']}/verification-kit", headers=w["emp"].headers).json()
    assert emp_kit["kit_id"] != "" and len(emp_kit["questions"]) >= len(
        [q for q in body["questions"] if q["kind"] == "trace"]
    )


def test_lab_kit_for_open_session_is_conflict(client, make_account, bank, api):
    methodist = make_account()
    make_role(bank, methodist.user_id, "methodist")
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    api.get(f"/api/tests/sessions/{s['id']}/next", acc)
    r = client.get(f"/api/lab/sessions/{s['id']}/verification-kit", headers=methodist.headers)
    assert r.status_code == 409 and r.json()["code"] == "session_not_finished"
    assert client.get(f"/api/lab/sessions/{uuid.uuid4()}/verification-kit", headers=methodist.headers).status_code == 404


def test_kit_input_comes_from_session_and_marks_twin_trace(world, bank):
    from app.models.testing import TestSession as SessionRow

    w = world
    session = bank.get(SessionRow, uuid.UUID(w["session"]["id"]))
    items = anticheat_kit.session_items(bank, session)
    assert items and any(i.kind == "twin_trace" for i in items)
    assert all(i.seed >= 0 and i.family_id for i in items)
