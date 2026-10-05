"""Отклик кандидата на опубликованную вакансию (доп. функционал; концепция §3.1 п. 13, spec-contact §2.3).

submitted (контакты видны сразу — кандидат откликнулся сам и дал отдельное согласие) → viewed →
interested | rejected(причина); кандидат может отозвать отклик (в том числе после «Есть интерес»);
через 7 дней без ответа — no_response, это bad для надёжности компании. Ответ в срок — good.
После отказа или молчания кандидат может отозвать согласие (петля revoke_consent, 152-ФЗ).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from .clock import Clock, now_of
from .common import ContactError, Outcome, Rule, SideEffect
from .disclosure import ContactDisclosure, disclosure_fields_problem, grant_disclosure
from .reliability import ReliabilityEvent
from .settings import ContactConfig, get_contact_config
from .statuses import Actor as R
from .statuses import ApplicationAction as A
from .statuses import ApplicationStatus as S


@dataclass(frozen=True)
class ApplicationEvent:
    id: str
    application_id: str
    seq: int
    from_status: str | None
    to_status: str
    action: str
    actor_role: str
    actor_id: str | None
    at: datetime
    payload: Mapping[str, Any]
    label: str


@dataclass
class Application:
    id: str
    vacancy_id: str
    company_id: str
    candidate_id: str
    status: S
    submitted_at: datetime
    sla_due_at: datetime
    disclosure: ContactDisclosure | None = None
    score_snapshot: Mapping[str, Any] | None = None
    viewed_at: datetime | None = None
    answered_at: datetime | None = None
    reject_reason: str | None = None
    reject_comment: str = ""
    events: list[ApplicationEvent] = field(default_factory=list)

    def status_label(self, cfg: ContactConfig | None = None) -> str:
        return application_status_label(self.status, cfg)

    def contacts_visible(self, at: datetime) -> bool:
        """Контакты видны работодателю, пока действует согласие, данное при отклике."""
        return self.disclosure is not None and self.disclosure.is_active(at)


def application_status_label(status: str, cfg: ContactConfig | None = None) -> str:
    cfg = cfg or get_contact_config()
    return cfg.status_labels.application[str(status)]


TRANSITIONS: dict[tuple[S, A, R], Rule] = {
    (S.SUBMITTED, A.VIEW, R.EMPLOYER): Rule(S.VIEWED, ("before_sla",), ("mark_viewed",)),
    (S.VIEWED, A.INTEREST, R.EMPLOYER): Rule(S.INTERESTED, ("before_sla",), ("mark_answered",)),
    (S.VIEWED, A.REJECT, R.EMPLOYER): Rule(
        S.REJECTED, ("before_sla", "reject_reason_valid"), ("mark_answered", "record_reject")
    ),
    (S.SUBMITTED, A.WITHDRAW, R.CANDIDATE): Rule(S.WITHDRAWN, (), ("revoke_disclosure",)),
    (S.VIEWED, A.WITHDRAW, R.CANDIDATE): Rule(S.WITHDRAWN, (), ("revoke_disclosure",)),
    # Кандидат может передумать и после «Есть интерес» (spec-contact §2.3: «может перевести в withdrawn»).
    (S.INTERESTED, A.WITHDRAW, R.CANDIDATE): Rule(S.WITHDRAWN, (), ("revoke_disclosure",)),
    (S.SUBMITTED, A.EXPIRE, R.SYSTEM): Rule(S.NO_RESPONSE, ("sla_passed",), ("reliability_no_response",)),
    (S.VIEWED, A.EXPIRE, R.SYSTEM): Rule(S.NO_RESPONSE, ("sla_passed",), ("reliability_no_response",)),
    # После отказа или молчания компании контакты у неё остаются, пока кандидат не отзовёт согласие:
    # отзыв возможен в любой момент (152-ФЗ), статус при этом не меняется.
    (S.REJECTED, A.REVOKE_CONSENT, R.CANDIDATE): Rule(
        S.REJECTED, ("consent_active",), ("revoke_disclosure",)
    ),
    (S.NO_RESPONSE, A.REVOKE_CONSENT, R.CANDIDATE): Rule(
        S.NO_RESPONSE, ("consent_active",), ("revoke_disclosure",)
    ),
}


@dataclass
class _Ctx:
    now: datetime
    at: datetime
    cfg: ContactConfig
    payload: Mapping[str, Any]
    out: Outcome
    event_payload: dict[str, Any] = field(default_factory=dict)


def _reliability(app: Application, ctx: _Ctx, kind: str) -> None:
    ev = ReliabilityEvent(
        company_id=app.company_id, kind=kind, at=ctx.at, candidate_id=app.candidate_id, application_id=app.id
    )
    ctx.out.side_effects.append(SideEffect("reliability", {"event": ev}))


def _g_before_sla(app: Application, ctx: _Ctx) -> str | None:
    return None if ctx.now < app.sla_due_at else "Срок ответа на отклик истёк"


def _g_sla_passed(app: Application, ctx: _Ctx) -> str | None:
    return None if ctx.now >= app.sla_due_at else "Срок ответа на отклик ещё не истёк"


def _g_reject_reason_valid(app: Application, ctx: _Ctx) -> str | None:
    options = ctx.cfg.reasons.application_reject
    if ctx.payload.get("reason") not in options:
        return "Укажите причину отказа: " + ", ".join(v.lower() for v in options.values())
    limit = ctx.cfg.counter_offer.comment_max_len
    if len(ctx.payload.get("comment") or "") > limit:
        return f"Комментарий — не длиннее {limit} символов"
    return None


def _g_consent_active(app: Application, ctx: _Ctx) -> str | None:
    return None if app.contacts_visible(ctx.now) else "Согласие на передачу контактов уже отозвано"


GUARDS: dict[str, Callable[[Application, _Ctx], str | None]] = {
    "before_sla": _g_before_sla,
    "sla_passed": _g_sla_passed,
    "reject_reason_valid": _g_reject_reason_valid,
    "consent_active": _g_consent_active,
}


def _e_mark_viewed(app: Application, ctx: _Ctx) -> None:
    app.viewed_at = ctx.at


def _e_mark_answered(app: Application, ctx: _Ctx) -> None:
    app.answered_at = ctx.at
    _reliability(app, ctx, "application_answered")


def _e_record_reject(app: Application, ctx: _Ctx) -> None:
    app.reject_reason = ctx.payload["reason"]
    app.reject_comment = ctx.payload.get("comment") or ""
    ctx.event_payload["reason"] = app.reject_reason
    ctx.out.side_effects.append(
        SideEffect(
            "feedback",
            {
                "target": "application",
                "target_id": app.id,
                "direction": "employer_to_candidate",
                "reason_code": app.reject_reason,
                "skill_ids": list(ctx.payload.get("skill_ids") or ()),
                "comment": app.reject_comment,
            },
        )
    )


def _e_revoke_disclosure(app: Application, ctx: _Ctx) -> None:
    # Отозванный отклик — кандидат больше не хочет, чтобы у компании были его контакты.
    if app.disclosure is not None:
        app.disclosure = app.disclosure.revoke(ctx.at)
        ctx.out.side_effects.append(SideEffect("disclosure_revoked", {"disclosure": app.disclosure}))


def _e_reliability_no_response(app: Application, ctx: _Ctx) -> None:
    _reliability(app, ctx, "application_no_response")


EFFECTS: dict[str, Callable[[Application, _Ctx], None]] = {
    "mark_viewed": _e_mark_viewed,
    "mark_answered": _e_mark_answered,
    "record_reject": _e_record_reject,
    "revoke_disclosure": _e_revoke_disclosure,
    "reliability_no_response": _e_reliability_no_response,
}


def _append_event(
    app: Application,
    from_status: str | None,
    action: str,
    actor: str,
    actor_id: str | None,
    at: datetime,
    payload: Mapping[str, Any],
    cfg: ContactConfig,
) -> ApplicationEvent:
    seq = len(app.events) + 1
    ev = ApplicationEvent(
        id=f"{app.id}-{seq:03d}",
        application_id=app.id,
        seq=seq,
        from_status=from_status,
        to_status=str(app.status),
        action=str(action),
        actor_role=str(actor),
        actor_id=actor_id,
        at=at,
        payload=dict(payload),
        label=application_status_label(app.status, cfg),
    )
    app.events.append(ev)
    return ev


def _fire(
    app: Application,
    action: A,
    actor: R,
    *,
    now: datetime,
    at: datetime,
    cfg: ContactConfig,
    actor_id: str | None,
    payload: Mapping[str, Any],
) -> Outcome:
    rule = TRANSITIONS.get((app.status, action, actor))
    if rule is None:
        raise ContactError(
            "transition_not_allowed",
            f"Действие недоступно: отклик в статусе «{application_status_label(app.status, cfg)}»",
        )
    out = Outcome()
    ctx = _Ctx(now=now, at=at, cfg=cfg, payload=payload, out=out)
    for name in rule.guards:
        problem = GUARDS[name](app, ctx)
        if problem:
            raise ContactError(name, problem)
    from_status = str(app.status)
    for name in rule.effects:
        EFFECTS[name](app, ctx)
    app.status = S(rule.to)
    out.events.append(_append_event(app, from_status, action, actor, actor_id, at, ctx.event_payload, cfg))
    return out


def submit_application(
    *,
    application_id: str,
    vacancy_id: str,
    company_id: str,
    candidate_id: str,
    consent: bool,
    clock: Clock | datetime,
    fields: Iterable[str] | str | None = None,
    score_snapshot: Mapping[str, Any] | None = None,
    cfg: ContactConfig | None = None,
) -> tuple[Application, Outcome]:
    """Отклик с отдельной галочкой согласия на передачу контактов: без неё отклик не отправляется."""
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    if consent is not True:
        raise ContactError(
            "consent_required", "Чтобы откликнуться, отметьте согласие на передачу контактов этой компании"
        )
    problem = disclosure_fields_problem(fields, cfg)
    if problem:
        raise ContactError("consent_fields", problem)
    disclosure = grant_disclosure(
        candidate_id=candidate_id,
        company_id=company_id,
        basis="application",
        basis_id=application_id,
        at=now,
        fields=fields,
        cfg=cfg,
    )
    app = Application(
        id=application_id,
        vacancy_id=vacancy_id,
        company_id=company_id,
        candidate_id=candidate_id,
        status=S.SUBMITTED,
        submitted_at=now,
        sla_due_at=now + timedelta(days=cfg.application.response_days),
        disclosure=disclosure,
        score_snapshot=score_snapshot,
    )
    out = Outcome()
    out.side_effects.append(SideEffect("disclosure_granted", {"disclosure": disclosure}))
    payload = {"sla_due_at": app.sla_due_at.isoformat(), "disclosure_fields": list(disclosure.fields)}
    out.events.append(_append_event(app, None, A.SUBMIT, R.CANDIDATE, candidate_id, now, payload, cfg))
    return app, out


def refresh_application(
    app: Application, clock: Clock | datetime, cfg: ContactConfig | None = None
) -> Outcome:
    """Ленивое применение срока: 7 дней без ответа → no_response (событие датируется дедлайном)."""
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    if app.status in (S.SUBMITTED, S.VIEWED) and now >= app.sla_due_at:
        return _fire(app, A.EXPIRE, R.SYSTEM, now=now, at=app.sla_due_at, cfg=cfg, actor_id=None, payload={})
    return Outcome()


def apply_application_action(
    app: Application,
    action: A | str,
    actor: R | str,
    clock: Clock | datetime,
    *,
    actor_id: str | None = None,
    cfg: ContactConfig | None = None,
    **payload: Any,
) -> Outcome:
    cfg = cfg or get_contact_config()
    now = now_of(clock)
    try:
        act, who = A(action), R(actor)
    except ValueError:
        raise ContactError("unknown_action", "Неизвестное действие с откликом") from None
    prior = refresh_application(app, now, cfg)
    try:
        out = _fire(app, act, who, now=now, at=now, cfg=cfg, actor_id=actor_id, payload=payload)
    except ContactError as err:
        err.prior = prior
        raise
    return prior.extend(out)
