"""Пул одноразовых экземпляров (spec-testing §2.5, §3.8): генерация потоками, идемпотентность, долив при
остатке < min_free, ключ только шифртекстом, приоры семейств, выдача FOR UPDATE SKIP LOCKED и скрипт."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.models.testing import ItemFamilyParams, ItemInstance
from app.services.testing.keystore import decrypt_key
from app.services.testing.pool import (
    SEED_FLOOR,
    ensure_pool,
    generate_rows,
    issue_instance,
    needs_top_up,
    pool_seed,
    pool_seeds,
    pool_stats,
    rotate_legacy,
    seed_family_params,
)
from app.testing.bank.registry import all_families
from app.testing.irt import b_from_expert, load_scale

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "generate_pool.py"


def _two_python_families() -> list[str]:
    return sorted(f.id for f in all_families().values() if f.module == "python_core")[:2]


def _counts(db: Session, fid: str) -> tuple[int, int]:
    total, free = db.execute(
        select(func.count(), func.count().filter(ItemInstance.issued_at.is_(None))).where(ItemInstance.family_id == fid)
    ).one()
    return int(total), int(free)


def test_pool_is_idempotent_and_tops_up_below_min_free(db_session):
    fids = _two_python_families()
    reps = ensure_pool(db_session, family_ids=fids, n=6, min_free=3, threads=2)
    assert {r.family_id: r.generated for r in reps} == {fids[0]: 6, fids[1]: 6}
    assert all(r.error is None for r in reps)
    assert [r.generated for r in ensure_pool(db_session, family_ids=fids, n=6, min_free=3, threads=2)] == [0, 0]

    # Ключ — только шифртекстом; rendered — только публичная часть; хеш совпадает с банком
    fam = all_families()[fids[0]]
    for inst in db_session.scalars(select(ItemInstance).where(ItemInstance.family_id == fids[0])):
        ref = fam.generate(inst.seed)
        assert decrypt_key(inst.key_enc) == ref.key and inst.key_hash == ref.key_hash
        assert set(inst.rendered) == {"prompt", "code", "language", "options", "lines", "table", "answer_mode", "answer_hint"}
        assert inst.variant_code == ref.variant_code and inst.issued_at is None

    # Выдача: следующий свободный, каждому — свой; при остатке < min_free — долив до n свободных
    issued = [
        issue_instance(db_session, fids[0], session_id=None, user_id=None, now=clock.now()) for _ in range(4)
    ]
    seeds = [int(i.seed) for i in issued]
    assert len({i.id for i in issued}) == 4 and seeds == sorted(seeds, reverse=True)
    assert set(seeds) <= set(pool_seeds(fids[0], range(6)))  # номера 0–5 → секретные seed
    assert _counts(db_session, fids[0]) == (6, 2)
    assert needs_top_up(db_session, fids[0], min_free=3)
    reps = ensure_pool(db_session, family_ids=[fids[0]], n=6, min_free=3)
    assert reps[0].generated == 4 and _counts(db_session, fids[0]) == (10, 6)
    assert not needs_top_up(db_session, fids[0], min_free=3)
    all_seeds = set(db_session.scalars(select(ItemInstance.seed).where(ItemInstance.family_id == fids[0])))
    assert all_seeds == set(pool_seeds(fids[0], range(10)))  # долив продолжил нумерацию без пропусков


def test_pool_seeds_are_secret_so_variant_code_does_not_reveal_the_key(db_session):
    """Код генераторов открыт, код варианта виден кандидату. При seed 0, 1, 2, … ключ находился перебором
    seed по коду варианта (sha256(семейство:seed)[:4]) и запуском открытого генератора. Seed боевого
    пула — HMAC от секрета в [2^50, 2^62): перебор небольших seed ничего не находит."""
    fid = _two_python_families()[0]
    fam = all_families()[fid]

    def recover(variant_code: str, rendered: dict) -> list[int]:
        """Атака по публичным данным задания: код варианта + текст → seed (а по seed — ключ)."""
        return [
            s
            for s in range(0, 3000)
            if hashlib.sha256(f"{fid}:{s}".encode()).hexdigest()[:4].upper() == variant_code
            and json.loads(json.dumps(fam.generate(s).rendered.to_public())) == rendered
        ]

    legacy = fam.generate(1234)  # так выглядел экземпляр старого пула
    assert recover(legacy.variant_code, json.loads(json.dumps(legacy.rendered.to_public()))) == [1234]

    ensure_pool(db_session, family_ids=[fid], n=3, min_free=1)
    inst = issue_instance(db_session, fid, session_id=None, user_id=None, now=clock.now())
    assert int(inst.seed) >= SEED_FLOOR
    assert recover(inst.variant_code, inst.rendered) == []
    # Seed зависят от секрета: другой секрет — другой пул; при одном секрете — детерминированно
    assert pool_seed(fid, 0, b"secret-a") != pool_seed(fid, 0, b"secret-b")
    assert pool_seed(fid, 0, b"secret-a") == pool_seed(fid, 0, b"secret-a")
    assert all(SEED_FLOOR <= pool_seed(fid, i, b"x") < 1 << 62 for i in range(200))


def test_rotate_legacy_replaces_predictable_free_instances(db_session):
    fid = _two_python_families()[1]
    fam = all_families()[fid]
    # Пул «до исправления»: seed 0–4, один уже выдан (на него ссылаются ответы — он остаётся)
    db_session.execute(pg_insert(ItemInstance).values(generate_rows(fam, range(5))))
    db_session.flush()
    old = issue_instance(db_session, fid, session_id=None, user_id=None, now=clock.now())
    assert int(old.seed) == 4  # других нет — выдаётся старый (с предупреждением в журнале)
    st = pool_stats(db_session, [fid])[fid]
    assert (st.total, st.free, st.secret, st.legacy_free) == (5, 4, 0, 4)

    assert rotate_legacy(db_session, family_ids=[fid]) == 4
    assert _counts(db_session, fid) == (1, 0)
    reps = ensure_pool(db_session, family_ids=[fid], n=5, min_free=2)
    assert reps[0].generated == 4
    st = pool_stats(db_session, [fid])[fid]
    assert (st.total, st.free, st.secret, st.legacy_free) == (5, 4, 4, 0)
    # Секретные выдаются раньше старых
    nxt = issue_instance(db_session, fid, session_id=None, user_id=None, now=clock.now())
    assert int(nxt.seed) >= SEED_FLOOR
    assert rotate_legacy(db_session, family_ids=[fid]) == 0


def test_family_priors_per_scale(db_session):
    shared = next(f for f in all_families().values() if len(f.specs) == 2)
    created = seed_family_params(db_session, family_ids=[shared.id])
    assert created == 2  # общий модуль — своя строка на каждой шкале
    assert seed_family_params(db_session, family_ids=[shared.id]) == 0
    rows = db_session.scalars(select(ItemFamilyParams).where(ItemFamilyParams.family_id == shared.id)).all()
    scale = load_scale()
    assert {r.specialization for r in rows} == set(shared.specs)
    for r in rows:
        assert r.b == pytest.approx(b_from_expert(shared.target_grade, shared.position, scale), abs=1e-4)
        assert r.sigma_b == scale.sigma_b_agree and r.status == "operational" and r.source == "prior"
    # Уточнённые калибровкой параметры повторный посев не трогает
    rows[0].b, rows[0].source = 0.123, "map"
    db_session.flush()
    seed_family_params(db_session, family_ids=[shared.id])
    assert db_session.get(ItemFamilyParams, (shared.id, rows[0].specialization)).b == 0.123


def test_generate_pool_script(db_session, monkeypatch, capsys):
    spec = importlib.util.spec_from_file_location("generate_pool_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    conn = db_session.connection()
    monkeypatch.setattr(
        mod, "get_sessionmaker", lambda: lambda: Session(bind=conn, join_transaction_mode="create_savepoint")
    )
    fid = _two_python_families()[0]
    assert mod.main(["--family", fid, "--n", "5", "--min-free", "2", "--threads", "2"]) == 0
    out = capsys.readouterr().out
    assert f"+ {fid}: 5" in out and "ошибок 0" in out
    assert _counts(db_session, fid) == (5, 5)
    assert db_session.get(ItemFamilyParams, (fid, "backend_python")) is not None
    assert mod.main(["--family", fid, "--n", "5", "--min-free", "2"]) == 0  # повтор — без изменений
    assert _counts(db_session, fid) == (5, 5)
    assert mod.main(["--stats", "--family", fid]) == 0
    out = capsys.readouterr().out
    assert "свободно    5" in out and "со старыми seed: 0" in out

    # Старый пул (seed 0, 1, …): --stats предупреждает, --rotate-legacy заменяет свободные экземпляры
    other = _two_python_families()[1]
    db_session.execute(pg_insert(ItemInstance).values(generate_rows(all_families()[other], range(3))))
    db_session.flush()
    assert mod.main(["--stats", "--family", other]) == 0
    assert "предсказуемых seed: 3" in capsys.readouterr().out
    assert mod.main(["--family", other, "--n", "3", "--min-free", "1", "--rotate-legacy"]) == 0
    out = capsys.readouterr().out
    assert "Удалено свободных экземпляров с предсказуемыми seed: 3" in out and f"+ {other}: 3" in out
    st = pool_stats(db_session, [other])[other]
    assert (st.total, st.legacy_free, st.secret) == (3, 0, 3)


def test_background_top_up_runs_once_per_family_at_a_time(db_engine, db_session, monkeypatch):
    """Долив после GET next: своя сессия на Engine; пока семейство доливается, повторный запуск пропускается."""
    from app.services.testing import pool as pool_mod

    fid = _two_python_families()[0]
    calls: list[list[str]] = []

    def fake_ensure(s, *, family_ids, threads):
        calls.append(list(family_ids))
        assert fid in pool_mod._TOPPING_UP  # пока идёт долив, семейство помечено
        return []

    monkeypatch.setattr(pool_mod, "ensure_pool", fake_ensure)
    pool_mod._TOPPING_UP.add(fid)
    try:
        pool_mod.top_up_background(db_engine, [fid])
        assert calls == []  # уже доливается — второй генерации нет
    finally:
        pool_mod._TOPPING_UP.discard(fid)
    pool_mod.top_up_background(db_engine, [fid])
    assert calls == [[fid]] and fid not in pool_mod._TOPPING_UP
    pool_mod.top_up_background(db_session.connection(), [fid])  # не Engine (тестовая транзакция) — не трогаем
    assert calls == [[fid]]


def test_parallel_sessions_never_get_the_same_instance(db_engine):
    """Две транзакции одновременно: первая держит строку (FOR UPDATE), вторая её пропускает (SKIP LOCKED)."""
    fid = f"test.skip_locked.{uuid.uuid4().hex[:8]}"  # отдельное «семейство» — не мешает другим тестам
    with Session(db_engine) as setup:
        for seed in range(3):
            setup.add(
                ItemInstance(family_id=fid, seed=seed, rendered={}, key_enc="x", key_hash=f"h{seed}", variant_code="0000")
            )
        setup.commit()
    a, b = Session(db_engine), Session(db_engine)
    try:
        ia = issue_instance(a, fid, session_id=None, user_id=None, now=clock.now())
        ib = issue_instance(b, fid, session_id=None, user_id=None, now=clock.now())
        assert ia is not None and ib is not None and ia.id != ib.id
        assert (ia.seed, ib.seed) == (2, 1)  # по убыванию seed; строку соседа пропустили
    finally:
        a.rollback()
        b.rollback()
        a.close()
        b.close()
        with Session(db_engine) as cleanup:
            cleanup.execute(delete(ItemInstance).where(ItemInstance.family_id == fid))
            cleanup.commit()


def test_issue_prefers_instance_with_ready_twin(db_session):
    """Клон с готовым двойником выдаётся раньше остальных (VALIDATION §10.4); уже показанный двойник приоритета не даёт."""
    from app.services.anticheat.models import AnticheatTwin

    fid = _two_python_families()[0]
    ensure_pool(db_session, family_ids=[fid], n=5, min_free=1, threads=1)
    free = list(db_session.scalars(
        select(ItemInstance).where(ItemInstance.family_id == fid, ItemInstance.issued_at.is_(None)).order_by(ItemInstance.seed)
    ))
    lowest, second = free[0], free[1]  # без двойников их бы выдали последними (порядок по убыванию seed)
    db_session.add(AnticheatTwin(instance_id=lowest.id, family_id=fid, status="ok", data={}))
    db_session.add(AnticheatTwin(instance_id=second.id, family_id=fid, status="none", data={}))  # двойника нет — не приоритет
    db_session.flush()
    first = issue_instance(db_session, fid, session_id=None, user_id=None, now=clock.now())
    assert first is not None and first.id == lowest.id
    nxt = issue_instance(db_session, fid, session_id=None, user_id=None, now=clock.now())
    assert nxt is not None and nxt.id == max(free, key=lambda i: i.seed).id  # дальше — прежний порядок по seed
