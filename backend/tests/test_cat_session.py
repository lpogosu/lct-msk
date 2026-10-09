"""Машины состояний сессии и квалификации, правила 14/30/90, Clock (spec-testing §2.4–2.5, concept §5.3)."""

import random
from datetime import UTC, datetime, timedelta

import pytest

from app.testing.cat import BORDERLINE_PASS, PASSED, Decision
from app.testing.detectors import HARD, KIND_FOREIGN_KEY, SOFT, Flag
from app.testing.irt import ItemParams, load_scale
from app.testing.session import (
    FixedClock,
    Qualification,
    QualStatus,
    SessionError,
    SessionOutcome,
    SessionStatus,
    TestSession,
    ago_ru,
    category_of,
    freshness_of,
    fullstack_grade,
    plural_ru,
    until_ru,
)
from app.testing.simulate import SimulatedExaminee, synthetic_families

SCALE = load_scale()
SPEC = "backend_python"
BANK = synthetic_families((SPEC,), seed=3)
BY_ID = {f.id: f for f in BANK}
T0 = datetime(2026, 10, 5, 12, 0, tzinfo=UTC)


def new_session(claimed="middle", seed=0, kind="initial", sid="s1"):
    clock = FixedClock(T0)
    rng = random.Random(seed)
    s = TestSession.create(
        session_id=sid, account_id="acc", spec=SPEC, claimed_grade=claimed, clock=clock, rng=rng, kind=kind
    )
    return s, clock, rng


def play(sess, clock, rng, theta, step_s=40):
    ex = SimulatedExaminee(theta=theta)
    while sess.status == SessionStatus.IN_PROGRESS:
        sel = sess.next_item(BANK, rng, clock)
        if sel is None:
            break
        clock.advance(seconds=step_s)
        sess.answer(sel.seq, ex.answer(BY_ID[sel.family_id], SPEC, SCALE, rng), clock)
    return sess


def outcome(**kw):
    base = dict(
        session_id="s1", kind="initial", status="FINAL", decision=PASSED, target_grade="middle",
        confirmed_grade="middle", borderline=False, p_pass=0.95, theta=0.4, se=0.3, pending_offers=False,
        trust=1.0, under_review=False, subscores={},
    )
    base.update(kw)
    return SessionOutcome(**base)


# --- Clock и относительные даты -----------------------------------------------------------------


def test_relative_dates_russian():
    assert plural_ru(1, "день", "дня", "дней") == "день"
    assert plural_ru(3, "день", "дня", "дней") == "дня"
    assert plural_ru(11, "день", "дня", "дней") == "дней"
    assert plural_ru(21, "день", "дня", "дней") == "день"
    assert until_ru(T0 + timedelta(days=14), T0) == "через 14 дней"
    assert until_ru(T0 + timedelta(hours=3), T0) == "через 3 часа"
    assert until_ru(T0 - timedelta(days=1), T0) == "сейчас"
    assert ago_ru(T0 - timedelta(days=2), T0) == "2 дня назад"


# --- тестовая сессия ----------------------------------------------------------------------------


def test_session_flow_idempotent_next_and_answer():
    s, clock, rng = new_session()
    with pytest.raises(SessionError):
        s.next_item(BANK, rng, clock)  # не начата
    s.start(clock)
    sel = s.next_item(BANK, rng, clock)
    assert s.next_item(BANK, rng, clock) is sel  # пока нет ответа — то же задание
    assert s.progress()["text_ru"] == "задание 1 · от 8 до 20"
    clock.advance(seconds=30)
    r1 = s.answer(sel.seq, True, clock)
    r2 = s.answer(sel.seq, False, clock)  # повтор с тем же seq — без двойного учёта
    assert r1 is r2 and r1.correct and len(s.cat.responses) == 1
    assert r1.rt_ms == 30_000
    with pytest.raises(SessionError):
        s.answer(99, True, clock)


