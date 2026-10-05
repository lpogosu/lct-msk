"""Автоответчик синтетических кандидатов (G2, DECISIONS п. 4): жюри не должно «висеть» без ответов.

Через 30–90 с после отправки синтетический кандидат отвечает на приглашение по правилам:

- «Да» — если верхняя граница вилки не ниже его минимума (в «на руки») и формат подходит;
- «Да, если: от min» — если вилка ниже минимума, но минимум не выше 1,2·верхней границы;
- «Нет» — иначе, причина берётся из персоны кандидата (demo_personas.profile.decline_reason).
  Исключение: подходит зарплата, но не формат — честная причина «формат».

Решение — чистая функция `decide` (тесты на контрольные случаи); `tick` берёт созревшие приглашения и отвечает через
тот же слой, что HTTP-роуты (app.services.demo.responder). Отвечает только на приглашения, отправленные ПОСЛЕ сборки
сценария (DemoState.scenario_built_at): заготовки «отправлено» и «встречное» остаются как есть. Человеческих кандидатов
(Анна, Илья, ступенчатые аккаунты) автоответчик не трогает никогда.
"""

from __future__ import annotations

import hashlib
import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.configs import load_config
from app.models.candidate import CandidateProfile
from app.models.contact import Invitation
from app.models.users import User
from app.services.demo import responder
from app.services.demo.cfg import section
from app.services.demo.models import DemoPersona
from app.services.demo.state import get_state

Kind = Literal["accept", "counter", "decline"]


@dataclass(frozen=True)
class Decision:
    kind: Kind
    reason: str | None = None  # только для decline
    salary_from: int | None = None  # только для counter
    why: str = ""  # для журнала и тестов


def _ndfl() -> float:
    return float(load_config("scoring")["U"]["ndfl_rate"])


def to_net(amount: int, salary_net: bool) -> float:
    """Верхняя граница вилки «на руки»: до НДФЛ → за вычетом налога (ставка — config/scoring.yaml → U.ndfl_rate)."""
    return float(amount) if salary_net else float(amount) * (1.0 - _ndfl())


def round_up(value: float, step: int) -> int:
    return int(math.ceil(value / step) * step)


def decide(
    terms: responder.Terms,
    *,
    salary_min: int | None,
    formats: list[str] | tuple[str, ...],
    persona_reason: str,
    ratio_max: float | None = None,
    step: int | None = None,
    max_ratio_to: float | None = None,
) -> Decision:
    """Решение по вилке и формату. salary_min — «на руки»; пустой минимум и пустой список форматов — «всё равно»."""
    cfg = section("autoresponder")
    ratio_max = float(cfg["counter_ratio_max"]) if ratio_max is None else ratio_max
    step = responder.counter_step() if step is None else step
    max_ratio_to = responder.counter_max_ratio() if max_ratio_to is None else max_ratio_to
    to = to_net(terms.salary_to, terms.salary_net)
    fmt_ok = terms.format is None or not formats or terms.format in formats
    if salary_min is None or to >= salary_min:
        if fmt_ok:
            return Decision("accept", why="вилка и формат подходят")
        return Decision("decline", reason="format", why="вилка подходит, формат нет")
    if salary_min <= ratio_max * to:
        # «Да, если: от min» — в валюте вилки приглашения; потолок встречного — 1,3·to (config/contact.yaml)
        raw = salary_min if terms.salary_net else salary_min / (1.0 - _ndfl())
        x = round_up(raw, step)
        ceiling = int(max_ratio_to * terms.salary_to) // step * step
        x = min(x, ceiling)
        if x > terms.salary_to:
            return Decision("counter", salary_from=x, why="минимум выше вилки, но в пределах 1,2×")
    return Decision("decline", reason=persona_reason, why="минимум намного выше вилки")


def delay_s(invitation_id: uuid.UUID) -> int:
    """Задержка ответа 30–90 с: детерминированно от id приглашения (без глобального random)."""
    cfg = section("autoresponder")
    lo, hi = int(cfg["delay_min_s"]), int(cfg["delay_max_s"])
    h = int.from_bytes(hashlib.sha256(invitation_id.bytes).digest()[:4], "big")
    return lo + h % (hi - lo + 1)


