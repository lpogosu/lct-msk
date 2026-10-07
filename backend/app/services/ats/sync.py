"""Исходящая синхронизация с ATS (адаптер «Хантфлоу»): вызывается из слоя контакта после сохранения событий.

Правила:
- ничего не уходит, пока у компании не выбран тип ATS «Хантфлоу»; тип «Вебхук» работает отдельным механизмом (webhooks.py);
- кандидат создаётся в ATS только когда его контакты раскрыты (взаимное «да»), и только раскрытыми полями;
- смена этапа — по событию машины состояний: связались → «Первичный контакт», собеседование → «Интервью», найм → «Нанят»,
  «не сложилось» → «Отказ» с причиной; отзыв согласия → «Отказ» и затирание персональных данных в нашем журнале;
- сбой ATS никогда не ломает действие пользователя: причина остаётся в «Журнале ATS».
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.contact import Application, Invitation, OfferTerms, Vacancy
from app.models.integration import AtsLog
from app.services.contact.common import now
from app.services.contact.profiles import candidate_facts

from .huntflow import AdapterError, HuntflowAdapter, adapter_for, money_text, today_iso

log = logging.getLogger("vzaimno.ats")

CONTACT_KEYS = ("full_name", "email", "phone", "telegram")
REDACTED = "[удалено: согласие отозвано]"


def _adapter(db: Session, company_id: uuid.UUID) -> HuntflowAdapter | None:
    company = db.get(Company, company_id)
    return adapter_for(db, company_id, company.name if company else None)


def _contacts(db: Session, candidate_id: uuid.UUID, disclosure: Any) -> dict[str, Any]:
    facts = candidate_facts(db, candidate_id)
    return {k: facts.get(k) for k in CONTACT_KEYS if disclosure is not None and disclosure.allows(k) and facts.get(k)}


def redact_applicant(db: Session, company_id: uuid.UUID, entity_type: str, entity_id: str) -> None:
    """152-ФЗ: после отзыва согласия персональные данные кандидата стираем из нашего журнала запросов."""
    rows = db.scalars(
        select(AtsLog).where(
            AtsLog.company_id == company_id, AtsLog.entity_type == entity_type, AtsLog.entity_id == entity_id, AtsLog.kind == "applicant.create"
        )
    )
    for r in rows:
        body = dict((r.request or {}).get("body") or {})
        for k in ("first_name", "last_name", "middle_name", "phone", "email"):
            if k in body:
                body[k] = REDACTED
        if "social" in body:
            body["social"] = [{"social_type": "TELEGRAM", "value": REDACTED}]
        r.request = {**(r.request or {}), "body": body}
    db.flush()


def _terms(db: Session, row: Invitation) -> OfferTerms | None:
    return db.scalar(select(OfferTerms).where(OfferTerms.invitation_id == row.id, OfferTerms.ver == row.current_terms_ver))


def _vacancy_key(row: Invitation) -> tuple[str, str]:
    """Вакансия в ATS: опубликованная, иначе по брифу, иначе — отдельная «позиция» под это приглашение."""
    if row.vacancy_id:
        return "vacancy", str(row.vacancy_id)
    if row.need_id:
        return "need", str(row.need_id)
    return "invitation", str(row.id)


def _stage_for(action: str, outcome: str | None) -> str | None:
    if action in ("mark_contacted",):
        return "contacted"
    if action == "mark_interview":
        return "interview"
    if action == "close":
        return "hired" if outcome == "hired" else "rejected"
    if action == "revoke_consent":
        return "rejected"
    return None


def on_invitation_events(db: Session, row: Invitation, inv: Any, events: Iterable[Any]) -> None:
    """events — доменные InvitationEvent, только что записанные движком."""
    events = list(events)
    if not events:
        return
    ad = _adapter(db, row.company_id)
    if ad is None:
        return
    try:
        _invitation_events(db, ad, row, inv, events)
    except AdapterError as e:
        log.info("ATS: %s", e)
    except Exception:  # noqa: BLE001 — синхронизация не должна ломать действие пользователя
        log.exception("ATS: сбой синхронизации приглашения %s", row.id)


def _invitation_events(db: Session, ad: HuntflowAdapter, row: Invitation, inv: Any, events: list[Any]) -> None:
    eid = str(row.id)
    for ev in events:
        applicant = ad.external_id("applicant.create", "invitation", eid)
        if applicant is None:
            if not inv.contacts_open(now()):
                continue  # до раскрытия кандидата в ATS нет: «слепому» кандидату там не место
            t = _terms(db, row)
            vkey = _vacancy_key(row)
            title = t.title if t else "Позиция"
            money = money_text(t.salary_from, t.salary_to, t.salary_net) if t else None
            vacancy = ad.ensure_vacancy(vkey[0], vkey[1], title=title, description=(t.description if t else title), money=money)
            if vacancy is None:
                continue
            contacts = _contacts(db, row.candidate_id, inv.disclosure)
            applicant = ad.ensure_applicant("invitation", eid, contacts, position=title, money=money)
            ad.move("invitation", eid, applicant_id=applicant, vacancy_id=vacancy, stage="new", event_key=f"{ev.id}:new")
        stage = _stage_for(ev.action, row.outcome)
        if stage == "rejected" and ev.action == "revoke_consent" and row.outcome == "hired":
            stage = None  # найм состоялся — этап не откатываем, только стираем ПДн
        if ev.action == "revoke_consent":
            redact_applicant(db, row.company_id, "invitation", eid)
        if stage is None:
            continue
        vkey = _vacancy_key(row)
        vacancy = ad.external_id("vacancy.create", *vkey)
        if vacancy is None:
            continue
        comment, reason = None, None
        if stage == "rejected":
            reason = row.outcome_reason.get("reason") if row.outcome_reason else None
            comment = (
                "Кандидат отозвал согласие на передачу контактов: удалите его персональные данные"
                if ev.action == "revoke_consent"
                else f"Не сложилось: {reason or 'без причины'}"
            )
        ad.move(
            "invitation", eid, applicant_id=applicant, vacancy_id=vacancy, stage=stage, event_key=str(ev.id), comment=comment,
            reason=reason, employment_date=today_iso() if stage == "hired" else None,
        )


APPLICATION_STAGES = {"interest": "contacted", "reject": "rejected", "withdraw": "rejected", "revoke_consent": "rejected"}


def on_application_events(db: Session, row: Application, app: Any, events: Iterable[Any]) -> None:
    events = list(events)
    if not events:
        return
    vac = db.get(Vacancy, row.vacancy_id)
    if vac is None:
        return
    ad = _adapter(db, vac.company_id)
    if ad is None:
        return
    try:
        eid = str(row.id)
        money = money_text(vac.salary_from, vac.salary_to, vac.salary_net)
        for ev in events:
            applicant = ad.external_id("applicant.create", "application", eid)
            if applicant is None:
                if not app.contacts_visible(now()):
                    continue
                vacancy = ad.ensure_vacancy("vacancy", str(vac.id), title=vac.title, description=vac.description, money=money)
                contacts = _contacts(db, row.candidate_id, app.disclosure)
                applicant = ad.ensure_applicant("application", eid, contacts, position=vac.title, money=money)
                ad.move("application", eid, applicant_id=applicant, vacancy_id=vacancy, stage="new", event_key=f"{ev.id}:new")
            stage = APPLICATION_STAGES.get(ev.action)
            vacancy_ext = ad.external_id("vacancy.create", "vacancy", str(vac.id))
            if stage is None or vacancy_ext is None:
                continue
            comment = None
            if stage == "rejected":
                comment = (
                    "Кандидат отозвал отклик или согласие: удалите его персональные данные"
                    if ev.action in ("withdraw", "revoke_consent")
                    else f"Отказ: {row.reject_reason or 'без причины'}"
                )
            ad.move("application", eid, applicant_id=applicant, vacancy_id=vacancy_ext, stage=stage, event_key=str(ev.id), comment=comment,
                    reason=row.reject_reason)
            if ev.action in ("withdraw", "revoke_consent"):
                redact_applicant(db, vac.company_id, "application", eid)
    except AdapterError as e:
        log.info("ATS: %s", e)
    except Exception:  # noqa: BLE001
        log.exception("ATS: сбой синхронизации отклика %s", row.id)


def on_vacancy_published(db: Session, vac: Vacancy) -> None:
    ad = _adapter(db, vac.company_id)
    if ad is None:
        return
    try:
        ad.ensure_vacancy("vacancy", str(vac.id), title=vac.title, description=vac.description, money=money_text(vac.salary_from, vac.salary_to, vac.salary_net))
    except AdapterError as e:
        log.info("ATS: %s", e)
    except Exception:  # noqa: BLE001
        log.exception("ATS: сбой отправки вакансии %s", vac.id)


def touch(db: Session, model: Any, row_id: uuid.UUID) -> None:
    """Сдвинуть updated_at, чтобы pull-API с `updated_since` увидел изменение, не меняющее поля строки (отзыв согласия)."""
    db.execute(update(model).where(model.id == row_id).values(updated_at=now()))
