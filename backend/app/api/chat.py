"""Чат после «Взаимно» (Q&A 07.10, DECISIONS 29): переписка по приглашению или отклику.

Доступ — только участникам и только после взаимного «да» с раскрытием контактов; правила — в app/services/chat.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbSession, LimiterDep, MailerDep, MetaDep, SettingsDep
from app.core.errors import error_responses
from app.services import chat as svc
from app.services.contact.common import cfg, check_rate

router = APIRouter(prefix="/chat", tags=["Чат"], responses=error_responses(400, 401, 403, 404, 409, 422, 429))

Kind = Literal["invitation", "application"]


class ChatMessageOut(BaseModel):
    id: uuid.UUID
    seq: int = Field(description="Порядковый номер: курсор `after` для «только новые»")
    from_: Literal["employer", "candidate"] = Field(alias="from", description="Кто написал")
    mine: bool = Field(description="Сообщение текущего пользователя")
    body: str
    created_at: datetime
    read: bool = Field(description="Прочитано собеседником")

    model_config = {"populate_by_name": True}


class ChatThreadOut(BaseModel):
    kind: Kind
    ref_id: uuid.UUID
    title: str = Field(description="Собеседник: компания (кандидату) или псевдоним (работодателю)")
    state: Literal["open", "readonly"]
    can_send: bool
    readonly_reason: str | None = None
    max_len: int
    messages: list[ChatMessageOut]
    marked_read: int = Field(description="Сколько сообщений собеседника отмечено прочитанными этим запросом")


class ChatSendIn(BaseModel):
    body: str = Field(description="Текст сообщения (только текст, длина — config/contact.yaml → chat.max_len)", max_length=5000)


class ChatThreadItem(BaseModel):
    kind: Kind
    ref_id: uuid.UUID
    title: str
    state: Literal["open", "readonly"]
    unread: int
    last_message: str | None = None
    last_at: datetime | None = None


class ChatThreadsOut(BaseModel):
    items: list[ChatThreadItem]
    unread_total: int


class ChatUnreadOut(BaseModel):
    unread_total: int


@router.get(
    "/threads",
    response_model=ChatThreadsOut,
    summary="Мои переписки",
    description="Переписки с открытым чатом или с историей: непрочитанные, последнее сообщение. Для счётчика в навигации.",
)
def threads(user: CurrentUser, db: DbSession) -> dict[str, Any]:
    items = svc.list_threads(db, user)
    return {"items": items, "unread_total": sum(i["unread"] for i in items)}


@router.get("/unread", response_model=ChatUnreadOut, summary="Непрочитанные сообщения (счётчик)", description="Сколько сообщений собеседников ещё не прочитано во всех доступных переписках.")
def unread(user: CurrentUser, db: DbSession) -> dict[str, int]:
    return {"unread_total": svc.unread_total(db, user)}


@router.get(
    "/{kind}/{ref_id}",
    response_model=ChatThreadOut,
    summary="Переписка",
    description=(
        "История (последние сообщения; `after` — только новые) и состояние чата. Открытие отмечает сообщения собеседника "
        "прочитанными (`mark=false` — не отмечать). 403 `chat_unavailable` — «взаимного да» ещё нет; 403 `chat_closed` — "
        "кандидат отозвал согласие (работодателю чат закрыт, кандидат видит историю в режиме чтения); 404 — вы не участник."
    ),
)
def get_thread(
    kind: Kind,
    ref_id: uuid.UUID,
    user: CurrentUser,
    db: DbSession,
    after: Annotated[int | None, Query(ge=0, description="Только сообщения с `seq` больше этого")] = None,
    mark: Annotated[bool, Query(description="Отметить сообщения собеседника прочитанными")] = True,
) -> dict[str, Any]:
    th = svc.resolve(db, kind, ref_id, user)
    out = svc.thread_view(db, th, after=after, mark=mark)
    db.commit()
    return out


@router.post(
    "/{kind}/{ref_id}/messages",
    response_model=ChatThreadOut,
    status_code=status.HTTP_201_CREATED,
    summary="Написать сообщение",
    description=(
        "Только текст, не длиннее `chat.max_len`; не чаще `chat.rate_per_minute` в минуту. Собеседнику уходит уведомление "
        "(и письмо без текста сообщения). 409 `chat_closed` — контакт завершён или согласие отозвано; `chat_too_long`, `chat_empty`."
    ),
)
def post_message(
    kind: Kind,
    ref_id: uuid.UUID,
    body: ChatSendIn,
    user: CurrentUser,
    db: DbSession,
    limiter: LimiterDep,
    mailer: MailerDep,
    settings: SettingsDep,
    meta: MetaDep,
) -> dict[str, Any]:
    th = svc.resolve(db, kind, ref_id, user)
    check_rate(limiter, "chat_send", user.id, cfg().chat.rate_per_minute)
    svc.send(db, th, user, body.body, mailer=mailer, ip=meta.ip)
    if settings.demo_mode:
        svc.demo_reply(db, th)
    db.commit()
    return svc.thread_view(db, th, mark=True)