def test_timeout_counts_as_wrong():
    s, clock, rng = new_session()
    s.start(clock)
    sel = s.next_item(BANK, rng, clock)
    clock.advance(seconds=sel.time_limit_s + 6)  # лимит + 5 с запаса истёк
    rec = s.answer(sel.seq, True, clock)
    assert rec.timed_out and not rec.correct


def test_pause_resume_and_abandon():
    s, clock, rng = new_session()
    s.start(clock)
    s.next_item(BANK, rng, clock)
    s.pause(clock)
    clock.advance(minutes=20)
    assert s.resume(clock) == SessionStatus.IN_PROGRESS
    s.pause(clock)
    clock.advance(minutes=31)
    assert s.tick(clock) == SessionStatus.ABANDONED
    assert s.outcome().confirmed_grade is None


def test_dropout_without_explicit_pause_is_abandoned():
    """Закрыли вкладку: паузу никто не вызвал, но тишина > 30 мин → ABANDONED (обрыв, §2.4)."""
    s, clock, rng = new_session()
    s.start(clock)
    sel = s.next_item(BANK, rng, clock)
    clock.advance(seconds=sel.time_limit_s + 5)  # дедлайн задания
    clock.advance(minutes=20)
    assert s.tick(clock) == SessionStatus.IN_PROGRESS  # опоздавший ответ ещё принимается (как неверный)
    clock.advance(minutes=11)
    assert s.tick(clock) == SessionStatus.ABANDONED
    # между заданиями (задание ещё не запрошено) — от последнего запроса
    s2, clock2, rng2 = new_session(sid="s2")
    s2.start(clock2)
    sel = s2.next_item(BANK, rng2, clock2)
    s2.answer(sel.seq, True, clock2)
    clock2.advance(minutes=29)
    assert s2.tick(clock2) == SessionStatus.IN_PROGRESS
    clock2.advance(minutes=2)
    assert s2.tick(clock2) == SessionStatus.ABANDONED


def test_decided_session_and_trust_mapping():
    s, clock, rng = new_session(claimed="junior", seed=1)
    s.start(clock)
    play(s, clock, rng, theta=2.5)
    assert s.decision.code == PASSED and 8 <= s.decision.n_items <= 20
    assert s.status == SessionStatus.EXTENSION_OFFERED  # уверенно выше → «попробовать Middle сейчас»
    assert s.offers[0].direction == "up"
    assert s.next_item(BANK, rng, clock) is None
    s.add_flags([Flag("paste", SOFT, "Вставка текста в поле ответа")])
    v = s.decline_extension(clock)
    assert s.status == SessionStatus.FINAL and s.trust == 0.75 and v.soft == 1
    out = s.outcome()
    assert out.confirmed_grade == "junior" and not out.pending_offers


@pytest.mark.parametrize(
    "flags,status,trust",
    [
        ([], SessionStatus.FINAL, 1.0),
        ([Flag("lz", SOFT, "")] * 2, SessionStatus.FINAL, 0.75),
        ([Flag("lz", SOFT, "")] * 3, SessionStatus.UNDER_REVIEW, None),
        ([Flag(KIND_FOREIGN_KEY, HARD, "")], SessionStatus.UNDER_REVIEW, None),
    ],
)
def test_close_trust_states(flags, status, trust):
    s, clock, rng = new_session(claimed="senior", seed=2)
    s.start(clock)
    play(s, clock, rng, theta=2.8)
    s.add_flags(flags)
    if s.status == SessionStatus.EXTENSION_OFFERED:
        s.decline_extension(clock)
    else:
        s.close(clock)
    assert s.status == status and s.trust == trust


def test_review_keep_or_invalidate():
    for keep, status, trust in ((True, SessionStatus.FINAL, 0.75), (False, SessionStatus.INVALIDATED, None)):
        s, clock, rng = new_session(claimed="senior", seed=2)
        s.start(clock)
        play(s, clock, rng, theta=2.8)
        s.add_flags([Flag(KIND_FOREIGN_KEY, HARD, "")])
        s.close(clock)
        s.review(keep, clock)
        assert s.status == status and s.trust == trust
    assert s.outcome().confirmed_grade is None  # аннулированная сессия грейда не даёт


