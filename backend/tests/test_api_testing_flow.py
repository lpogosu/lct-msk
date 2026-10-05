"""API тестирования: полный путь кандидата по HTTP со сценарным «кандидатом» (spec-testing §2.2–2.5, §3.5).

Сильный кандидат → PASSED и добор вверх; слабый → FAILED; превью карточки, «Мой грейд», контракт для
подбора; утечек ключей нет ни в одном ответе; D17 — показанных заданий ≤ 20 вместе с pretest.
"""

from __future__ import annotations

import uuid

import pytest

from app.models.testing import ItemFamilyParams
from app.services.testing.qualification import get_qualification_snapshot
from tests import test_api_testing_support as support
from tests.test_api_testing_support import (
    PUBLIC_ITEM_FIELDS,
    assert_no_leaks,
    by_ability,
    do_survey,
    find_scenario,
    run_session,
    set_family_status,
    start,
)

# Фикстуры общего модуля (присваиванием, а не импортом: так их видит pytest и не ругается линтер)
api = support.api
bank = support.bank
fixed_seed = support.fixed_seed

pytestmark = pytest.mark.usefixtures("frozen_clock")


def test_strong_candidate_passes_and_extends_up(api, bank, make_account):
    acc = make_account()
    st = do_survey(api, acc, months=60)
    assert st["completed"] and st["classification"]["chosen"] == "backend_python"

    brief = api.get("/api/tests/briefing?specialization=backend_python", acc)
    assert brief["mode"] == "test" and brief["can_start"]["ok"]
    assert not brief["consent"]["given"]
    assert [g["code"] for g in brief["grades"]] == ["intern", "junior", "middle", "senior"]
    assert brief["experience_hint"] and brief["senior_warning"] is None

    s = start(api, acc, "middle")
    assert s["kind"] == "initial" and s["status"] == "IN_PROGRESS"
    assert s["target_grade"]["code"] == "middle"

    run = run_session(api, bank, acc, s["id"], by_ability(3.0))
    n = len(run.items)
    assert 8 <= n <= 20
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["visible"] and res["decision"]["code"] == "PASSED"
    assert res["confirmed_grade"]["code"] == "middle"
    assert res["category"]["code"] == "backend_python:middle"
    assert res["n_shown"] == n
    assert res["ruler"]["cuts"][0]["score"] == 300 and res["ruler"]["cuts"][-1]["score"] == 600
    lo, hi = res["interval80"]
    assert lo <= res["score"] <= hi
    assert abs(sum(c["probability"] for c in res["certainty"]) - 1.0) < 1e-3
    assert {c["grade"] for c in res["certainty"]} == {"below_intern", "intern", "junior", "middle", "senior"}
    assert sum(line["total"] for line in res["by_level"]) == n  # без pretest: все показанные — зачётные
    assert res["stop_text"].startswith(f"Тест остановился на {n}-м задании")
    assert res["blind_card_preview"]["category"] == "Backend (Python) · Middle"
    assert "работодатель не видит" in res["blind_card_preview"]["note"].lower()
    assert res["modules"] and all(m["symbol"] in ("✓✓", "✓", "~", "") for m in res["modules"])

    # Развилка: добор вверх (Senior сейчас) — уверенный проход даёт P(выше Senior) ≥ 0.3
    assert res["session"]["status"] == "EXTENSION_OFFERED"
    up = next(o for o in res["offers"] if o["direction"] == "up")
    assert up["target_grade"]["code"] == "senior"

    ext = api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "accept", "direction": "up"})
    assert ext["session"]["status"] == "FINAL"
    child = ext["child"]
    assert child["kind"] == "ext_up" and child["parent_session_id"] == s["id"]
    if child["status"] == "IN_PROGRESS":
        run2 = run_session(api, bank, acc, child["id"], by_ability(3.0))
        assert len(run2.items) <= 8
    res2 = api.get(f"/api/tests/sessions/{child['id']}/result", acc)
    final_grade = "senior" if res2["decision"]["code"] == "PASSED" else "middle"
    assert res2["confirmed_grade"]["code"] == final_grade

    quals = api.get("/api/tests/qualification", acc)
    q = next(x for x in quals if x["specialization"]["code"] == "backend_python")
    assert q["status"] == "CONFIRMED" and q["grade"]["code"] == final_grade
    assert q["trust"] == "высокое" and q["freshness"]["band"] == "fresh"
    assert q["next_change_allowed_at"] is not None and q["next_change_in"] == "через 90 дней"
    assert q["history"][0]["reason"] == "initial" and q["history"][0]["counts_as_change"] is False
    assert {a["kind"] for a in q["attempts"]} >= {"initial"}
    assert q["can_start"]["code"] == "already_confirmed"
    assert q["can_upgrade"]["ok"] is False

    # Контракт для подбора
    snap = get_qualification_snapshot(bank, acc.user_id, "backend_python")
    assert snap is not None and snap.confirmed and snap.grade == final_grade
    assert snap.theta is not None and snap.se_t is not None and snap.se_t >= snap.se
    assert snap.trust == 1.0 and snap.evidence_weight == pytest.approx(0.8)
    assert snap.modules and all(m.se > 0 for m in snap.modules.values())
    assert snap.visible and snap.category["code"] == f"backend_python:{final_grade}"
    all_snaps = get_qualification_snapshot(bank, acc.user_id)
    assert [x.specialization for x in all_snaps] == ["backend_python"]

    assert_no_leaks(api.bodies, run.instances)


