"""Реализации хуков теста (app.services.testing.anticheat_hooks): «двойник в буфере» при выдаче и сверка при ответе.

Выдача (clipboard_variant): если у экземпляра есть готовый двойник и в сессии их выдано меньше
evidence.twin_rate_per_session (config/anticheat.yaml), клиент получает `clip = {render_id, clipboard_text}`. Ключ двойника,
позиция изменённого литерала, карта подмены и тексты-«до/после» остаются на сервере: по ответу API нельзя понять, какой
именно литерал отличается (разница видна только при сравнении текстов вручную — это «сложный и редкий путь, сравнимый
с перепечаткой», spec §7).

Ответ (record_answer): ответ классифицируется относительно ключа экрана и ключа копии и записывается как улика
(kind/matched/strength). Верность для оценки уровня уже посчитана тестом: ответ двойника — это неверный ответ на X (его
ключ другой, гейт 1), поэтому уровень падает без всякого флага — наивный копипаст наказывает себя сам. Флаги доверия
НЕ ставятся: вес улики 0 до калибровки ε (evidence.trust_weight), автобана нет.

Любая ошибка хука не должна ломать тест кандидата и отравлять транзакцию: работа идёт в SAVEPOINT.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.testing import ItemInstance, TestResponse
from app.models.testing import TestSession as SessionRow
from app.services.anticheat import store
from app.services.anticheat.models import AnticheatEvidence
from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.evidence import classify_for_twin
from app.testing.bank.registry import all_families

log = logging.getLogger("vzaimno.anticheat.runtime")


def issue_clip(db: Session, inst_row: ItemInstance) -> bool:
    """Положить в inst_row.rendered['clip'] данные для обработчика копирования. True — двойник выдан."""
    sid = inst_row.issued_to_session_id
    if sid is None:
        return False
    cfg = load_anticheat_config()
    try:
        with db.begin_nested():
            if store.shown_count(db, sid) >= cfg.evidence.twin_rate_per_session:
                return False
            row = store.ensure_twin_row(db, inst_row)
            if row is None or row.status != store.STATUS_OK or not row.data.get("clipboard_text"):
                return False
            row.shown_in_session_id = sid
            row.render_id = row.render_id or store.render_id_for(inst_row.id)
            # Новый словарь, а не правка на месте: instance.rendered — JSONB без отслеживания вложенных изменений
            inst_row.rendered = {
                **(inst_row.rendered or {}),
                "clip": {"render_id": row.render_id, "clipboard_text": str(row.data["clipboard_text"])},
            }
            db.flush()
            return True
    except Exception:  # noqa: BLE001 — защита не должна мешать тесту: задание уйдёт без подмены
        log.exception("Антиподсказчик: не удалось выдать двойника экземпляра %s", inst_row.id)
        return False


def record_answer(db: Session, inst_row: ItemInstance, answer: Any) -> AnticheatEvidence | None:
    """Записать улику по ответу на задание, к которому клиент получил двойника. Повтор безопасен (один ответ — одна запись)."""
    if answer is None:
        return None
    try:
        with db.begin_nested():
            row = store.get_row(db, inst_row.id)
            if row is None or row.status != store.STATUS_OK or row.shown_in_session_id is None:
                return None
            resp = db.scalars(select(TestResponse).where(TestResponse.instance_id == inst_row.id)).first()
            if resp is None or resp.session_id != row.shown_in_session_id:
                return None
            if db.scalar(select(AnticheatEvidence.id).where(AnticheatEvidence.response_id == resp.id)) is not None:
                return None
            fam = all_families().get(inst_row.family_id)
            twin = store.twin_from_row(row)
            if fam is None or twin is None:
                return None
            inst = store.instance_from_row(inst_row)
            ac = classify_for_twin(fam, inst, twin, answer)
            session = db.get(SessionRow, resp.session_id)
            ev = AnticheatEvidence(
                session_id=resp.session_id,
                response_id=resp.id,
                instance_id=inst_row.id,
                seq=resp.seq,
                family_id=inst_row.family_id,
                variant_code=inst_row.variant_code,
                answer=answer if isinstance(answer, str) else str(answer),
                kind=ac.kind,
                matched=ac.matched,
                strength=ac.strength,
                tag=ac.tag,
                mutation_class=twin.mutation_class,
                mutation_subclass=twin.mutation_subclass,
                honest_p=twin.honest_collision_estimate,
                render_id=row.render_id,
                demo_assisted=bool(session.demo_assisted) if session is not None else False,
            )
            db.add(ev)
            db.flush()
            return ev
    except Exception:  # noqa: BLE001 — улика не должна ронять ответ кандидата
        log.exception("Антиподсказчик: не удалось записать улику по экземпляру %s", inst_row.id)
        return None