def test_offer_expires_after_24h():
    s, clock, rng = new_session(claimed="junior", seed=1)
    s.start(clock)
    play(s, clock, rng, theta=2.5)
    assert s.status == SessionStatus.EXTENSION_OFFERED
    clock.advance(hours=23)
    assert s.tick(clock) == SessionStatus.EXTENSION_OFFERED
    clock.advance(hours=2)
    assert s.tick(clock) == SessionStatus.FINAL
    with pytest.raises(SessionError):
        s.accept_extension("up", clock, child_id="x")


def _failed_middle(theta=-0.9, seeds=range(60)):
    for seed in seeds:
        s, clock, rng = new_session(claimed="middle", seed=seed)
        s.start(clock)
        play(s, clock, rng, theta=theta)
        if not s.decision.passed:
            return s, clock, rng
    raise AssertionError("нет провала")


def test_failed_then_down_extension_reuses_answers():
    s, clock, rng = _failed_middle(theta=-0.6)
    down = next(o for o in s.offers if o.direction == "down")
    child = s.accept_extension("down", clock, child_id="s1-ext1")
    assert s.status == SessionStatus.FINAL and s.extended_by == "s1-ext1"
    assert child.parent_id == "s1" and child.kind == "ext_down"
    assert child.cat.n_scored == s.cat.n_scored  # прошлые ответы засчитываются
    if down.instant:
        assert child.decision is not None and child.decision.n_items == 0 and child.decision.code == PASSED
        assert child.outcome().confirmed_grade == "junior"
    else:
        play(child, clock, rng, theta=-0.6)
        assert child.decision.n_items <= 8


def test_extension_up_needs_090_borderline_keeps_base_grade():
    s, clock, rng = new_session(claimed="junior", seed=1)
    s.start(clock)
    play(s, clock, rng, theta=2.5)
    child = s.accept_extension("up", clock, child_id="s1-up")
    assert child.base_grade == "junior" and child.kind == "ext_up"
    # Добор вверх «на границе» не повышает (гистерезис)
    child.decision = Decision(BORDERLINE_PASS, "middle", 0.8, 0.1, 0.3, 8, 20, "max_items")
    child.status = SessionStatus.DECIDED
    assert child.outcome().confirmed_grade == "junior"
    child.decision = Decision(PASSED, "middle", 0.93, 0.5, 0.3, 4, 16, "confidence")
    assert child.outcome().confirmed_grade == "middle"


# --- квалификация --------------------------------------------------------------------------------