def test_weak_candidate_fails_without_grade(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc, months=3)
    s = start(api, acc, "junior")
    run = run_session(api, bank, acc, s["id"], by_ability(-4.0))
    assert len(run.items) >= 8
    res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["decision"]["code"] == "FAILED" and res["confirmed_grade"] is None
    if res["session"]["status"] == "EXTENSION_OFFERED":
        down = res["offers"][0]
        assert down["direction"] == "down" and down["target_grade"]["code"] == "intern"
        api.post(f"/api/tests/sessions/{s['id']}/extension", acc, {"decision": "decline"})
        res = api.get(f"/api/tests/sessions/{s['id']}/result", acc)
    assert res["session"]["status"] == "FINAL"
    assert "через 14 дней" in res["message"]
    q = api.get("/api/tests/qualification/backend_python", acc)
    assert q["status"] == "NOT_CONFIRMED" and q["grade"] is None
    assert q["can_start"]["code"] == "retake_cooldown" and q["next_attempt_in"] == "через 14 дней"
    snap = get_qualification_snapshot(bank, acc.user_id, "backend_python")
    assert snap is not None and not snap.confirmed and not snap.visible
    assert_no_leaks(api.bodies, run.instances)


def test_review_only_after_decision_and_only_own_instances(api, bank, make_account):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    nxt = api.get(f"/api/tests/sessions/{s['id']}/next", acc)
    assert set(nxt["item"]) == PUBLIC_ITEM_FIELDS
    err = api.get(f"/api/tests/sessions/{s['id']}/review", acc, expect=409)
    assert err["code"] == "result_not_ready"
    err = api.get(f"/api/tests/sessions/{s['id']}/result", acc, expect=409)
    assert err["code"] == "result_not_ready"

    run = run_session(api, bank, acc, s["id"], by_ability(0.0))
    rev = api.get(f"/api/tests/sessions/{s['id']}/review", acc)
    assert rev["n_total"] == len(run.items)
    assert len(rev["items"]) == rev["n_wrong"]
    own_codes = {f"#{i.variant_code}" for i in run.instances}
    for it in rev["items"]:
        assert it["variant_code"] in own_codes
        assert it["correct_answer"] is not None and it["correct_answer_text"]
        assert it["your_answer"] != it["correct_answer"]


def test_shown_items_capped_at_20_with_pretest(api, bank, make_account, fixed_seed):
    """DECISIONS п. 17: показанных заданий ≤ 20 вместе с pretest (2 pretest → не больше 18 зачётных)."""
    pretest = {
        p.family_id
        for p in bank.query(ItemFamilyParams).filter(
            ItemFamilyParams.specialization == "backend_python", ItemFamilyParams.b.between(-0.5, 0.5)
        )
    }
    pretest = set(sorted(pretest)[:4])
    set_family_status(bank, "backend_python", pretest, "pretest")

    def at_cap(res, run):
        return res["decision"]["reason"] == "max_items" and sum(i.family_id in pretest for i in run.instances) == 2

    acc, s, run, res = find_scenario(api, bank, make_account, "middle", [0.0, -0.05, 0.05, -0.1, 0.1, -0.15], at_cap)
    assert len(run.items) == 20 and res["n_shown"] == 20
    assert sum(line["total"] for line in res["by_level"]) == 18  # pretest в оценку не входят
    assert res["decision"]["code"] in ("BORDERLINE_PASS", "BORDERLINE_FAIL")
    pre_pos = [i + 1 for i, inst in enumerate(run.instances) if inst.family_id in pretest]
    assert all(3 <= p <= 14 for p in pre_pos)  # позиции pretest-слотов 3–14
    assert run.last["session"]["progress"]["position"] == 20
    assert "20" in res["stop_text"]


def test_session_and_result_are_owner_only(api, bank, make_account):
    a, b = make_account(), make_account()
    do_survey(api, a)
    s = start(api, a, "middle")
    for path in ("", "/next", "/result", "/review"):
        err = api.get(f"/api/tests/sessions/{s['id']}{path}", b, expect=404)
        assert err["code"] == "session_not_found"
    err = api.post(f"/api/tests/sessions/{s['id']}/answer", b, {"seq": 1, "answer": "x"}, expect=404)
    assert err["code"] == "session_not_found"
    api.get(f"/api/tests/sessions/{uuid.uuid4()}", a, expect=404)
    emp = make_account(role="employer")
    err = api.get(f"/api/tests/sessions/{s['id']}", emp, expect=403)
    assert err["code"] == "role_forbidden"
    api.get(f"/api/tests/sessions/{s['id']}", None, expect=401)
