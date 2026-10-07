"""Чат после «Взаимно» (Q&A 07.10, DECISIONS 29): переписка по приглашению или отклику.

Правила доступа (все — по состоянию контакта, не по роли в запросе):
- пока нет взаимного «да» с раскрытием контактов — чата нет ни у кого (403 `chat_unavailable`);
- приглашение «Принято» (отклик «Есть интерес») + действующее согласие → переписка открыта обеим сторонам;
- контакт завершён («Завершено»), согласие при этом действует → обе стороны читают, писать нельзя;
- согласие отозвано → работодатель теряет доступ целиком (403 `chat_closed`), кандидат видит историю без права писать;
- чужой (не участник) получает 404, как и по остальным сущностям.
Текст только, длина и частота — config/contact.yaml → chat. В тексте уведомлений и писем самого сообщения нет.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.errors import ApiError, conflict, forbidden, not_found
from app.mail.sender import Mailer
from app.models.company import Company
from app.models.contact import Application, ApplicationEvent, ContactDisclosure, Invitation, Vacancy
from app.models.integration import ChatMessage
from app.models.users import User
from app.services.contact.common import cfg, now
from app.services.notify import company_members, notify_company, notify_user, send_mail_safe

KINDS = ("invitation", "application")


@dataclass
class Thread:
    kind: str
    ref_id: uuid.UUID
    company_id: uuid.UUID
    candidate_id: uuid.UUID
    role: str  # кем является запрашивающий: employer | candidate
    can_send: bool
    readonly_reason: str | None
    title: str  # как назвать собеседника (для кандидата — компания, для работодателя — псевдоним)


def _disclosure(db: Session, kind: str, ref_id: uuid.UUID) -> ContactDisclosure | None:
    return db.scalar(
        select(ContactDisclosure)
        .where(ContactDisclosure.basis == kind, ContactDisclosure.basis_id == ref_id)
        .order_by(ContactDisclosure.granted_at.desc())
        .limit(1)
    )


def _load(db: Session, kind: str, ref_id: uuid.UUID, user: User) -> tuple[Any, uuid.UUID, uuid.UUID, str]:
    """(строка, company_id, candidate_id, роль). Не участник → 404."""
    if kind == "invitation":
        inv = db.get(Invitation, ref_id)
        if inv is None:
            raise not_found("chat_not_found", "Переписка не найдена")
        company_id, candidate_id, row = inv.company_id, inv.candidate_id, inv
    elif kind == "application":
        app = db.get(Application, ref_id)
        vac = db.get(Vacancy, app.vacancy_id) if app else None
        if app is None or vac is None:
            raise not_found("chat_not_found", "Переписка не найдена")
        company_id, candidate_id, row = vac.company_id, app.candidate_id, app
    else:
        raise not_found("chat_not_found", "Переписка не найдена")
    if user.id == candidate_id:
        return row, company_id, candidate_id, "candidate"
    if user.company_id is not None and user.company_id == company_id and user.role == "employer":
        return row, company_id, candidate_id, "employer"
    raise not_found("chat_not_found", "Переписка не найдена")


def _was_mutual(db: Session, kind: str, row: Any, disc: ContactDisclosure | None) -> bool:
    if disc is None:
        return False
    if kind == "invitation":
        return row.accepted_at is not None
    n = db.scalar(
        select(func.count()).select_from(ApplicationEvent).where(
            ApplicationEvent.application_id == row.id, ApplicationEvent.to_status == "interested"
        )
    )
    return bool(n)


def resolve(db: Session, kind: str, ref_id: uuid.UUID, user: User) -> Thread:
    row, company_id, candidate_id, role = _load(db, kind, ref_id, user)
    disc = _disclosure(db, kind, ref_id)
    active = disc is not None and disc.revoked_at is None
    open_status = "accepted" if kind == "invitation" else "interested"
    if not _was_mutual(db, kind, row, disc):
        raise forbidden("chat_unavailable", "Чат откроется после взаимного «да» и передачи контактов")
    can_send, reason = False, None
    if active and row.status == open_status:
        can_send = True
    elif active:
        reason = "Контакт завершён — переписка доступна только для чтения"
    elif role == "employer":
        raise forbidden("chat_closed", "Кандидат отозвал согласие на передачу контактов — чат закрыт")
    else:
        reason = "Вы отозвали согласие на передачу контактов — переписка закрыта, история доступна только для чтения"
    if role == "candidate":
        company = db.get(Company, company_id)
        title = (company.name if company else None) or "Компания"
    elif kind == "invitation":
        title = row.pseudonym
    else:
        title = "Кандидат по отклику"
    return Thread(kind, ref_id, company_id, candidate_id, role, can_send, reason, title)


def _out(m: ChatMessage, viewer: str) -> dict[str, Any]:
    return {
        "id": m.id,
        "seq": m.seq,
        "from": m.sender_role,
        "mine": m.sender_role == viewer,
        "body": m.body,
        "created_at": m.created_at,
        "read": m.read_at is not None,
    }


def history(db: Session, th: Thread, *, after: int | None = None, limit: int | None = None) -> list[ChatMessage]:
    limit = min(limit or cfg().chat.page_size, cfg().chat.page_size)
    q = select(ChatMessage).where(ChatMessage.kind == th.kind, ChatMessage.ref_id == th.ref_id)
    if after is not None:
        q = q.where(ChatMessage.seq > after)
        return list(db.scalars(q.order_by(ChatMessage.seq).limit(limit)))
    # без курсора — последние limit сообщений в хронологическом порядке
    rows = list(db.scalars(q.order_by(ChatMessage.seq.desc()).limit(limit)))
    return rows[::-1]


def mark_read(db: Session, th: Thread) -> int:
    at = now()
    rows = list(
        db.scalars(
            select(ChatMessage).where(
                ChatMessage.kind == th.kind,
                ChatMessage.ref_id == th.ref_id,
                ChatMessage.sender_role != th.role,
                ChatMessage.read_at.is_(None),
            )
        )
    )
    for m in rows:
        m.read_at = at
    return len(rows)


def thread_view(db: Session, th: Thread, *, after: int | None = None, mark: bool = True) -> dict[str, Any]:
    marked = mark_read(db, th) if mark else 0
    msgs = history(db, th, after=after)
    return {
        "kind": th.kind,
        "ref_id": th.ref_id,
        "title": th.title,
        "state": "open" if th.can_send else "readonly",
        "can_send": th.can_send,
        "readonly_reason": th.readonly_reason,
        "max_len": cfg().chat.max_len,
        "messages": [_out(m, th.role) for m in msgs],
        "marked_read": marked,
    }


def _clean(body: str) -> str:
    text = "\n".join(line.rstrip() for line in (body or "").replace("\r\n", "\n").strip().split("\n"))
    max_len = cfg().chat.max_len
    if not text:
        raise conflict("chat_empty", "Сообщение пустое")
    if len(text) > max_len:
        raise conflict("chat_too_long", f"Сообщение длиннее {max_len} знаков — сократите текст")
    return text


def _email_due(db: Session, th: Thread, sender_role: str, at: datetime) -> bool:
    c = cfg().chat
    if not c.email_notify:
        return False
    since = at - timedelta(minutes=c.email_min_interval_min)
    prev = db.scalar(
        select(func.count()).select_from(ChatMessage).where(
            ChatMessage.kind == th.kind,
            ChatMessage.ref_id == th.ref_id,
            ChatMessage.sender_role == sender_role,
            ChatMessage.created_at > since,
        )
    )
    return not prev


def send(db: Session, th: Thread, user: User, body: str, *, mailer: Mailer | None = None, ip: str | None = None) -> ChatMessage:
    if not th.can_send:
        raise conflict("chat_closed", th.readonly_reason or "Чат закрыт")
    text = _clean(body)
    total = db.scalar(
        select(func.count()).select_from(ChatMessage).where(ChatMessage.kind == th.kind, ChatMessage.ref_id == th.ref_id)
    )
    if (total or 0) >= cfg().chat.thread_max_messages:
        raise conflict("chat_thread_full", "В переписке достигнут предел сообщений — продолжите общение напрямую по контактам")
    at = now()
    email_due = _email_due(db, th, th.role, at)
    msg = ChatMessage(kind=th.kind, ref_id=th.ref_id, sender_role=th.role, sender_id=user.id, body=text, created_at=at)
    db.add(msg)
    db.flush()
    audit(
        db, "chat.message_sent", actor_id=user.id, actor_role=th.role, entity_type=f"chat_{th.kind}",
        entity_id=th.ref_id, ip=ip, length=len(text),
    )
    _notify(db, th, email_due, mailer)
    return msg


def _notify(db: Session, th: Thread, email_due: bool, mailer: Mailer | None) -> None:
    if th.role == "employer":
        company = db.get(Company, th.company_id)
        who = (company.name if company else None) or "Компания"
        notify_user(
            db, th.candidate_id, th.kind, f"Новое сообщение в чате от «{who}»",
            entity_type=th.kind, entity_id=th.ref_id, payload={"chat": True},
        )
        user = db.get(User, th.candidate_id)
        if email_due and user is not None:
            send_mail_safe(mailer, user.email, "Новое сообщение в чате «Взаимно»", f"«{who}» написала вам в чате. Откройте «Взаимно», чтобы ответить.")
    else:
        notify_company(
            db, th.company_id, th.kind, f"Новое сообщение в чате от кандидата {th.title}",
            entity_type=th.kind, entity_id=th.ref_id, payload={"chat": True},
        )
        if email_due:
            for u in company_members(db, th.company_id):
                send_mail_safe(mailer, u.email, "Новое сообщение в чате «Взаимно»", "Кандидат ответил вам в чате. Откройте «Взаимно», чтобы прочитать.")


# --- счётчики и список переписок --------------------------------------------------------------------------


def _my_threads(db: Session, user: User) -> list[tuple[str, uuid.UUID]]:
    out: list[tuple[str, uuid.UUID]] = []
    if user.role == "candidate":
        out += [
            ("invitation", i)
            for i in db.scalars(select(Invitation.id).where(Invitation.candidate_id == user.id, Invitation.accepted_at.is_not(None)))
        ]
        out += [("application", i) for i in db.scalars(select(Application.id).where(Application.candidate_id == user.id))]
    elif user.company_id is not None:
        out += [
            ("invitation", i)
            for i in db.scalars(select(Invitation.id).where(Invitation.company_id == user.company_id, Invitation.accepted_at.is_not(None)))
        ]
        out += [
            ("application", i)
            for i in db.scalars(
                select(Application.id).join(Vacancy, Vacancy.id == Application.vacancy_id).where(Vacancy.company_id == user.company_id)
            )
        ]
    return out


def list_threads(db: Session, user: User) -> list[dict[str, Any]]:
    """Переписки пользователя, где уже есть сообщения или чат открыт. Недоступные (нет «взаимно», отзыв у работодателя) пропускаем."""
    items: list[dict[str, Any]] = []
    for kind, ref in _my_threads(db, user):
        try:
            th = resolve(db, kind, ref, user)
        except ApiError:
            continue
        last = db.scalar(
            select(ChatMessage)
            .where(ChatMessage.kind == kind, ChatMessage.ref_id == ref)
            .order_by(ChatMessage.seq.desc())
            .limit(1)
        )
        if last is None and not th.can_send:
            continue
        unread = db.scalar(
            select(func.count()).select_from(ChatMessage).where(
                ChatMessage.kind == kind, ChatMessage.ref_id == ref, ChatMessage.sender_role != th.role, ChatMessage.read_at.is_(None)
            )
        )
        items.append(
            {
                "kind": kind,
                "ref_id": ref,
                "title": th.title,
                "state": "open" if th.can_send else "readonly",
                "unread": int(unread or 0),
                "last_message": (last.body[:80] if last else None),
                "last_at": last.created_at if last else None,
            }
        )
    items.sort(key=lambda i: i["last_at"].timestamp() if i["last_at"] else 0, reverse=True)
    return items


def unread_total(db: Session, user: User) -> int:
    return sum(i["unread"] for i in list_threads(db, user))


# --- демо: синтетический кандидат отвечает заготовкой ------------------------------------------------------


def demo_reply(db: Session, th: Thread) -> ChatMessage | None:
    """DEMO_MODE: на сообщение работодателя синтетический кандидат (DemoPersona.kind=synthetic) отвечает заготовкой
    из config/contact.yaml → chat.demo_replies (детерминированно: по id переписки и номеру сообщения)."""
    from app.services.demo.models import DemoPersona

    c = cfg().chat
    if not c.demo_reply_enabled or th.role != "employer" or not th.can_send:
        return None
    persona = db.get(DemoPersona, th.candidate_id)
    if persona is None or persona.kind != "synthetic":
        return None
    counts = dict(
        db.execute(
            select(ChatMessage.sender_role, func.count())
            .where(ChatMessage.kind == th.kind, ChatMessage.ref_id == th.ref_id)
            .group_by(ChatMessage.sender_role)
        ).all()
    )
    sent_by_cand, sent_by_emp = counts.get("candidate", 0), counts.get("employer", 0)
    if sent_by_cand >= sent_by_emp:
        return None
    h = int.from_bytes(hashlib.sha256(f"{th.ref_id}:{sent_by_cand}".encode()).digest()[:4], "big")
    reply = c.demo_replies[h % len(c.demo_replies)]
    msg = ChatMessage(kind=th.kind, ref_id=th.ref_id, sender_role="candidate", sender_id=th.candidate_id, body=reply, created_at=now())
    db.add(msg)
    db.flush()
    return msg
