"""DEMO_MODE выключен (по умолчанию): все маршруты /api/demo отвечают 404 в общем формате, ничего не меняют и не раскрывают."""

from __future__ import annotations

import pytest

ROUTES = [
    ("GET", "/api/demo/state", None),
    ("GET", "/api/demo/accounts", None),
    ("POST", "/api/demo/clock", {"preset": "plus_1d"}),
    ("POST", "/api/demo/reset", None),
    ("POST", "/api/demo/autoresponder", {"respond_now": True}),
    ("POST", "/api/demo/jury-mode", {"enabled": True}),
    ("GET", "/api/demo/jury/hint?session_id=00000000-0000-0000-0000-000000000000", None),
    ("GET", "/api/demo/synthetic", None),
    ("GET", "/api/demo/webhook-sink", None),
    ("POST", "/api/demo/webhook-sink", {"type": "x"}),
]


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
def test_demo_endpoints_are_404_when_demo_mode_is_off(client, make_account, method, path, body):
    assert client.get("/api/health").json()["demo_mode"] is False
    anonymous = client.request(method, path, json=body)
    assert anonymous.status_code == 404, (method, path, anonymous.text)
    assert anonymous.json() == {"code": "not_found", "message": "Ресурс не найден", "details": None}
    # и под любым входом — маршрута «нет»: 404 раньше, чем проверка прав
    acc = make_account()
    logged = client.request(method, path, json=body, headers=acc.headers)
    assert logged.status_code == 404, (method, path, logged.text)


def test_clock_is_not_touched_when_demo_is_off(client, make_account):
    from app.core.clock import clock

    acc = make_account()
    client.post("/api/demo/clock", json={"preset": "plus_3d"}, headers=acc.headers)
    assert clock.offset_seconds == 0


def test_admin_tools_do_not_depend_on_demo_mode(client, make_account):
    """/api/admin работает всегда, но только для роли «администратор»."""
    acc = make_account()
    assert client.get("/api/admin/validation", headers=acc.headers).status_code == 403
    assert client.get("/api/admin/validation").status_code == 401
