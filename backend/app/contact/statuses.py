"""Статусы, действия и роли механики контакта (концепция §4.3, spec-contact §2.3)."""

from __future__ import annotations

from enum import StrEnum


class InvitationStatus(StrEnum):
    SENT = "sent"
    VIEWED = "viewed"
    COUNTERED = "countered"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    COUNTER_DECLINED = "counter_declined"
    EXPIRED = "expired"
    WITHDRAWN = "withdrawn"
    CLOSED = "closed"
    CONSENT_REVOKED = "consent_revoked"


class InvitationAction(StrEnum):
    SEND = "send"
    VIEW = "view"
    ACCEPT = "accept"
    DECLINE = "decline"
    COUNTER = "counter"
    WITHDRAW = "withdraw"
    ACCEPT_COUNTER = "accept_counter"
    DECLINE_COUNTER = "decline_counter"
    EXPIRE = "expire"
    CLOSE = "close"
    REVOKE_CONSENT = "revoke_consent"
    # события без смены статуса (петли в accepted/closed)
    GRANT_CONSENT = "grant_consent"
    MARK_INTERVIEW = "mark_interview"
    MARK_CONTACTED = "mark_contacted"
    DISPUTE_CONTACT = "dispute_contact"
    FLAG_CONTACT_OVERDUE = "flag_contact_overdue"
    OUTCOME_CHECK = "outcome_check"


class Actor(StrEnum):
    CANDIDATE = "candidate"
    EMPLOYER = "employer"
    SYSTEM = "system"


class ApplicationStatus(StrEnum):
    SUBMITTED = "submitted"
    VIEWED = "viewed"
    INTERESTED = "interested"
    REJECTED = "rejected"
    WITHDRAWN = "withdrawn"
    NO_RESPONSE = "no_response"


class ApplicationAction(StrEnum):
    SUBMIT = "submit"
    VIEW = "view"
    INTEREST = "interest"
    REJECT = "reject"
    WITHDRAW = "withdraw"
    EXPIRE = "expire"
    REVOKE_CONSENT = "revoke_consent"  # петля в финальных статусах: скрыть контакты (152-ФЗ)


# Ждут ответа одной из сторон: занимают слот брифа и блокируют пару.
PENDING_STATUSES: frozenset[str] = frozenset(
    {InvitationStatus.SENT, InvitationStatus.VIEWED, InvitationStatus.COUNTERED}
)
# «Одно активное приглашение на пару»: открытый контакт тоже считается активным.
PAIR_ACTIVE_STATUSES: frozenset[str] = PENDING_STATUSES | {InvitationStatus.ACCEPTED}
TERMINAL_STATUSES: frozenset[str] = frozenset(
    {
        InvitationStatus.DECLINED,
        InvitationStatus.COUNTER_DECLINED,
        InvitationStatus.EXPIRED,
        InvitationStatus.WITHDRAWN,
        InvitationStatus.CONSENT_REVOKED,
    }
)
