"""Хранение доменной машины приглашения (app/contact/invitation.py) в БД: загрузка, сохранение, побочные
эффекты (пауза пары, надёжность, раскрытие и журнал согласий, обратная связь, уведомления, вебхуки).

Правило слоя: решения принимает движок (таблица переходов, guards, сроки), здесь — только перенос состояния.
Сроки применяются лениво при чтении (refresh_rows) и датируются самим дедлайном (DECISIONS 19: часы общие).
"""

from __future__ import annotations

import contextvars
import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.common import Outcome
from app.contact.disclosure import ContactDisclosure as DDisclosure
from app.contact.invitation import Invitation as DInv
from app.contact.invitation import InvitationEvent as DEvent
from app.contact.invitation import due_reminders, refresh, status_label
from app.contact.quotas import Cooldown as DCooldown
from app.contact.statuses import PENDING_STATUSES
from app.contact.statuses import InvitationStatus as S
from app.contact.terms import CounterOffer
from app.contact.terms import OfferTerms as DTerms
from app.core.consents import grant_consent, revoke_consent
from app.mail.sender import Mailer
from app.models.company import Company
from app.models.contact import (
    ContactDisclosure,
    Cooldown,
    Feedback,
    Invitation,
    InvitationEvent,
    OfferTerms,
    OutcomeCheck,
)
from app.models.enums import ConsentPurpose
from app.models.users import Consent
from app.services.notify import notify_company, notify_user

from . import reliability as rel_store
from . import webhooks
from .common import cfg, now
from .profiles import candidate_facts, domain_disclosure

# Отзыв согласия, начатый самим модулем контакта: обработчик app.core.consents не должен повторять переход
_REVOKING: contextvars.ContextVar[bool] = contextvars.ContextVar("contact_revoking", default=False)


def revoking_in_progress() -> bool:
    return _REVOKING.get()


@dataclass
class Ctx:
    """Контекст одного запроса: сессия, почта, секрет псевдонимов; какие компании ждут доставки вебхуков."""

    db: Session
    mailer: Mailer | None = None
    secret: str = ""
    companies: set[uuid.UUID] = field(default_factory=set)

    def finish(self) -> None:
        """Зафиксировать транзакцию и отправить созревшие вебхуки затронутых компаний (после commit)."""
        self.db.commit()
        if not self.companies:
            return
        for company_id in sorted(self.companies, key=str):
            webhooks.deliver_due(self.db, company_id)
        self.companies.clear()
        self.db.commit()


def uid(v: Any) -> uuid.UUID | None:
    if v is None:
        return None
    return v if isinstance(v, uuid.UUID) else uuid.UUID(str(v))


def jsonable(v: Any) -> Any:
    if isinstance(v, Mapping):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set | frozenset):
        return [jsonable(x) for x in v]
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, uuid.UUID):
        return str(v)
    return v


# --- снимки условий и встречное ---------------------------------------------------------------------


def terms_from_row(t: OfferTerms, vacancy_id: uuid.UUID | None) -> DTerms:
    cm = t.contact_method or {}
    return DTerms(
        invitation_id=str(t.invitation_id),
        ver=t.ver,
        author=t.author,
        title=t.title,
        description=t.description,
        salary_from=t.salary_from,
        salary_to=t.salary_to,
        salary_type="net" if t.salary_net else "gross",
        contact_method=str(cm.get("method", "")),
        contact_value=str(cm.get("value", "")),
        created_at=t.created_at,
        format=t.format,
        start_date=t.start_date,
        vacancy_id=str(vacancy_id) if vacancy_id else None,
        conditions=tuple(sorted((str(k), str(v)) for k, v in (t.conditions or {}).items())),
        sha256=t.sha256,
    )


def terms_to_row(t: DTerms) -> OfferTerms:
    return OfferTerms(
        invitation_id=uuid.UUID(t.invitation_id),
        ver=t.ver,
        author=t.author,
        title=t.title,
        description=t.description,
        salary_from=t.salary_from,
        salary_to=t.salary_to,
        salary_net=t.salary_type == "net",
        format=t.format,
        start_date=t.start_date,
        contact_method={"method": t.contact_method, "value": t.contact_value},
        conditions=dict(t.conditions),
        sha256=t.sha256,
        created_at=t.created_at,
    )


