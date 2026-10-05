"""Детекторы целостности и маппинг доверия (spec-testing §3.10)."""

import random
from datetime import UTC, datetime, timedelta

import numpy as np

from app.testing.bank.base import Instance, Rendered
from app.testing.detectors import (
    HARD,
    KIND_FAST,
    KIND_FOCUS,
    KIND_FOREIGN_KEY,
    KIND_LZ,
    KIND_PASTE,
    SOFT,
    Flag,
    IssuedInstance,
    SessionEvent,
    TimedResponse,
    answer_hash,
    cusum_scan,
    detect_events,
    detect_fast_correct,
    detect_foreign_key,
    detect_person_fit,
    drift_probs,
    evaluate_session,
    family_needs_quarantine_by_foreign_keys,
    key_collision_rate,
    trust_from_flags,
)
from app.testing.irt import ItemParams, Posterior, load_scale

SCALE = load_scale()
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def inst(fid, seed, key):
    return Instance(family_id=fid, seed=seed, rendered=Rendered(prompt="Что выведет код?"), key=key)


# --- «чужой ключ» -----------------------------------------------------------------------------------


def test_answer_hash_matches_instance_key_hash():
    i = inst("py.slicing.step", 7, "[3, 5, 7]")
    assert answer_hash("py.slicing.step", "[3, 5, 7]") == i.key_hash
    j = inst("py.parsons", 1, ["a", "c", "b"])
    assert answer_hash("py.parsons", ["a", "c", "b"]) == j.key_hash


def test_foreign_key_detected_with_source():
    anna = inst("py.slicing.step", 1, "[0, 2, 4]")
    ilya = inst("py.slicing.step", 2, "[1, 4]")
    issued = [
        IssuedInstance("i-anna", anna.family_id, anna.key_hash, "acc-anna", "sess-anna"),
        IssuedInstance("i-ilya", ilya.family_id, ilya.key_hash, "acc-ilya", "sess-ilya"),
    ]
    # Илья вставил ответ Анны в своё задание → неверно и «чужой ключ», источник найден
    flag = detect_foreign_key(
        family_id="py.slicing.step",
        answer_key_hash=answer_hash("py.slicing.step", "[0, 2, 4]"),
        own_key_hash=ilya.key_hash,
        account_id="acc-ilya",
        issued=issued,
        family_collision_rate=0.0,
    )
    assert flag is not None and flag.kind == KIND_FOREIGN_KEY and flag.severity == HARD
    assert flag.source_session_id == "sess-anna" and flag.source_account_id == "acc-anna"
    # Л3 (методист) видит источник; сам кандидат свой флаг видит, но не чужую сессию/аккаунт
    assert flag.to_dict()["source_session_id"] == "sess-anna"
    public = flag.to_dict(for_candidate=True)
    assert "source_session_id" not in public and "source_account_id" not in public
    assert "anna" not in str(public)
    # свой верный ответ — не флаг
    assert detect_foreign_key(
        family_id="py.slicing.step", answer_key_hash=ilya.key_hash, own_key_hash=ilya.key_hash,
        account_id="acc-ilya", issued=issued, family_collision_rate=0.0,
    ) is None
    # совпадение с собственным старым экземпляром (тот же аккаунт) — не флаг
    assert detect_foreign_key(
        family_id="py.slicing.step", answer_key_hash=anna.key_hash, own_key_hash="other",
        account_id="acc-anna", issued=issued, family_collision_rate=0.0,
    ) is None
    # много коллизий ключей в семействе (≥ 2%) → совпадение может быть случайным
    assert detect_foreign_key(
        family_id="py.slicing.step", answer_key_hash=anna.key_hash, own_key_hash=ilya.key_hash,
        account_id="acc-ilya", issued=issued, family_collision_rate=0.05,
    ) is None


def test_key_collision_rate_and_family_quarantine():
    assert key_collision_rate(["a", "b", "c", "d"]) == 0.0
    assert key_collision_rate(["a"] * 50 + [str(i) for i in range(50)]) == 0.49
    times = [T0 - timedelta(days=d) for d in (1, 3, 6)]
    assert family_needs_quarantine_by_foreign_keys(times, T0)
    assert not family_needs_quarantine_by_foreign_keys(times[:2], T0)
    assert not family_needs_quarantine_by_foreign_keys([T0 - timedelta(days=d) for d in (1, 8, 9)], T0)


# --- person-fit и «быстро и верно» ----------------------------------------------------------------


