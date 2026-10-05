"""API банка кандидатов (Р9, концепция §3.2 шаг 7): фильтры, сортировка 0,55·T + 0,25·F + 0,20·A,
«📌 в шортлист брифа X», карточка из банка (открытие записывается), приватность, роли."""

from __future__ import annotations

import pytest

from app.models.users import User
from app.services.matching import card_opened, resolve_ref
from tests.test_api_needs_support import (
    DEMO_BRIEF,
    FSP_MEDIUM,
    FSP_STRONG,
    assert_no_pii,
    ekp_numbers,
    seed_candidate,
    seed_demo_pool,
)

pytestmark = pytest.mark.usefixtures("frozen_clock")


@pytest.fixture
def employer(make_account):
    return make_account("hr@severny-kod.ru", role="employer")


@pytest.fixture
def pool(db_session):
    return seed_demo_pool(db_session)


def _all(client, emp, **params) -> list[dict]:
    first = client.get("/api/bank", params=params, headers=emp.headers)
    assert first.status_code == 200, first.text
    body = first.json()
    items = list(body["items"])
    for p in range(2, body["pages"] + 1):
        items += client.get("/api/bank", params={**params, "page": p}, headers=emp.headers).json()["items"]
    assert len(items) == body["total"]
    return items


def _ids(db, emp, items) -> list:
    company = db.get(User, emp.user_id).company_id
    return [resolve_ref(db, company, i["ref"]) for i in items]


def test_bank_lists_visible_once_sorted_by_bank_score(client, db_session, employer, pool):
    items = _all(client, employer)
    ids = _ids(db_session, employer, items)
    # 12 Middle + 3 Senior + Fullstack-кандидат один раз; скрытых нет
    assert len(items) == 16 and len(set(ids)) == 16
    for key in ("hidden_mode", "no_consent", "minor", "under_review"):
        assert pool[key] not in ids
    scores = [i["score"] for i in items]
    assert scores == sorted(scores, reverse=True)
    body = client.get("/api/bank", headers=employer.headers).json()
    assert body["weights"] == {"T": 0.55, "F": 0.25, "A": 0.2}
    for it in items:
        # балл банка = Σ вкладов T, F, A (шаг 0,1); веса — из config/scoring.yaml
        assert set(it["parts"]) == {"T", "F", "A"} and abs(sum(it["parts"].values()) - it["score"]) <= 0.1 + 1e-9
        assert abs(it["components"]["T"] * 55 + it["components"]["F"] * 25 + it["components"]["A"] * 20 - it["score"]) < 1.0
    assert_no_pii(body, pool.values(), ekp_numbers(FSP_STRONG) | ekp_numbers(FSP_MEDIUM))
    fs = next(i for i, cid in zip(items, ids, strict=True) if cid == pool["fullstack"])
    assert fs["category"] in ("Backend (Python) · Middle", "Fullstack · Junior", "Frontend (JS/TS) · Junior")


def test_bank_filters(client, db_session, employer, pool):
    # категория / специализация / грейд
    mid = _all(client, employer, category="backend_python:middle")
    assert len(mid) == 13 and all(i["category"] == "Backend (Python) · Middle" for i in mid)
    seniors = _all(client, employer, spec="backend_python", grade="senior")
    assert len(seniors) == 3 and all(i["grade"] == "senior" for i in seniors)
    fullstack = _all(client, employer, spec="fullstack")
    assert _ids(db_session, employer, fullstack) == [pool["fullstack"]] and fullstack[0]["grade"] == "junior"
    # есть ФСП
    fsp = _all(client, employer, has_fsp=True)
    assert sorted(_ids(db_session, employer, fsp)) == sorted([pool["anna"], pool["vera"]])
    assert all(i["has_fsp"] and i["fsp_line"] for i in fsp)
    no_fsp = _all(client, employer, has_fsp=False)
    assert len(no_fsp) == 14 and all(i["fsp_line"].startswith("Достижения ФСП не привязаны") for i in no_fsp)
    # отрасль и режим поиска
    gov = _all(client, employer, industry="gov")
    assert _ids(db_session, employer, gov) == [pool["mila"]] and gov[0]["industries"] == ["Госсектор"]
    active = _all(client, employer, search_mode="active")
    assert _ids(db_session, employer, active) == [pool["nina"]]
    # стек «только подтверждённые»: SQL у Егора «~» (мало данных) — он не проходит
    sql = _all(client, employer, skill=["postgresql"], category="backend_python:middle")
    sql_ids = _ids(db_session, employer, sql)
    assert pool["egor"] not in sql_ids and pool["anna"] in sql_ids
    assert all(i["skills"][0]["state"] == "confirmed" and i["skills"][0]["source"] == "test" for i in sql)
    # Kafka тестом не проверяется: подтверждённых нет, а с заявленными — только Анна (серый чип «заявлено»)
    assert _all(client, employer, skill=["kafka"]) == []
    declared = _all(client, employer, skill=["kafka"], confirmed_only=False)
    assert _ids(db_session, employer, declared) == [pool["anna"]]
    assert declared[0]["skills"][0]["state"] == "declared" and declared[0]["skills"][0]["label"] == "заявлено"
    assert declared[0]["declared"] == ["Kafka"]
    # ошибки фильтров — 422 по-русски
    for params, code in (
        ({"category": "backend_python"}, "category_invalid"),
        ({"spec": "cobol"}, "spec_invalid"),
        ({"skill": "квантовый_компьютер"}, "skill_invalid"),
        ({"industry": "space"}, "industry_invalid"),
        ({"search_mode": "hidden"}, "search_mode_invalid"),
    ):
        r = client.get("/api/bank", params=params, headers=employer.headers)
        assert r.status_code == 422 and r.json()["code"] == code, (params, r.text)


