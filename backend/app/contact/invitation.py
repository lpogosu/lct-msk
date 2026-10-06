"""Приглашение — ядро механики контакта (концепция §4.3, spec-contact §2.3).

Машина состояний задана таблицей (from, action, actor) → (to, guards, side_effects) и повторяет
stateDiagram концепции один в один. Петли в accepted/closed (отметка «связались», «со мной не связались»,
сверка условий, собеседование, согласие после встречного) — события без смены статуса.

Каждое действие пишет InvitationEvent. Сроки применяются лениво при чтении (refresh) — это источник истины;
фоновая задача только рассылает напоминания (due_reminders). Время берётся только из Clock.
Чистый Python: внешний слой сохраняет Invitation, события и выполняет SideEffect (Cooldown, надёжность,
уведомления, вебхуки).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from .antifraud import check_text, comment_contacts_problem
from .clock import Clock, add_business_days, local_date, now_of
from .common import ContactError, Outcome, Rule, SideEffect
from .disclosure import ContactDisclosure, disclosure_fields_problem, grant_disclosure
from .quotas import (
    CandidateState,
    SenderState,
    SentRecord,
    check_invite,
    cooldown_after,
    cooldown_after_withdrawal,
    is_personal_email,
)
from .reliability import ReliabilityEvent
from .rules import Check, validate_invitation_content
from .settings import ContactConfig, get_contact_config
from .statuses import PENDING_STATUSES
from .statuses import Actor as R
from .statuses import InvitationAction as A
from .statuses import InvitationStatus as S
from .terms import CounterOffer, OfferTerms, counter_summary, terms_after_counter, validate_counter
from .texts import count_words, relative, salary_range

# --- модель ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InvitationEvent:
    id: str  # «{invitation_id}-{seq}» — идемпотентный event_id для вебхуков ATS (концепция §8.2)
    invitation_id: str
    seq: int
    from_status: str | None
    to_status: str
    action: str
    actor_role: str
    actor_id: str | None
    at: datetime
    payload: Mapping[str, Any]
    label: str  # подпись статуса после события — одинакова для обеих сторон


@dataclass
class Invitation:
    id: str
    company_id: str
    candidate_id: str
    pseudonym: str
    status: S
    sent_at: datetime
    respond_by: datetime
    terms: list[OfferTerms]
    need_id: str | None = None
    vacancy_id: str | None = None
    why_you: tuple[str, ...] = ()
    flags: list[str] = field(default_factory=list)
    caution: tuple[str, ...] = ()  # причины плашки «Требует осторожности» (антифейк)
    score_snapshot: Mapping[str, Any] | None = None
    rank_at_send: int | None = None
    viewed_at: datetime | None = None
    counter: CounterOffer | None = None
    countered_at: datetime | None = None
    counter_respond_by: datetime | None = None
    accepted_at: datetime | None = None
    disclosure: ContactDisclosure | None = None
    contact_due_at: datetime | None = None
    contacted_at: datetime | None = None
    contact_overdue: bool = False
    disputed: bool = False
    # Какой bad за «не связались» уже записан (contact_overdue | contact_disputed | withdrawn_after_accept):
    # один гостинг — один штраф, сколько бы путей к нему ни вело.
    contact_penalty: str | None = None
    decline_reason: str | None = None
    decline_comment: str = ""
    withdraw_reason: str | None = None
    counter_decline_reason: str | None = None
    stage: str | None = None
    outcome: str | None = None
    outcome_reason: str | None = None
    outcome_skill_ids: tuple[str, ...] = ()
    closed_at: datetime | None = None
    outcome_check: str | None = None
    outcome_check_at: datetime | None = None
    events: list[InvitationEvent] = field(default_factory=list)

    @property
    def current_terms(self) -> OfferTerms:
        return self.terms[-1]

    def status_label(self, cfg: ContactConfig | None = None) -> str:
        return status_label(self.status, cfg)

    def contacts_open(self, at: datetime) -> bool:
        return self.disclosure is not None and self.disclosure.is_active(at)

    def to_sent_record(self) -> SentRecord:
        return SentRecord(
            invitation_id=self.id,
            company_id=self.company_id,
            candidate_id=self.candidate_id,
            sent_at=self.sent_at,
            status=str(self.status),
            viewed=self.viewed_at is not None,
            need_id=self.need_id,
        )


@dataclass(frozen=True)
class InvitationDraft:
    """Форма приглашения (Р7): заполняется из брифа, «почему вы» — своё для каждого кандидата."""

    invitation_id: str
    company_id: str
    candidate_id: str
    title: str
    description: str
    salary_from: int
    salary_to: int
    contact_method: str
    contact_value: str
    salary_type: str = "net"
    response_days: int | None = None  # None → по умолчанию 5 дней из config
    format: str | None = None
    start_date: date | None = None
    need_id: str | None = None
    vacancy_id: str | None = None
    is_internship: bool = False
    why_you: tuple[str, ...] = ()
    actor_id: str | None = None
    score_snapshot: Mapping[str, Any] | None = None
    rank_at_send: int | None = None


def status_label(status: str, cfg: ContactConfig | None = None) -> str:
    """Подпись статуса. Сторона не передаётся намеренно: у кандидата и работодателя она одна и та же."""
    cfg = cfg or get_contact_config()
    return cfg.status_labels.invitation[str(status)]


# --- таблица переходов ------------------------------------------------------------------------------

TRANSITIONS: dict[tuple[S, A, R], Rule] = {
    (S.SENT, A.VIEW, R.CANDIDATE): Rule(S.VIEWED, ("before_response_deadline",), ("mark_viewed",)),
    (S.SENT, A.EXPIRE, R.SYSTEM): Rule(
        S.EXPIRED, ("response_deadline_passed",), ("return_quota_slot", "set_pause", "notify")
    ),
    (S.SENT, A.WITHDRAW, R.EMPLOYER): Rule(
        S.WITHDRAWN, ("withdraw_reason_valid",), ("record_withdraw", "set_withdrawal_pause", "notify")
    ),
    (S.VIEWED, A.EXPIRE, R.SYSTEM): Rule(S.EXPIRED, ("response_deadline_passed",), ("set_pause", "notify")),
    (S.VIEWED, A.WITHDRAW, R.EMPLOYER): Rule(
        S.WITHDRAWN, ("withdraw_reason_valid",), ("record_withdraw", "set_withdrawal_pause", "notify")
    ),
    (S.VIEWED, A.ACCEPT, R.CANDIDATE): Rule(
        S.ACCEPTED,
        ("before_response_deadline", "consent_given"),
        ("mark_accepted", "create_disclosure", "notify"),
    ),
    (S.VIEWED, A.DECLINE, R.CANDIDATE): Rule(
        S.DECLINED,
        ("before_response_deadline", "decline_reason_valid"),
        ("record_decline", "set_pause", "notify"),
    ),
    (S.VIEWED, A.COUNTER, R.CANDIDATE): Rule(
        S.COUNTERED,
        ("before_response_deadline", "counter_round_available", "counter_valid"),
        ("record_counter", "notify"),
    ),
    (S.COUNTERED, A.ACCEPT_COUNTER, R.EMPLOYER): Rule(
        S.ACCEPTED,
        ("before_counter_deadline",),
        ("new_terms_from_counter", "mark_accepted", "auto_disclose", "notify"),
    ),
    (S.COUNTERED, A.DECLINE_COUNTER, R.EMPLOYER): Rule(
        S.COUNTER_DECLINED,
        ("before_counter_deadline", "counter_decline_reason_valid"),
        ("record_counter_decline", "set_pause", "notify"),
    ),
    (S.COUNTERED, A.EXPIRE, R.SYSTEM): Rule(
        S.EXPIRED, ("counter_deadline_passed",), ("reliability_counter_unanswered", "set_pause", "notify")
    ),
    (S.COUNTERED, A.WITHDRAW, R.EMPLOYER): Rule(
        S.WITHDRAWN, ("withdraw_reason_valid",), ("record_withdraw", "set_withdrawal_pause", "notify")
    ),
    # Итог «Найм» без отметки «Связались» невозможен; «Не сложилось» без контакта = отзыв после accepted.
    (S.ACCEPTED, A.CLOSE, R.EMPLOYER): Rule(
        S.CLOSED, ("outcome_valid", "hire_requires_contact"), ("record_outcome",)
    ),
    (S.ACCEPTED, A.REVOKE_CONSENT, R.CANDIDATE): Rule(
        S.CONSENT_REVOKED, (), ("revoke_disclosure", "set_withdrawal_pause", "notify")
    ),
    # Петли: события без смены статуса
    (S.ACCEPTED, A.GRANT_CONSENT, R.CANDIDATE): Rule(
        S.ACCEPTED, ("disclosure_missing", "consent_given"), ("create_disclosure",)
    ),
    (S.ACCEPTED, A.MARK_INTERVIEW, R.EMPLOYER): Rule(S.ACCEPTED, ("no_stage_yet",), ("record_interview",)),
    (S.ACCEPTED, A.MARK_CONTACTED, R.EMPLOYER): Rule(
        S.ACCEPTED, ("disclosure_active", "not_contacted_yet"), ("record_contacted",)
    ),
    (S.ACCEPTED, A.DISPUTE_CONTACT, R.CANDIDATE): Rule(S.ACCEPTED, ("dispute_allowed",), ("record_dispute",)),
    (S.ACCEPTED, A.FLAG_CONTACT_OVERDUE, R.SYSTEM): Rule(
        S.ACCEPTED, ("contact_deadline_passed",), ("record_contact_overdue",)
    ),
    (S.ACCEPTED, A.OUTCOME_CHECK, R.CANDIDATE): Rule(
        S.ACCEPTED, ("outcome_check_due",), ("record_outcome_check",)
    ),
    (S.CLOSED, A.OUTCOME_CHECK, R.CANDIDATE): Rule(
        S.CLOSED, ("outcome_check_due",), ("record_outcome_check",)
    ),
    # Закрытие итога не отнимает у кандидата кнопки «со мной не связались» (иначе работодатель мог бы
    # закрыть приглашение сразу после «да» и уйти от жалобы) и права отозвать согласие (152-ФЗ: в любой момент).
    (S.CLOSED, A.DISPUTE_CONTACT, R.CANDIDATE): Rule(S.CLOSED, ("dispute_allowed",), ("record_dispute",)),
    (S.CLOSED, A.REVOKE_CONSENT, R.CANDIDATE): Rule(
        S.CLOSED, ("consent_active",), ("revoke_disclosure", "set_withdrawal_pause", "notify")
    ),
}


def available_actions(status: str, actor: str) -> list[A]:
    """Какие кнопки показать стороне в данном статусе (без проверки guards)."""
    return [
        a
        for (s, a, r) in TRANSITIONS
        if s == status and r == actor and a not in (A.EXPIRE, A.FLAG_CONTACT_OVERDUE)
    ]


# --- контекст действия ------------------------------------------------------------------------------


@dataclass
class _Ctx:
    now: datetime  # реальное «сейчас» — для guards
    at: datetime  # момент события: для ленивых сроков — сам дедлайн
    action: A
    to: S
    cfg: ContactConfig
    actor: R
    actor_id: str | None
    payload: Mapping[str, Any]
    out: Outcome
    event_payload: dict[str, Any] = field(default_factory=dict)

    def effect(self, kind: str, **data: Any) -> None:
        self.out.side_effects.append(SideEffect(kind, data))


def _reliability(inv: Invitation, ctx: _Ctx, kind: str) -> None:
    ev = ReliabilityEvent(
        company_id=inv.company_id, kind=kind, at=ctx.at, candidate_id=inv.candidate_id, invitation_id=inv.id
    )
    ctx.effect("reliability", event=ev)


def _reason_list(options: Mapping[str, str]) -> str:
    return ", ".join(v.lower() for v in options.values())


# --- guards: None — можно, строка — понятная причина отказа -------------------------------------------

Guard = Callable[[Invitation, _Ctx], str | None]


def _g_before_response_deadline(inv: Invitation, ctx: _Ctx) -> str | None:
    return None if ctx.now < inv.respond_by else "Срок ответа на приглашение истёк"


def _g_response_deadline_passed(inv: Invitation, ctx: _Ctx) -> str | None:
    return None if ctx.now >= inv.respond_by else "Срок ответа ещё не истёк"


def _g_before_counter_deadline(inv: Invitation, ctx: _Ctx) -> str | None:
    due = inv.counter_respond_by
    return None if due is not None and ctx.now < due else "Срок ответа на встречное условие истёк"


def _g_counter_deadline_passed(inv: Invitation, ctx: _Ctx) -> str | None:
    due = inv.counter_respond_by
    return None if due is not None and ctx.now >= due else "Срок ответа на встречное условие ещё не истёк"


def _g_consent_given(inv: Invitation, ctx: _Ctx) -> str | None:
    if ctx.payload.get("consent") is not True:
        return "Чтобы открыть контакты, отметьте согласие на их передачу этой компании"
    return disclosure_fields_problem(ctx.payload.get("fields"), ctx.cfg)


def _g_consent_active(inv: Invitation, ctx: _Ctx) -> str | None:
    return None if inv.contacts_open(ctx.now) else "Согласие на передачу контактов уже отозвано"


def _comment_ok(ctx: _Ctx) -> str | None:
    limit = ctx.cfg.counter_offer.comment_max_len
    if len(ctx.payload.get("comment") or "") > limit:
        return f"Комментарий — не длиннее {limit} символов"
    return None


def _g_decline_reason_valid(inv: Invitation, ctx: _Ctx) -> str | None:
    options = ctx.cfg.reasons.decline
    if ctx.payload.get("reason") not in options:
        return f"Выберите причину отказа: {_reason_list(options)}"
    # DECISIONS 20: комментарий кандидата уходит работодателю до раскрытия — без контактов
    return _comment_ok(ctx) or comment_contacts_problem(ctx.payload.get("comment"), ctx.cfg)


def _g_withdraw_reason_valid(inv: Invitation, ctx: _Ctx) -> str | None:
    options = ctx.cfg.reasons.withdraw
    if ctx.payload.get("reason") not in options:
        return f"Укажите причину отзыва: {_reason_list(options)}"
    return _comment_ok(ctx)


def _g_counter_decline_reason_valid(inv: Invitation, ctx: _Ctx) -> str | None:
    options = ctx.cfg.reasons.counter_decline
    if ctx.payload.get("reason") not in options:
        return f"Укажите причину отказа: {_reason_list(options)}"
    return _comment_ok(ctx)


def _g_counter_round_available(inv: Invitation, ctx: _Ctx) -> str | None:
    used = 1 if inv.counter is not None else 0
    if used >= ctx.cfg.counter_offer.max_rounds:
        return "Встречное условие можно предложить только один раз"
    return None


def _g_counter_valid(inv: Invitation, ctx: _Ctx) -> str | None:
    counter = ctx.payload.get("counter")
    if not isinstance(counter, CounterOffer):
        return "Заполните встречное условие"
    check = validate_counter(counter, inv.current_terms, local_date(ctx.now, ctx.cfg), ctx.cfg)
    if not check.ok:
        return "; ".join(check.messages())
    problem = comment_contacts_problem(counter.comment, ctx.cfg)  # DECISIONS 20
    if problem:
        return problem
    # Галочка «раскрыть контакты, если примут»: набор полей проверяем сейчас, а не в момент согласия
    # работодателя — иначе кандидат узнал бы о проблеме, когда исправить её уже нельзя.
    if _auto_disclose(counter, ctx.cfg) and counter.disclosure_fields is not None:
        return disclosure_fields_problem(counter.disclosure_fields, ctx.cfg)
    return None


def _g_outcome_valid(inv: Invitation, ctx: _Ctx) -> str | None:
    reasons = ctx.cfg.reasons
    outcome = ctx.payload.get("outcome")
    if outcome not in reasons.outcome:
        return f"Выберите итог: {_reason_list(reasons.outcome)}"
    if outcome == "not_matched":
        reason = ctx.payload.get("reason")
        if reason not in reasons.not_matched:
            return f"Без причины закрыть нельзя: {_reason_list(reasons.not_matched)}"
        if reason == "skills_missing" and not ctx.payload.get("skill_ids"):
            return "Отметьте, каких навыков из брифа не хватило"
    return _comment_ok(ctx)


def _g_hire_requires_contact(inv: Invitation, ctx: _Ctx) -> str | None:
    if ctx.payload.get("outcome") == "hired" and inv.contacted_at is None:
        return "Сначала отметьте «Связались» или «Собеседование» — найм без контакта не закрыть"
    return None


def _g_disclosure_missing(inv: Invitation, ctx: _Ctx) -> str | None:
    return "Контакты уже открыты" if inv.contacts_open(ctx.now) else None


def _g_disclosure_active(inv: Invitation, ctx: _Ctx) -> str | None:
    return None if inv.contacts_open(ctx.now) else "Контакты кандидата ещё не открыты"


def _g_not_contacted_yet(inv: Invitation, ctx: _Ctx) -> str | None:
    return "Контакт уже отмечен" if inv.contacted_at is not None else None


def _dispute_blocker(inv: Invitation, now: datetime) -> str | None:
    """Почему «со мной не связались» пока нельзя отправить (None — можно). Один источник правды для guard
    перехода и для флага can_dispute в карточке: иначе кнопка горела сразу после «да», а ответом было 409."""
    if inv.disputed:
        return "Вы уже сообщили, что с вами не связались"
    if inv.contact_due_at is None:
        return "Контакты ещё не открыты"
    # После закрытия итога ждать срока незачем: компания уже решила, что связываться не будет.
    if inv.status == S.ACCEPTED and inv.contacted_at is None and now < inv.contact_due_at:
        return f"У компании ещё есть время связаться с вами — срок истекает {relative(inv.contact_due_at, now)}"
    return None


def _g_dispute_allowed(inv: Invitation, ctx: _Ctx) -> str | None:
    return _dispute_blocker(inv, ctx.now)


def can_dispute(inv: Invitation, clock: Clock | datetime) -> bool:
    """Кандидат может отправить «со мной не связались» прямо сейчас (статус допускает действие и срок контакта прошёл)."""
    if (inv.status, A.DISPUTE_CONTACT, R.CANDIDATE) not in TRANSITIONS:
        return False
    return _dispute_blocker(inv, now_of(clock)) is None


def _g_contact_deadline_passed(inv: Invitation, ctx: _Ctx) -> str | None:
    due = inv.contact_due_at
    if due is None or inv.contacted_at is not None or inv.contact_overdue or inv.disputed or ctx.now < due:
        return "Срок первого контакта не нарушен"
    return None


def outcome_check_at(inv: Invitation, cfg: ContactConfig | None = None) -> datetime | None:
    """Когда спрашивать кандидата «условия совпали?» — через 10 дней после «да»."""
    cfg = cfg or get_contact_config()
    if inv.accepted_at is None:
        return None
    return inv.accepted_at + timedelta(days=cfg.employer.outcome_check_after_days)


def _g_outcome_check_due(inv: Invitation, ctx: _Ctx) -> str | None:
    if inv.outcome_check is not None:
        return "Вы уже ответили на этот вопрос"
    due = outcome_check_at(inv, ctx.cfg)
    if due is None:
        return "Сверка условий доступна только после принятия приглашения"
    if ctx.now < due:
        return f"Сверка условий станет доступна {relative(due, ctx.now)}"
    options = ctx.cfg.reasons.outcome_check
    if ctx.payload.get("answer") not in options:
        return f"Выберите ответ: {_reason_list(options)}"
    return None


def _g_no_stage_yet(inv: Invitation, ctx: _Ctx) -> str | None:
    return "Собеседование уже отмечено" if inv.stage is not None else None


GUARDS: dict[str, Guard] = {
    "before_response_deadline": _g_before_response_deadline,
    "response_deadline_passed": _g_response_deadline_passed,
    "before_counter_deadline": _g_before_counter_deadline,
    "counter_deadline_passed": _g_counter_deadline_passed,
    "consent_given": _g_consent_given,
    "consent_active": _g_consent_active,
    "hire_requires_contact": _g_hire_requires_contact,
    "decline_reason_valid": _g_decline_reason_valid,
    "withdraw_reason_valid": _g_withdraw_reason_valid,
    "counter_decline_reason_valid": _g_counter_decline_reason_valid,
    "counter_round_available": _g_counter_round_available,
    "counter_valid": _g_counter_valid,
    "outcome_valid": _g_outcome_valid,
    "disclosure_missing": _g_disclosure_missing,
    "disclosure_active": _g_disclosure_active,
    "not_contacted_yet": _g_not_contacted_yet,
    "dispute_allowed": _g_dispute_allowed,
    "contact_deadline_passed": _g_contact_deadline_passed,
    "outcome_check_due": _g_outcome_check_due,
    "no_stage_yet": _g_no_stage_yet,
}


# --- side effects -----------------------------------------------------------------------------------

Effect = Callable[[Invitation, _Ctx], None]


def _e_mark_viewed(inv: Invitation, ctx: _Ctx) -> None:
    inv.viewed_at = ctx.at


def _e_return_quota_slot(inv: Invitation, ctx: _Ctx) -> None:
    # Квота считается по статусам (quotas.counts_against_quota); эффект — для уведомления и журнала.
    ctx.effect("quota_slot_returned", invitation_id=inv.id, company_id=inv.company_id)
    ctx.event_payload["quota_slot_returned"] = True


def _e_set_pause(inv: Invitation, ctx: _Ctx) -> None:
    reason = inv.decline_reason if ctx.to == S.DECLINED else inv.counter_decline_reason
    cd = cooldown_after(
        company_id=inv.company_id,
        candidate_id=inv.candidate_id,
        status=ctx.to,
        at=ctx.at,
        decline_reason=reason if ctx.to in (S.DECLINED, S.COUNTER_DECLINED) else None,
        last_salary_to=inv.current_terms.salary_to,
        cfg=ctx.cfg,
    )
    if cd is not None:
        ctx.effect("cooldown", cooldown=cd)
        ctx.event_payload["pause_until"] = cd.until.isoformat()


def _e_set_withdrawal_pause(inv: Invitation, ctx: _Ctx) -> None:
    # DECISIONS 22: отзыв приглашения и отзыв согласия (в т. ч. из «Завершено») ставят паузу пары.
    reason = S.CONSENT_REVOKED if ctx.action == A.REVOKE_CONSENT else S.WITHDRAWN
    cd = cooldown_after_withdrawal(
        company_id=inv.company_id, candidate_id=inv.candidate_id, reason=reason, at=ctx.at, cfg=ctx.cfg
    )
    if cd is not None:
        ctx.effect("cooldown", cooldown=cd)
        ctx.event_payload["pause_until"] = cd.until.isoformat()


def _e_record_withdraw(inv: Invitation, ctx: _Ctx) -> None:
    inv.withdraw_reason = ctx.payload["reason"]
    ctx.event_payload["reason"] = inv.withdraw_reason
    if ctx.payload.get("comment"):
        ctx.event_payload["comment"] = ctx.payload["comment"]


def _e_mark_accepted(inv: Invitation, ctx: _Ctx) -> None:
    inv.accepted_at = ctx.at


def _grant(inv: Invitation, ctx: _Ctx, fields: Iterable[str] | None) -> None:
    inv.disclosure = grant_disclosure(
        candidate_id=inv.candidate_id,
        company_id=inv.company_id,
        basis="invitation",
        basis_id=inv.id,
        at=ctx.at,
        fields=fields,
        cfg=ctx.cfg,
    )
    # «Свяжется до …»: 3 рабочих дня от момента, когда контакты реально открылись.
    inv.contact_due_at = add_business_days(ctx.at, ctx.cfg.employer.first_contact_business_days, ctx.cfg)
    ctx.effect("disclosure_granted", disclosure=inv.disclosure)
    ctx.event_payload["disclosure_fields"] = list(inv.disclosure.fields)
    ctx.event_payload["consent_text_ver"] = inv.disclosure.consent_text_ver
    ctx.event_payload["contact_due_at"] = inv.contact_due_at.isoformat()


def _e_create_disclosure(inv: Invitation, ctx: _Ctx) -> None:
    _grant(inv, ctx, ctx.payload.get("fields"))


def _auto_disclose(counter: CounterOffer | None, cfg: ContactConfig) -> bool:
    """Галочка «раскрыть контакты, если примут»: значение кандидата или по умолчанию из config."""
    if counter is not None and counter.auto_disclose is not None:
        return counter.auto_disclose
    return cfg.counter_offer.auto_disclose_default


def _e_auto_disclose(inv: Invitation, ctx: _Ctx) -> None:
    counter = inv.counter
    if _auto_disclose(counter, ctx.cfg):
        _grant(inv, ctx, counter.disclosure_fields if counter else None)
    else:
        ctx.out.notes.append("Условия приняты. Контакты откроются, когда кандидат подтвердит согласие")
        ctx.event_payload["disclosure_pending"] = True


def _e_record_decline(inv: Invitation, ctx: _Ctx) -> None:
    inv.decline_reason = ctx.payload["reason"]
    inv.decline_comment = ctx.payload.get("comment") or ""
    ctx.event_payload["reason"] = inv.decline_reason
    if inv.decline_comment:
        ctx.event_payload["comment"] = inv.decline_comment
    ctx.effect(
        "feedback",
        target="invitation",
        target_id=inv.id,
        direction="candidate_to_employer",
        reason_code=inv.decline_reason,
        comment=inv.decline_comment,
    )


def _e_record_counter(inv: Invitation, ctx: _Ctx) -> None:
    counter: CounterOffer = ctx.payload["counter"]
    inv.counter = counter
    inv.countered_at = ctx.at
    inv.counter_respond_by = ctx.at + timedelta(days=ctx.cfg.employer.counter_response_days)
    terms = inv.current_terms
    ctx.event_payload.update(
        conditions={
            k: (v.isoformat() if isinstance(v, date) else v) for k, v in counter.conditions().items()
        },
        comment=counter.comment,
        within_range=counter.salary_from is not None and counter.salary_from <= terms.salary_to,
        summary=counter_summary(counter, terms, ctx.at, ctx.cfg),
        counter_respond_by=inv.counter_respond_by.isoformat(),
    )


def _e_new_terms_from_counter(inv: Invitation, ctx: _Ctx) -> None:
    assert inv.counter is not None
    new = terms_after_counter(inv.current_terms, inv.counter, ctx.at)
    inv.terms.append(new)
    ctx.effect("new_terms", terms=new)
    ctx.event_payload["terms_ver"] = new.ver
    ctx.event_payload["terms_sha256"] = new.sha256


def _e_record_counter_decline(inv: Invitation, ctx: _Ctx) -> None:
    inv.counter_decline_reason = ctx.payload["reason"]
    ctx.event_payload["reason"] = inv.counter_decline_reason
    if ctx.payload.get("comment"):
        ctx.event_payload["comment"] = ctx.payload["comment"]


def _e_reliability_counter_unanswered(inv: Invitation, ctx: _Ctx) -> None:
    _reliability(inv, ctx, "counter_unanswered")


def _e_record_outcome(inv: Invitation, ctx: _Ctx) -> None:
    inv.outcome = ctx.payload["outcome"]
    inv.outcome_reason = ctx.payload.get("reason") if inv.outcome == "not_matched" else None
    inv.outcome_skill_ids = tuple(ctx.payload.get("skill_ids") or ())
    inv.closed_at = ctx.at
    ctx.event_payload.update(
        outcome=inv.outcome, reason=inv.outcome_reason, skill_ids=list(inv.outcome_skill_ids)
    )
    # good «итог с причиной» — только если контакт был. Закрыть с любой причиной, так и не связавшись, —
    # это «отзыв после accepted» (bad), а не способ уйти от срока первого контакта (концепция §4.3:
    # против гостинга после раскрытия). Если штраф за «не связались» уже записан или контакты так и
    # не открылись (кандидат не подтвердил согласие), событие не пишем.
    if inv.contacted_at is not None:
        _reliability(inv, ctx, "outcome_reported")
    elif inv.disclosure is not None and inv.contact_penalty is None:
        inv.contact_penalty = "withdrawn_after_accept"
        _reliability(inv, ctx, "withdrawn_after_accept")
    ctx.effect(
        "feedback",
        target="invitation",
        target_id=inv.id,
        direction="employer_to_candidate",
        reason_code=inv.outcome_reason or inv.outcome,
        skill_ids=list(inv.outcome_skill_ids),
        comment=ctx.payload.get("comment") or "",
    )


def _e_revoke_disclosure(inv: Invitation, ctx: _Ctx) -> None:
    if inv.disclosure is not None:
        inv.disclosure = inv.disclosure.revoke(ctx.at)
        ctx.effect("disclosure_revoked", disclosure=inv.disclosure)


def _e_record_contacted(inv: Invitation, ctx: _Ctx) -> None:
    inv.contacted_at = ctx.at
    in_time = inv.contact_due_at is not None and ctx.at <= inv.contact_due_at and not inv.contact_overdue
    ctx.event_payload["in_time"] = in_time
    if in_time:
        _reliability(inv, ctx, "contacted_in_time")


def _e_record_interview(inv: Invitation, ctx: _Ctx) -> None:
    inv.stage = "interview"
    ctx.event_payload["stage"] = inv.stage
    # Собеседование без контакта не бывает: если «Связались» не нажали, отмечаем контакт сейчас —
    # иначе честный работодатель получил бы bad за просрочку, хотя уже провёл собеседование.
    if inv.contacted_at is None:
        _e_record_contacted(inv, ctx)


def _e_record_dispute(inv: Invitation, ctx: _Ctx) -> None:
    inv.disputed = True
    if ctx.payload.get("comment"):
        ctx.event_payload["comment"] = ctx.payload["comment"]
    # Один «не связались» на приглашение: если штраф уже записан (просрочка, закрытие без контакта),
    # второй bad не добавляем.
    if inv.contact_penalty is None:
        inv.contact_penalty = "contact_disputed"
        _reliability(inv, ctx, "contact_disputed")


def _e_record_contact_overdue(inv: Invitation, ctx: _Ctx) -> None:
    inv.contact_overdue = True
    if inv.contact_penalty is None:
        inv.contact_penalty = "contact_overdue"
        _reliability(inv, ctx, "contact_overdue")


_OUTCOME_CHECK_EVENTS = {
    "match": "terms_matched",
    "lower_salary": "terms_lower_salary",
    "no_vacancy": "terms_no_vacancy",
}


def _e_record_outcome_check(inv: Invitation, ctx: _Ctx) -> None:
    inv.outcome_check = ctx.payload["answer"]
    inv.outcome_check_at = ctx.at
    ctx.event_payload["answer"] = inv.outcome_check
    kind = _OUTCOME_CHECK_EVENTS.get(inv.outcome_check)
    if kind:
        _reliability(inv, ctx, kind)


def _business_days_phrase(n: int) -> str:
    return count_words(n, ("рабочего дня", "рабочих дней", "рабочих дней"))


def _e_notify(inv: Invitation, ctx: _Ctx) -> None:
    """Короткие уведомления сторонам (письмо/кабинет/вебхук). Формулировки нейтральны к полу кандидата."""
    cfg = ctx.cfg
    p = inv.pseudonym
    t = inv.current_terms
    msgs: list[tuple[str, str]] = []
    if ctx.action == A.REVOKE_CONSENT:  # из accepted и из closed
        msgs.append(("employer", f"Согласие {p} на передачу контактов отозвано — контакты снова скрыты"))
    elif ctx.to == S.COUNTERED:
        msgs.append(("employer", f"Встречное условие от {p}: {ctx.event_payload.get('summary', '')}"))
    elif ctx.to == S.ACCEPTED:
        if inv.disclosure is not None:
            n = _business_days_phrase(cfg.employer.first_contact_business_days)
            msgs.append(("employer", f"Взаимное «да» с {p}: контакты открыты. Свяжитесь в течение {n}"))
            msgs.append(("candidate", f"Контакт открыт: компания должна связаться с вами в течение {n}"))
        else:
            msgs.append(
                ("candidate", "Компания приняла ваши условия. Подтвердите согласие, чтобы открыть контакты")
            )
    elif ctx.to == S.DECLINED:
        msgs.append(
            ("employer", f"Отказ от {p}: {cfg.reasons.decline[inv.decline_reason or 'other'].lower()}")
        )
    elif ctx.to == S.COUNTER_DECLINED:
        reason = cfg.reasons.counter_decline[inv.counter_decline_reason or "other"].lower()
        msgs.append(("candidate", f"Компания не приняла встречное условие: {reason}"))
    elif ctx.to == S.EXPIRED:
        msgs.append(("candidate", f"Приглашение «{t.title}» истекло"))
        msgs.append(("employer", f"Приглашение для {p} истекло без ответа"))
    elif ctx.to == S.WITHDRAWN:
        msgs.append(
            (
                "candidate",
                f"Компания отозвала приглашение: {cfg.reasons.withdraw[inv.withdraw_reason or 'other'].lower()}",
            )
        )
    for to, text in msgs:
        ctx.effect("notify", to=to, text=text, invitation_id=inv.id)


EFFECTS: dict[str, Effect] = {
    "mark_viewed": _e_mark_viewed,
    "return_quota_slot": _e_return_quota_slot,
    "set_pause": _e_set_pause,
    "set_withdrawal_pause": _e_set_withdrawal_pause,
    "record_withdraw": _e_record_withdraw,
    "mark_accepted": _e_mark_accepted,
    "create_disclosure": _e_create_disclosure,
    "auto_disclose": _e_auto_disclose,
    "record_decline": _e_record_decline,
    "record_counter": _e_record_counter,
    "new_terms_from_counter": _e_new_terms_from_counter,
    "record_counter_decline": _e_record_counter_decline,
    "reliability_counter_unanswered": _e_reliability_counter_unanswered,
    "record_outcome": _e_record_outcome,
    "revoke_disclosure": _e_revoke_disclosure,
    "record_interview": _e_record_interview,
    "record_contacted": _e_record_contacted,
    "record_dispute": _e_record_dispute,
    "record_contact_overdue": _e_record_contact_overdue,
    "record_outcome_check": _e_record_outcome_check,
    "notify": _e_notify,
}


# --- движок -----------------------------------------------------------------------------------------


def _append_event(
    inv: Invitation,
    *,
    from_status: str | None,
    action: str,
    actor: str,
    actor_id: str | None,
    at: datetime,
    payload: Mapping[str, Any],
    cfg: ContactConfig,
) -> InvitationEvent:
    seq = len(inv.events) + 1
    ev = InvitationEvent(
        id=f"{inv.id}-{seq:03d}",
        invitation_id=inv.id,
        seq=seq,
        from_status=from_status,
        to_status=str(inv.status),
        action=str(action),
        actor_role=str(actor),
        actor_id=actor_id,
        at=at,
        payload=dict(payload),
        label=status_label(inv.status, cfg),
    )
    inv.events.append(ev)
    return ev


def _fire(
    inv: Invitation,
    action: A,
    actor: R,
    *,
    now: datetime,
    at: datetime,
    cfg: ContactConfig,
    actor_id: str | None,
    payload: Mapping[str, Any],
) -> Outcome:
    rule = TRANSITIONS.get((inv.status, action, actor))
    if rule is None:
        raise ContactError(
            "transition_not_allowed",
            f"Действие недоступно: приглашение в статусе «{status_label(inv.status, cfg)}»",
        )
    out = Outcome()
    ctx = _Ctx(
        now=now,
        at=at,
        action=action,
        to=S(rule.to),
        cfg=cfg,
        actor=actor,
        actor_id=actor_id,
        payload=payload,
        out=out,
    )
    for name in rule.guards:
        problem = GUARDS[name](inv, ctx)
        if problem:
            raise ContactError(name, problem)
    from_status = str(inv.status)
    # Статус меняем до эффектов «notify», но после доменных: эффекты читают ctx.to, а не inv.status.
    for name in rule.effects:
        if name != "notify":
            EFFECTS[name](inv, ctx)
    inv.status = S(rule.to)
    if "notify" in rule.effects:
        EFFECTS["notify"](inv, ctx)
    out.events.append(
        _append_event(
            inv,
            from_status=from_status,
            action=action,
            actor=actor,
            actor_id=actor_id,
            at=at,
            payload=ctx.event_payload,
            cfg=cfg,
        )
    )
    return out


def refresh(inv: Invitation, clock: Clock | datetime, cfg: ContactConfig | None = None) -> Outcome:
    """Ленивое применение сроков при чтении. Идемпотентно: повторный вызов ничего не добавит.

    Событие датируется самим дедлайном, а не моментом чтения — история не зависит от того, когда её открыли.
    """
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    out = Outcome()
    while True:
        if inv.status in (S.SENT, S.VIEWED) and now >= inv.respond_by:
            at, action = inv.respond_by, A.EXPIRE
        elif (
            inv.status == S.COUNTERED and inv.counter_respond_by is not None and now >= inv.counter_respond_by
        ):
            at, action = inv.counter_respond_by, A.EXPIRE
        elif (
            inv.status == S.ACCEPTED
            and inv.contact_due_at is not None
            and inv.contacted_at is None
            and not inv.contact_overdue
            and not inv.disputed
            and now >= inv.contact_due_at
        ):
            at, action = inv.contact_due_at, A.FLAG_CONTACT_OVERDUE
        else:
            return out
        out.extend(_fire(inv, action, R.SYSTEM, now=now, at=at, cfg=cfg, actor_id=None, payload={}))


def apply_action(
    inv: Invitation,
    action: A | str,
    actor: R | str,
    clock: Clock | datetime,
    *,
    actor_id: str | None = None,
    cfg: ContactConfig | None = None,
    **payload: Any,
) -> Outcome:
    """Действие стороны. Сначала применяются истёкшие сроки, затем проверяется переход по таблице.

    При отказе ContactError.prior содержит события ленивых сроков — их нужно сохранить и в этом случае.
    """
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    try:
        act, who = A(action), R(actor)
    except ValueError:
        # Строка из HTTP-запроса: неизвестное действие — понятный отказ, а не 500.
        raise ContactError("unknown_action", "Неизвестное действие с приглашением") from None
    prior = refresh(inv, now, cfg)
    try:
        out = _fire(inv, act, who, now=now, at=now, cfg=cfg, actor_id=actor_id, payload=payload)
    except ContactError as err:
        err.prior = prior
        raise
    return prior.extend(out)


# --- отправка ---------------------------------------------------------------------------------------


def send_invitation(
    draft: InvitationDraft,
    sender: SenderState,
    candidate: CandidateState,
    clock: Clock | datetime,
    *,
    cfg: ContactConfig | None = None,
    planned: Sequence[SentRecord] = (),
) -> tuple[Invitation, Outcome]:
    """[*] → sent: guards — содержимое, вилка, квота, лимит кандидата, пауза, одно активное на пару,
    карточка открыта или в шортлисте. Нарушение → ContactError с Check (все причины по-русски)."""
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    if draft.company_id != sender.company_id or draft.candidate_id != candidate.candidate_id:
        raise ValueError("Черновик не соответствует отправителю или кандидату")
    response_days = draft.response_days if draft.response_days is not None else cfg.response.default_days
    check = validate_invitation_content(
        title=draft.title,
        description=draft.description,
        contact_method=draft.contact_method,
        contact_value=draft.contact_value,
        response_days=response_days,
        work_format=draft.format,
        cfg=cfg,
    )
    check.merge(
        check_invite(
            sender,
            candidate,
            need_id=draft.need_id,
            salary_from=draft.salary_from,
            salary_to=draft.salary_to,
            salary_type=draft.salary_type,
            is_internship=draft.is_internship,
            now=now,
            cfg=cfg,
            planned=planned,
        )
    )
    if not check.ok:
        raise ContactError("send_rejected", check.errors[0].message, check=check)

    terms = OfferTerms(
        invitation_id=draft.invitation_id,
        ver=1,
        author="employer",
        title=draft.title.strip(),
        description=draft.description.strip(),
        salary_from=draft.salary_from,
        salary_to=draft.salary_to,
        salary_type=draft.salary_type,
        contact_method=draft.contact_method,
        contact_value=draft.contact_value.strip(),
        created_at=now,
        format=draft.format,
        start_date=draft.start_date,
        vacancy_id=draft.vacancy_id,
    )
    fraud = check_text(terms.title, terms.description, cfg=cfg)
    flags = []
    if is_personal_email(sender.email, cfg):
        flags.append("personal_email")
    if "weak_match" in check.warning_codes:
        flags.append("weak_match")
    if fraud.caution:
        flags.append("caution")
    inv = Invitation(
        id=draft.invitation_id,
        company_id=draft.company_id,
        candidate_id=draft.candidate_id,
        pseudonym=candidate.pseudonym,
        status=S.SENT,
        sent_at=now,
        respond_by=now + timedelta(days=response_days),
        terms=[terms],
        need_id=draft.need_id,
        vacancy_id=draft.vacancy_id,
        why_you=tuple(draft.why_you[: cfg.invitation.why_you_max_items]),
        flags=flags,
        caution=tuple(fraud.reasons),
        score_snapshot=draft.score_snapshot,
        rank_at_send=draft.rank_at_send,
    )
    out = Outcome(warnings=[i.message for i in check.warnings], notes=[i.message for i in check.notes])
    out.events.append(
        _append_event(
            inv,
            from_status=None,
            action=A.SEND,
            actor=R.EMPLOYER,
            actor_id=draft.actor_id,
            at=now,
            payload={
                "terms_ver": 1,
                "terms_sha256": terms.sha256,
                "response_days": response_days,
                "respond_by": inv.respond_by.isoformat(),
                "flags": list(flags),
                "caution": list(inv.caution),
            },
            cfg=cfg,
        )
    )
    type_label = cfg.salary.types.get(draft.salary_type, "")
    out.side_effects.append(
        SideEffect("quota_slot_used", {"invitation_id": inv.id, "company_id": inv.company_id})
    )
    out.side_effects.append(
        SideEffect(
            "notify",
            {
                "to": "candidate",
                "text": f"Новое приглашение: {terms.title} · {salary_range(terms.salary_from, terms.salary_to)} {type_label}".rstrip(),
                "invitation_id": inv.id,
            },
        )
    )
    return inv, out


@dataclass
class BatchResult:
    sent: list[Invitation] = field(default_factory=list)
    outcome: Outcome = field(default_factory=Outcome)
    rejected: dict[str, Check] = field(default_factory=dict)


def send_batch(
    items: Sequence[tuple[InvitationDraft, CandidateState]],
    sender: SenderState,
    clock: Clock | datetime,
    *,
    cfg: ContactConfig | None = None,
) -> BatchResult:
    """Пачка из шортлиста (≤ 10): кому можно — отправляем, остальным — понятная причина отказа."""
    cfg = cfg or get_contact_config()
    if len(items) > cfg.quotas.batch_max:
        raise ContactError(
            "batch_too_large", f"В пачке — не больше {cfg.quotas.batch_max} приглашений: выбрано {len(items)}"
        )
    now = now_of(clock)
    result = BatchResult()
    planned: list[SentRecord] = []
    seen: set[str] = set()
    for draft, cand in items:
        if cand.candidate_id in seen:
            continue  # дубль в пачке
        seen.add(cand.candidate_id)
        try:
            inv, out = send_invitation(draft, sender, cand, now, cfg=cfg, planned=planned)
        except ContactError as err:
            result.rejected[cand.candidate_id] = err.check or Check()
            continue
        planned.append(inv.to_sent_record())
        result.sent.append(inv)
        result.outcome.extend(out)
    return result


# --- вспомогательное для UI и фоновой задачи --------------------------------------------------------


def decline_hint(inv: Invitation, reason: str, cfg: ContactConfig | None = None) -> str | None:
    """При причине «зарплата» предлагаем «Да, если…» вместо «Нет» (концепция §4.3)."""
    cfg = cfg or get_contact_config()
    if reason in cfg.reasons.decline_suggest_counter and inv.status == S.VIEWED and inv.counter is None:
        return "Если дело в деньгах, ответьте «Да, если…» и назовите сумму — компания увидит её до раскрытия контактов"
    return None


def withdraw_for_closed_need(
    invitations: Iterable[Invitation],
    clock: Clock | datetime,
    *,
    actor_id: str | None = None,
    cfg: ContactConfig | None = None,
) -> Outcome:
    """Бриф закрыт → все ждущие ответа приглашения отзываются с причиной «позиция закрыта» (spec-contact §2.3)."""
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    out = Outcome()
    for inv in invitations:
        out.extend(refresh(inv, now, cfg))
        if inv.status in PENDING_STATUSES:
            out.extend(
                apply_action(
                    inv, A.WITHDRAW, R.EMPLOYER, now, actor_id=actor_id, cfg=cfg, reason="position_closed"
                )
            )
    return out


@dataclass(frozen=True)
class Reminder:
    kind: str
    to: str  # candidate | employer
    due_at: datetime
    text: str


def due_reminders(
    inv: Invitation, clock: Clock | datetime, sent_kinds: Iterable[str] = (), cfg: ContactConfig | None = None
) -> list[Reminder]:
    """Напоминания, которые пора отправить (фоновая задача раз в 5 минут). Статусы — после refresh()."""
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    done = set(sent_kinds)
    rem = cfg.response.reminders
    found: list[Reminder] = []
    if inv.status in (S.SENT, S.VIEWED):
        last_call = inv.respond_by - timedelta(hours=rem.before_deadline_hours)
        first = inv.sent_at + timedelta(hours=rem.after_sent_hours)
        title = inv.current_terms.title
        # При сроке 3 дня «через 48 ч» совпадает с «за 24 ч до конца» — шлём одно напоминание.
        if first < last_call and now >= first:
            found.append(
                Reminder(
                    "candidate_after_sent",
                    "candidate",
                    first,
                    f"Вас ждёт приглашение «{title}» — ответ нужен {relative(inv.respond_by, now)}",
                )
            )
        if now >= last_call:
            found.append(
                Reminder(
                    "candidate_before_deadline",
                    "candidate",
                    last_call,
                    f"Срок ответа на приглашение «{title}» истекает {relative(inv.respond_by, now)}",
                )
            )
    elif inv.status == S.COUNTERED and inv.counter_respond_by is not None:
        due = inv.counter_respond_by - timedelta(hours=rem.before_deadline_hours)
        if now >= due:
            found.append(
                Reminder(
                    "employer_counter_before_deadline",
                    "employer",
                    due,
                    f"Встречное условие от {inv.pseudonym} ждёт ответа — срок истекает "
                    f"{relative(inv.counter_respond_by, now)}",
                )
            )
    elif inv.status == S.ACCEPTED and inv.contact_due_at is not None and inv.contacted_at is None:
        due = inv.contact_due_at - timedelta(hours=cfg.employer.contact_reminder_before_hours)
        if now >= due and not inv.contact_overdue:
            found.append(
                Reminder(
                    "employer_contact_due",
                    "employer",
                    due,
                    f"Свяжитесь с {inv.pseudonym}: срок истекает {relative(inv.contact_due_at, now)}",
                )
            )
    check_at = outcome_check_at(inv, cfg)
    if (
        inv.status in (S.ACCEPTED, S.CLOSED)
        and check_at is not None
        and inv.outcome_check is None
        and now >= check_at
    ):
        found.append(
            Reminder(
                "candidate_outcome_check",
                "candidate",
                check_at,
                "Условия совпали с приглашением? Ответ поможет другим кандидатам",
            )
        )
    return [r for r in found if r.kind not in done]
