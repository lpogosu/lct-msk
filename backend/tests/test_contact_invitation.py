"""Машина состояний приглашения: сквозной путь, «Да, если…», сроки через FakeClock, таблица переходов."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime, timedelta

import pytest

from app.contact.clock import FakeClock
from app.contact.common import ContactError
from app.contact.invitation import (
    TRANSITIONS,
    Invitation,
    InvitationDraft,
    apply_action,
    available_actions,
    decline_hint,
    due_reminders,
    refresh,
    send_batch,
    send_invitation,
    status_label,
    withdraw_for_closed_need,
)
from app.contact.quotas import CandidateState, SenderState
from app.contact.statuses import Actor, InvitationAction, InvitationStatus
from app.contact.terms import CounterOffer

# Понедельник 05.10.2026, 12:00 по Москве
T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)
DESCRIPTION = (
    "Команда платёжного шлюза ищет бэкенд-разработчика: Python, FastAPI, PostgreSQL. "
    "Гибрид, Москва. Код-ревью, дежурства раз в месяц."
)


def sender(email: str = "hr@severny-kod.ru", sends=()) -> SenderState:
    return SenderState(company_id="co-1", email=email, account_id="acc-1", sends=tuple(sends))


def candidate(cid: str = "cand-1", pseudonym: str = "К-4821", **kw) -> CandidateState:
    kw.setdefault("card_opened", True)
    return CandidateState(candidate_id=cid, pseudonym=pseudonym, **kw)


def draft(inv_id: str = "inv-1", cid: str = "cand-1", **kw) -> InvitationDraft:
    base = dict(
        invitation_id=inv_id,
        company_id="co-1",
        candidate_id=cid,
        title="Backend Middle в платёжный шлюз",
        description=DESCRIPTION,
        salary_from=220_000,
        salary_to=280_000,
        contact_method="telegram",
        contact_value="@severny_hr",
        format="hybrid",
        need_id="need-1",
        why_you=("Тест: верхние 10% категории", "FastAPI подтверждён модулем http_api", "Свежая задача 4/5"),
    )
    base.update(kw)
    return InvitationDraft(**base)


def sent_invitation(clock: FakeClock, **kw) -> Invitation:
    inv, _ = send_invitation(draft(**kw), sender(), candidate(), clock)
    return inv


def viewed_invitation(clock: FakeClock, **kw) -> Invitation:
    inv = sent_invitation(clock, **kw)
    clock.advance(hours=1)
    apply_action(inv, "view", "candidate", clock)
    return inv


def reliability_kinds(outcome) -> list[str]:
    return [e.data["event"].kind for e in outcome.effects_of("reliability")]


# --- сквозной путь ------------------------------------------------------------------------------------


def test_full_happy_path_send_view_accept_contact_close_check():
    clock = FakeClock(T0)
    inv, out = send_invitation(draft(), sender(), candidate(), clock)
    assert inv.status == InvitationStatus.SENT
    assert inv.status_label() == "Отправлено"
    assert inv.respond_by == T0 + timedelta(days=5)  # срок по умолчанию — 5 дней
    assert inv.pseudonym == "К-4821"
    assert inv.terms[0].ver == 1 and inv.terms[0].author == "employer"
    assert len(inv.terms[0].sha256) == 64 and inv.terms[0].verify()
    assert out.events[0].action == "send" and out.events[0].from_status is None
    assert out.effects_of("quota_slot_used")
    assert inv.contacts_open(clock.now()) is False

    clock.advance(hours=2)
    apply_action(inv, InvitationAction.VIEW, Actor.CANDIDATE, clock)
    assert inv.status == InvitationStatus.VIEWED and inv.viewed_at == clock.now()

    clock.advance(hours=1)  # понедельник 15:00 МСК
    out = apply_action(inv, "accept", "candidate", clock, actor_id="user-cand", consent=True)
    assert inv.status == InvitationStatus.ACCEPTED
    assert inv.status_label() == "Принято — контакт открыт"
    assert inv.contacts_open(clock.now())
    assert inv.disclosure.company_id == "co-1" and inv.disclosure.basis == "invitation"
    assert "email" in inv.disclosure.fields
    # 3 рабочих дня: понедельник → четверг, то же время
    assert inv.contact_due_at == clock.now() + timedelta(days=3)
    assert out.effects_of("disclosure_granted")
    notified = {e.data["to"] for e in out.effects_of("notify")}
    assert notified == {"employer", "candidate"}

    clock.advance(days=1)
    out = apply_action(inv, "mark_contacted", "employer", clock)
    assert inv.status == InvitationStatus.ACCEPTED  # петля, статус не меняется
    assert reliability_kinds(out) == ["contacted_in_time"]

    clock.advance(days=2)
    apply_action(inv, "mark_interview", "employer", clock)
    assert inv.stage == "interview"
    out = apply_action(inv, "close", "employer", clock, outcome="hired")
    assert inv.status == InvitationStatus.CLOSED and inv.status_label() == "Завершено"
    assert reliability_kinds(out) == ["outcome_reported"]

    clock.advance(days=8)  # > 10 дней после принятия
    out = apply_action(inv, "outcome_check", "candidate", clock, answer="match")
    assert reliability_kinds(out) == ["terms_matched"]
    assert inv.status == InvitationStatus.CLOSED

    actions = [e.action for e in inv.events]
    assert actions == ["send", "view", "accept", "mark_contacted", "mark_interview", "close", "outcome_check"]
    assert [e.id for e in inv.events][:2] == ["inv-1-001", "inv-1-002"]
    assert [e.seq for e in inv.events] == list(range(1, 8))
    assert inv.events[2].payload["consent_text_ver"] == "contact-consent-v1"
    assert inv.events[2].actor_id == "user-cand"


def test_accept_requires_separate_consent():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "accept", "candidate", clock)
    assert err.value.code == "consent_given"
    assert "согласие" in err.value.message
    assert inv.status == InvitationStatus.VIEWED and inv.disclosure is None


def test_candidate_must_open_before_answering():
    clock = FakeClock(T0)
    inv = sent_invitation(clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "accept", "candidate", clock, consent=True)
    assert err.value.code == "transition_not_allowed"
    assert "«Отправлено»" in err.value.message


def test_disclosure_fields_follow_candidate_choice():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True, fields=["full_name", "telegram", "gender"])
    assert inv.disclosure.fields == ("full_name", "telegram")  # пол не раскрывается никогда


# --- «Да, если…» --------------------------------------------------------------------------------------


def test_counter_offer_accepted_creates_terms_v2_and_auto_discloses():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    out = apply_action(
        inv,
        "counter",
        "candidate",
        clock,
        counter=CounterOffer(salary_from=270_000, format="remote", comment="Готов выйти через 2 недели"),
    )
    assert inv.status == InvitationStatus.COUNTERED and inv.status_label() == "Встречное условие"
    ev = out.events[-1]
    assert ev.payload["within_range"] is True
    assert ev.payload["conditions"] == {"salary_from": 270_000, "format": "remote"}
    assert "270 000 ₽" in ev.payload["summary"]
    assert "220 000–280 000 ₽" in ev.payload["summary"]
    assert "удалённо" in ev.payload["summary"] and "в пределах вашей вилки" in ev.payload["summary"]
    assert inv.counter_respond_by == clock.now() + timedelta(days=3)
    assert out.effects_of("notify")[0].data["to"] == "employer"
    assert inv.contacts_open(clock.now()) is False

    clock.advance(days=1)
    out = apply_action(inv, "accept_counter", "employer", clock)
    assert inv.status == InvitationStatus.ACCEPTED
    v1, v2 = inv.terms
    assert (v2.ver, v2.author) == (2, "candidate")
    assert (v2.salary_from, v2.salary_to, v2.format) == (270_000, 280_000, "remote")
    assert v1.salary_from == 220_000 and v1.verify() and v2.verify()  # v1 не изменился
    assert v1.sha256 != v2.sha256
    assert inv.contacts_open(clock.now())  # галочка «раскрыть, если примут» стоит по умолчанию
    assert out.effects_of("new_terms")[0].data["terms"] is v2


def test_counter_above_range_raises_to_as_well():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    out = apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(salary_from=360_000))
    assert out.events[-1].payload["within_range"] is False
    assert "в пределах вашей вилки" not in out.events[-1].payload["summary"]
    apply_action(inv, "accept_counter", "employer", clock)
    assert (inv.current_terms.salary_from, inv.current_terms.salary_to) == (360_000, 360_000)


@pytest.mark.parametrize(
    "counter,code",
    [
        (CounterOffer(salary_from=365_000), "counter_salary_above_cap"),  # 1.3 × 280 000 = 364 000
        (CounterOffer(salary_from=215_000), "counter_salary_below_range"),
        (CounterOffer(salary_from=272_000), "counter_salary_step"),  # шаг 5000
        (CounterOffer(), "counter_empty"),
        (
            CounterOffer(salary_from=270_000, format="remote", start_date=date(2026, 11, 1)),
            "counter_too_many",
        ),
        (CounterOffer(format="hybrid"), "counter_format_same"),
        (CounterOffer(start_date=date(2026, 9, 1)), "counter_start_date_past"),
        (CounterOffer(format="remote", comment="ж" * 301), "counter_comment_too_long"),
    ],
)
def test_counter_offer_validation(counter, code):
    from app.contact.terms import validate_counter

    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    check = validate_counter(counter, inv.current_terms, date(2026, 10, 5))
    assert code in check.error_codes
    with pytest.raises(ContactError) as err:
        apply_action(inv, "counter", "candidate", clock, counter=counter)
    assert err.value.code == "counter_valid"
    assert inv.status == InvitationStatus.VIEWED


def test_counter_only_once():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(format="remote"))
    # повторного «Да, если…» в таблице нет вовсе …
    with pytest.raises(ContactError) as err:
        apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(format="office"))
    assert err.value.code == "transition_not_allowed"
    # … и guard «1 раунд» держит даже при ручной порче статуса
    inv.status = InvitationStatus.VIEWED
    with pytest.raises(ContactError) as err:
        apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(format="office"))
    assert err.value.code == "counter_round_available"


def test_counter_declined_sets_salary_pause():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(salary_from=300_000))
    with pytest.raises(ContactError) as err:
        apply_action(inv, "decline_counter", "employer", clock)
    assert err.value.code == "counter_decline_reason_valid"
    out = apply_action(inv, "decline_counter", "employer", clock, reason="salary")
    assert inv.status == InvitationStatus.COUNTER_DECLINED
    assert inv.status_label() == "Не сошлись по условиям"
    cd = out.effects_of("cooldown")[0].data["cooldown"]
    assert cd.until == clock.now() + timedelta(days=30)
    assert cd.salary_unlock_from == 322_000  # ⌈1.15 × 280 000⌉
    assert out.effects_of("notify")[0].data["to"] == "candidate"
    assert inv.contacts_open(clock.now()) is False


def test_counter_without_auto_disclose_waits_for_consent():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(
        inv, "counter", "candidate", clock, counter=CounterOffer(format="remote", auto_disclose=False)
    )
    out = apply_action(inv, "accept_counter", "employer", clock)
    assert inv.status == InvitationStatus.ACCEPTED and inv.disclosure is None
    assert out.notes and "подтвердит согласие" in out.notes[0]
    with pytest.raises(ContactError):
        apply_action(inv, "mark_contacted", "employer", clock)
    clock.advance(hours=3)
    apply_action(inv, "grant_consent", "candidate", clock, consent=True)
    assert inv.contacts_open(clock.now())
    assert inv.contact_due_at is not None
    with pytest.raises(ContactError) as err:
        apply_action(inv, "grant_consent", "candidate", clock, consent=True)
    assert err.value.code == "disclosure_missing"


# --- сроки через FakeClock ----------------------------------------------------------------------------


def test_unviewed_expiry_returns_quota_slot_and_pauses_14_days():
    clock = FakeClock(T0)
    inv = sent_invitation(clock)
    clock.advance(days=4, hours=23)
    assert refresh(inv, clock).events == []
    clock.advance(hours=2)
    out = refresh(inv, clock)
    assert inv.status == InvitationStatus.EXPIRED and inv.status_label() == "Истекло"
    ev = out.events[0]
    assert ev.at == inv.respond_by  # событие датировано дедлайном, а не моментом чтения
    assert ev.actor_role == "system" and ev.payload["quota_slot_returned"] is True
    assert out.effects_of("quota_slot_returned")
    cd = out.effects_of("cooldown")[0].data["cooldown"]
    assert cd.reason == "expired" and cd.until == inv.respond_by + timedelta(days=14)
    assert refresh(inv, clock).events == []  # идемпотентно
    assert inv.to_sent_record().viewed is False


def test_viewed_expiry_does_not_return_slot():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    clock.advance(days=6)
    out = refresh(inv, clock)
    assert inv.status == InvitationStatus.EXPIRED
    assert not out.effects_of("quota_slot_returned")


@pytest.mark.parametrize("days", [3, 7])
def test_response_window_options(days):
    clock = FakeClock(T0)
    inv = sent_invitation(clock, response_days=days)
    assert inv.respond_by == T0 + timedelta(days=days)


def test_response_window_must_be_3_5_or_7():
    clock = FakeClock(T0)
    with pytest.raises(ContactError) as err:
        sent_invitation(clock, response_days=4)
    assert "response_days" in err.value.check.error_codes
    assert "3, 5 или 7 дней" in err.value.message


def test_action_after_deadline_fails_but_reports_lazy_expiry():
    clock = FakeClock(T0)
    inv = sent_invitation(clock)
    clock.advance(days=5)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "view", "candidate", clock)
    assert "«Истекло»" in err.value.message
    assert [e.to_status for e in err.value.prior.events] == ["expired"]


def test_counter_unanswered_3_days_expires_with_reliability_penalty():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(format="remote"))
    clock.advance(days=3)
    out = refresh(inv, clock)
    assert inv.status == InvitationStatus.EXPIRED
    assert reliability_kinds(out) == ["counter_unanswered"]
    assert out.effects_of("cooldown")[0].data["cooldown"].reason == "expired"


def test_contact_overdue_is_flag_not_status_and_counted_once():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    due = inv.contact_due_at
    clock.set(due + timedelta(minutes=1))
    out = refresh(inv, clock)
    assert inv.status == InvitationStatus.ACCEPTED and inv.contact_overdue
    assert reliability_kinds(out) == ["contact_overdue"]
    assert out.events[0].at == due
    # «со мной не связались» после просрочки — второй bad не пишется
    out = apply_action(inv, "dispute_contact", "candidate", clock)
    assert inv.disputed and reliability_kinds(out) == []
    # поздняя отметка «связались» не даёт good
    out = apply_action(inv, "mark_contacted", "employer", clock)
    assert reliability_kinds(out) == [] and out.events[-1].payload["in_time"] is False


def test_dispute_too_early_explains_with_relative_date():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "dispute_contact", "candidate", clock)
    assert "через 3 дня" in err.value.message
    apply_action(inv, "mark_contacted", "employer", clock)
    out = apply_action(inv, "dispute_contact", "candidate", clock, comment="Никто не написал")
    assert reliability_kinds(out) == ["contact_disputed"]


def test_contact_due_skips_weekend():
    clock = FakeClock(datetime(2026, 10, 9, 12, 0, tzinfo=UTC))  # пятница, 15:00 МСК
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    assert inv.contact_due_at == datetime(2026, 10, 14, 13, 0, tzinfo=UTC)  # среда, то же время


# --- отказ, отзыв, итог -------------------------------------------------------------------------------


def test_decline_requires_reason_and_salary_suggests_counter():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "decline", "candidate", clock, reason="boring")
    assert "зарплата" in err.value.message and "стек" in err.value.message
    assert "Да, если" in decline_hint(inv, "salary")
    assert decline_hint(inv, "stack") is None
    out = apply_action(inv, "decline", "candidate", clock, reason="salary")
    assert inv.status == InvitationStatus.DECLINED and inv.status_label() == "Отклонено"
    cd = out.effects_of("cooldown")[0].data["cooldown"]
    assert cd.reason == "declined" and cd.salary_unlock_from == 322_000
    assert out.effects_of("feedback")[0].data["reason_code"] == "salary"
    assert "зарплата" in out.effects_of("notify")[0].data["text"]


def test_decline_other_reason_has_no_salary_exception():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    out = apply_action(inv, "decline", "candidate", clock, reason="stack")
    cd = out.effects_of("cooldown")[0].data["cooldown"]
    assert cd.salary_unlock_from is None and cd.until == clock.now() + timedelta(days=30)


def test_withdraw_requires_reason_and_brief_close_withdraws_pending():
    clock = FakeClock(T0)
    a = sent_invitation(clock, inv_id="inv-a")
    with pytest.raises(ContactError):
        apply_action(a, "withdraw", "employer", clock)
    b = viewed_invitation(clock, inv_id="inv-b")
    c = viewed_invitation(clock, inv_id="inv-c")
    apply_action(c, "accept", "candidate", clock, consent=True)
    out = withdraw_for_closed_need([a, b, c], clock)
    assert (a.status, b.status, c.status) == ("withdrawn", "withdrawn", "accepted")
    assert a.withdraw_reason == "position_closed" and a.status_label() == "Отозвано"
    assert any("Позиция закрыта".lower() in e.data["text"] for e in out.effects_of("notify"))


def test_close_requires_reason_for_not_matched():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "close", "employer", clock, outcome="not_matched")
    assert "Без причины закрыть нельзя" in err.value.message
    with pytest.raises(ContactError) as err:
        apply_action(inv, "close", "employer", clock, outcome="not_matched", reason="skills_missing")
    assert "навыков" in err.value.message
    apply_action(inv, "mark_contacted", "employer", clock)
    out = apply_action(
        inv, "close", "employer", clock, outcome="not_matched", reason="skills_missing", skill_ids=["docker"]
    )
    fb = out.effects_of("feedback")[0].data
    assert fb["direction"] == "employer_to_candidate" and fb["skill_ids"] == ["docker"]
    assert reliability_kinds(out) == ["outcome_reported"]


def test_close_position_closed_without_contact_counts_as_withdrawal_after_accept():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    out = apply_action(inv, "close", "employer", clock, outcome="not_matched", reason="position_closed")
    assert reliability_kinds(out) == ["withdrawn_after_accept"]


def test_consent_revoked_hides_contacts_again():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    clock.advance(days=1)
    out = apply_action(inv, "revoke_consent", "candidate", clock)
    assert inv.status == InvitationStatus.CONSENT_REVOKED and inv.status_label() == "Согласие отозвано"
    assert inv.contacts_open(clock.now()) is False
    assert inv.disclosure.revoked_at == clock.now()
    assert out.effects_of("disclosure_revoked")
    clock.advance(days=30)
    assert refresh(inv, clock).events == []  # из финального статуса сроки ничего не меняют


def test_outcome_check_timing_and_negative_answers():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    apply_action(inv, "mark_contacted", "employer", clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "outcome_check", "candidate", clock, answer="lower_salary")
    assert "10 дней" in err.value.message
    clock.advance(days=10)
    out = apply_action(inv, "outcome_check", "candidate", clock, answer="lower_salary")
    assert reliability_kinds(out) == ["terms_lower_salary"]
    with pytest.raises(ContactError):
        apply_action(inv, "outcome_check", "candidate", clock, answer="match")


# --- таблица переходов --------------------------------------------------------------------------------

DIAGRAM_EDGES = {
    ("sent", "viewed"),
    ("sent", "expired"),
    ("viewed", "expired"),
    ("sent", "withdrawn"),
    ("viewed", "withdrawn"),
    ("viewed", "accepted"),
    ("viewed", "declined"),
    ("viewed", "countered"),
    ("countered", "accepted"),
    ("countered", "counter_declined"),
    ("countered", "expired"),
    ("countered", "withdrawn"),
    ("accepted", "closed"),
    ("accepted", "consent_revoked"),
}


def test_transition_table_matches_concept_diagram_exactly():
    edges = {(str(s), str(rule.to)) for (s, _a, _r), rule in TRANSITIONS.items() if s != rule.to}
    assert edges == DIAGRAM_EDGES


def test_terminal_statuses_have_no_outgoing_transitions():
    for status in ("declined", "counter_declined", "expired", "withdrawn", "consent_revoked"):
        assert not [k for k in TRANSITIONS if k[0] == status]
    # из «Завершено» — только действия кандидата без смены статуса: сверка условий, «со мной не связались»,
    # отзыв согласия (152-ФЗ)
    closed = {(a, r): rule.to for (s, a, r), rule in TRANSITIONS.items() if s == "closed"}
    assert set(closed) == {
        ("outcome_check", "candidate"),
        ("dispute_contact", "candidate"),
        ("revoke_consent", "candidate"),
    }
    assert set(closed.values()) == {"closed"}


def test_every_pair_outside_table_is_rejected():
    """Property-тест: любой (статус, действие, роль) вне таблицы — отказ без изменений."""
    clock = FakeClock(T0)
    for status in InvitationStatus:
        for action in InvitationAction:
            for actor in Actor:
                if (status, action, actor) in TRANSITIONS or action == InvitationAction.SEND:
                    continue
                inv = sent_invitation(clock)
                inv.status = status
                before = len(inv.events)
                with pytest.raises(ContactError) as err:
                    apply_action(inv, action, actor, clock)
                assert err.value.code == "transition_not_allowed"
                assert inv.status == status and len(inv.events) == before


def test_wrong_actor_cannot_act_for_the_other_side():
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    for action in ("accept", "decline", "counter"):
        with pytest.raises(ContactError):
            apply_action(inv, action, "employer", clock, consent=True, reason="salary")
    apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(format="remote"))
    with pytest.raises(ContactError):
        apply_action(inv, "accept_counter", "candidate", clock)


def test_available_actions_for_buttons():
    assert set(available_actions("viewed", "candidate")) == {"accept", "decline", "counter"}
    assert set(available_actions("countered", "employer")) == {
        "accept_counter",
        "decline_counter",
        "withdraw",
    }
    assert available_actions("declined", "candidate") == []


# --- подписи, флаги, антифейк, пачка, напоминания -----------------------------------------------------

LABELS = {
    "sent": "Отправлено",
    "viewed": "Просмотрено",
    "countered": "Встречное условие",
    "accepted": "Принято — контакт открыт",
    "declined": "Отклонено",
    "counter_declined": "Не сошлись по условиям",
    "expired": "Истекло",
    "withdrawn": "Отозвано",
    "closed": "Завершено",
    "consent_revoked": "Согласие отозвано",
}


def test_russian_status_labels_for_both_sides():
    for status in InvitationStatus:
        assert status_label(status) == LABELS[status]
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    assert [e.label for e in inv.events] == ["Отправлено", "Просмотрено"]


def test_caution_and_personal_email_flags():
    clock = FakeClock(T0)
    text = DESCRIPTION + " Перед стартом нужна предоплата за обучение 5000 ₽, пишите на hr.boss@mail.ru"
    inv, out = send_invitation(draft(description=text), sender(email="boss@gmail.com"), candidate(), clock)
    assert "caution" in inv.flags and "personal_email" in inv.flags
    assert len(inv.caution) == 2
    assert any("личный email" in n for n in out.notes)


def test_weak_match_is_warning_not_block():
    clock = FakeClock(T0)
    inv, out = send_invitation(draft(), sender(), candidate(score=35.0), clock)
    assert inv.status == "sent" and "weak_match" in inv.flags
    assert any("Слабое соответствие" in w for w in out.warnings)


def test_send_rejected_explains_every_reason():
    clock = FakeClock(T0)
    with pytest.raises(ContactError) as err:
        send_invitation(
            draft(description="коротко", salary_to=460_000), sender(), candidate(card_opened=False), clock
        )
    codes = err.value.check.error_codes
    assert {"description_too_short", "salary_too_wide", "card_not_opened"} <= set(codes)


def test_send_batch_partial_with_reasons():
    clock = FakeClock(T0)
    items = [
        (draft(inv_id=f"inv-{i}", cid=f"cand-{i}"), candidate(cid=f"cand-{i}", pseudonym=f"К-{1000 + i}"))
        for i in range(3)
    ]
    items.append(
        (
            draft(inv_id="inv-x", cid="cand-x"),
            candidate(cid="cand-x", pseudonym="К-9999", search_mode="hidden"),
        )
    )
    result = send_batch(items, sender(), clock)
    assert [i.id for i in result.sent] == ["inv-0", "inv-1", "inv-2"]
    assert result.rejected["cand-x"].error_codes == ["candidate_unavailable"]
    with pytest.raises(ContactError) as err:
        send_batch(items * 3, sender(), clock)
    assert err.value.code == "batch_too_large"


def test_reminders_after_48h_and_24h_before_deadline():
    clock = FakeClock(T0)
    inv = sent_invitation(clock)
    clock.advance(hours=47)
    assert due_reminders(inv, clock) == []
    clock.advance(hours=1)
    rems = due_reminders(inv, clock)
    assert [r.kind for r in rems] == ["candidate_after_sent"]
    assert "через 3 дня" in rems[0].text
    clock.advance(days=2)  # за 24 ч до конца
    kinds = [r.kind for r in due_reminders(inv, clock, sent_kinds={"candidate_after_sent"})]
    assert kinds == ["candidate_before_deadline"]


def test_reminders_collapse_for_3_day_window():
    clock = FakeClock(T0)
    inv = sent_invitation(clock, response_days=3)
    clock.advance(hours=48)
    assert [r.kind for r in due_reminders(inv, clock)] == ["candidate_before_deadline"]


def test_offer_terms_hash_detects_tampering():
    clock = FakeClock(T0)
    inv = sent_invitation(clock)
    terms = inv.terms[0]
    tampered = dataclasses.replace(terms, salary_from=200_000)
    assert tampered.sha256 == terms.sha256 and not tampered.verify()
    again = dataclasses.replace(terms, sha256="")
    assert again.sha256 == terms.sha256  # хеш детерминирован по содержимому
    with pytest.raises(dataclasses.FrozenInstanceError):
        terms.salary_from = 1  # type: ignore[misc]


def test_outcome_check_promise_broken_is_a_reliability_minus():
    """«Обещание процесса» нарушено → событие надёжности terms_promise_broken (config/contact.yaml: invitation.promises)."""
    clock = FakeClock(T0)
    inv = viewed_invitation(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    apply_action(inv, "mark_contacted", "employer", clock)
    clock.advance(days=10)
    out = apply_action(inv, "outcome_check", "candidate", clock, answer="promise_broken")
    assert reliability_kinds(out) == ["terms_promise_broken"]
