"""API лаборатории (Л1–Л4): доступ только методисту/администратору, трасса без «θ», банк с пулом,
«3 клона рядом», валидация, калибровка фазы 1 по ответам кандидатов с согласием на калибровку."""

from __future__ import annotations

import json
import random
import uuid

import pytest
from sqlalchemy import select

from app.core.clock import clock
from app.core.consents import grant_consent
from app.models.enums import ConsentPurpose
from app.models.testing import (
    CalibrationRun,
    DriftEvent,
    ItemFamilyParams,
    ItemInstance,
    TestResponse,
    ValidationRun,
)
from app.models.testing import TestSession as SessionRow
from app.services.testing.snapshot import session_to_state
from app.testing.bank.registry import all_families
from app.testing.cat import Selection
from app.testing.irt import ItemParams, load_scale
from app.testing.session import SessionStatus, TestSession
from tests import test_api_testing_support as support
from tests.test_api_testing_support import (
    by_ability,
    do_survey,
    make_role,
    run_session,
    start,
)

# Фикстуры общего модуля (присваиванием, а не импортом: так их видит pytest и не ругается линтер)
api = support.api
bank = support.bank

pytestmark = pytest.mark.usefixtures("frozen_clock")
SPEC = "backend_python"
LAB_GETS = ("/api/lab/sessions", "/api/lab/bank", "/api/lab/flags", "/api/lab/validation")


@pytest.fixture
def methodist(make_account, bank):
    acc = make_account()
    make_role(bank, acc.user_id, "methodist")
    return acc


def test_lab_is_for_methodist_and_admin_only(api, bank, make_account, methodist):
    cand, emp, admin = make_account(), make_account(role="employer"), make_account()
    make_role(bank, admin.user_id, "admin")
    for url in LAB_GETS:
        assert api.get(url, None, expect=401)["code"] == "not_authenticated"
        assert api.get(url, cand, expect=403)["code"] == "role_forbidden"
        assert api.get(url, emp, expect=403)["code"] == "role_forbidden"
        api.get(url, methodist)
        api.get(url, admin)
    api.post("/api/lab/calibrate", cand, {}, expect=403)
    api.post(f"/api/lab/flags/{uuid.uuid4()}/resolve", cand, {"decision": "dismiss"}, expect=403)
    api.get(f"/api/lab/sessions/{uuid.uuid4()}/trace", emp, expect=403)
    # Кандидатский раздел методисту недоступен
    api.get("/api/tests/qualification", methodist, expect=403)


def test_trace_bank_and_sessions(api, bank, make_account, methodist):
    acc = make_account()
    do_survey(api, acc)
    s = start(api, acc, "middle")
    run = run_session(api, bank, acc, s["id"], by_ability(0.4))

    sessions = api.get(f"/api/lab/sessions?specialization={SPEC}", methodist)
    row = next(x for x in sessions if x["id"] == s["id"])
    assert row["candidate_id"] == str(acc.user_id) and row["n_items"] == len(run.items)

    tr = api.get(f"/api/lab/sessions/{s['id']}/trace", methodist)
    assert tr["labels"]["level"] == "уровень" and tr["labels"]["error"] == "погрешность"
    assert len(tr["steps"]) == len(run.items) and tr["target_grade"] == "middle" and tr["target_level"] == 0.0
    first = tr["steps"][0]
    assert first["level"] is not None and first["error"] is not None and first["score"] is not None
    assert first["reason"]["chosen"] == first["family_id"] and first["reason"]["top_k"]
    assert first["variant_code"] == f"#{run.instances[0].variant_code}"
    errors = [st["error"] for st in tr["steps"] if not st["is_pretest"]]
    assert errors[-1] < errors[0]  # погрешность сужается по ходу теста
    assert "θ" not in json.dumps(tr, ensure_ascii=False)
    api.get(f"/api/lab/sessions/{uuid.uuid4()}/trace", methodist, expect=404)

    b = api.get(f"/api/lab/bank?specialization={SPEC}", methodist)
    assert b["total"] == len([f for f in all_families().values() if SPEC in f.specs])
    assert b["by_status"] == {"operational": b["total"]}
    fam = next(f for f in b["families"] if f["family_id"] == run.instances[0].family_id)
    assert fam["pool"]["total"] == 4 and fam["pool"]["issued"] == 1 and fam["pool"]["free"] == 3
    assert fam["params"][0]["source"] == "prior" and fam["params"][0]["specialization"] == SPEC
    assert api.get("/api/lab/bank?specialization=nope", methodist, expect=400)["code"] == "unknown_specialization"


