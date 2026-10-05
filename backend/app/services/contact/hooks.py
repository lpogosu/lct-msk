"""Отзыв согласия на передачу контактов из журнала согласий (К13, /api/consents/{id}/revoke):
приглашение → consent_revoked (контакты у компании снова скрыты, пауза пары — DECISIONS 22),
отклик → отозван (или петля revoke_consent в финальных статусах). Концепция §3.1 п. 10, §4.3.

Обработчик регистрируется в app.core.consents при импорте пакета app.services.contact.
"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.contact.application import apply_application_action
from app.contact.common import ContactError
from app.contact.invitation import apply_action
from app.contact.statuses import ApplicationStatus as AS
from app.contact.statuses import InvitationStatus as S
from app.core.consents import on_consent_revoked
from app.models.contact import Application, ContactDisclosure, Invitation
from app.models.enums import ConsentPurpose
from app.models.users import Consent

from .common import cfg, now
from .profiles import domain_disclosure
from .store import Ctx, persist, revoking_in_progress, to_domain


@on_consent_revoked
def _on_contact_consent_revoked(db: Session, consent: Consent) -> None:
    if consent.purpose != ConsentPurpose.contact_disclosure.value or revoking_in_progress():
        return
    ctx = Ctx(db=db)
    rows = list(db.scalars(select(ContactDisclosure).where(ContactDisclosure.consent_id == consent.id)))
    for d in rows:
        # Ядро уже погасило запись раскрытия; для перехода движку нужно видеть её действующей «до» отзыва
        active = replace(domain_disclosure(d), revoked_at=None) if d is not None else None
        try:
            if d.basis == "invitation":
                row = db.get(Invitation, d.basis_id)
                if row is None or row.status not in (S.ACCEPTED, S.CLOSED):
                    continue
                inv = to_domain(db, row, disclosure=active)
                out = apply_action(inv, "revoke_consent", "candidate", now(), actor_id=str(consent.user_id), cfg=cfg())
                persist(ctx, row, inv, out)
            elif d.basis == "application":
                from .applications import persist as app_persist
                from .applications import to_domain as app_domain

                arow = db.get(Application, d.basis_id)
                if arow is None:
                    continue
                app = app_domain(db, arow, disclosure=active)
                action = (
                    "revoke_consent" if app.status in (AS.REJECTED, AS.NO_RESPONSE) else "withdraw"
                )
                if app.status == AS.WITHDRAWN:
                    continue
                out = apply_application_action(app, action, "candidate", now(), actor_id=str(consent.user_id), cfg=cfg())
                app_persist(ctx, arow, app, out)
        except ContactError:
            # Переход недоступен (например, уже закрыто без контактов): журнал согласий всё равно отозван
            continue
    db.flush()
