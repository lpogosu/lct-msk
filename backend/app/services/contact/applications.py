"""Отклики кандидата на вакансии [доп.] (концепция §3.1 шаг 13, §3.2 шаг 11; spec-contact §2.3).

submitted (контакты видны сразу: кандидат откликнулся сам и дал отдельное согласие) → viewed →
interested | rejected(причина); кандидат может отозвать отклик; 7 дней без ответа → no_response (bad для R).
Машина состояний — app/contact/application.py; здесь хранение, побочные эффекты и DTO.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.contact.application import Application as DApp
from app.contact.application import ApplicationEvent as DEvent
from app.contact.application import (
    application_status_label,
    apply_application_action,
    refresh_application,
    submit_application,
)
from app.contact.common import ContactError, Outcome
from app.contact.statuses import ApplicationStatus as AS
from app.core.errors import conflict, not_found
from app.models.company import Company
from app.models.contact import Application, ApplicationEvent, Feedback, Vacancy
from app.models.enums import UserRole, UserStatus, VacancyStatus
from app.models.users import User
from app.services.notify import notify_company, notify_user

from . import identity, webhooks
from . import reliability as rel_store
from .common import cfg, contact_error, now, rel
from .profiles import candidate_facts, candidate_for_employer, company_card, domain_disclosure
from .store import (
    CONTACT_KEYS,
    Ctx,
    disclosure_row,
    jsonable,
    revoke_disclosure_rows,
    save_disclosure,
    save_feedback,
    uid,
)
from .vacancies import scores_for, vacancy_base

PENDING = (AS.SUBMITTED, AS.VIEWED)


# --- загрузка и сохранение ---------------------------------------------------------------------


def to_domain(db: Session, row: Application, *, disclosure: Any = False) -> DApp:
    vac = db.get(Vacancy, row.vacancy_id)
    events = [
        DEvent(
            id=f"{row.id}-{e.seq:03d}",
            application_id=str(row.id),
            seq=e.seq,
            from_status=e.from_status,
            to_status=e.to_status,
            action=e.action,
            actor_role=e.actor_role,
            actor_id=str(e.actor_id) if e.actor_id else None,
            at=e.at,
            payload=dict(e.payload or {}),
            label=application_status_label(e.to_status, cfg()),
        )
        for e in db.scalars(
            select(ApplicationEvent).where(ApplicationEvent.application_id == row.id).order_by(ApplicationEvent.seq)
        )
    ]
    disc = domain_disclosure(disclosure_row(db, "application", row.id)) if disclosure is False else disclosure
    assert vac is not None and row.sla_due_at is not None
    return DApp(
        id=str(row.id),
        vacancy_id=str(row.vacancy_id),
        company_id=str(vac.company_id),
        candidate_id=str(row.candidate_id),
        status=AS(row.status),
        submitted_at=row.created_at,
        sla_due_at=row.sla_due_at,
        disclosure=disc,
        score_snapshot=row.score_snapshot or None,
        viewed_at=row.viewed_at,
        answered_at=row.responded_at,
        reject_reason=row.reject_reason,
        reject_comment=row.reject_comment or "",
        events=events,
    )


def _notify(ctx: Ctx, row: Application, app: DApp, ev: DEvent) -> None:
    """Короткие уведомления сторонам. Работодателю — только псевдоним кандидата (DECISIONS 20)."""
    db = ctx.db
    vac = db.get(Vacancy, row.vacancy_id)
    company = db.get(Company, vac.company_id) if vac else None
    if vac is None or company is None:
        return
    title = vac.title
    pseudonym = identity.alias(db, ctx.secret, company.id, row.candidate_id).pseudonym
    if ev.action == "submit":
        notify_company(
            db, company.id, "application", f"Новый отклик от {pseudonym} на вакансию «{title}»",
            entity_type="application", entity_id=row.id,
        )
    elif ev.to_status == AS.INTERESTED and ev.from_status != ev.to_status:
        notify_user(
            db, row.candidate_id, "application",
            f"{company.name or 'Компания'}: есть интерес к вашему отклику на «{title}» — ждите контакта",
            entity_type="application", entity_id=row.id,
        )
    elif ev.to_status == AS.WITHDRAWN and ev.from_status != ev.to_status:
        notify_company(
            db, company.id, "application", f"Отклик {pseudonym} на «{title}» отозван — контакты снова скрыты",
            entity_type="application", entity_id=row.id,
        )
    elif ev.to_status == AS.NO_RESPONSE and ev.from_status != ev.to_status:
        days = cfg().application.response_days
        notify_user(
            db, row.candidate_id, "application",
            f"Компания не ответила на отклик «{title}» за {days} дней — это отразилось на её надёжности",
            entity_type="application", entity_id=row.id,
        )
        notify_company(
            db, company.id, "application",
            f"Отклик {pseudonym} на «{title}» остался без ответа {days} дней — минус к надёжности",
            entity_type="application", entity_id=row.id,
        )
    elif ev.action == "revoke_consent":
        notify_company(
            db, company.id, "application", f"{pseudonym} отозвал(а) согласие — контакты снова скрыты",
            entity_type="application", entity_id=row.id,
        )


def webhook_data(ctx: Ctx, row: Application, app: DApp, ev: DEvent) -> dict[str, Any]:
    """Событие отклика для ATS: без внутренних id кандидата; контакты — только при действующем согласии."""
    company_id = uid(app.company_id)
    assert company_id is not None
    ident = identity.alias(ctx.db, ctx.secret, company_id, row.candidate_id)
    data: dict[str, Any] = {
        "application_id": str(row.id),
        "vacancy_id": str(row.vacancy_id),
        "pseudonym": ident.pseudonym,
        "seq": ev.seq,
        "action": ev.action,
        "from_status": ev.from_status,
        "status": ev.to_status,
        "status_label": ev.label,
        "actor_role": ev.actor_role,
        "at": ev.at.isoformat(),
        "score": (row.score_snapshot or {}).get("score"),
    }
    if app.contacts_visible(now()) and app.disclosure is not None:
        facts = candidate_facts(ctx.db, row.candidate_id)
        data["contacts"] = {k: facts.get(k) for k in CONTACT_KEYS if app.disclosure.allows(k) and facts.get(k)}
    return data


def persist(ctx: Ctx, row: Application, app: DApp, out: Outcome) -> None:
    db = ctx.db
    row.status = str(app.status)
    row.viewed_at = app.viewed_at
    row.responded_at = app.answered_at
    row.reject_reason = app.reject_reason
    row.reject_comment = app.reject_comment or None
    row.updated_at = now()
    for ev in out.events:
        db.add(
            ApplicationEvent(
                application_id=row.id,
                seq=ev.seq,
                from_status=ev.from_status,
                to_status=ev.to_status,
                action=ev.action,
                actor_role=ev.actor_role,
                actor_id=uid(ev.actor_id),
                at=ev.at,
                payload=jsonable(ev.payload),
            )
        )
    db.flush()
    for eff in out.side_effects:
        if eff.kind == "reliability":
            rel_store.record(db, eff.data["event"])
        elif eff.kind == "disclosure_granted":
            d = save_disclosure(db, eff.data["disclosure"])
            if row.consent_id is None:
                row.consent_id = d.consent_id
        elif eff.kind == "disclosure_revoked":
            revoke_disclosure_rows(db, eff.data["disclosure"])
        elif eff.kind == "feedback":
            save_feedback(db, eff.data, candidate_id=row.candidate_id, entity_type="application")
    db.flush()
    for ev in out.events:
        _notify(ctx, row, app, ev)
    company = db.get(Company, uid(app.company_id))
    if company is not None and company.webhook_url:
        for ev in out.events:
            event_type = f"application.{ev.to_status}" if ev.from_status != ev.to_status else f"application.{ev.action}"
            webhooks.enqueue(db, company, event_type, webhook_data(ctx, row, app, ev), ev.id, ev.at)
        ctx.companies.add(company.id)


def refresh_rows(ctx: Ctx, rows: Iterable[Application]) -> None:
    """Ленивое применение срока 7 дней: no_response датируется самим дедлайном (bad для надёжности)."""
    at = now()
    for row in rows:
        if row.status not in PENDING:
            continue
        ctx.db.refresh(row, with_for_update=True)
        if row.status not in PENDING:
            continue
        app = to_domain(ctx.db, row)
        out = refresh_application(app, at, cfg())
        if out.events:
            persist(ctx, row, app, out)
    ctx.db.flush()


def act(ctx: Ctx, row: Application, action: str, actor: str, actor_id: uuid.UUID, **payload: Any) -> Outcome:
    ctx.db.refresh(row, with_for_update=True)  # одно действие за раз: второе увидит уже новый статус
    app = to_domain(ctx.db, row)
    try:
        out = apply_application_action(app, action, actor, now(), actor_id=str(actor_id), cfg=cfg(), **payload)
    except ContactError as err:
        prior = err.prior
        if prior is not None and (prior.events or prior.side_effects):
            persist(ctx, row, app, prior)
            ctx.finish()
        raise contact_error(err) from None
    persist(ctx, row, app, out)
    return out


# --- отклик (кандидат) -------------------------------------------------------------------------


def submit(
    ctx: Ctx, user: User, vacancy_id: uuid.UUID, *, consent: bool, fields: Sequence[str] | None
) -> Application:
    db = ctx.db
    vac = db.get(Vacancy, vacancy_id)
    if vac is None or vac.status != VacancyStatus.published.value:
        raise not_found("vacancy_not_found", "Вакансия не найдена или снята с публикации")
    existing = db.scalar(
        select(Application).where(Application.vacancy_id == vac.id, Application.candidate_id == user.id)
    )
    if existing is not None:
        raise conflict(
            "application_exists",
            "Вы уже откликались на эту вакансию",
            {"application_id": str(existing.id), "status": existing.status},
        )
    from app.models.candidate import CandidateProfile

    profile = db.get(CandidateProfile, user.id)
    if profile is not None and vac.company_id in set(profile.blocked_company_ids or []):
        raise conflict("company_blocked", "Эта компания в вашем блок-листе — уберите её оттуда, чтобы откликнуться")
    if fields is None and profile is not None and profile.field_visibility:
        fields = [k for k, on in profile.field_visibility.items() if on]
    at = now()
    score = scores_for(db, user.id, [vac]).get(vac.id)
    snapshot = (
        {"score": score.score, "parts": score.parts, "spec": score.spec, "grade": score.grade, "category": score.category}
        if score
        else {}
    )
    app_id = uuid.uuid4()
    try:
        app, out = submit_application(
            application_id=str(app_id),
            vacancy_id=str(vac.id),
            company_id=str(vac.company_id),
            candidate_id=str(user.id),
            consent=consent,
            clock=at,
            fields=fields,
            score_snapshot=snapshot,
            cfg=cfg(),
        )
    except ContactError as err:
        raise contact_error(err) from None
    row = Application(
        id=app_id,
        vacancy_id=vac.id,
        candidate_id=user.id,
        status=str(app.status),
        score_snapshot=jsonable(snapshot),
        sla_due_at=app.sla_due_at,
        created_at=at,
        updated_at=at,
    )
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError:
        raise conflict("application_exists", "Вы уже откликались на эту вакансию") from None
    # Псевдоним для этой компании фиксируется при первом показе (подпись в отклике = подпись в подборке)
    identity.alias(db, ctx.secret, vac.company_id, user.id)
    persist(ctx, row, app, out)
    return row


# --- чтение ------------------------------------------------------------------------------------


def get_for_candidate(db: Session, user: User, application_id: uuid.UUID) -> Application:
    row = db.get(Application, application_id)
    if row is None or row.candidate_id != user.id:
        raise not_found("application_not_found", "Отклик не найден")
    return row


def get_for_employer(db: Session, user: User, application_id: uuid.UUID) -> Application:
    row = db.get(Application, application_id)
    vac = db.get(Vacancy, row.vacancy_id) if row else None
    if row is None or vac is None or user.company_id is None or vac.company_id != user.company_id:
        raise not_found("application_not_found", "Отклик не найден")
    return row


def _actions(app: DApp, actor: str) -> list[str]:
    from app.contact.application import TRANSITIONS

    names = {"view": "view", "interest": "interest", "reject": "reject", "withdraw": "withdraw", "revoke_consent": "revoke-consent"}
    return [names[str(a)] for (s, a, r) in TRANSITIONS if s == app.status and r == actor and str(a) in names]


def _timers(app: DApp, at: datetime) -> dict[str, Any]:
    pending = app.status in PENDING
    return {
        "sla_due_at": app.sla_due_at if pending else None,
        "sla_due_text": rel(app.sla_due_at, at) if pending else None,
    }


def employer_card(ctx: Ctx, row: Application, app: DApp | None = None, at: datetime | None = None) -> dict[str, Any]:
    at = at or now()
    app = app or to_domain(ctx.db, row)
    company_id = uid(app.company_id)
    assert company_id is not None
    ident = identity.alias(ctx.db, ctx.secret, company_id, row.candidate_id)
    snap = row.score_snapshot or {}
    return {
        "id": row.id,
        "vacancy_id": row.vacancy_id,
        "pseudonym": ident.pseudonym,
        "candidate_ref": ident.ref,
        "status": str(app.status),
        "status_label": application_status_label(app.status, cfg()),
        "score": snap.get("score"),
        "category": snap.get("category"),
        "submitted_text": rel(app.submitted_at, at),
        "viewed": app.viewed_at is not None,
        "contacts_visible": app.contacts_visible(at),
        "reject_reason": app.reject_reason,
        "reject_reason_label": cfg().reasons.application_reject.get(app.reject_reason) if app.reject_reason else None,
        "timers": _timers(app, at),
        "actions": _actions(app, "employer"),
    }


def employer_detail(ctx: Ctx, row: Application) -> dict[str, Any]:
    at = now()
    app = to_domain(ctx.db, row)
    card = employer_card(ctx, row, app, at)
    vac = ctx.db.get(Vacancy, row.vacancy_id)
    company_id = uid(app.company_id)
    assert company_id is not None and vac is not None
    card["vacancy"] = vacancy_base(vac, at)
    card["score_snapshot"] = row.score_snapshot or None
    card["candidate"] = candidate_for_employer(
        ctx.db,
        candidate_id=row.candidate_id,
        company_id=company_id,
        pseudonym=card["pseudonym"],
        at=at,
        offer_salary_to=vac.salary_to,
        need_id=vac.need_id,
        ref=card.get("candidate_ref"),
    )
    card["events"] = [
        {"seq": e.seq, "at": e.at, "action": e.action, "from_status": e.from_status, "to_status": e.to_status,
         "label": e.label, "actor_role": e.actor_role, "payload": jsonable(dict(e.payload))}
        for e in app.events
    ]
    card["reject_reasons"] = dict(cfg().reasons.application_reject)
    return card


def employer_rows(db: Session, company_id: uuid.UUID, vacancy_id: uuid.UUID | None, status: str | None) -> list[Application]:
    q = select(Application).join(Vacancy, Vacancy.id == Application.vacancy_id).where(Vacancy.company_id == company_id)
    if vacancy_id is not None:
        q = q.where(Application.vacancy_id == vacancy_id)
    if status is not None:
        q = q.where(Application.status == status)
    return list(db.scalars(q.order_by(Application.created_at.desc())))


def sort_by_score(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Отклики ранжируются тем же Score (концепция §3.2 шаг 11): Score ↓, затем новые выше."""
    return sorted(cards, key=lambda c: (-(c["score"] if c["score"] is not None else -1.0), str(c["id"])))


