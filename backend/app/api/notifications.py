"""Уведомления в кабинете: приглашения, отклики, задачи — список, счётчики непрочитанных, отметка «прочитано».

Каждый видит только свои уведомления (чужое неотличимо от несуществующего — 404). Тексты готовые и русские,
даты — относительные (DECISIONS 9); работодателю — только псевдоним кандидата (DECISIONS 20).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbSession
from app.core.access import get_owned_or_404
from app.core.errors import error_responses
from app.models.contact import Notification
from app.services import notify
from app.services.contact.common import cfg, now, rel
from app.services.contact.schemas import (
    NotificationListOut,
    NotificationOut,
    ReadAllIn,
    ReadAllOut,
    UnreadOut,
)

router = APIRouter(prefix="/notifications", tags=["Уведомления"], responses=error_responses())

Kind = Literal["invitation", "application", "task", "company", "system"]


def _out(n: Notification) -> dict[str, Any]:
    at = now()
    return {
        "id": n.id,
        "kind": n.kind,
        "kind_label": cfg().notifications.kinds.get(n.kind, n.kind),
        "message": n.message,
        "entity_type": n.entity_type,
        "entity_id": n.entity_id,
        "payload": n.payload or {},
        "created_at": n.created_at,
        "created_text": rel(n.created_at, at) or "",
        "read": n.read_at is not None,
    }


def _unread(db: DbSession, user_id: uuid.UUID) -> dict[str, Any]:
    by_kind = notify.unread_counts(db, user_id)
    return {"total": sum(by_kind.values()), "by_kind": by_kind}


@router.get(
    "",
    response_model=NotificationListOut,
    summary="Мои уведомления",
    description="Последние уведомления (новые сверху) и счётчики непрочитанных по видам. Фильтры: `kind`, `unread_only`.",
)
def list_notifications(
    user: CurrentUser,
    db: DbSession,
    kind: Annotated[Kind | None, Query(description="Вид уведомлений")] = None,
    unread_only: Annotated[bool, Query(description="Только непрочитанные")] = False,
    limit: Annotated[int, Query(ge=1, le=200, description="Сколько вернуть")] = 50,
) -> dict[str, Any]:
    rows = notify.list_for(
        db, user.id, unread_only=unread_only, kind=kind, limit=min(limit, cfg().notifications.list_limit)
    )
    return {"items": [_out(r) for r in rows], "unread": _unread(db, user.id)}


@router.get(
    "/unread",
    response_model=UnreadOut,
    summary="Счётчики непрочитанных",
    description="Число непрочитанных уведомлений всего и по видам — для бейджей в меню.",
)
def unread(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    return _unread(db, user.id)


@router.post(
    "/read-all",
    response_model=ReadAllOut,
    summary="Отметить все прочитанными",
    description="Отмечает прочитанными все уведомления (или только одного вида — `kind`).",
)
def read_all(body: ReadAllIn, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    n = notify.mark_all_read(db, user.id, body.kind)
    db.commit()
    return {"marked": n, "unread": _unread(db, user.id)}


@router.post(
    "/{notification_id}/read",
    response_model=NotificationOut,
    summary="Отметить прочитанным",
    description="Отмечает одно своё уведомление прочитанным. 404 — уведомления нет или оно чужое.",
)
def read_one(notification_id: uuid.UUID, user: CurrentUser, db: DbSession) -> dict[str, Any]:
    row = get_owned_or_404(db, Notification, notification_id, user, message="Уведомление не найдено")
    notify.mark_read(db, row)
    db.commit()
    return _out(row)