def test_qualification_happy_path_and_onboarding_extension():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    assert not q.can_start_test(clock).ok and q.can_start_test(clock).code == "survey_required"
    q.record_survey(clock)
    assert q.status == QualStatus.SURVEY_DONE
    assert q.start_test("s1", clock) == "initial" and q.status == QualStatus.TESTING
    # первичный тест сдан, предложен добор вверх
    q.apply(outcome(status="EXTENSION_OFFERED", pending_offers=True, trust=None), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "middle"
    assert q.next_change_allowed_at == T0 + timedelta(days=90)
    # добор вверх в течение 24 ч прошёл → Senior, сменой не считается
    clock.advance(hours=2)
    q.apply(outcome(status="FINAL", trust=1.0), clock)  # родитель закрылся
    q.apply(outcome(session_id="s1-up", kind="ext_up", target_grade="senior", confirmed_grade="senior"), clock)
    assert q.grade == "senior" and q.history[-1].counts_as_change is False
    assert q.next_change_allowed_at == T0 + timedelta(days=90)
    assert q.trust == 1.0


def test_upgrade_rules_90_and_30_days_never_forced_downgrade():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(confirmed_grade="junior", target_grade="junior"), clock)
    clock.advance(days=30)
    chk = q.can_start_upgrade(clock)
    assert not chk.ok and chk.code == "change_interval" and "через 60 дней" in chk.message_ru
    clock.advance(days=61)
    assert q.can_start_upgrade(clock).ok
    assert q.start_upgrade("u1", clock) == "middle" and q.status == QualStatus.UPGRADE_TESTING
    # неудача повышения грейд не снижает; следующая попытка через 30 дней
    q.apply(outcome(session_id="u1", kind="upgrade", decision="FAILED", target_grade="middle",
                    confirmed_grade=None, p_pass=0.05), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"
    clock.advance(days=10)
    assert q.can_start_upgrade(clock).code == "upgrade_cooldown"
    clock.advance(days=21)
    q.start_upgrade("u2", clock)
    # BORDERLINE на повышении не повышает
    q.apply(outcome(session_id="u2", kind="upgrade", decision=BORDERLINE_PASS, target_grade="middle",
                    confirmed_grade=None, p_pass=0.8), clock)
    assert q.grade == "junior"
    clock.advance(days=31)
    q.start_upgrade("u3", clock)
    q.apply(outcome(session_id="u3", kind="upgrade", target_grade="middle", confirmed_grade="middle"), clock)
    assert q.grade == "middle" and q.history[-1].counts_as_change
    assert q.next_change_allowed_at == clock.now() + timedelta(days=90)


def test_not_confirmed_retake_14_days_and_3_attempts_in_90():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    for attempt in range(3):
        assert q.start_test(f"s{attempt}", clock) in ("initial", "retake")
        # провал без добора (отказ) → NOT_CONFIRMED
        q.apply(outcome(session_id=f"s{attempt}", decision="FAILED", confirmed_grade=None, p_pass=0.05), clock)
        assert q.status == QualStatus.NOT_CONFIRMED
        clock.advance(days=13)
        chk = q.can_start_test(clock)
        if attempt < 2:
            assert not chk.ok and chk.code == "retake_cooldown" and "через 1 день" in chk.message_ru
            clock.advance(days=1)
            assert q.can_start_test(clock).ok
    clock.advance(days=1)
    chk = q.can_start_test(clock)
    assert not chk.ok and chk.code == "attempts_limit"
    with pytest.raises(SessionError):
        q.start_test("s4", clock)
    clock.advance(days=50)
    assert q.can_start_test(clock).ok


def test_failed_with_pending_down_offer_keeps_testing_until_resolved():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(status="EXTENSION_OFFERED", decision="FAILED", confirmed_grade=None, pending_offers=True,
                    trust=None), clock)
    assert q.status == QualStatus.TESTING
    q.apply(outcome(status="FINAL", decision="FAILED", confirmed_grade=None, extended=True), clock)
    assert q.status == QualStatus.TESTING  # дочерний добор ещё идёт
    q.apply(outcome(session_id="s1-ext1", kind="ext_down", target_grade="junior", confirmed_grade="junior"), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"


def test_stale_outcome_of_old_attempt_does_not_end_retake():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    old = outcome(session_id="s1", decision="FAILED", confirmed_grade=None, p_pass=0.05)
    q.apply(old, clock)
    clock.advance(days=14)
    q.start_test("s2", clock)
    q.apply(old, clock)  # API повторно применил итог старой попытки
    q.apply(outcome(session_id="s1", status="ABANDONED", decision=None, confirmed_grade=None), clock)
    assert q.status == QualStatus.TESTING and q.active_session_id == "s2"
    q.apply(outcome(session_id="s2", confirmed_grade="junior", target_grade="junior"), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"


def test_abandoned_counts_as_attempt():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(status="ABANDONED", decision=None, confirmed_grade=None), clock)
    assert q.status == QualStatus.NOT_CONFIRMED and len(q.attempts) == 1
    assert q.next_attempt_allowed_at == T0 + timedelta(days=14)


def test_voluntary_downgrade_counts_as_change():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(), clock)
    clock.advance(days=40)
    with pytest.raises(SessionError) as e:
        q.voluntary_downgrade(clock)
    assert e.value.code == "change_interval"
    clock.advance(days=50)
    assert q.voluntary_downgrade(clock) == "junior"
    assert q.history[-1].counts_as_change and q.next_change_allowed_at == clock.now() + timedelta(days=90)
    q2 = Qualification("acc", SPEC, status=QualStatus.CONFIRMED, grade="intern")
    with pytest.raises(SessionError):
        q2.voluntary_downgrade(clock)


