"""Отклик: submitted → viewed → interested / rejected(причина); 7 дней без ответа → no_response."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.contact.application import (
    TRANSITIONS,
    application_status_label,
    apply_application_action,
    refresh_application,
    submit_application,
)
from app.contact.clock import FakeClock
from app.contact.common import ContactError
from app.contact.statuses import ApplicationStatus

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def submit(clock, consent=True, **kw):
    return submit_application(
        application_id="app-1",
        vacancy_id="vac-1",
        company_id="co-1",
        candidate_id="cand-1",
        consent=consent,
        clock=clock,
        **kw,
    )


def kinds(out):
    return [e.data["event"].kind for e in out.effects_of("reliability")]


def test_submit_requires_consent_and_makes_contacts_visible():
    clock = FakeClock(T0)
    with pytest.raises(ContactError) as err:
        submit(clock, consent=False)
    assert "согласие" in err.value.message
    app, out = submit(clock)
    assert app.status == ApplicationStatus.SUBMITTED and app.status_label() == "Отправлен"
    assert app.contacts_visible(clock.now())  # откликнулся сам — контакты видны сразу
    assert app.disclosure.basis == "application"
    assert app.sla_due_at == T0 + timedelta(days=7)
    assert out.events[0].action == "submit" and out.effects_of("disclosure_granted")


def test_view_then_interested_in_time_is_good():
    clock = FakeClock(T0)
    app, _ = submit(clock)
    clock.advance(days=1)
    apply_application_action(app, "view", "employer", clock)
    assert app.status_label() == "Просмотрен"
    clock.advance(days=2)
    out = apply_application_action(app, "interest", "employer", clock)
    assert app.status == ApplicationStatus.INTERESTED and app.status_label() == "Есть интерес"
    assert kinds(out) == ["application_answered"]
    assert [e.action for e in app.events] == ["submit", "view", "interest"]


def test_reject_requires_reason():
    clock = FakeClock(T0)
    app, _ = submit(clock)
    apply_application_action(app, "view", "employer", clock)
    with pytest.raises(ContactError) as err:
        apply_application_action(app, "reject", "employer", clock)
    assert "причину" in err.value.message
    out = apply_application_action(
        app, "reject", "employer", clock, reason="skills_missing", skill_ids=["docker"]
    )
    assert app.status == ApplicationStatus.REJECTED and app.status_label() == "Отказ"
    fb = out.effects_of("feedback")[0].data
    assert fb["reason_code"] == "skills_missing" and fb["skill_ids"] == ["docker"]
    assert kinds(out) == ["application_answered"]


def test_no_response_after_7_days_is_bad_and_lazy():
    clock = FakeClock(T0)
    app, _ = submit(clock)
    apply_application_action(app, "view", "employer", clock)
    clock.advance(days=6, hours=23)
    assert refresh_application(app, clock).events == []
    clock.advance(days=2)
    out = refresh_application(app, clock)
    assert app.status == ApplicationStatus.NO_RESPONSE and app.status_label() == "Нет ответа"
    assert out.events[0].at == app.sla_due_at and out.events[0].actor_role == "system"
    assert kinds(out) == ["application_no_response"]
    assert refresh_application(app, clock).events == []
    with pytest.raises(ContactError) as err:
        apply_application_action(app, "interest", "employer", clock)
    assert "«Нет ответа»" in err.value.message


def test_answer_after_sla_reports_lazy_expiry():
    clock = FakeClock(T0)
    app, _ = submit(clock)
    clock.advance(days=8)
    with pytest.raises(ContactError) as err:
        apply_application_action(app, "view", "employer", clock)
    assert [e.to_status for e in err.value.prior.events] == ["no_response"]


def test_candidate_withdraw_revokes_contacts():
    clock = FakeClock(T0)
    app, _ = submit(clock)
    clock.advance(hours=5)
    out = apply_application_action(app, "withdraw", "candidate", clock)
    assert app.status == ApplicationStatus.WITHDRAWN and app.status_label() == "Отозван"
    assert not app.contacts_visible(clock.now())
    assert out.effects_of("disclosure_revoked")


def test_must_view_before_answer_and_roles_enforced():
    clock = FakeClock(T0)
    app, _ = submit(clock)
    with pytest.raises(ContactError):
        apply_application_action(app, "interest", "employer", clock)
    with pytest.raises(ContactError):
        apply_application_action(app, "view", "candidate", clock)
    # из финальных статусов — только действия кандидата: отзыв после интереса и отзыв согласия (петля)
    final = {"interested", "rejected", "withdrawn", "no_response"}
    moves = {(str(s), str(a), str(r), str(rule.to)) for (s, a, r), rule in TRANSITIONS.items() if s in final}
    assert moves == {
        ("interested", "withdraw", "candidate", "withdrawn"),
        ("rejected", "revoke_consent", "candidate", "rejected"),
        ("no_response", "revoke_consent", "candidate", "no_response"),
    }


def test_application_labels_russian():
    expected = {
        "submitted": "Отправлен",
        "viewed": "Просмотрен",
        "interested": "Есть интерес",
        "rejected": "Отказ",
        "withdrawn": "Отозван",
        "no_response": "Нет ответа",
    }
    for status in ApplicationStatus:
        assert application_status_label(status) == expected[status]