def test_pin_from_bank_into_brief_and_back(client, db_session, employer, pool):
    nid = client.post("/api/needs", json={"text": DEMO_BRIEF}, headers=employer.headers).json()["id"]
    v1 = client.post(f"/api/needs/{nid}/versions", json={}, headers=employer.headers).json()
    body = client.get("/api/bank", params={"has_fsp": True, "need_id": nid}, headers=employer.headers).json()
    assert body["pin_targets"][0]["need_id"] == nid
    target = body["items"][0]
    assert target["shortlisted"] is False
    r = client.put(f"/api/needs/{nid}/shortlist/{target['ref']}", headers=employer.headers)
    assert r.status_code == 200, r.text
    # назад в подборку — версии и шортлист целы
    sel = client.get(f"/api/needs/{nid}/selection", headers=employer.headers).json()
    assert sel["n"] == v1["n"] and [e["ref"] for e in sel["shortlist"]] == [target["ref"]]
    again = client.get("/api/bank", params={"has_fsp": True, "need_id": nid}, headers=employer.headers).json()
    assert next(i for i in again["items"] if i["ref"] == target["ref"])["shortlisted"] is True
    # Senior из банка в Middle-бриф тоже можно закрепить: в текущей версии его нет — жёлтая метка
    senior = _all(client, employer, grade="senior")[0]
    r = client.put(f"/api/needs/{nid}/shortlist/{senior['ref']}", headers=employer.headers)
    entry = next(e for e in r.json()["items"] if e["ref"] == senior["ref"])
    assert entry["mark"] == "yellow" and entry["reason"] == "Нет в текущей версии подборки"


def test_bank_card_records_view(client, db_session, employer, pool):
    items = _all(client, employer, category="backend_python:middle")
    ref = items[0]["ref"]
    company = db_session.get(User, employer.user_id).company_id
    cid = resolve_ref(db_session, company, ref)
    assert card_opened(db_session, company, cid) is False
    r = client.get(f"/api/bank/candidates/{ref}", headers=employer.headers)
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["view"] == "blind" and card["pseudonym"] == items[0]["pseudonym"] and card["selection"] is None
    assert card["category"]["name"] == "Backend (Python) · Middle" and card["test"]["band"]
    assert_no_pii(card, pool.values(), ekp_numbers(FSP_STRONG) | ekp_numbers(FSP_MEDIUM))
    assert card_opened(db_session, company, cid) is True
    assert client.get("/api/bank/candidates/cnotarealref000000000", headers=employer.headers).status_code == 404


def test_bank_roles_and_blocklist(client, db_session, make_account):
    cand = make_account(role="candidate")
    assert client.get("/api/bank", headers=cand.headers).status_code == 403
    assert client.get("/api/bank").status_code == 401
    emp = make_account("hr@severny-kod.ru", role="employer")
    company = db_session.get(User, emp.user_id).company_id
    seed_candidate(db_session, theta=0.9, blocked=[company])
    visible = seed_candidate(db_session, theta=0.1)
    items = _all(client, emp)
    assert _ids(db_session, emp, items) == [visible]
