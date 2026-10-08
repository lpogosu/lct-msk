"""Списки приглашений и откликов грузят связанные таблицы пакетом: число запросов не растёт с длиной списка.

Нагрузочный тест (docs/LOAD_TEST.md): GET /api/invitations делал ~5 запросов на каждое приглашение,
p50 1,9 с при 100 пользователях. Теперь — запросов на страницу константа, счётчики колонок — агрегатом.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event

from tests.test_api_contact_support import invite, make_candidate, make_employer, make_need, open_card
from tests.test_api_contact_vacancies import _publish, features  # noqa: F401  (фикстура признаков)

pytestmark = pytest.mark.usefixtures("frozen_clock")


def _count(db, fn):
    queries: list[str] = []

    def hook(conn, cur, stmt, params, ctx, many):
        queries.append(stmt)

    bind = db.get_bind()
    event.listen(bind, "before_cursor_execute", hook)
    try:
        result = fn()
    finally:
        event.remove(bind, "before_cursor_execute", hook)
    print(*(q[:110].replace(chr(10),' ') for q in queries),sep=chr(10)); print('=====')
    return result, len(queries)


def test_employer_kanban_queries_constant_and_paginated(client, make_account, db_session, mailer):
    emp = make_employer(client, make_account, db_session)
    cands = [make_candidate(make_account, db_session, n=i + 1) for i in range(6)]
    for c in cands[:2]:
        a = open_card(db_session, emp, c)
        assert invite(client, emp, a.ref).status_code == 201
    db_session.commit()
    small, q_small = _count(db_session, lambda: client.get("/api/invitations?limit=50", headers=emp.headers))
    for c in cands[2:]:
        a = open_card(db_session, emp, c)
        assert invite(client, emp, a.ref).status_code == 201
    db_session.commit()
    big, q_big = _count(db_session, lambda: client.get("/api/invitations?limit=50", headers=emp.headers))
    assert small.status_code == big.status_code == 200
    assert sum(col["count"] for col in big.json()["columns"]) == 6
    assert q_big <= q_small + 1, (q_small, q_big)  # 4 приглашения сверху — не больше запросов

    # страницы: курсор, колонки считают все приглашения, а не только страницу
    p1 = client.get("/api/invitations?limit=4", headers=emp.headers).json()
    assert p1["next_cursor"] and p1["funnel"]["sent"] == 6
    assert sum(col["count"] for col in p1["columns"]) == 6
    assert sum(f["sent"] for f in p1["per_need"]) + 0 <= 6  # по брифам (приглашения без брифа не входят)
    assert sum(len(col["items"]) for col in p1["columns"]) == 4
    p2 = client.get(f"/api/invitations?limit=4&cursor={p1['next_cursor']}", headers=emp.headers).json()
    assert p2["next_cursor"] is None and sum(len(col["items"]) for col in p2["columns"]) == 2
    ids1 = {i["id"] for col in p1["columns"] for i in col["items"]}
    ids2 = {i["id"] for col in p2["columns"] for i in col["items"]}
    assert not ids1 & ids2 and len(ids1 | ids2) == 6
    assert client.get("/api/invitations?cursor=%%%", headers=emp.headers).status_code == 400


def test_candidate_inbox_queries_constant(client, make_account, db_session, mailer):
    cand = make_candidate(make_account, db_session)
    emps = [
        make_employer(client, make_account, db_session, f"firma{i}.ru", profile=_profile(i)) for i in range(4)
    ]
    for e in emps[:1]:
        assert invite(client, e, open_card(db_session, e, cand).ref).status_code == 201
    db_session.commit()
    small, q_small = _count(db_session, lambda: client.get("/api/invitations/inbox", headers=cand.headers))
    for e in emps[1:]:
        assert invite(client, e, open_card(db_session, e, cand).ref).status_code == 201
    db_session.commit()
    big, q_big = _count(db_session, lambda: client.get("/api/invitations/inbox", headers=cand.headers))
    assert len(small.json()["items"]) == 1 and len(big.json()["items"]) == 4
    assert big.json()["new_count"] == 4
    assert q_big <= q_small + 1, (q_small, q_big)
    page = client.get("/api/invitations/inbox?limit=3", headers=cand.headers).json()
    assert len(page["items"]) == 3 and page["next_cursor"] and page["new_count"] == 4


def test_application_lists_queries_constant(client, make_account, db_session, features):  # noqa: F811
    emp = make_employer(client, make_account, db_session)
    needs = [make_need(db_session, emp, title=f"Backend {i}") for i in range(4)]
    vacs = [_publish(client, emp, n) for n in needs]
    cand = make_candidate(make_account, db_session)
    for v in vacs[:1]:
        assert client.post("/api/applications", json={"vacancy_id": v["id"], "consent": True}, headers=cand.headers).status_code == 201
    db_session.commit()
    _, q_small = _count(db_session, lambda: client.get("/api/applications/mine", headers=cand.headers))
    _, e_small = _count(db_session, lambda: client.get("/api/applications", headers=emp.headers))
    for v in vacs[1:]:
        assert client.post("/api/applications", json={"vacancy_id": v["id"], "consent": True}, headers=cand.headers).status_code == 201
    db_session.commit()
    mine, q_big = _count(db_session, lambda: client.get("/api/applications/mine", headers=cand.headers))
    lst, e_big = _count(db_session, lambda: client.get("/api/applications", headers=emp.headers))
    assert len(mine.json()) == 4 and len(lst.json()) == 4
    assert q_big <= q_small + 1, (q_small, q_big)
    assert e_big <= e_small + 1, (e_small, e_big)


def _profile(i: int) -> dict:
    return {
        "name": f"ООО «Фирма {i}»",
        "field": "Разработка",
        "description": "Разрабатываем внутренние сервисы и интеграции для сетей магазинов и логистики.",
        "contact_methods": {"email": f"jobs@firma{i}.ru"},
    }
