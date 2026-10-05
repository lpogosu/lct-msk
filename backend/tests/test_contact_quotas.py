"""Квоты, лимит кандидата, пауза пары, «только после просмотра», вилка (концепция §4.3, DECISIONS 6–8)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contact.quotas import (
    CandidateState,
    Cooldown,
    SenderState,
    SentRecord,
    check_batch,
    check_invite,
    cooldown_after,
    is_personal_email,
    quota_owner_key,
    quota_usage,
)
from app.contact.reliability import ReliabilityEvent, compute_reliability
from app.contact.rules import validate_salary_range

NOW = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)  # понедельник, 12:00 МСК


def rec(
    i: int,
    *,
    ago: timedelta = timedelta(days=1),
    status: str = "sent",
    viewed: bool = False,
    need_id: str | None = "need-1",
    cand: str | None = None,
) -> SentRecord:
    return SentRecord(
        invitation_id=f"inv-{i}",
        company_id="co-1",
        candidate_id=cand or f"other-{i}",
        sent_at=NOW - ago,
        status=status,
        viewed=viewed,
        need_id=need_id,
    )


def sender(sends=(), reliability=None, email="hr@severny-kod.ru") -> SenderState:
    return SenderState(
        company_id="co-1", email=email, account_id="acc-1", sends=tuple(sends), reliability=reliability
    )


def cand(**kw) -> CandidateState:
    kw.setdefault("card_opened", True)
    kw.setdefault("candidate_id", "cand-1")
    kw.setdefault("pseudonym", "К-4821")
    return CandidateState(**kw)


def check(s=None, c=None, **kw):
    params = dict(need_id="need-1", salary_from=220_000, salary_to=280_000, now=NOW)
    params.update(kw)
    return check_invite(s or sender(), c or cand(), **params)


def test_clean_invite_passes():
    result = check()
    assert result.ok and not result.warnings


# --- владелец квоты (DECISIONS 8) ---------------------------------------------------------------------


def test_quota_owner_personal_vs_corporate_domain():
    assert quota_owner_key("hr@Severny-Kod.ru", "acc-1") == "domain:severny-kod.ru"
    assert quota_owner_key("boss@gmail.com", "acc-1") == "account:acc-1"
    assert quota_owner_key("boss@gmail.com", "acc-2") == "account:acc-2"  # gmail не делит одну квоту
    for domain in (
        "mail.ru",
        "yandex.ru",
        "bk.ru",
        "list.ru",
        "inbox.ru",
        "icloud.com",
        "outlook.com",
        "hotmail.com",
    ):
        assert is_personal_email(f"x@{domain}")
    assert not is_personal_email("x@severny-kod.ru")


# --- квота 60 за скользящие 7 дней --------------------------------------------------------------------


def test_owner_quota_exhausted_with_relative_next_slot():
    sends = [rec(i, ago=timedelta(days=2), need_id=None) for i in range(60)]
    sends[0] = rec(0, ago=timedelta(days=5), need_id=None)
    usage = quota_usage(sender(sends), NOW)
    assert (usage.used, usage.limit, usage.label) == (60, 60, "осталось 0/60")
    result = check(sender(sends))
    assert result.error_codes == ["owner_quota"]
    msg = result.errors[0].message
    assert "60 из 60" in msg and "через 2 дня" in msg


def test_expired_unviewed_returns_slot_but_viewed_does_not():
    sends = [rec(i, need_id=None) for i in range(59)]
    assert check(sender([*sends, rec(99, status="expired", viewed=False, need_id=None)])).ok
    assert check(sender([*sends, rec(99, status="expired", viewed=True, need_id=None)])).error_codes == [
        "owner_quota"
    ]
    # отозванное работодателем слот не возвращает
    assert check(sender([*sends, rec(99, status="withdrawn", need_id=None)])).error_codes == ["owner_quota"]


def test_rolling_window_drops_old_sends():
    old = [rec(i, ago=timedelta(days=7, minutes=1), need_id=None) for i in range(60)]
    assert check(sender(old)).ok
    usage = quota_usage(sender(old), NOW)
    assert usage.used == 0 and usage.remaining == 60


def test_low_reliability_halves_quota():
    bad = [
        ReliabilityEvent("co-1", "contact_overdue", NOW - timedelta(days=i + 1), candidate_id=f"c{i}")
        for i in range(10)
    ]
    rel = compute_reliability(bad, NOW)
    assert rel.quota_factor == 0.5
    sends = [rec(i, need_id=None) for i in range(30)]
    result = check(sender(sends, reliability=rel))
    assert "owner_quota" in result.error_codes
    assert "quota_reduced" in result.warning_codes
    assert "низкая надёжность" in result.warnings[0].message
    assert quota_usage(sender(sends[:29], reliability=rel), NOW).label == "осталось 1/30"


def test_few_events_do_not_halve_quota_even_if_r_is_low():
    bad = [
        ReliabilityEvent("co-1", "contact_overdue", NOW - timedelta(days=1), candidate_id=f"c{i}")
        for i in range(4)
    ]
    rel = compute_reliability(bad, NOW)
    assert rel.r < 0.6 and rel.quota_factor == 1.0


def test_discrepancy_badge_halves_quota():
    events = [
        ReliabilityEvent("co-1", "terms_lower_salary", NOW - timedelta(days=10), candidate_id="a"),
        ReliabilityEvent("co-1", "terms_no_vacancy", NOW - timedelta(days=20), candidate_id="b"),
    ]
    usage = quota_usage(sender(reliability=compute_reliability(events, NOW)), NOW)
    assert usage.limit == 30 and usage.reduced_reason == "есть расхождения условий"


# --- ≤ 20 активных на бриф ----------------------------------------------------------------------------


def test_active_per_need_limit():
    pending = [rec(i, status=s) for i, s in enumerate(["sent", "viewed", "countered", "sent"] * 5)]
    result = check(sender(pending))
    assert result.error_codes == ["need_active_limit"]
    assert "20 активных приглашений из 20" in result.errors[0].message
    assert check(sender(pending), need_id="need-2").ok
    # принятые и закрытые слот брифа не занимают
    mixed = [rec(i) for i in range(19)] + [rec(100 + i, status="accepted") for i in range(5)]
    assert check(sender(mixed)).ok
    # приглашение из банка без брифа лимит брифа не проверяет
    assert check(sender(pending), need_id=None).ok


# --- пачка ≤ 10 ---------------------------------------------------------------------------------------


def test_batch_size_and_progressive_quota():
    many = [cand(candidate_id=f"c{i}", pseudonym=f"К-{1000 + i}") for i in range(11)]
    result = check_batch(sender(), many, need_id="need-1", salary_from=220_000, salary_to=280_000, now=NOW)
    assert result.errors[0].code == "batch_too_large" and result.allowed == []

    sends = [rec(i, need_id=None) for i in range(58)]  # осталось 2 слота
    three = many[:3]
    result = check_batch(
        sender(sends), three, need_id="need-1", salary_from=220_000, salary_to=280_000, now=NOW
    )
    assert result.allowed == ["c0", "c1"]
    assert result.per_candidate["c2"].error_codes == ["owner_quota"]


def test_batch_ignores_duplicates():
    one = cand(candidate_id="c1")
    result = check_batch(
        sender(), [one, one], need_id="need-1", salary_from=220_000, salary_to=280_000, now=NOW
    )
    assert result.allowed == ["c1"] and len(result.per_candidate) == 1


# --- лимит кандидата 3/5/10/∞ в неделю ----------------------------------------------------------------


def test_candidate_weekly_limit_default_5_with_relative_reset():
    this_week = tuple(NOW - timedelta(hours=h) for h in (1, 2, 3, 4, 5))  # с понедельника 00:00 МСК
    result = check(c=cand(received_at=this_week))
    assert result.error_codes == ["candidate_weekly_limit"]
    msg = result.errors[0].message
    assert msg.startswith("У К-4821") and "лимит" in msg and "через 7 дней" in msg


def test_candidate_weekly_limit_counts_only_current_iso_week():
    last_week = tuple(NOW - timedelta(days=1, hours=h) for h in range(10))  # воскресенье и раньше
    assert check(c=cand(received_at=last_week)).ok


@pytest.mark.parametrize(
    "cap,received,ok", [(3, 2, True), (3, 3, False), (10, 9, True), (10, 10, False), (None, 50, True)]
)
def test_candidate_weekly_cap_options(cap, received, ok):
    times = tuple(NOW - timedelta(minutes=m + 1) for m in range(received))
    assert check(c=cand(weekly_cap=cap, received_at=times)).ok is ok


# --- пара компания—кандидат --------------------------------------------------------------------------


def test_one_active_invitation_per_pair():
    result = check(c=cand(pair_statuses=("declined", "viewed")))
    assert result.error_codes == ["pair_active"]
    assert "«Просмотрено»" in result.errors[0].message
    assert check(c=cand(pair_statuses=("accepted",))).error_codes == ["pair_contact_open"]
    assert check(c=cand(pair_statuses=("declined", "expired", "withdrawn"))).ok


def test_pause_after_decline_30_days():
    cd = cooldown_after(
        company_id="co-1",
        candidate_id="cand-1",
        status="declined",
        at=NOW - timedelta(days=10),
        decline_reason="stack",
        last_salary_to=280_000,
    )
    assert cd.until == NOW + timedelta(days=20) and cd.salary_unlock_from is None
    result = check(c=cand(cooldowns=(cd,)))
    assert result.error_codes == ["pair_pause"]
    assert "через 20 дней" in result.errors[0].message
    # другая компания паузой не связана
    other = Cooldown("co-2", "cand-1", NOW + timedelta(days=20), "declined")
    assert check(c=cand(cooldowns=(other,))).ok
    # пауза закончилась
    assert check(
        c=cand(
            cooldowns=(
                cooldown_after(
                    company_id="co-1", candidate_id="cand-1", status="declined", at=NOW - timedelta(days=30)
                ),
            )
        )
    ).ok


def test_salary_decline_pause_lifted_by_115_percent_from():
    cd = cooldown_after(
        company_id="co-1",
        candidate_id="cand-1",
        status="declined",
        at=NOW - timedelta(days=3),
        decline_reason="salary",
        last_salary_to=280_000,
    )
    assert cd.salary_unlock_from == 322_000
    blocked = check(c=cand(cooldowns=(cd,)), salary_from=321_000, salary_to=380_000)
    assert blocked.error_codes == ["pair_pause"]
    assert "от 322 000 ₽" in blocked.errors[0].message
    lifted = check(c=cand(cooldowns=(cd,)), salary_from=322_000, salary_to=380_000)
    assert lifted.ok and lifted.notes[0].code == "pause_lifted_by_salary"


def test_pause_after_expired_14_days_and_none_after_withdrawn():
    cd = cooldown_after(
        company_id="co-1", candidate_id="cand-1", status="expired", at=NOW - timedelta(days=5)
    )
    assert cd.until == NOW + timedelta(days=9)
    result = check(c=cand(cooldowns=(cd,)))
    assert "истёкшего" in result.errors[0].message and "через 9 дней" in result.errors[0].message
    assert cooldown_after(company_id="co-1", candidate_id="cand-1", status="withdrawn", at=NOW) is None


# --- просмотр карточки, доступность, соответствие -------------------------------------------------------


def test_invite_only_after_card_opened_or_shortlisted():
    result = check(c=cand(card_opened=False))
    assert result.error_codes == ["card_not_opened"]
    assert "откройте карточку К-4821" in result.errors[0].message
    assert check(c=cand(card_opened=False, shortlisted=True)).ok


@pytest.mark.parametrize(
    "kw",
    [
        {"search_mode": "hidden"},
        {"blocked_company_ids": frozenset({"co-1"})},
        {"birth_year": 2012},  # младше 16 — не виден вовсе
        {"birth_year": 2009},  # 16–17 — только стажировки
    ],
)
def test_unavailable_candidate_reason_is_not_detailed(kw):
    result = check(c=cand(**kw))
    assert result.error_codes == ["candidate_unavailable"]
    assert result.errors[0].message == "К-4821 сейчас не принимает приглашения"


def test_minor_16_17_only_for_internship():
    assert check(c=cand(birth_year=2009), is_internship=True).ok
    assert check(c=cand(birth_year=2012), is_internship=True).error_codes == ["candidate_unavailable"]
    assert check(c=cand(birth_year=2007)).ok


def test_weak_match_warns_but_does_not_block():
    result = check(c=cand(score=39.4))
    assert result.ok and result.warning_codes == ["weak_match"]
    assert "Слабое соответствие брифу: 39 из 100" in result.warnings[0].message
    assert "Score" not in result.warnings[0].message  # текст интерфейса — по-русски
    # 39,6 не показываем как «40 из 100» при пороге «ниже 40»
    assert "39 из 100" in check(c=cand(score=39.6)).warnings[0].message
    assert check(c=cand(score=40.0)).warnings == []


# --- вилка (DECISIONS 6) ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "lo,hi,errors,warnings",
    [
        (100_000, 160_000, [], []),  # ровно 1.6× — норма
        (100_000, 170_000, [], ["salary_wide"]),
        (100_000, 200_000, [], ["salary_wide"]),  # ровно 2.0× — ещё предупреждение
        (100_000, 201_000, ["salary_too_wide"], []),
        (50_000, 500_000, ["salary_too_wide"], []),  # «50–500 тыс.» запрещено
        (29_000, 40_000, ["salary_from_min"], []),
        (100_500, 150_000, ["salary_step"], []),
        (200_000, 150_000, ["salary_order"], []),
    ],
)
def test_salary_range_rules(lo, hi, errors, warnings):
    result = validate_salary_range(lo, hi, "net")
    assert result.error_codes == errors
    assert result.warning_codes == warnings


def test_salary_messages_are_russian():
    wide = validate_salary_range(100_000, 180_000)
    assert "1,80 раза" in wide.warnings[0].message
    block = validate_salary_range(100_000, 250_000)
    assert "100 000–250 000 ₽" in block.errors[0].message and "2,0×" in block.errors[0].message
    assert validate_salary_range(100_000, 150_000, "cash").error_codes == ["salary_type"]


def test_invite_check_includes_salary_rules():
    result = check(salary_from=100_000, salary_to=190_000)
    assert result.ok and result.warning_codes == ["salary_wide"]
    assert check(salary_from=100_000, salary_to=250_000).error_codes == ["salary_too_wide"]