def _feedback(db: Session, row: Application) -> list[dict[str, Any]]:
    from .feedback import reason_label, skill_links

    return [
        {
            "reason": f.reason_code,
            "label": reason_label(f.reason_code, "application"),
            "comment": f.comment,
            "links": skill_links(list(f.skill_ids or [])),
        }
        for f in db.scalars(
            select(Feedback).where(
                Feedback.target_type == "application",
                Feedback.target_id == row.id,
                Feedback.direction == "company_to_candidate",
            )
        )
    ]


def candidate_card(ctx: Ctx, row: Application, app: DApp | None = None, at: datetime | None = None) -> dict[str, Any]:
    at = at or now()
    app = app or to_domain(ctx.db, row)
    vac = ctx.db.get(Vacancy, row.vacancy_id)
    company = ctx.db.get(Company, vac.company_id) if vac else None
    comp = company_card(ctx.db, company, at) if company else None
    return {
        "id": row.id,
        "vacancy": vacancy_base(vac, at) if vac else None,
        "company": {
            "id": comp["id"],
            "name": comp["name"],
            "reliability": comp["reliability"]["display"],
            "r": comp["reliability"]["r"],
        }
        if comp
        else None,
        "status": str(app.status),
        "status_label": application_status_label(app.status, cfg()),
        "submitted_text": rel(app.submitted_at, at),
        "score": (row.score_snapshot or {}).get("score"),
        "contacts_shared": app.contacts_visible(at),
        "timers": _timers(app, at),
        "actions": _actions(app, "candidate"),
    }