def test_under_review_hides_and_resolution():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(status="UNDER_REVIEW", trust=None, under_review=True), clock)
    assert q.status == QualStatus.CONFIRMED and q.hidden
    assert q.handoff(clock)["visible"] is False
    q.resolve_review(True, clock)
    assert not q.hidden and q.trust == 0.75
    # аннулирование первичной сессии → грейда нет
    q2 = Qualification("acc", SPEC)
    q2.record_survey(clock)
    q2.start_test("s1", clock)
    q2.apply(outcome(status="UNDER_REVIEW", trust=None, under_review=True), clock)
    q2.resolve_review(False, clock)
    assert q2.status == QualStatus.NOT_CONFIRMED and q2.grade is None


def test_freshness_and_handoff():
    rel0 = freshness_of(T0, T0)
    assert rel0["relevance"] == 100 and rel0["band"] == "fresh"
    f120 = freshness_of(T0, T0 + timedelta(days=120))
    assert f120["relevance"] == 37 and f120["band"] == "aging"
    assert freshness_of(T0, T0 + timedelta(days=300))["band_ru"] == "устарел"

    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    subs = {"python_core": {"tag": "strong", "theta": 0.6}, "sql": {"tag": "insufficient", "theta": 0.3}}
    q.apply(outcome(theta=0.4, se=0.3, trust=0.75, subscores=subs), clock)
    h = q.handoff(clock)
    # position_in_grade = clip((θ̂ − 0.5·SE − cut(g)) / (cut(g+1) − cut(g)), 0, 1)
    assert h["position_in_grade"] == pytest.approx((0.4 - 0.15 - 0.0) / 1.0)
    assert h["evidence_weight"] == pytest.approx(0.8 * 0.75)
    assert h["module_tags"] == {"python_core": "strong"}
    assert h["category"] == {"code": "backend_python:middle", "name": "Backend (Python) · Middle"}
    clock.advance(days=365)
    assert q.se_now(clock) == pytest.approx((0.09 + 0.02 * 365 / 30.4375) ** 0.5, abs=1e-6)
    assert q.handoff(clock)["position_in_grade"] < h["position_in_grade"]  # устаревание → консервативнее


def test_regular_task_updates_theta_not_grade():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC, status=QualStatus.CONFIRMED, grade="middle", theta=0.85, se=0.3,
                      last_activity_at=T0)
    res = q.apply_regular_task(ItemParams(b=1.2, a=1.35), True, clock)
    assert q.grade == "middle" and res["theta"] > 0.85
    assert res["upgrade_hint"] == (res["p_next"] >= 0.7)
    for _ in range(6):  # серия успешных задач на уровне Senior → уведомление, но грейд прежний
        res = q.apply_regular_task(ItemParams(b=1.3, a=1.35), True, clock)
        if res["upgrade_hint"]:
            break
    assert res["upgrade_hint"] and res["hint_text_ru"] == "Вам доступен тест на повышение"
    assert res["p_next"] >= 0.7
    assert q.grade == "middle"


def test_category_and_fullstack():
    assert category_of("frontend_js", "junior")["name"] == "Frontend (JS/TS) · Junior"
    assert category_of("frontend_js", "junior") == category_of("frontend_js", "junior")
    assert fullstack_grade("senior", "middle") == "middle"
    assert fullstack_grade("junior", None) is None


