"""Правки движка контакта, сделанные при подключении API (чистые функции, без БД):

- DECISIONS 20: комментарий кандидата до раскрытия («Нет», «Да, если…») не может нести контакты и ссылки;
- DECISIONS 22: отзыв приглашения и отзыв согласия ставят паузу пары 14 дней (как после expired);
- DECISIONS 18: единый список личных доменов (config/contact.yaml) читают и движок, и платформа.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.contact.antifraud import comment_contacts_problem
from app.contact.clock import FakeClock
from app.contact.common import ContactError
from app.contact.invitation import InvitationDraft, apply_action, send_invitation
from app.contact.quotas import CandidateState, Cooldown, SenderState, check_invite, cooldown_after_withdrawal
from app.contact.terms import CounterOffer

T0 = datetime(2026, 10, 5, 9, 0, tzinfo=UTC)


def _sent(clock: FakeClock):
    draft = InvitationDraft(
        invitation_id="inv-1",
        company_id="co-1",
        candidate_id="cand-1",
        title="Backend Middle в платёжный шлюз",
        description="Команда платёжного шлюза ищет бэкенд-разработчика: Python, FastAPI, PostgreSQL. Гибрид.",
        salary_from=220_000,
        salary_to=280_000,
        contact_method="telegram",
        contact_value="@severny_hr",
    )
    inv, _ = send_invitation(
        draft,
        SenderState(company_id="co-1", email="hr@severny-kod.ru", account_id="acc-1"),
        CandidateState(candidate_id="cand-1", pseudonym="К-4821", card_opened=True),
        clock,
    )
    return inv


@pytest.mark.parametrize(
    "text",
    [
        "пишите на anna@example.com",
        "мой номер +7 916 123-45-67",
        "телеграм @anna_dev",
        "t.me/anna_dev",
        "подробнее: bit.ly/abc123",
    ],
)
def test_comment_with_contacts_is_rejected(text):
    assert comment_contacts_problem(text) is not None


@pytest.mark.parametrize("text", ["", None, "Готова выйти через две недели", "Рассматриваю от 280 тысяч на руки"])
def test_plain_comment_is_ok(text):
    assert comment_contacts_problem(text) is None


def test_decline_and_counter_comments_screened():
    clock = FakeClock(T0)
    inv = _sent(clock)
    apply_action(inv, "view", "candidate", clock)
    with pytest.raises(ContactError) as e:
        apply_action(inv, "decline", "candidate", clock, reason="salary", comment="звоните +7 916 123-45-67")
    assert "контакты" in e.value.message
    with pytest.raises(ContactError):
        apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(salary_from=270_000, comment="anna@example.com"))
    out = apply_action(inv, "counter", "candidate", clock, counter=CounterOffer(salary_from=270_000, comment="Готова обсудить"))
    assert inv.status == "countered" and out.events


def test_withdraw_sets_pair_pause():
    clock = FakeClock(T0)
    inv = _sent(clock)
    out = apply_action(inv, "withdraw", "employer", clock, reason="chose_other")
    cd = out.effects_of("cooldown")[0].data["cooldown"]
    assert cd.reason == "withdrawn" and (cd.until - T0).days == 14 and cd.salary_unlock_from is None


def test_revoke_consent_sets_pair_pause_from_accepted_and_closed():
    clock = FakeClock(T0)
    inv = _sent(clock)
    apply_action(inv, "view", "candidate", clock)
    apply_action(inv, "accept", "candidate", clock, consent=True)
    out = apply_action(inv, "revoke_consent", "candidate", clock)
    assert inv.status == "consent_revoked"
    cd = out.effects_of("cooldown")[0].data["cooldown"]
    assert cd.reason == "consent_revoked" and (cd.until - T0).days == 14


def test_pause_messages_for_new_reasons():
    sender = SenderState(company_id="co-1", email="hr@severny-kod.ru", account_id="acc-1")
    for reason, text in (("withdrawn", "отозванного приглашения"), ("consent_revoked", "отзыва согласия")):
        cd = cooldown_after_withdrawal(company_id="co-1", candidate_id="cand-1", reason=reason, at=T0)
        assert isinstance(cd, Cooldown)
        chk = check_invite(
            sender,
            CandidateState(candidate_id="cand-1", pseudonym="К-4821", card_opened=True, cooldowns=(cd,)),
            need_id=None,
            salary_from=220_000,
            salary_to=280_000,
            now=T0,
        )
        assert chk.error_codes == ["pair_pause"] and text in chk.errors[0].message


def test_single_personal_domain_list():
    from app.contact.quotas import is_personal_email
    from app.core.domains import is_personal_domain

    for domain in ("gmail.com", "yandex.by", "proton.me", "yahoo.com", "mail.ru"):
        assert is_personal_domain(domain) and is_personal_email(f"hr@{domain}")
    assert not is_personal_domain("severny-kod.ru") and not is_personal_email("hr@severny-kod.ru")