@dataclass
class TickResult:
    answered: int = 0
    accepted: int = 0
    countered: int = 0
    declined: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "answered": self.answered,
            "accepted": self.accepted,
            "countered": self.countered,
            "declined": self.declined,
            "skipped": self.skipped,
            "errors": self.errors,
        }


def due_invitations(db: Session, now: datetime, *, limit: int | None = None) -> list[tuple[Invitation, DemoPersona]]:
    """Приглашения синтетическим кандидатам, у которых истекла задержка ответа (и которые отправлены после сборки мира)."""
    cfg = section("autoresponder")
    built = get_state(db).scenario_built_at
    q = (
        select(Invitation, DemoPersona)
        .join(DemoPersona, DemoPersona.user_id == Invitation.candidate_id)
        .where(DemoPersona.kind == "synthetic", Invitation.status.in_(("sent", "viewed")))
        .order_by(Invitation.created_at)
    )
    if built is not None:
        q = q.where(Invitation.created_at > built)
    rows = db.execute(q.limit(int(limit or cfg["batch_limit"]) * 4)).all()
    due = [(inv, p) for inv, p in rows if inv.created_at + timedelta(seconds=delay_s(inv.id)) <= now]
    return due[: int(limit or cfg["batch_limit"])]


def tick(db: Session, *, secret: str = "", now: datetime | None = None, force: bool = False) -> TickResult:
    """Один проход автоответчика. force — ответить сразу, не дожидаясь задержки (кнопка «ответить сейчас», тесты)."""
    out = TickResult()
    state = get_state(db)
    if not state.autoresponder and not force:
        return out
    now = now or clock.now()
    if force:
        built = state.scenario_built_at
        q = (
            select(Invitation, DemoPersona)
            .join(DemoPersona, DemoPersona.user_id == Invitation.candidate_id)
            .where(DemoPersona.kind == "synthetic", Invitation.status.in_(("sent", "viewed")))
        )
        if built is not None:
            q = q.where(Invitation.created_at > built)
        pairs = list(db.execute(q.order_by(Invitation.created_at).limit(int(section("autoresponder")["batch_limit"]))).all())
    else:
        pairs = due_invitations(db, now)
    for inv, persona in pairs:
        user = db.get(User, inv.candidate_id)
        prof = db.get(CandidateProfile, inv.candidate_id)
        if user is None or prof is None or not user.is_active:
            out.skipped += 1
            continue
        try:
            terms = responder.current_terms(db, inv)
        except LookupError as e:
            out.errors.append(str(e))
            continue
        d = decide(
            terms,
            salary_min=prof.salary_min,
            formats=list(prof.formats or ()),
            persona_reason=str((persona.profile or {}).get("decline_reason") or "other"),
        )
        comment = str(section("autoresponder").get("decline_comment") or "")
        if d.kind == "accept":
            ok, err = responder.safe(responder.accept, db, user, inv.id, secret=secret)
        elif d.kind == "counter":
            text = str(section("autoresponder")["counter_comment"])
            ok, err = responder.safe(
                responder.counter, db, user, inv.id, salary_from=d.salary_from, comment=text, secret=secret
            )
            if not ok:  # встречное недопустимо (потолок, шаг) → честный отказ по зарплате
                db.rollback()
                ok, err = responder.safe(
                    responder.decline, db, user, inv.id, reason="salary", comment=comment, secret=secret
                )
                d = Decision("decline", reason="salary", why="встречное недопустимо")
        else:
            ok, err = responder.safe(
                responder.decline, db, user, inv.id, reason=str(d.reason), comment=comment, secret=secret
            )
        if not ok:
            db.rollback()
            out.errors.append(f"{inv.id}: {err}")
            continue
        out.answered += 1
        if d.kind == "accept":
            out.accepted += 1
        elif d.kind == "counter":
            out.countered += 1
        else:
            out.declined += 1
    return out