def test_end_to_end_session_into_qualification():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    kind = q.start_test("s1", clock)
    rng = random.Random(8)
    s = TestSession.create(session_id="s1", account_id="acc", spec=SPEC, claimed_grade="middle", clock=clock,
                           rng=rng, kind=kind)
    s.start(clock)
    play(s, clock, rng, theta=0.9)
    q.apply(s.outcome(), clock)
    if s.status == SessionStatus.EXTENSION_OFFERED:
        s.decline_extension(clock)
    else:
        s.close(clock)
    q.apply(s.outcome(), clock)
    if s.decision.passed:
        assert q.status == QualStatus.CONFIRMED and q.grade == "middle" and q.trust == 1.0
        assert set(q.module_theta) >= {"python_core", "sql"}
    else:
        assert q.status in (QualStatus.NOT_CONFIRMED, QualStatus.TESTING)
    res = s.result()
    assert {"theta", "se", "score", "interval80", "class_probs", "by_level_text", "subscores"} <= set(res)
    # номер последнего задания — по всем показанным (pretest неотличимы от зачётных)
    assert str(len(s.cat.responses)) in res["stop_text"]
    assert "θ" not in res["stop_text"]


def _confirmed_chain(claimed, theta, direction, seeds=range(200), accept_after=None, flags_on_parent=()):
    """Реальная цепочка: основная часть → предложение добора direction → принятие. Возвращает всё нужное."""
    for seed in seeds:
        clock = FixedClock(T0)
        rng = random.Random(seed)
        q = Qualification("acc", SPEC)
        q.record_survey(clock)
        q.start_test("s1", clock)
        s = TestSession.create(session_id="s1", account_id="acc", spec=SPEC, claimed_grade=claimed, clock=clock,
                               rng=rng)
        s.start(clock)
        play(s, clock, rng, theta)
        offer = next((o for o in s.offers if o.direction == direction and not o.instant), None)
        if offer is None:
            continue
        q.apply(s.outcome(), clock)
        if accept_after is not None:
            clock.advance(**accept_after)
        s.add_flags(list(flags_on_parent))
        child = s.accept_extension(direction, clock, child_id="s1-ext")
        q.apply(s.outcome(), clock)  # родитель закрылся
        return q, s, child, clock, rng
    raise AssertionError("нет подходящего сценария")


def _finish(q, child, clock, rng, theta):
    play(child, clock, rng, theta)
    q.apply(child.outcome(), clock)
    if child.status == SessionStatus.EXTENSION_OFFERED:
        child.decline_extension(clock)
    elif child.status == SessionStatus.DECIDED:
        child.close(clock)
    q.apply(child.outcome(), clock)


def test_parent_foreign_key_carries_over_to_grade_from_down_extension():
    """«Чужой ключ» в проваленной основной части: грейд из добора (тот же апостериор) скрыт до решения."""
    hard = Flag(KIND_FOREIGN_KEY, HARD, "Ответ совпал с ключом варианта, выданного другому кандидату")
    q, s, child, clock, rng = _confirmed_chain("middle", -0.7, "down", flags_on_parent=[hard])
    assert s.status == SessionStatus.UNDER_REVIEW
    _finish(q, child, clock, rng, -0.7)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"  # грейд из добора вниз
    assert q.hidden and "s1" in q.reviews and q.handoff(clock)["visible"] is False
    # методист аннулировал родителя → вся цепочка (общий апостериор) аннулирована
    q.resolve_review(False, clock, session_id="s1")
    assert q.status == QualStatus.NOT_CONFIRMED and q.grade is None and not q.hidden


def test_chain_trust_is_min_over_parent_and_extension():
    q, s, child, clock, rng = _confirmed_chain("junior", 1.6, "up",
                                               flags_on_parent=[Flag("paste", SOFT, "Вставка текста")])
    assert q.trust == 0.75
    _finish(q, child, clock, rng, 1.6)
    assert child.trust == 1.0
    assert q.trust == 0.75  # чистый добор не «отмывает» флаг основной части