def counter_to_dict(c: CounterOffer | None) -> dict[str, Any] | None:
    if c is None:
        return None
    return {
        "salary_from": c.salary_from,
        "format": c.format,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "comment": c.comment,
        "auto_disclose": c.auto_disclose,
        "disclosure_fields": list(c.disclosure_fields) if c.disclosure_fields is not None else None,
    }


def counter_from_dict(d: Mapping[str, Any] | None) -> CounterOffer | None:
    if not d:
        return None
    sd = d.get("start_date")
    fields = d.get("disclosure_fields")
    return CounterOffer(
        salary_from=d.get("salary_from"),
        format=d.get("format"),
        start_date=date.fromisoformat(sd) if sd else None,
        comment=d.get("comment") or "",
        auto_disclose=d.get("auto_disclose"),
        disclosure_fields=tuple(fields) if fields is not None else None,
    )


# --- загрузка -------------------------------------------------------------------------------------------


def disclosure_row(db: Session, basis: str, basis_id: uuid.UUID) -> ContactDisclosure | None:
    return db.scalar(
        select(ContactDisclosure)
        .where(ContactDisclosure.basis == basis, ContactDisclosure.basis_id == basis_id)
        .order_by(ContactDisclosure.granted_at.desc())
        .limit(1)
    )


def to_domain(db: Session, row: Invitation, *, disclosure: DDisclosure | None | bool = False) -> DInv:
    """ORM → доменное приглашение. disclosure=False — взять из БД; иначе — подставить переданное."""
    terms = [
        terms_from_row(t, row.vacancy_id)
        for t in db.scalars(select(OfferTerms).where(OfferTerms.invitation_id == row.id).order_by(OfferTerms.ver))
    ]
    events = [
        DEvent(
            id=f"{row.id}-{e.seq:03d}",
            invitation_id=str(row.id),
            seq=e.seq,
            from_status=e.from_status,
            to_status=e.to_status,
            action=e.action,
            actor_role=e.actor_role,
            actor_id=str(e.actor_id) if e.actor_id else None,
            at=e.at,
            payload=dict(e.payload or {}),
            label=status_label(e.to_status, cfg()),
        )
        for e in db.scalars(
            select(InvitationEvent).where(InvitationEvent.invitation_id == row.id).order_by(InvitationEvent.seq)
        )
    ]
    disc = domain_disclosure(disclosure_row(db, "invitation", row.id)) if disclosure is False else disclosure
    oc = db.get(OutcomeCheck, row.id)
    reason = row.outcome_reason or {}
    return DInv(
        id=str(row.id),
        company_id=str(row.company_id),
        candidate_id=str(row.candidate_id),
        pseudonym=row.pseudonym,
        status=S(row.status),
        sent_at=row.created_at,
        respond_by=row.respond_by,
        terms=terms,
        need_id=str(row.need_id) if row.need_id else None,
        vacancy_id=str(row.vacancy_id) if row.vacancy_id else None,
        why_you=tuple(row.why_you or ()),
        flags=list(row.flags or []),
        caution=tuple(row.caution or ()),
        score_snapshot=row.score_snapshot or None,
        rank_at_send=row.rank_at_send,
        viewed_at=row.viewed_at,
        counter=counter_from_dict(row.counter),
        countered_at=row.countered_at,
        counter_respond_by=row.counter_respond_by,
        accepted_at=row.accepted_at,
        disclosure=disc if isinstance(disc, DDisclosure) else None,
        contact_due_at=row.contact_due_at,
        contacted_at=row.contacted_at,
        contact_overdue=bool(row.contact_overdue),
        disputed=row.disputed_at is not None,
        contact_penalty=row.contact_penalty,
        decline_reason=row.decline_reason,
        decline_comment=row.decline_comment or "",
        withdraw_reason=row.withdraw_reason,
        counter_decline_reason=row.counter_decline_reason,
        stage=row.stage,
        outcome=row.outcome,
        outcome_reason=reason.get("reason"),
        outcome_skill_ids=tuple(reason.get("skill_ids") or ()),
        closed_at=row.closed_at,
        outcome_check=oc.answer if oc else None,
        outcome_check_at=oc.answered_at if oc else None,
        events=events,
    )


