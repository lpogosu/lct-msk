"""Настоящие сессии адаптивного теста для синтетических кандидатов.

Не повторяем движок: сессия создаётся, задания выдаются и проверяются теми же сервисами, что и в HTTP-API
(app.services.testing.sessions): экземпляры берутся из пула и «сгорают», ключи проверяет семейство, трасса уровня
и решение пишутся как у человека — поэтому в лаборатории (Л1) у демо-кандидатов есть настоящие трассы.
Отвечает симулянт с латентной истиной (eval/population.py): верно с вероятностью модели ответа 3PL от его уровня
по модулю и сложности семейства (app.testing.simulate.SimulatedExaminee), с шумом клонов σ_inst.

Время ответа — 25–90 с на задание (config/demo.yaml): через общие часы платформы, поэтому детекторы «быстро и верно»
не срабатывают на симулянте, а сессия не оказывается «в будущем» (вызывающий ставит часы в прошлое).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.core.clock import clock
from app.models.users import User
from app.services.demo.cfg import section
from app.services.testing import sessions
from app.services.testing.catalog import family_metas
from app.services.testing.keystore import decrypt_key
from app.testing.cat import FamilyMeta
from app.testing.irt import load_scale
from app.testing.session import SessionStatus
from app.testing.simulate import SimulatedExaminee

OPEN = (SessionStatus.CREATED, SessionStatus.IN_PROGRESS, SessionStatus.PAUSED)


def wrong_answer(key: Any, rendered: dict[str, Any]) -> Any:
    """Заведомо неверный ответ в формате задания (как в тестах API тестирования)."""
    mode = rendered.get("answer_mode") or "choice"
    options = list(rendered.get("options") or ())
    lines = list(rendered.get("lines") or ())
    if mode == "choice" and options:
        return next((o["id"] for o in options if str(o["id"]) != str(key)), options[0]["id"])
    if mode == "line_choice" and isinstance(key, dict):
        n = max(1, len(lines))
        line = int(key["line"]) % n + 1 if n > 1 else int(key["line"])
        fix = key["fix"] if n > 1 else next((o["id"] for o in options if o["id"] != key["fix"]), key["fix"])
        return {"line": line, "fix": fix}
    if mode == "order" and isinstance(key, list | tuple):
        rev = list(reversed(key))
        return rev if rev != list(key) else list(key[1:]) + list(key[:1])
    return "заведомо-неверный-ответ"


@dataclass
class Trace:
    """Итог прогона: цепочка сессий (основная + доборы) и что в ней произошло."""

    session_ids: list[uuid.UUID] = field(default_factory=list)
    answered: int = 0
    status: str = ""
    decision: str | None = None
    open_pending_seq: int | None = None  # задание выдано, ответа нет (stop_after)

    @property
    def main_id(self) -> uuid.UUID:
        return self.session_ids[0]


def _meta_by_id(db: Session, spec: str) -> dict[str, FamilyMeta]:
    return {m.id: m for m in family_metas(db, spec, clock.now())}


def _answer_for(
    examinee: SimulatedExaminee, meta: FamilyMeta | None, spec: str, item_rendered: dict[str, Any], key: Any,
    rng: random.Random,
) -> Any:
    scale = load_scale()
    delta = rng.gauss(0.0, scale.sigma_inst) if scale.sigma_inst > 0 else 0.0
    p = examinee.p_correct(meta, spec, scale, delta) if meta is not None else 0.5
    return key if rng.random() < p else wrong_answer(key, item_rendered)


def _think(rng: random.Random, limit_s: float | None) -> None:
    c = section("population")
    lo, hi = float(c["answer_seconds_min"]), float(c["answer_seconds_max"])
    if limit_s:
        hi = min(hi, max(lo, limit_s * 0.9))
    clock.advance(rng.uniform(lo, hi))


def run_session(
    db: Session,
    user: User,
    *,
    spec: str,
    claimed_grade: str,
    examinee: SimulatedExaminee,
    rng: random.Random,
    stop_after: int | None = None,
    issue_pending: bool = True,
    extensions: str = "auto",
) -> Trace:
    """Один тест кандидата до решения или до stop_after ответов. После stop_after следующее задание выдаётся только
    при issue_pending (иначе сессия остаётся без «висящего» задания: у него серверный дедлайн — оно протухло бы).

    extensions: auto — добор вниз принимается всегда, вверх — если истинный уровень симулянта не ниже порога
    следующего грейда; decline — от добора отказ; none — предложение оставить как есть (для «середины теста»).
    """
    scale = load_scale()
    metas = _meta_by_id(db, spec)
    ld = sessions.create_session(
        db, user, spec=spec, grade=claimed_grade, upgrade=False, consent_test_logging=True
    )
    trace = Trace(session_ids=[ld.row.id])
    current = ld
    for _guard in range(8):  # главная сессия + не больше двух доборов; запас на непредвиденное
        while True:
            if stop_after is not None and trace.answered >= stop_after:
                if issue_pending:
                    current, item = sessions.next_item(db, user, current.row.id)
                    trace.open_pending_seq = item.response.seq if item is not None else None
                trace.status = str(current.sess.status)
                return trace
            current, item = sessions.next_item(db, user, current.row.id)
            if item is None or current.sess.status not in OPEN:
                break
            inst = item.instance
            key = decrypt_key(inst.key_enc)
            answer = _answer_for(examinee, metas.get(inst.family_id), spec, inst.rendered or {}, key, rng)
            sel = current.sess.pending
            _think(rng, float(sel.time_limit_s) if sel is not None else None)
            res = sessions.submit_answer(db, user, current.row.id, item.response.seq, answer)
            current = res.ld
            trace.answered += 1
            if current.sess.status not in OPEN:
                break
        status = current.sess.status
        if status != SessionStatus.EXTENSION_OFFERED or extensions == "none":
            break
        offer = _pick_offer(current, examinee, scale, extensions)
        if offer is None:
            current, _ = sessions.resolve_extension(db, user, current.row.id, direction=None, accept=False)
            break
        _, child = sessions.resolve_extension(db, user, current.row.id, direction=offer, accept=True)
        if child is None:
            break
        trace.session_ids.append(child.row.id)
        current = child
        metas = _meta_by_id(db, spec)
    trace.status = str(current.sess.status)
    d = current.sess.decision
    trace.decision = d.code if d is not None else None
    return trace


def _pick_offer(ld: Any, examinee: SimulatedExaminee, scale: Any, mode: str) -> str | None:
    if mode == "decline":
        return None
    for o in ld.sess.offers:
        if o.direction == "down":
            return "down"
    for o in ld.sess.offers:
        if o.direction == "up" and examinee.theta >= scale.cut(o.target_grade) - 0.1:
            return "up"
    return None