def candidate_detail(ctx: Ctx, row: Application) -> dict[str, Any]:
    at = now()
    app = to_domain(ctx.db, row)
    card = candidate_card(ctx, row, app, at)
    card["reject_reason"] = app.reject_reason
    card["reject_reason_label"] = cfg().reasons.application_reject.get(app.reject_reason) if app.reject_reason else None
    card["feedback"] = _feedback(ctx.db, row)
    card["disclosure_fields"] = list(app.disclosure.fields) if app.disclosure else []
    card["events"] = [
        {"seq": e.seq, "at": e.at, "action": e.action, "to_status": e.to_status, "label": e.label, "actor_role": e.actor_role}
        for e in app.events
    ]
    return card


def mark_viewed_by_employer(ctx: Ctx, row: Application, user: User) -> None:
    """Открытие отклика работодателем = «просмотрен» (ответить можно только после просмотра)."""
    refresh_rows(ctx, [row])
    if row.status == AS.SUBMITTED:
        act(ctx, row, "view", "employer", user.id)


def candidate_rows(db: Session, user_id: uuid.UUID) -> list[Application]:
    return list(db.scalars(select(Application).where(Application.candidate_id == user_id).order_by(Application.created_at.desc())))


def is_active_candidate(user: User | None) -> bool:
    return user is not None and user.role == UserRole.candidate.value and user.status == UserStatus.active.value


def reject_payload(reason: str, comment: str | None, skill_ids: Sequence[str] | None) -> Mapping[str, Any]:
    return {"reason": reason, "comment": comment or "", "skill_ids": list(skill_ids or ())}