# --- сохранение -----------------------------------------------------------------------------------------


def _copy_back(db: Session, row: Invitation, inv: DInv, comment: str | None) -> None:
    row.status = str(inv.status)
    row.viewed_at = inv.viewed_at
    row.counter = counter_to_dict(inv.counter)
    row.countered_at = inv.countered_at
    row.counter_respond_by = inv.counter_respond_by
    row.accepted_at = inv.accepted_at
    row.contact_due_at = inv.contact_due_at
    row.contacted_at = inv.contacted_at
    row.contact_overdue = inv.contact_overdue
    row.contact_penalty = inv.contact_penalty
    if inv.disputed and row.disputed_at is None:
        row.disputed_at = now()
    row.decline_reason = inv.decline_reason
    row.decline_comment = inv.decline_comment or None
    row.withdraw_reason = inv.withdraw_reason
    row.counter_decline_reason = inv.counter_decline_reason
    row.stage = inv.stage
    row.outcome = inv.outcome
    if inv.outcome is not None:
        row.outcome_reason = {
            "reason": inv.outcome_reason,
            "skill_ids": list(inv.outcome_skill_ids),
            "comment": comment if comment is not None else (row.outcome_reason or {}).get("comment"),
        }
    row.closed_at = inv.closed_at
    row.current_terms_ver = inv.current_terms.ver
    if inv.status in (S.ACCEPTED, S.DECLINED, S.COUNTERED) and row.responded_at is None:
        row.responded_at = now()
    if inv.outcome_check is not None:
        oc = db.get(OutcomeCheck, row.id)
        if oc is None:
            asked = (
                inv.accepted_at + timedelta(days=cfg().employer.outcome_check_after_days)
                if inv.accepted_at
                else now()
            )
            oc = OutcomeCheck(invitation_id=row.id, asked_at=asked)
            db.add(oc)
        oc.answer = inv.outcome_check
        oc.answered_at = inv.outcome_check_at
        # Ответ уже есть — напоминать незачем, и закрытое приглашение больше не нужно пересчитывать на чтении
        if OUTCOME_CHECK_REMINDER not in (row.reminders_sent or []):
            row.reminders_sent = [*(row.reminders_sent or []), OUTCOME_CHECK_REMINDER]


