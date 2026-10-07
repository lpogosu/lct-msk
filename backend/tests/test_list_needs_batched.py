"""Список брифов не делает запросов «на каждый бриф» (N+1): число запросов не растёт с числом брифов.

Нагрузочный тест (docs/LOAD_TEST.md) нашёл: 90 брифов → 183 запроса и ≈ 0,5 с на GET /api/needs.
"""

from __future__ import annotations

import pytest
from sqlalchemy import event, select

from app.models.users import User
from app.services.matching import needs as svc
from tests.test_api_needs_flow import _create, employer, pool  # noqa: F401  (фикстуры)

pytestmark = pytest.mark.usefixtures("frozen_clock")


def test_list_needs_query_count_is_constant(client, employer, pool, db_session):  # noqa: F811
    ids = [_create(client, employer).json()["id"] for _ in range(4)]
    client.post(f"/api/needs/{ids[0]}/versions", json={}, headers=employer.headers)
    user = db_session.scalar(select(User).where(User.email == employer.email))
    queries: list[str] = []

    def count(conn, cur, stmt, params, ctx, many):
        queries.append(stmt)

    bind = db_session.get_bind()
    event.listen(bind, "before_cursor_execute", count)
    try:
        items = svc.list_needs(db_session, user, None)
    finally:
        event.remove(bind, "before_cursor_execute", count)
    assert len(items) == 4
    assert len(queries) <= 3, queries  # брифы + версии + закреплённые, независимо от числа брифов
    by_id = {str(i["id"]): i for i in items}
    assert by_id[ids[0]]["current_version"] == 1 and by_id[ids[1]]["current_version"] is None