def test_extension_accepted_in_window_counts_even_if_finished_later():
    """Добор принят за 5 минут до конца окна 24 ч, закончен позже — итог не теряется (concept §5.3)."""
    for seed in range(0, 200, 7):
        q, s, child, clock, rng = _confirmed_chain("junior", 2.2, "up", seeds=range(seed, seed + 7),
                                                   accept_after={"hours": 23, "minutes": 55})
        play(child, clock, rng, 2.2)
        clock.advance(minutes=30)
        assert clock.now() > q.onboarding_until
        q.apply(child.outcome(), clock)
        if child.granted_grade() == "middle":
            assert q.grade == "middle" and q.history[-1].reason == "onboarding_extension"
            assert q.history[-1].counts_as_change is False
            return
    pytest.fail("нет успешного добора вверх")


def test_failed_upgrade_under_review_hides_but_invalidation_keeps_grade():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(confirmed_grade="junior", target_grade="junior"), clock)
    clock.advance(days=91)
    q.start_upgrade("u1", clock)
    q.apply(outcome(session_id="u1", kind="upgrade", status="DECIDED", decision="FAILED", target_grade="middle",
                    confirmed_grade=None, trust=None), clock)
    q.apply(outcome(session_id="u1", kind="upgrade", status="UNDER_REVIEW", decision="FAILED",
                    target_grade="middle", confirmed_grade=None, trust=None, under_review=True), clock)
    assert q.grade == "junior" and q.hidden and q.trust == 1.0
    assert not q.can_start_upgrade(clock).ok
    q.resolve_review(False, clock, session_id="u1")  # проваленное повышение грейд не меняло
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior" and not q.hidden


def test_upgrade_invalidated_rolls_back_to_previous_grade():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock)
    q.apply(outcome(confirmed_grade="junior", target_grade="junior", trust=0.75), clock)
    clock.advance(days=91)
    q.start_upgrade("u1", clock)
    q.apply(outcome(session_id="u1", kind="upgrade", target_grade="middle", confirmed_grade="middle",
                    status="UNDER_REVIEW", trust=None, under_review=True), clock)
    assert q.grade == "middle" and q.hidden
    q.resolve_review(False, clock, session_id="u1")
    assert q.grade == "junior" and q.status == QualStatus.CONFIRMED and q.trust == 0.75 and not q.hidden


def test_abandon_only_open_session_and_russian_errors():
    s, clock, rng = new_session(claimed="junior", seed=1)
    with pytest.raises(SessionError) as e:
        s.answer(1, True, clock)
    assert "ещё не начат" in e.value.message_ru and "CREATED" not in e.value.message_ru
    s.start(clock)
    play(s, clock, rng, theta=2.5)
    s.decline_extension(clock)
    with pytest.raises(SessionError):
        s.abandon(clock)  # закрытую сессию «обнулить» нельзя
    assert s.status == SessionStatus.FINAL


def test_handoff_with_theta_exactly_zero():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC, status=QualStatus.CONFIRMED, grade="middle", theta=0.0, se=0.2, trust=1.0,
                      last_activity_at=T0, p_pass=0.91)
    h = q.handoff(clock)
    # θ̂ = 0.0 — валидная оценка, а не «нет данных»: (0 − 0.1 − 0) / 1 → 0 после клипа
    assert h["position_in_grade"] == 0.0 and h["theta"] == 0.0
    q.theta = 0.6
    assert q.handoff(clock)["position_in_grade"] == pytest.approx(0.5)


# --- политика пересдач и повышения (ответы постановщиков, DECISIONS п. 29) --------------------------


def _failed_at(q, clock, grade, sid):
    q.apply(
        outcome(session_id=sid, decision="FAILED", target_grade=grade, confirmed_grade=None, p_pass=0.05,
                claimed_grade=grade),
        clock,
    )


