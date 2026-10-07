"""API тестирования: правила — идемпотентность, серверный дедлайн, обрыв, повтор 14 дней и ≤ 3 попыток
за 90 дней, повышение не раньше 90 дней и повтор через 30, добровольное понижение, ошибки старта.
Время — только через frozen_clock (spec-testing §2.4–2.5, concept §5.3)."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models.testing import TestResponse
from app.services.testing.keystore import decrypt_key
from tests import test_api_testing_support as support
from tests.test_api_testing_support import (
    advance,
    advance_days,
    by_ability,
    do_survey,
    instance_for,
    run_session,
    start,
)

# Фикстуры общего модуля (присваиванием, а не импортом: так их видит pytest и не ругается линтер)
api = support.api
bank = support.bank

pytestmark = pytest.mark.usefixtures("frozen_clock")


def _finish_failed(api, db, acc, sid):
    run_session(api, db, acc, sid, by_ability(-4.0))
    res = api.get(f"/api/tests/sessions/{sid}/result", acc)
    if res["session"]["status"] == "EXTENSION_OFFERED":
        api.post(f"/api/tests/sessions/{sid}/extension", acc, {"decision": "decline"})
    return res


def _confirm_middle(api, db, acc):
    s = start(api, acc, "middle")
    run = run_session(api, db, acc, s["id"], by_ability(3.0))
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["decision"]["code"] == "PASSED"
    if res["session"]["status"] == "EXTENSION_OFFERED":
        api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "decline"})
    return s, run


def test_next_and_answer_are_idempotent(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    url = f"/api/tests/sessions/{s['id']}"
    a = api.get(f"{url}/next", acc)
    b = api.get(f"{url}/next", acc)
    assert a["item"]["seq"] == b["item"]["seq"] == 1
    assert a["item"]["variant_code"] == b["item"]["variant_code"]
    assert a["item"]["deadline_at"] == b["item"]["deadline_at"]
    assert a["session"]["progress"]["text"] == "задание 1 · обычно 12–20"

    inst = instance_for(bank, s["id"], 1)
    key = decrypt_key(inst.key_enc)
    first = api.post(f"{url}/answer", acc, {"seq": 1, "answer": key})
    assert first["accepted"] and not first["duplicate"] and not first["timed_out"]
    again = api.post(f"{url}/answer", acc, {"seq": 1, "answer": "другой ответ"})
    assert again["duplicate"] is True
    row = bank.scalars(select(TestResponse).where(TestResponse.instance_id == inst.id)).one()
    assert row.is_correct is True and row.answer == key  # второй ответ первый не перезаписал

    # Ответ на невыданное задание — 409; следующий next выдаёт новое задание с другим экземпляром
    err = api.post(f"{url}/answer", acc, {"seq": 5, "answer": "x"}, expect=409)
    assert err["code"] == "no_pending_item"
    c = api.get(f"{url}/next", acc)
    assert c["item"]["seq"] == 2 and c["item"]["variant_code"] != a["item"]["variant_code"]
    assert c["session"]["progress"]["position"] == 2


def test_server_deadline_counts_late_answer_as_wrong(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    url = f"/api/tests/sessions/{s['id']}"
    item = api.get(f"{url}/next", acc)["item"]
    inst = instance_for(bank, s["id"], 1)
    advance(item["time_limit_s"] + 6)  # лимит + 5 с запаса — уже поздно
    res = api.post(f"{url}/answer", acc, {"seq": 1, "answer": decrypt_key(inst.key_enc)})
    assert res["timed_out"] is True
    row = bank.scalars(select(TestResponse).where(TestResponse.instance_id == inst.id)).one()
    assert row.is_correct is False

    # Задание без ответа после дедлайна засчитывается неверным при следующем next
    item2 = api.get(f"{url}/next", acc)["item"]
    assert item2["seq"] == 2
    advance(item2["time_limit_s"] + 6)
    item3 = api.get(f"{url}/next", acc)["item"]
    assert item3["seq"] == 3
    row2 = bank.scalars(
        select(TestResponse).where(TestResponse.session_id == row.session_id, TestResponse.seq == 2)
    ).one()
    assert row2.answered_at is not None and row2.is_correct is False and row2.answer is None


def test_disconnect_over_30_minutes_abandons_attempt(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    item = api.get(f"/api/tests/sessions/{s['id']}/next", acc)["item"]
    advance(item["time_limit_s"] + 5 + 31 * 60)
    st = api.get(f"/api/tests/sessions/{s['id']}", acc)
    assert st["status"] == "ABANDONED" and st["finished"]
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["visible"] is False and "30 минут" in res["message"]
    api.get(f"/api/tests/sessions/{s['id']}/review", acc, expect=409)
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "NOT_CONFIRMED" and q["can_start"]["code"] == "retake_cooldown"


def test_retake_after_14_days_and_at_most_3_attempts_in_90(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc, months=3)
    s1 = start(api, acc, "junior")
    _finish_failed(api, bank, acc, s1["id"])
    err = start(api, acc, "junior", expect=409)
    assert err["code"] == "retake_cooldown" and "через 14 дней" in err["message"]
    assert err["details"]["available_at"]

    advance_days(14)
    advance(60)
    s2 = start(api, acc, "junior")
    assert s2["kind"] == "retake"
    run2 = _finish_failed(api, bank, acc, s2["id"])
    assert run2["decision"]["passed"] is False
    advance_days(14)
    advance(60)
    s3 = start(api, acc, "intern")
    assert s3["kind"] == "retake"
    run_session(api, bank, acc, s3["id"], by_ability(-6.0))
    res3 = api.get(f"/api/tests/sessions/{s3['id']}/result", acc)
    if res3["session"]["status"] == "EXTENSION_OFFERED":
        api.post(f"/api/tests/sessions/{s3['id']}/extension", acc, {"decision": "decline"})
    advance_days(14)
    advance(60)
    err = start(api, acc, "intern", expect=409)
    assert err["code"] == "attempts_limit" and "3 попыток за 90 дней" in err["message"]
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["can_start"]["code"] == "attempts_limit"
    assert len([a for a in q["attempts"] if a["kind"] in ("initial", "retake")]) == 3


def test_upgrade_waits_90_days_and_failed_upgrade_keeps_grade(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc, months=60)
    s1, run1 = _confirm_middle(api, bank, acc)
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["grade"]["code"] == "middle" and q["can_upgrade"]["code"] == "change_interval"

    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    assert brief["mode"] == "unavailable" and brief["current_grade"]["code"] == "middle"
    assert brief["upgrade_target"]["code"] == "senior" and brief["next_change_in"] == "через 90 дней"

    err = start(api, acc, None, upgrade=True, expect=409)
    assert err["code"] == "change_interval"
    err = start(api, acc, "middle", expect=409)
    assert err["code"] == "already_confirmed"

    advance_days(90)
    advance(60)
    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    assert brief["mode"] == "upgrade" and brief["can_start"]["ok"]
    err = start(api, acc, "intern", upgrade=True, expect=400)
    assert err["code"] == "upgrade_one_step"
    up = start(api, acc, None, upgrade=True)
    assert up["kind"] == "upgrade" and up["target_grade"]["code"] == "senior"
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "UPGRADE_TESTING" and q["grade"]["code"] == "middle"

    run_up = run_session(api, bank, acc, up["id"], by_ability(-4.0))
    # Тест на повышение — на непересекающихся семействах (неповтор 12 месяцев)
    assert not {i.family_id for i in run_up.instances} & {i.family_id for i in run1.instances}
    res = api.get(f"/api/tests/sessions/{up['id']}/result", acc)
    assert res["decision"]["passed"] is False and res["offers"] == []
    assert "остаётся Middle" in res["message"] and "30 дней" in res["message"]
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "CONFIRMED" and q["grade"]["code"] == "middle"  # принудительно не понижаем
    assert q["can_upgrade"]["code"] == "upgrade_cooldown"
    err = start(api, acc, None, upgrade=True, expect=409)
    assert err["code"] == "upgrade_cooldown"
    advance_days(30)
    advance(60)
    up2 = start(api, acc, None, upgrade=True)
    assert up2["kind"] == "upgrade"


def test_voluntary_downgrade_counts_as_change_outside_onboarding(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc, months=60)
    _confirm_middle(api, bank, acc)
    before = api.get("/api/tests/qualification/backend_python", acc)
    q = api.post("/api/tests/qualification/backend_python/downgrade", acc)
    assert q["grade"]["code"] == "junior" and q["category"]["code"] == "backend_python:junior"
    # Уверенность и теги — уже для Junior (по тому же апостериору): не ниже, чем были для Middle
    assert q["confidence_pct"] >= before["confidence_pct"]
    assert len(q["modules_confirmed"]) >= len(before["modules_confirmed"])
    last = q["history"][-1]
    assert last["reason"] == "voluntary_downgrade" and last["counts_as_change"] is False  # онбординг 24 ч
    advance_days(2)
    err = api.post("/api/tests/qualification/backend_python/downgrade", acc, expect=409)
    assert err["code"] == "change_interval"
    err = api.post("/api/tests/qualification/frontend_js/downgrade", acc, expect=404)
    assert err["code"] == "qualification_not_found"


def test_start_errors_and_consent(api, bank, make_account, client):
    acc = make_account()
    err = start(api, acc, "middle", expect=409)
    assert err["code"] == "survey_required"
    do_survey(api, acc)
    err = start(api, acc, "middle", consent=False, expect=409)
    assert err["code"] == "consent_required" and err["details"]["purpose"] == "test_logging"
    consents = client.get("/api/consents", headers=acc.headers).json()
    assert not any(c["purpose"] == "test_logging" for c in consents)

    assert start(api, acc, "lead", expect=400)["code"] == "grade_required"
    assert start(api, acc, None, expect=400)["code"] == "grade_required"
    assert start(api, acc, "middle", spec="nope", expect=400)["code"] == "unknown_specialization"
    assert start(api, acc, "middle", spec="fullstack", expect=400)["code"] == "derived_specialization"
    assert start(api, acc, "middle", spec="data_ml", expect=409)["code"] == "specialization_not_testable"

    s = start(api, acc, "middle")
    consents = client.get("/api/consents", headers=acc.headers).json()
    assert any(c["purpose"] == "test_logging" and c["active"] for c in consents)
    err = start(api, acc, "middle", consent=False, expect=409)
    assert err["code"] == "active_session_exists" and err["details"]["session_id"] == s["id"]
    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    assert brief["active_session_id"] == s["id"] and brief["consent"]["given"] is True
    assert api.get("/api/tests/briefing?specialization=data_ml", acc)["can_start"]["code"] == (
        "specialization_not_testable"
    )


def test_senior_warning_in_briefing(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc, months=10)
    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    assert brief["senior_warning"] and "Senior" in brief["senior_warning"]
    assert any(g["suggested"] for g in brief["grades"])
    assert brief["rules"] and any("90 дней" in r for r in brief["rules"])


def test_failed_grade_allows_lower_grade_immediately_without_burning_retake(api, bank, make_account):
    """Политика пересдач (DECISIONS п. 29): провал Middle → тест на Junior сразу, он не расходует пересдачу."""
    acc = make_account()
    do_survey(api, acc, months=30)
    s1 = start(api, acc, "middle")
    _finish_failed(api, bank, acc, s1["id"])
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "NOT_CONFIRMED" and q["can_start"]["code"] == "retake_cooldown"
    assert q["lower_grades_open"] == ["intern", "junior"]
    # на тот же грейд — ждать, на ниже — сразу
    assert start(api, acc, "middle", expect=409)["code"] == "retake_cooldown"
    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    by_code = {g["code"]: g for g in brief["grades"]}
    assert by_code["middle"]["available"] is False and by_code["middle"]["note"]
    assert by_code["junior"]["available"] is True
    assert brief["mode"] == "test" and brief["can_start"]["ok"] is True
    assert brief["retake_rules"] and "через 14 дней" in " ".join(brief["retake_rules"])
    assert brief["retake_status"] and "сразу" in brief["retake_status"]
    s2 = start(api, acc, "junior")
    assert s2["kind"] == "retake"
    _finish_failed(api, bank, acc, s2["id"])
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["can_start"]["code"] == "retake_cooldown"  # ожидание на Middle не продлили
    # бесплатная попытка не съела лимит из 3: после 14 дней доступно и Middle
    advance_days(14)
    advance(60)
    s3 = start(api, acc, "middle")
    assert s3["kind"] == "retake"
