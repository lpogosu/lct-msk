"""Регрессии по ревью модуля контакта: гостинг после «да», пустое согласие, 152-ФЗ, квота, антифейк."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from app.contact.antifraud import check_text
from app.contact.application import apply_application_action, submit_application
from app.contact.clock import FakeClock
from app.contact.common import ContactError
from app.contact.disclosure import disclosure_fields_problem, grant_disclosure
from app.contact.invitation import (
    Invitation,
    InvitationDraft,
    apply_action,
    refresh,
    send_invitation,
)
from app.contact.quotas import CandidateState, SenderState, SentRecord, quota_usage
from app.contact.reliability import ReliabilityEvent, compute_reliability
from app.contact.settings import ContactConfig, get_contact_config
from app.contact.statuses import ApplicationStatus, InvitationStatus
from app.contact.terms import CounterOffer
from app.core.configs import load_config

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)  # понедельник, 12:00 МСК
DESCRIPTION = (
    "Команда платёжного шлюза ищет бэкенд-разработчика: Python, FastAPI, PostgreSQL. "
    "Гибрид, Москва. Код-ревью, дежурства раз в месяц."
)


def viewed(clock: FakeClock) -> Invitation:
    draft = InvitationDraft(
        invitation_id="inv-1",
        company_id="co-1",
        candidate_id="cand-1",
        title="Backend Middle",
        description=DESCRIPTION,
        salary_from=220_000,
        salary_to=280_000,
        contact_method="telegram",
        contact_value="@severny_hr",
        format="hybrid",
    )
    inv, _ = send_invitation(
        draft,
        SenderState("co-1", "hr@severny-kod.ru", "acc-1"),
        CandidateState("cand-1", "К-4821", card_opened=True),
        clock,
    )
    apply_action(inv, "view", "candidate", clock)
    return inv


def accepted(clock: FakeClock) -> Invitation:
    inv = viewed(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    return inv


def kinds(outcome) -> list[str]:
    return [e.data["event"].kind for e in outcome.effects_of("reliability")]


# --- гостинг после раскрытия: закрыть без контакта — не способ уйти от срока ----------------------------


@pytest.mark.parametrize("reason", ["chose_other", "skills_missing", "position_closed"])
def test_close_without_contact_is_withdrawal_after_accept_for_any_reason(reason):
    clock = FakeClock(T0)
    inv = accepted(clock)
    extra = {"skill_ids": ["docker"]} if reason == "skills_missing" else {}
    out = apply_action(inv, "close", "employer", clock, outcome="not_matched", reason=reason, **extra)
    assert kinds(out) == ["withdrawn_after_accept"]  # не good «итог с причиной»
    assert inv.contact_penalty == "withdrawn_after_accept"
    # после срока первого контакта второй bad не появляется, жалоба кандидата тоже не удваивает штраф
    clock.advance(days=10)
    assert kinds(refresh(inv, clock)) == []
    out = apply_action(inv, "dispute_contact", "candidate", clock)
    assert inv.disputed and kinds(out) == []


def test_hire_requires_contact_mark_and_interview_implies_contact():
    clock = FakeClock(T0)
    inv = accepted(clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "close", "employer", clock, outcome="hired")
    assert err.value.code == "hire_requires_contact" and "Связались" in err.value.message
    assert inv.status == InvitationStatus.ACCEPTED
    # «Собеседование» в срок = контакт в срок: честный работодатель не получает bad за просрочку
    clock.advance(days=1)
    out = apply_action(inv, "mark_interview", "employer", clock)
    assert kinds(out) == ["contacted_in_time"] and inv.contacted_at == clock.now()
    clock.advance(days=5)
    assert kinds(refresh(inv, clock)) == []  # просрочки нет
    out = apply_action(inv, "close", "employer", clock, outcome="hired")
    assert kinds(out) == ["outcome_reported"]


def test_close_after_overdue_adds_no_second_penalty_and_no_good():
    clock = FakeClock(T0)
    inv = accepted(clock)
    clock.advance(days=4)  # срок (чт) прошёл
    out = apply_action(inv, "close", "employer", clock, outcome="not_matched", reason="chose_other")
    assert kinds(out) == ["contact_overdue"]  # ленивый срок применился до закрытия; закрытие — без событий
    assert inv.status == InvitationStatus.CLOSED


def test_close_when_contacts_never_opened_is_neutral():
    clock = FakeClock(T0)
    inv = viewed(clock)
    apply_action(
        inv, "counter", "candidate", clock, counter=CounterOffer(format="remote", auto_disclose=False)
    )
    apply_action(inv, "accept_counter", "employer", clock)
    assert inv.disclosure is None
    out = apply_action(inv, "close", "employer", clock, outcome="not_matched", reason="position_closed")
    assert kinds(out) == []  # связаться было нечем — ни good, ни bad


def test_candidate_can_dispute_after_employer_closed_immediately():
    clock = FakeClock(T0)
    inv = accepted(clock)
    apply_action(inv, "mark_contacted", "employer", clock)
    apply_action(inv, "close", "employer", clock, outcome="hired")
    # срок первого контакта ещё не прошёл, но итог закрыт — кандидат может пожаловаться сразу
    out = apply_action(inv, "dispute_contact", "candidate", clock, comment="Никто не написал")
    assert kinds(out) == ["contact_disputed"] and inv.status == InvitationStatus.CLOSED
    with pytest.raises(ContactError):
        apply_action(inv, "dispute_contact", "candidate", clock)  # одна жалоба на приглашение


# --- 152-ФЗ: согласие отзывается в любой момент -----------------------------------------------------------


def test_revoke_consent_after_closed_hides_contacts_without_status_change():
    clock = FakeClock(T0)
    inv = accepted(clock)
    apply_action(inv, "mark_contacted", "employer", clock)
    apply_action(inv, "close", "employer", clock, outcome="hired")
    clock.advance(days=20)
    out = apply_action(inv, "revoke_consent", "candidate", clock)
    assert inv.status == InvitationStatus.CLOSED and not inv.contacts_open(clock.now())
    assert out.effects_of("disclosure_revoked")
    assert [e.data["to"] for e in out.effects_of("notify")] == ["employer"]
    assert out.events[-1].action == "revoke_consent" and out.events[-1].label == "Завершено"
    with pytest.raises(ContactError) as err:
        apply_action(inv, "revoke_consent", "candidate", clock)
    assert err.value.code == "consent_active"


def test_application_consent_revocable_after_answer():
    clock = FakeClock(T0)
    app, _ = submit_application(
        application_id="app-1",
        vacancy_id="vac-1",
        company_id="co-1",
        candidate_id="cand-1",
        consent=True,
        clock=clock,
    )
    apply_application_action(app, "view", "employer", clock)
    apply_application_action(app, "reject", "employer", clock, reason="chose_other")
    out = apply_application_action(app, "revoke_consent", "candidate", clock)
    assert app.status == ApplicationStatus.REJECTED and not app.contacts_visible(clock.now())
    assert out.effects_of("disclosure_revoked")

    clock2 = FakeClock(T0)
    app2, _ = submit_application(
        application_id="app-2",
        vacancy_id="vac-1",
        company_id="co-1",
        candidate_id="cand-1",
        consent=True,
        clock=clock2,
    )
    apply_application_action(app2, "view", "employer", clock2)
    apply_application_action(app2, "interest", "employer", clock2)
    apply_application_action(app2, "withdraw", "candidate", clock2)
    assert app2.status == ApplicationStatus.WITHDRAWN and not app2.contacts_visible(clock2.now())


# --- согласие без канала связи --------------------------------------------------------------------------


@pytest.mark.parametrize("fields", [[], ["full_name", "resume"], ["gender"]])
def test_accept_refuses_consent_without_any_contact_channel(fields):
    clock = FakeClock(T0)
    inv = viewed(clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "accept", "candidate", clock, consent=True, fields=fields)
    assert err.value.code == "consent_given" and "способ связи" in err.value.message
    assert inv.status == InvitationStatus.VIEWED and inv.disclosure is None


def test_single_field_string_is_one_field_not_letters():
    clock = FakeClock(T0)
    inv = viewed(clock)
    apply_action(inv, "accept", "candidate", clock, consent=True, fields="email")
    assert inv.disclosure.fields == ("email",)


def test_counter_disclosure_fields_checked_when_counter_is_sent():
    clock = FakeClock(T0)
    inv = viewed(clock)
    with pytest.raises(ContactError) as err:
        apply_action(
            inv,
            "counter",
            "candidate",
            clock,
            counter=CounterOffer(format="remote", disclosure_fields=("resume",)),
        )
    assert "способ связи" in err.value.message and inv.status == InvitationStatus.VIEWED
    # без автораскрытия поля не важны — согласие будет дано позже отдельно
    apply_action(
        inv,
        "counter",
        "candidate",
        clock,
        counter=CounterOffer(format="remote", auto_disclose=False, disclosure_fields=("resume",)),
    )
    assert inv.status == InvitationStatus.COUNTERED


def test_grant_disclosure_and_application_refuse_empty_consent():
    assert disclosure_fields_problem(["phone"]) is None
    assert disclosure_fields_problem(None) is None  # значения по умолчанию содержат канал
    with pytest.raises(ValueError):
        grant_disclosure(
            candidate_id="c", company_id="co", basis="invitation", basis_id="i", at=T0, fields=[]
        )
    with pytest.raises(ContactError) as err:
        submit_application(
            application_id="app-1",
            vacancy_id="vac-1",
            company_id="co-1",
            candidate_id="cand-1",
            consent=True,
            clock=T0,
            fields=["full_name"],
        )
    assert err.value.code == "consent_fields"


# --- неизвестное действие из HTTP — понятный отказ, а не 500 ------------------------------------------------


def test_unknown_action_or_actor_is_contact_error():
    clock = FakeClock(T0)
    inv = viewed(clock)
    for action, actor in (("hack", "candidate"), ("accept", "admin")):
        with pytest.raises(ContactError) as err:
            apply_action(inv, action, actor, clock)
        assert err.value.code == "unknown_action"
    app, _ = submit_application(
        application_id="app-1",
        vacancy_id="vac-1",
        company_id="co-1",
        candidate_id="cand-1",
        consent=True,
        clock=T0,
    )
    with pytest.raises(ContactError) as err:
        apply_application_action(app, "approve", "employer", T0)
    assert err.value.code == "unknown_action"


# --- квота ×0.5: когда освободится слот ---------------------------------------------------------------------


def test_reduced_quota_next_slot_waits_until_usage_drops_below_limit():
    bad = [
        ReliabilityEvent("co-1", "contact_overdue", T0 - timedelta(days=30), candidate_id=f"c{i}")
        for i in range(10)
    ]
    rel = compute_reliability(bad, T0)
    start = T0 - timedelta(days=6)
    sends = tuple(
        SentRecord(f"s{i}", "co-1", f"x{i}", start + timedelta(hours=2 * i), "sent") for i in range(45)
    )
    usage = quota_usage(SenderState("co-1", "hr@severny-kod.ru", "acc-1", sends, rel), T0)
    assert (usage.used, usage.limit) == (45, 30)
    # чтобы стало 29 из 30, окно должны покинуть 16 самых старых: 16-я отправлена в start + 30 ч
    assert usage.next_slot_at == start + timedelta(hours=30) + timedelta(days=7)
    full = quota_usage(SenderState("co-1", "hr@severny-kod.ru", "acc-1", sends[:30]), T0)
    assert full.next_slot_at == start + timedelta(days=7)  # без снижения — первая же старая


def test_quota_reduced_reason_comes_from_config():
    bad = [
        ReliabilityEvent("co-1", "contact_overdue", T0 - timedelta(days=1), candidate_id=f"c{i}")
        for i in range(10)
    ]
    usage = quota_usage(SenderState("co-1", "hr@x.ru", "acc-1", (), compute_reliability(bad, T0)), T0)
    assert usage.reduced_reason == get_contact_config().quotas.low_reliability.label


def test_candidate_weekly_cap_default_comes_from_config():
    assert CandidateState("c", "К-1").weekly_cap == get_contact_config().candidate_limits.default_weekly


# --- тексты без захардкоженных чисел ----------------------------------------------------------------------


def test_outcome_check_too_early_uses_relative_date():
    clock = FakeClock(T0)
    inv = accepted(clock)
    clock.advance(days=3)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "outcome_check", "candidate", clock, answer="match")
    assert "через 7 дней" in err.value.message


def test_counter_cap_message_derived_from_config():
    clock = FakeClock(T0)
    inv = viewed(clock)
    with pytest.raises(ContactError) as err:
        apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(salary_from=365_000))
    assert "360 000 ₽" in err.value.message and "130%" in err.value.message


# --- антифейк: честные льготы не пугают кандидата ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Официальное трудоустройство, белая зарплата, все страховые взносы",
        "ДМС, компенсация спорта, оплата обучения, курсов и конференций",
        "Компания готова оплатить обучение и сертификацию",
        "Оформление по ТК РФ в день выхода, при себе паспорт",
        "Делаем сервис проверки паспортов и банковских карт",
    ],
)
def test_honest_benefits_do_not_raise_caution(text):
    assert check_text("Backend", DESCRIPTION + " " + text).codes == []


@pytest.mark.parametrize(
    "text,code",
    [
        ("Нужно оплатить обучение 5000 рублей до начала работы", "prepayment"),
        ("Оплатите доступ к платформе", "prepayment"),
        ("Вступительный взнос 2000 ₽", "prepayment"),
        ("Взнос за участие в стажировке 3000 ₽", "prepayment"),
        ("Отправьте фото паспорта в ответном письме", "documents"),
        ("Укажите паспортные данные в анкете", "documents"),
        ("Сообщите серию и номер паспорта", "documents"),
        ("Пришлите копию СНИЛС", "documents"),
    ],
)
def test_scam_phrasings_still_caught(text, code):
    assert code in check_text("Вакансия", text).codes


# --- слепая карточка без теста, разложение надёжности -----------------------------------------------------


def test_blind_view_without_test_or_fsp_does_not_crash():
    from app.contact.visibility import blind_view, pii_keys_in

    profile = {
        "candidate_id": "c1",
        "category": {"name": "Backend · Middle"},
        "test": None,
        "fsp": None,
        "full_name": "Иванов Иван",
    }
    view = blind_view(profile, pseudonym="К-1")
    assert view["test"] is None and view["fsp"] is None and pii_keys_in(view) == set()


def test_closing_without_contact_counts_in_contact_ratio():
    evs = [
        ReliabilityEvent("co-1", "contacted_in_time", T0 - timedelta(days=1), candidate_id=f"c{i}")
        for i in range(4)
    ]
    evs.append(ReliabilityEvent("co-1", "withdrawn_after_accept", T0 - timedelta(days=1), candidate_id="x"))
    assert compute_reliability(evs, T0).breakdown == ("связывается после принятия 4/5",)


# --- конфиг: возраст и пол — никогда, канал связи — обязателен ----------------------------------------------


def _raw_config() -> dict:
    return json.loads(json.dumps(load_config("contact"), default=str))


def test_config_refuses_age_in_disclosed_view():
    raw = _raw_config()
    raw["visibility"]["fields"]["age"]["disclosed"] = "show"
    with pytest.raises(ValidationError):
        ContactConfig.model_validate(raw)


def test_config_refuses_default_consent_without_contact_channel():
    raw = _raw_config()
    raw["disclosure"]["default_fields"] = ["full_name", "resume"]
    with pytest.raises(ValidationError):
        ContactConfig.model_validate(raw)