def test_session_without_engine_state_does_not_break_lab_or_candidate(api, bank, make_account, methodist):
    """Строка сессии без снимка движка (например, вставлена скриптом) — понятная ошибка, а не 500."""
    acc = make_account()
    do_survey(api, acc)
    sid = uuid.uuid4()
    bank.add(
        SessionRow(
            id=sid, candidate_id=acc.user_id, specialization=SPEC, kind="initial", claimed_grade="middle",
            seed=1, status="IN_PROGRESS", state={},
        )
    )
    bank.flush()
    assert api.get(f"/api/lab/sessions/{sid}/trace", methodist, expect=409)["code"] == "trace_unavailable"
    assert api.get(f"/api/tests/sessions/{sid}", acc, expect=409)["code"] == "session_state_missing"
    api.get("/api/tests/qualification", acc)  # таймеры других сессий такую строку пропускают
    api.get(f"/api/tests/briefing?specialization={SPEC}", acc)


def test_three_clones_side_by_side(api, bank, methodist):
    fid = "py.slicing.step" if "py.slicing.step" in all_families() else sorted(all_families())[0]
    out = api.get(f"/api/lab/bank/{fid}/clones?n=3", methodist)
    assert out["family_id"] == fid and len(out["clones"]) == 3
    assert len({c["seed"] for c in out["clones"]}) == 3
    assert all(c["seed"] >= 1_000_000_000 for c in out["clones"])  # вне боевого пула
    assert len({c["variant_code"] for c in out["clones"]}) == 3
    p = bank.get(ItemFamilyParams, (fid, SPEC))
    assert out["difficulty"] == pytest.approx(p.b)
    pool_seeds = set(bank.scalars(select(ItemInstance.seed).where(ItemInstance.family_id == fid)))
    assert not pool_seeds & {c["seed"] for c in out["clones"]}
    assert api.get("/api/lab/bank/no.such.family/clones", methodist, expect=404)["code"] == "family_not_found"
    api.get(f"/api/lab/bank/{fid}/clones?n=50", methodist, expect=422)


def test_validation_latest_run(api, bank, methodist):
    out = api.get("/api/lab/validation", methodist)
    assert out["available"] is False and out["run"] is None and out["message"]
    bank.add(ValidationRun(kind="sim", config={"n": 1}, seeds=[1, 2], metrics={"accuracy": 0.93}, git_sha="abc"))
    bank.flush()
    out = api.get("/api/lab/validation?kind=sim", methodist)
    assert out["available"] and out["run"]["metrics"] == {"accuracy": 0.93} and out["kinds"] == ["sim"]


def _fake_sessions(db, user_id, family_id: str, n: int, correct: bool) -> None:
    """n решённых сессий по одному ответу на семейство — данные для калибровки без прогона HTTP."""
    p = db.get(ItemFamilyParams, (family_id, SPEC))
    fam = all_families()[family_id]
    inst = db.scalars(select(ItemInstance).where(ItemInstance.family_id == family_id)).first()
    item = ItemParams.from_family(p.b, p.sigma_b, fam.c, load_scale())
    for i in range(n):
        sid = uuid.uuid4()
        sess = TestSession.create(
            session_id=str(sid), account_id=str(user_id), spec=SPEC, claimed_grade="middle", clock=clock,
            rng=random.Random(i),
        )
        sess.start(clock)
        sess.pending = Selection(
            seq=1, family_id=family_id, module=p.module, family_grade=fam.target_grade, is_pretest=False,
            item=item, time_limit_s=90, reason={},
        )
        sess.pending_shown_at = clock.now()
        sess.answer(1, correct, clock, instance_id=str(inst.id))
        sess.status = SessionStatus.FINAL
        db.add(
            SessionRow(
                id=sid, candidate_id=user_id, specialization=SPEC, kind="initial", claimed_grade="middle", seed=i,
                status="FINAL", state=session_to_state(sess),
            )
        )
        db.flush()
        db.add(
            TestResponse(
                session_id=sid, seq=1, instance_id=inst.id, family_id=family_id, module=p.module,
                answered_at=clock.now(), is_correct=correct, rt_ms=30_000,
            )
        )
    db.flush()