def test_lower_grade_attempt_is_free_during_cooldown_and_does_not_burn_retake():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock, grade="senior")
    _failed_at(q, clock, "senior", "s1")
    assert q.status == QualStatus.NOT_CONFIRMED and q.last_failed_grade == "senior"
    until = q.next_attempt_allowed_at
    assert until == T0 + timedelta(days=14)
    # то же и более высокий грейд — ждать; грейд ниже — сразу
    assert q.can_start_test(clock, grade="senior").code == "retake_cooldown"
    assert q.can_start_test(clock).code == "retake_cooldown"
    assert q.can_start_test(clock, grade="middle").code == "lower_grade_free"
    assert q.lower_grades_open(clock) == ["intern", "junior", "middle"]
    q.start_test("s2", clock, grade="middle")
    assert len(q.attempts) == 1  # бесплатная попытка в лимит не засчитана
    _failed_at(q, clock, "middle", "s2")
    assert q.next_attempt_allowed_at == until  # ожидание не продлено
    assert q.last_failed_grade == "middle"
    assert q.can_start_test(clock, grade="senior").code == "retake_cooldown"
    q.start_test("s3", clock, grade="junior")
    q.apply(outcome(session_id="s3", target_grade="junior", confirmed_grade="junior", claimed_grade="junior"), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"
    assert len(q.attempts) == 1


def test_free_lower_attempt_abandoned_keeps_cooldown_and_limit_never_blocks_lower():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock, grade="middle")
    _failed_at(q, clock, "middle", "s1")
    until = q.next_attempt_allowed_at
    q.start_test("s2", clock, grade="junior")
    q.apply(outcome(session_id="s2", status="ABANDONED", decision=None, confirmed_grade=None), clock)
    assert q.status == QualStatus.NOT_CONFIRMED and q.next_attempt_allowed_at == until and len(q.attempts) == 1
    # самый низкий грейд: ниже нет, значит ждать как обычно
    q.start_test("s3", clock, grade="junior")
    _failed_at(q, clock, "junior", "s3")
    q.start_test("s4", clock, grade="intern")
    _failed_at(q, clock, "intern", "s4")
    assert q.can_start_test(clock, grade="intern").code == "retake_cooldown"


def test_dropped_level_is_retaken_after_retake_days_not_change_interval():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock, grade="middle")
    q.apply(outcome(status="EXTENSION_OFFERED", decision="FAILED", target_grade="middle", confirmed_grade=None,
                    pending_offers=True, trust=None, claimed_grade="middle"), clock)
    q.apply(outcome(session_id="s1-ext1", kind="ext_down", target_grade="junior", confirmed_grade="junior",
                    claimed_grade="middle"), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"  # дали тот грейд, который подтвердил тест
    assert q.next_change_allowed_at == T0 + timedelta(days=14)
    clock.advance(days=13)
    assert q.can_start_upgrade(clock).code == "change_interval"
    clock.advance(days=1)
    assert q.can_start_upgrade(clock).ok
    assert q.start_upgrade("u1", clock) == "middle"


def test_failed_voluntary_promotion_never_lowers_confirmed_grade_and_promotion_is_separate():
    clock = FixedClock(T0)
    q = Qualification("acc", SPEC)
    q.record_survey(clock)
    q.start_test("s1", clock, grade="junior")
    q.apply(outcome(target_grade="junior", confirmed_grade="junior", claimed_grade="junior"), clock)
    # повышение — только отдельной сессией и только после интервала смены грейда
    assert q.can_start_test(clock, grade="middle").code == "already_confirmed"
    assert q.can_start_upgrade(clock).code == "change_interval"
    clock.advance(days=90)
    q.start_upgrade("u1", clock)
    q.apply(outcome(session_id="u1", kind="upgrade", decision="FAILED", target_grade="middle",
                    confirmed_grade=None, p_pass=0.05), clock)
    assert q.status == QualStatus.CONFIRMED and q.grade == "junior"
    assert q.last_failed_grade is None  # на подтверждённый грейд неудачное повышение не влияет
    assert q.can_start_upgrade(clock).code == "upgrade_cooldown"