def test_person_fit_flags_aberrant_pattern():
    items = [ItemParams(b=b, a=1.35) for b in np.linspace(-2, 2, 12)]
    aberrant = [0 if it.b < 0 else 1 for it in items]
    normal = [1 if it.b < 0.2 else 0 for it in items]
    f = detect_person_fit(items, aberrant)
    assert f is not None and f.kind == KIND_LZ and f.severity == SOFT and f.value < -2.33
    assert detect_person_fit(items, normal) is None
    assert detect_person_fit(items[:5], [0, 0, 1, 1, 1]) is None  # мало заданий


def test_fast_and_correct_on_hard_items():
    easy = [TimedResponse(f"e{i}", ItemParams(b=-1.0, a=1.35), i % 3 != 0, 40_000) for i in range(8)]
    hard_fast = [TimedResponse(f"h{i}", ItemParams(b=1.5, a=1.35), True, 5_000) for i in range(2)]
    medians = {r.family_id: 60_000 for r in easy + hard_fast}
    flag = detect_fast_correct(easy + hard_fast, medians)
    assert flag is not None and flag.kind == KIND_FAST and flag.value == 2
    assert flag.message_ru.endswith("(2 раза)")
    assert detect_fast_correct(easy + hard_fast[:1], medians) is None  # один раз — не флаг
    slow = [TimedResponse(r.family_id, r.item, True, 30_000) for r in hard_fast]
    assert detect_fast_correct(easy + slow, medians) is None


def test_focus_and_paste_events():
    def blurs(n, secs):
        ev = []
        for i in range(n):
            t = T0 + timedelta(minutes=i)
            ev += [SessionEvent(t, "blur"), SessionEvent(t + timedelta(seconds=secs), "focus")]
        return ev

    assert [f.kind for f in detect_events(blurs(4, 15))] == [KIND_FOCUS]
    assert detect_events(blurs(4, 15))[0].message_ru == "Уход со вкладки дольше 10 с: 4 раза"
    assert detect_events(blurs(5, 15))[0].message_ru.endswith(": 5 раз")
    assert detect_events(blurs(3, 15)) == []  # нужно больше 3
    assert detect_events(blurs(10, 5)) == []  # короткие уходы не считаются
    flags = detect_events([SessionEvent(T0, "paste", {"field": "answer"})])
    assert [f.kind for f in flags] == [KIND_PASTE]
    assert detect_events([SessionEvent(T0, "paste", {"field": "search"})]) == []


def test_trust_mapping():
    soft = Flag("lz", SOFT, "")
    hard = Flag(KIND_FOREIGN_KEY, HARD, "")
    assert trust_from_flags([]).trust == 1.0
    assert trust_from_flags([soft]).trust == 0.75
    assert trust_from_flags([soft, soft]).trust == 0.75
    v = trust_from_flags([soft] * 3)
    assert v.under_review and v.trust is None and v.status == "UNDER_REVIEW"
    assert trust_from_flags([hard]).under_review
    dismissed = Flag("lz", SOFT, "", status="dismissed")
    assert trust_from_flags([dismissed] * 5).trust == 1.0


def test_evaluate_session_collects_flags():
    items = [ItemParams(b=b, a=1.35) for b in np.linspace(-2, 2, 12)]
    xs = [0 if it.b < 0 else 1 for it in items]
    events = [SessionEvent(T0, "paste")]
    flags = evaluate_session(items, xs, events=events)
    assert {f.kind for f in flags} == {KIND_LZ, KIND_PASTE}
    assert trust_from_flags(flags).trust == 0.75


# --- CUSUM дрейфа --------------------------------------------------------------------------------


def _stream(n, b_effective, rng, b_model=0.0):
    """Ответы на семейство: θ ~ N(0,1), апостериор кандидата ≈ N(θ, 0.3²)."""
    item = ItemParams(b=b_model, a=1.35)
    obs = []
    for _ in range(n):
        th = rng.gauss(0, 1)
        post = Posterior.prior(SCALE, mu=th + rng.gauss(0, 0.3), sd=0.3)
        p0, p1 = drift_probs(post, item, 0.5)
        p_true = 1 / (1 + np.exp(-1.35 * (th - b_effective)))
        obs.append((rng.random() < p_true, p0, p1))
    return obs


def test_cusum_quiet_on_honest_and_alarms_on_leak():
    rng = random.Random(42)
    _, alarm = cusum_scan(_stream(300, 0.0, rng), h=6.0)
    assert alarm is None
    # метод слит: семейство «стало легче» на 1 логит → тревога в пределах ~200 ответов
    _, alarm = cusum_scan(_stream(400, -1.0, random.Random(7)), h=6.0)
    assert alarm is not None and alarm <= 200