def test_calibrate_updates_difficulty_only_with_consent(api, bank, make_account, methodist):
    # Лёгкие семейства: верные ответы кандидатов среднего уровня для них ожидаемы — дрейфа нет
    fams = sorted(
        f.id
        for f in all_families().values()
        if SPEC in f.specs and not f.is_anchor and f.module == "python_core" and f.target_grade in ("intern", "junior")
    )
    f1, f2 = fams[0], fams[1]
    agreed, refused = make_account(), make_account()
    grant_consent(bank, agreed.user_id, ConsentPurpose.calibration)
    _fake_sessions(bank, agreed.user_id, f1, 35, correct=True)
    _fake_sessions(bank, refused.user_id, f2, 35, correct=True)  # без согласия «calibration»
    before = bank.get(ItemFamilyParams, (f1, SPEC)).b

    out = api.post("/api/lab/calibrate", methodist, {"specialization": SPEC})
    assert out["specializations"] == [SPEC] and out["families_updated"] == 1
    rep = {e["family_id"]: e for e in out["report"][SPEC]["families"]}
    assert rep[f1]["updated"] and rep[f1]["n"] == 35 and rep[f1]["reason"] in ("map", "map_not_converged")
    assert rep[f2]["n"] == 0 and rep[f2]["reason"] == "not_enough_responses"
    assert {d["family_id"]: d["action"] for d in out["report"][SPEC]["drift"]} == {f1: None}
    p1 = bank.get(ItemFamilyParams, (f1, SPEC))
    assert p1.source == "map" and p1.b < before and p1.n_responses == 35 and p1.params_version == 2
    assert bank.get(CalibrationRun, uuid.UUID(out["run_id"])).families_updated == 1

    # Повтор: новых ответов нет — ничего не меняется (ответы до calibrated_at не учитываются)
    out2 = api.post("/api/lab/calibrate", methodist, {"specialization": SPEC})
    assert out2["families_updated"] == 0
    assert api.post("/api/lab/calibrate", methodist, {"specialization": "data_ml"}, expect=400)["code"] == (
        "specialization_not_testable"
    )


def test_cusum_quarantines_family_that_suddenly_became_easy(api, bank, make_account, methodist):
    """Слив метода: трудное семейство вдруг решают все подряд → CUSUM > h → карантин (spec-testing §3.10)."""
    hard = sorted(
        f.id
        for f in all_families().values()
        if SPEC in f.specs and not f.is_anchor and f.target_grade == "senior" and f.c == 0
    )[0]
    acc = make_account()
    grant_consent(bank, acc.user_id, ConsentPurpose.calibration)
    _fake_sessions(bank, acc.user_id, hard, 40, correct=True)
    out = api.post("/api/lab/calibrate", methodist, {"specialization": SPEC})
    drift = {d["family_id"]: d for d in out["report"][SPEC]["drift"]}
    assert drift[hard]["action"] == "quarantine" and drift[hard]["cusum"] > 6
    p = bank.get(ItemFamilyParams, (hard, SPEC))
    assert p.status == "quarantine" and p.source == "prior"  # в этом прогоне не уточняется
    assert bank.scalars(select(DriftEvent).where(DriftEvent.family_id == hard)).one().statistic == "cusum"
    assert hard not in {e["family_id"] for e in out["report"][SPEC]["families"]}
    b = api.get(f"/api/lab/bank?specialization={SPEC}", methodist)
    assert b["by_status"]["quarantine"] == 1
    fam = next(f for f in b["families"] if f["family_id"] == hard)
    assert fam["params"][0]["cusum"] > 6
