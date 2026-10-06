"""Семейства со своим Family.twin() (QA, DevOps: ключ считает решатель по показанному тексту): сборка двойника идёт через
dispatch — `Family.twin()` первым, общий мутатор только если вернулся None; предрасчёт, выдача и улика работают и для них."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.models.testing import ItemInstance, TestResponse
from app.models.testing import TestSession as SessionRow
from app.services.anticheat import models as _models  # noqa: F401 — регистрирует таблицы
from app.services.anticheat import runtime, store
from app.services.anticheat.models import AnticheatEvidence, AnticheatTwin
from app.services.testing.pool import generate_rows
from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.dispatch import family_twin_result, overrides_twin, twin_applicable, variant_twin
from app.testing.anticheat.twins import try_make_twin
from app.testing.anticheat.verification import SessionItem, build_kit
from app.testing.bank.base import Family
from app.testing.bank.registry import all_families
from tests import test_api_testing_support as support

bank = support.bank
api = support.api

pytestmark = pytest.mark.usefixtures("frozen_clock")

OWN = ["ops.docker.cache", "qa.pytest.failed", "ops.cron.next_runs"]
GENERIC = "py.defaults.mutable"


def test_dispatch_prefers_family_twin_and_falls_back_to_generic():
    reg = all_families()
    cfg = load_anticheat_config()
    for fid in OWN:
        fam = reg[fid]
        assert overrides_twin(fam) and type(fam).twin is not Family.twin
        inst = fam.generate(3)
        assert twin_applicable(fam, inst, cfg)  # общий мутатор такому «тексту» не подходит, но свой двойник есть
        assert try_make_twin(fam, inst, cfg=cfg).reason == "language_not_executable"
        res = family_twin_result(fam, inst, cfg=cfg)
        assert res.twin is not None and res.reason == "ok" and res.twin.executor == "scenario_solver"
        own = fam.twin(inst)
        assert own is not None and own.twin_key == res.twin.twin_key and own.clipboard_text == res.twin.clipboard_text
        assert res.twin.twin_key != res.twin.key and res.twin.clipboard_text != res.twin.shown_text
    # Обычное семейство: Family.twin не переопределён — тот же общий мутатор, но с гейтом пула и причинами отказа
    fam = reg[GENERIC]
    inst = fam.generate(4)
    assert not overrides_twin(fam)
    a, b = family_twin_result(fam, inst), try_make_twin(fam, inst)
    assert a.twin is not None and a.twin.twin_key == b.twin.twin_key


def test_variant_twin_uses_a_different_literal_for_own_twin_families():
    fam = all_families()["ops.docker.cache"]
    inst = fam.generate(5)
    base = family_twin_result(fam, inst).twin
    assert base is not None
    v = variant_twin(fam, inst, avoid=[base.changed_literal.ident], salt="verify#t")
    assert v is not None and v.changed_literal.ident != base.changed_literal.ident
    assert v.clipboard_text != base.clipboard_text


def test_verification_kit_builds_for_own_twin_families():
    fam = all_families()["qa.pytest.failed"]
    items = [SessionItem(fam.id, 7 + i, fam.generate(7 + i).key, True, rt_ms=10_000, median_rt_ms=40_000) for i in range(2)]
    kit = build_kit(items, candidate_ref="t")
    assert kit.trace_questions and kit.trace_questions[0].method == "literal_mutation"
    assert kit.trace_questions[0].verified_by == "execution" and kit.trace_questions[0].expected_answer


def test_twin_blob_roundtrip_keeps_keys_encrypted():
    fam = all_families()["ops.cron.next_runs"]
    inst = fam.generate(6)
    twin = family_twin_result(fam, inst).twin
    assert twin is not None
    blob = store.twin_blob(twin)
    assert twin.twin_key not in str(blob["data"]) and twin.key not in blob["secret_enc"]
    back = store.twin_from_blob(blob)
    assert back.twin_key == twin.twin_key and back.key == twin.key and back.changed_literal == twin.changed_literal
    assert back.err_trace_keys == twin.err_trace_keys and back.clipboard_text == twin.clipboard_text


def _insert_pool(db, family_id: str, n: int = 3) -> list[ItemInstance]:
    fam = all_families()[family_id]
    rows = generate_rows(fam, [10**9 + 100 + i for i in range(n)])
    db.execute(pg_insert(ItemInstance).values(rows).on_conflict_do_nothing(index_elements=["family_id", "seed"]))
    db.flush()
    return list(db.query(ItemInstance).filter(ItemInstance.family_id == family_id).order_by(ItemInstance.seed))


def test_precompute_issue_and_evidence_for_family_with_own_twin(bank, make_account):
    fid = "qa.pytest.failed"
    insts = _insert_pool(bank, fid)
    reports = store.precompute_twins(bank, family_ids=[fid], per_family=3, workers=2)
    assert reports[0].built >= 1 and reports[0].skipped_ineligible == 0  # форма «text» не отсекает свой двойник
    # Повторный запуск ничего не делает
    assert store.precompute_twins(bank, family_ids=[fid], per_family=3)[0].candidates == 0
    row = next(r for i in insts if (r := bank.get(AnticheatTwin, i.id)) is not None and r.status == "ok")
    inst = bank.get(ItemInstance, row.instance_id)
    # Выдача: инстанс «выдан» сессии → в rendered появляется clip без ключей
    acc = make_account()
    sid = uuid.uuid4()
    bank.add(
        SessionRow(
            id=sid, candidate_id=acc.user_id, specialization="qa", kind="initial", claimed_grade="middle", seed=1,
            status="IN_PROGRESS", state={},
        )
    )
    bank.flush()
    inst.issued_to_session_id = sid
    inst.issued_at = datetime(2026, 10, 5, tzinfo=UTC)
    assert runtime.issue_clip(bank, inst) is True
    clip = inst.rendered["clip"]
    assert set(clip) == {"render_id", "clipboard_text"} and clip["clipboard_text"] != (inst.rendered.get("code") or "")
    twin = store.twin_from_row(row)
    assert twin.twin_key not in str(inst.rendered) or twin.twin_key in clip["clipboard_text"]
    # Ответ ключом копии — улика twin_trace, ключом экрана — correct
    resp = TestResponse(session_id=sid, seq=1, instance_id=inst.id, family_id=fid, module="qa", shown_at=datetime(2026, 10, 5, tzinfo=UTC))
    bank.add(resp)
    bank.flush()
    ev = runtime.record_answer(bank, inst, twin.twin_key)
    assert ev is not None and ev.kind == "twin_trace" and ev.matched == "twin_key" and ev.strength == "exact"
    assert runtime.record_answer(bank, inst, twin.twin_key) is None  # повтор безопасен: одна запись на ответ
    assert bank.query(AnticheatEvidence).count() == 1


def test_hook_failure_does_not_poison_the_transaction(bank, make_account, monkeypatch):
    """Сбой SQL внутри хука откатывается в SAVEPOINT: запрос кандидата продолжает работать с той же транзакцией."""
    from sqlalchemy import text

    insts = _insert_pool(bank, "py.defaults.mutable", 2)
    inst = insts[0]
    acc = make_account()
    sid = uuid.uuid4()
    bank.add(
        SessionRow(
            id=sid, candidate_id=acc.user_id, specialization="backend_python", kind="initial", claimed_grade="middle",
            seed=1, status="IN_PROGRESS", state={},
        )
    )
    bank.flush()
    inst.issued_to_session_id = sid
    bank.flush()

    def boom(db, *a, **k):
        db.execute(text("select * from no_such_table"))

    monkeypatch.setattr(store, "ensure_twin_row", boom)
    assert runtime.issue_clip(bank, inst) is False
    assert "clip" not in (inst.rendered or {})
    assert bank.execute(text("select 1")).scalar() == 1  # транзакция жива
