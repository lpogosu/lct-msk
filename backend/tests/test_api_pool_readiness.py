"""Прогрев пула после `docker compose up` (#14): пока пул не готов — «Готовим одноразовые задания…», а не ошибка."""

from __future__ import annotations

from tests.test_api_testing_support import api, bank, do_survey  # noqa: F401


def test_health_pool_cold_then_ready(client, db_session):
    cold = client.get("/api/health/pool").json()
    assert cold["ready"] is False and cold["progress"] == 0 and cold["eta_s"] > 0
    assert "Готовим одноразовые задания" in cold["message"]
    h = client.get("/api/health").json()
    assert h["pool_ready"] is False and h["pool_progress"] == 0

    from tests.test_api_testing_support import install_bank

    install_bank(db_session, "backend_python")
    warm = client.get("/api/health/pool", params={"specialization": "backend_python"}).json()
    assert warm["ready"] is True and warm["progress"] == 1 and warm["eta_s"] == 0


def test_briefing_and_start_report_warming_up(api, client, make_account):  # noqa: F811
    acc = make_account("cand-pool@example.com")
    do_survey(api, acc)
    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    assert brief["can_start"]["code"] == "pool_warming_up" and brief["pool"]["ready"] is False
    assert "Готовим одноразовые задания" in brief["can_start"]["message"]
    r = client.post(
        "/api/tests/sessions",
        json={"specialization": "backend_python", "grade": "middle", "consent_test_logging": True},
        headers=acc.headers,
    )
    assert r.status_code == 503 and r.json()["code"] == "pool_warming_up"
