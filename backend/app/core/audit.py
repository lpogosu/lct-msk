"""Запись в журнал аудита (вход, токены, согласия, раскрытия). ПДн в payload не кладём —
только идентификаторы и коды причин."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import clock
from app.models.users import AuditLog


def audit(
    db: Session,
    action: str,
    *,
    actor_id: uuid.UUID | None = None,
    actor_role: str | None = None,
    entity_type: str | None = None,
    entity_id: Any = None,
    ip: str | None = None,
    **payload: Any,
) -> AuditLog:
    entry = AuditLog(
        at=clock.now(),
        action=action,
        actor_user_id=actor_id,
        actor_role=actor_role,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        ip=ip,
        payload={k: (str(v) if isinstance(v, uuid.UUID) else v) for k, v in payload.items()},
    )
    db.add(entry)
    return entry