def persist(ctx: Ctx, row: Invitation, inv: DInv, out: Outcome, *, comment: str | None = None) -> None:
    """Сохранить результат движка: поля, новые события, побочные эффекты, вебхуки."""
    db = ctx.db
    _copy_back(db, row, inv, comment)
    for ev in out.events:
        db.add(
            InvitationEvent(
                invitation_id=row.id,
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
        _apply_effect(ctx, row, inv, eff.kind, eff.data)
    db.flush()
    company = db.get(Company, row.company_id)
    if company is not None and company.webhook_url:
        for ev in out.events:
            webhooks.enqueue(db, company, webhook_type(ev), webhook_data(ctx, row, inv, ev), ev.id, ev.at)
        ctx.companies.add(company.id)
    if out.events:
        from app.services.ats import sync as ats_sync  # локально: ats импортирует слой контакта

        ats_sync.on_invitation_events(db, row, inv, out.events)


def webhook_type(ev: DEvent) -> str:
    """invitation.accepted / invitation.countered / … ; петли без смены статуса — по действию."""
    if ev.from_status != ev.to_status:
        return f"invitation.{ev.to_status}"
    return f"invitation.{ev.action}"


CONTACT_KEYS = ("full_name", "email", "phone", "telegram")


def webhook_data(ctx: Ctx, row: Invitation, inv: DInv, ev: DEvent) -> dict[str, Any]:
    """Событие для ATS. Без внутренних id кандидата (DECISIONS 20); контакты — только при активном согласии."""
    t = inv.current_terms
    data: dict[str, Any] = {
        "invitation_id": str(row.id),
        "pseudonym": row.pseudonym,
        "need_id": str(row.need_id) if row.need_id else None,
        "vacancy_id": str(row.vacancy_id) if row.vacancy_id else None,
        "seq": ev.seq,
        "action": ev.action,
        "from_status": ev.from_status,
        "status": ev.to_status,
        "status_label": ev.label,
        "actor_role": ev.actor_role,
        "at": ev.at.isoformat(),
        "terms": {
            "ver": t.ver,
            "title": t.title,
            "salary_from": t.salary_from,
            "salary_to": t.salary_to,
            "salary_type": t.salary_type,
            "format": t.format,
            "sha256": t.sha256,
        },
    }
    if inv.contacts_open(now()) and inv.disclosure is not None:
        facts = candidate_facts(ctx.db, row.candidate_id)
        data["contacts"] = {k: facts.get(k) for k in CONTACT_KEYS if inv.disclosure.allows(k) and facts.get(k)}
    return data


def _apply_effect(ctx: Ctx, row: Invitation, inv: DInv, kind: str, data: Mapping[str, Any]) -> None:
    db = ctx.db
    if kind == "cooldown":
        save_cooldown(db, data["cooldown"], source_invitation_id=row.id)
    elif kind == "reliability":
        rel_store.record(db, data["event"])
    elif kind == "disclosure_granted":
        save_disclosure(db, data["disclosure"])
    elif kind == "disclosure_revoked":
        revoke_disclosure_rows(db, data["disclosure"])
        row.updated_at = now()  # pull-API ATS (updated_since) должен увидеть пропажу контактов
    elif kind == "new_terms":
        db.add(terms_to_row(data["terms"]))
    elif kind == "feedback":
        save_feedback(db, data, candidate_id=row.candidate_id, entity_type="invitation")
    elif kind == "notify":
        text = str(data.get("text", ""))
        if data.get("to") == "candidate":
            notify_user(db, row.candidate_id, "invitation", text, entity_type="invitation", entity_id=row.id)
        else:
            notify_company(db, row.company_id, "invitation", text, entity_type="invitation", entity_id=row.id)
    # quota_slot_used / quota_slot_returned: квота считается по статусам приглашений, хранить нечего


def save_cooldown(db: Session, cd: DCooldown, *, source_invitation_id: uuid.UUID | None) -> Cooldown:
    key = (uid(cd.company_id), uid(cd.candidate_id))
    row = db.get(Cooldown, key)
    if row is None:
        row = Cooldown(company_id=key[0], candidate_id=key[1], until=cd.until, reason=cd.reason)
        db.add(row)
    elif row.is_active and row.until > cd.until:
        return row  # действующая пауза длиннее — не укорачиваем
    row.until = cd.until
    row.reason = cd.reason
    row.salary_unlock_from = cd.salary_unlock_from
    row.decline_reason = cd.decline_reason
    row.source_invitation_id = source_invitation_id
    row.is_active = True
    return row


def save_disclosure(db: Session, d: DDisclosure) -> ContactDisclosure:
    """Отдельное согласие 152-ФЗ: запись журнала согласий + ContactDisclosure, связанные consent_id."""
    candidate_id = uid(d.candidate_id)
    assert candidate_id is not None
    consent = grant_consent(
        db,
        candidate_id,
        ConsentPurpose.contact_disclosure,
        scope={"company_id": d.company_id, "basis": d.basis, "basis_id": d.basis_id, "fields": list(d.fields)},
    )
    row = ContactDisclosure(
        candidate_id=candidate_id,
        company_id=uid(d.company_id),
        basis=d.basis,
        basis_id=uid(d.basis_id),
        fields=list(d.fields),
        consent_id=consent.id,
        consent_text_ver=d.consent_text_ver,
        granted_at=d.granted_at,
    )
    db.add(row)
    return row


def revoke_disclosure_rows(db: Session, d: DDisclosure) -> None:
    """Контакты снова скрыты: гасим ContactDisclosure и связанную запись журнала согласий."""
    rows = list(
        db.scalars(
            select(ContactDisclosure).where(
                ContactDisclosure.basis == d.basis,
                ContactDisclosure.basis_id == uid(d.basis_id),
                ContactDisclosure.revoked_at.is_(None),
            )
        )
    )
    consent_ids = [r.consent_id for r in rows if r.consent_id]
    for r in rows:
        r.revoked_at = d.revoked_at or now()
    db.flush()
    token = _REVOKING.set(True)
    try:
        for cid in consent_ids:
            consent = db.get(Consent, cid)
            if consent is not None and consent.revoked_at is None:
                revoke_consent(db, consent)
    finally:
        _REVOKING.reset(token)


def save_feedback(db: Session, data: Mapping[str, Any], *, candidate_id: uuid.UUID, entity_type: str) -> Feedback:
    direction = str(data.get("direction", ""))
    # Направление в словаре хранения: candidate_to_company | company_to_candidate
    stored_direction = "company_to_candidate" if direction.startswith("employer") else "candidate_to_company"
    row = Feedback(
        target_type=str(data.get("target", entity_type)),
        target_id=uid(data.get("target_id")),
        direction=stored_direction,
        reason_code=str(data.get("reason_code") or "other"),
        skill_ids=list(data.get("skill_ids") or []),
        comment=(data.get("comment") or None),
        created_at=now(),
    )
    db.add(row)
    if stored_direction == "company_to_candidate":
        from .feedback import feedback_message

        text, links = feedback_message(row.reason_code, row.skill_ids, entity_type)
        notify_user(
            db,
            candidate_id,
            "invitation" if entity_type == "invitation" else "application",
            text,
            entity_type=entity_type,
            entity_id=row.target_id,
            payload={"links": links, "reason": row.reason_code},
        )
    return row


# --- ленивые сроки и напоминания -------------------------------------------------------------------


OUTCOME_CHECK_REMINDER = "candidate_outcome_check"


def needs_refresh(row: Invitation) -> bool:
    if row.status in PENDING_STATUSES:
        return True
    if row.status == S.ACCEPTED:
        return True
    if row.status == S.CLOSED:
        return OUTCOME_CHECK_REMINDER not in (row.reminders_sent or [])
    return False


def refresh_rows(ctx: Ctx, rows: Iterable[Invitation]) -> None:
    """Применить истёкшие сроки (событие датируется дедлайном) и разослать созревшие напоминания."""
    at = now()
    c = cfg()
    # Строки блокируются в одном порядке (по id): два параллельных чтения списка с пересекающимися приглашениями
    # иначе берут блокировки навстречу друг другу и получают deadlock (нагрузочный тест, docs/LOAD_TEST.md)
    for row in sorted(rows, key=lambda r: str(r.id)):
        if not needs_refresh(row):
            continue
        # Пробный прогон без блокировки: чтение списка почти всегда ничего не меняет (срок не наступил, напоминаний нет),
        # и блокировать «на всякий случай» каждую ожидающую строку на каждом GET незачем
        probe = to_domain(ctx.db, row)
        probe_out = refresh(probe, at, c)
        if not (probe_out.events or probe_out.side_effects or due_reminders(probe, at, list(row.reminders_sent or []), c)):
            continue
        # Блокировка строки: два одновременных чтения не применят один и тот же срок дважды
        ctx.db.refresh(row, with_for_update=True)
        if not needs_refresh(row):
            continue
        inv = to_domain(ctx.db, row)
        out = refresh(inv, at, c)
        if out.events or out.side_effects:
            persist(ctx, row, inv, out)
        sent = list(row.reminders_sent or [])
        for rem in due_reminders(inv, at, sent, c):
            if rem.to == "candidate":
                notify_user(ctx.db, row.candidate_id, "invitation", rem.text, entity_type="invitation", entity_id=row.id)
            else:
                notify_company(ctx.db, row.company_id, "invitation", rem.text, entity_type="invitation", entity_id=row.id)
            sent.append(rem.kind)
        if sent != list(row.reminders_sent or []):
            row.reminders_sent = sent
    ctx.db.flush()
