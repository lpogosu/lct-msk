"""«Режим жюри» (02-compliance RK2, DECISIONS п. 4): жюри в роли кандидата не должно застрять на тесте.

Пока режим включён, кандидат может запросить подсказку к текущему заданию — верный ответ в формате задания. Сессия,
для которой подсказка запрошена (или которая начата в режиме жюри), получает метку demo_assisted = true: она исключена
из метрик лаборатории, из нормы семейств и из калибровки (app.services.testing.catalog, lab). Подсказка доступна только
владельцу сессии и только в DEMO_MODE; без неё ключи заданий наружу не выходят никогда.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.clock import clock
from app.core.errors import conflict
from app.models.testing import TestSession as SessionRow
from app.models.users import User
from app.services.demo.models import DemoPersona, DemoState
from app.services.demo.state import get_state
from app.services.testing import sessions
from app.services.testing.keystore import decrypt_key


def set_mode(db: Session, enabled: bool) -> DemoState:
    state = get_state(db)
    if enabled and not state.jury_mode:
        state.jury_mode_since = clock.now()
    if not enabled:
        state.jury_mode_since = None
    state.jury_mode = enabled
    db.flush()
    if enabled:
        mark_assisted(db)
    return state


def mark_assisted(db: Session) -> int:
    """Сессии, начатые после включения режима жюри (кроме синтетических кандидатов), помечаются demo_assisted."""
    state = get_state(db)
    if not state.jury_mode or state.jury_mode_since is None:
        return 0
    synthetic = select(DemoPersona.user_id).where(DemoPersona.kind == "synthetic")
    res = db.execute(
        update(SessionRow)
        .where(
            SessionRow.created_at >= state.jury_mode_since,
            SessionRow.demo_assisted.is_(False),
            SessionRow.candidate_id.not_in(synthetic),
        )
        .values(demo_assisted=True)
    )
    db.flush()
    return int(res.rowcount or 0)


def hint(db: Session, user: User, session_id: uuid.UUID) -> dict[str, Any]:
    """Верный ответ на текущее (выданное) задание. 409 — режим выключен или заданий в выдаче нет."""
    if not get_state(db).jury_mode:
        raise conflict("jury_mode_off", "Режим жюри выключен: подсказки недоступны")
    ld = sessions.load(db, session_id, user, lock=True)
    item = sessions.pending_item(db, ld)
    if item is None:
        raise conflict("no_pending_item", "Сейчас нет выданного задания: откройте задание и запросите подсказку")
    key = decrypt_key(item.instance.key_enc)
    rendered = item.instance.rendered or {}
    mode = rendered.get("answer_mode") or "choice"
    text: str | None = None
    if mode == "choice":
        text = next((str(o.get("text")) for o in rendered.get("options") or () if str(o.get("id")) == str(key)), None)
    ld.row.demo_assisted = True
    db.flush()
    return {
        "session_id": ld.row.id,
        "seq": item.response.seq,
        "answer_mode": mode,
        "answer": key,
        "answer_text": text,
        "demo_assisted": True,
        "note": "Режим жюри: сессия помечена demo_assisted и не попадает в метрики",
    }
