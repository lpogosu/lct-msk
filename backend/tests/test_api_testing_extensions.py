"""API тестирования: доборы (spec-testing §2.4, §3.5; concept §5.3) — «ваших ответов достаточно» одним
нажатием (0 заданий), добор вниз до 8 заданий с тем же апостериором, истечение предложения через 24 ч."""

from __future__ import annotations

import pytest

from tests import test_api_testing_support as support
from tests.test_api_testing_support import (
    advance,
    by_ability,
    do_survey,
    find_scenario,
    run_session,
    start,
)

# Фикстуры общего модуля (присваиванием, а не импортом: так их видит pytest и не ругается линтер)
api = support.api
bank = support.bank
fixed_seed = support.fixed_seed

pytestmark = pytest.mark.usefixtures("frozen_clock", "fixed_seed")
NEAR_MIDDLE = [-0.1, -0.3, -0.05, -0.2, -0.15, -0.25, -0.35, -0.4, -0.5]


def _down(instant: bool):
    def want(res, run):
        offers = [o for o in res["offers"] if o["direction"] == "down"]
        return res["decision"]["passed"] is False and bool(offers) and offers[0]["instant"] is instant

    return want


def test_instant_confirmation_of_lower_grade(api, bank, make_account):
    acc, s, _, res = find_scenario(api, bank, make_account, "middle", NEAR_MIDDLE, _down(True))
    assert res["decision"]["passed"] is False and res["confirmed_grade"] is None
    offer = res["offers"][0]
    assert offer["direction"] == "down" and offer["instant"] and offer["max_items"] == 0
    assert offer["text"].startswith("Ваших ответов достаточно, чтобы подтвердить Junior")
    assert res["offers_expire_in"] == "через 24 часа" or res["offers_expire_in"] == "завтра"

    err = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "up"}, expect=409)
    assert err["code"] == "no_offer"
    err = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept"}, expect=400)
    assert err["code"] == "direction_required"
    ext = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "down"})
    child = ext["child"]
    assert child["kind"] == "ext_down" and child["status"] == "FINAL" and child["finished"]
    assert ext["message"] == "Готово: решение по уже данным ответам"
    assert api.get(f"/api/tests/sessions/{child['id']}/next", acc)["item"] is None
    res2 = api.get(f"/api/tests/sessions/{child['id']}/result", acc)
    assert res2["confirmed_grade"]["code"] == "junior" and res2["decision"]["code"] == "PASSED"
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "CONFIRMED" and q["grade"]["code"] == "junior"
    assert q["history"][0]["reason"] == "initial" and q["history"][0]["counts_as_change"] is False
    # Повторно принять добор нельзя
    err = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "down"}, expect=409)
    assert err["code"] == "no_extension_offer"


def test_down_extension_continues_same_posterior(api, bank, make_account, monkeypatch):
    # Тест про правила добора, а не про прогрев: в малом тестовом пуле (по 4 клона) перебор уровней в find_scenario
    # расходует запас, и «пул готовится» (503) перекрыл бы ожидаемое 409. Порог готовности здесь отключаем.
    from app.services.testing import limits

    monkeypatch.setattr(limits, "POOL_READY_MIN_FREE", 0)
    acc, s, run1, res = find_scenario(api, bank, make_account, "middle", NEAR_MIDDLE, _down(False))
    offer = res["offers"][0]
    assert offer["direction"] == "down" and not offer["instant"] and offer["max_items"] == 8
    # Пока предложение не решено, новый тест не начать
    err = start(api, acc, "junior", expect=409)
    assert err["code"] in ("extension_pending", "already_testing")
    ext = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "down"})
    child = ext["child"]
    assert child["status"] == "IN_PROGRESS" and child["target_grade"]["code"] == "junior"
    parent = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert parent["session"]["child_session_id"] == child["id"]
    assert parent["message"] == "Принят добор: итоговый грейд — в результате добора"
    nxt = api.get(f"/api/tests/sessions/{child['id']}/next", acc)
    assert nxt["item"]["seq"] == len(run1.items) + 1  # нумерация продолжается: тот же апостериор
    run2 = run_session(api, bank, acc, child["id"], by_ability(-0.6))
    assert len(run2.items) <= 8
    assert not {i.family_id for i in run2.instances} & {i.family_id for i in run1.instances}
    res2 = api.get(f"/api/tests/sessions/{child['id']}/result", acc)
    assert res2["n_shown"] == len(run1.items) + len(run2.items)
    q = api.get("/api/tests/qualification/backend_python", acc)
    if res2["decision"]["passed"]:
        assert q["grade"]["code"] == "junior"
    else:
        assert q["grade"] is None


def test_offer_expires_after_24_hours(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    run_session(api, bank, acc, s["id"], by_ability(3.0))
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["session"]["status"] == "EXTENSION_OFFERED"
    advance(24 * 3600 + 60)
    q = api.get("/api/tests/qualification/backend_python", acc)  # чтение сдвигает таймеры
    assert q["grade"]["code"] == "middle"
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["session"]["status"] == "FINAL" and res["offers"] == []
    err = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "up"}, expect=409)
    assert err["code"] == "no_extension_offer"
