"""Точки расширения защиты от подсказчика/ИИ-ассистента (DECISIONS п. 13, spec-testing §3.10,
_internal/design/spec-antiassist.md §7 «API»). Реализация — app.services.anticheat.runtime.

Сервис тестирования вызывает хуки в трёх местах:
- clipboard_variant(instance) — один раз, когда экземпляр выдан кандидату (до первого показа задания).
  Если у экземпляра есть готовый «двойник в буфере» (предрасчёт пула: `python -m app.services.anticheat.precompute`) и
  в сессии их выдано меньше evidence.twin_rate_per_session, в instance.rendered["clip"] кладётся
  {render_id, clipboard_text} — API отдаёт это как ItemOut.clip. Ключ двойника, позиция литерала и тексты «до/после»
  остаются на сервере (таблица anticheat_twins). Изменения сохраняются вместе с экземпляром, повторные GET …/next
  отдают тот же вариант. instance.rendered — JSONB без отслеживания вложенных правок: присваивается новый словарь.
  Ключ, key_hash и теги дистракторов хук не трогает.
- on_client_event(session, event) — на каждое принятое событие фронта (blur/focus/paste/copy/cut/dragstart/
  self_paste/copy_bypass/reconnect). События пишутся в журнал сервисом; контекст сверки (время копирования, вставка в своё
  поле) собирается из журнала при чтении (app.services.anticheat.evidence). Сигналов (флагов) нет.
- on_answer(session, instance, answer, *, normalized, correct) — на каждый принятый (не повторный) ответ, после его
  проверки. Ответ на задание с двойником записывается как улика (anticheat_evidence). Верность ответа и оценка уровня не
  меняются: ответ двойника для X и так неверный. Флаги доверия не ставятся — вес улики 0, пока ε не откалибрована по людям,
  а автоматического бана нет никогда: решает методист в лаборатории.

Каждый сигнал, который вернёт хук, становится флагом сессии (severity soft | hard; details сохраняются в
flags.details.signal и видны методисту, не кандидату) и учитывается в доверии при закрытии сессии, как остальные
детекторы. Исключение в хуке не ломает тест: сервис пишет его в журнал и продолжает без сигналов.
Работа с БД внутри хуков идёт в SAVEPOINT: ошибка не отравляет транзакцию запроса.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import object_session


@dataclass(frozen=True)
class AntiAssistSignal:
    kind: str  # короткий код (≤ 16 символов), например "clipboard"
    message_ru: str  # что увидит кандидат в «Мой грейд» рядом с кнопкой «оспорить»
    severity: str = "soft"  # soft | hard
    value: float | None = None
    details: dict[str, Any] | None = None  # карточка доказательства для методиста (JSON)


def clipboard_variant(instance: Any) -> None:
    """Вызывается при выдаче задания (GET …/next). Кладёт instance.rendered["clip"], если есть двойник."""
    db = object_session(instance)
    if db is None:
        return None
    from app.services.anticheat import (
        runtime,  # noqa: PLC0415 — сервис тестирования импортирует хуки при загрузке
    )

    runtime.issue_clip(db, instance)
    return None


def on_client_event(session: Any, event: Any) -> list[AntiAssistSignal]:
    """Вызывается на каждое принятое событие клиента (POST …/events). Сигналов нет: события — контекст акта сверки."""
    return []


def on_answer(session: Any, instance: Any, answer: Any, *, normalized: Any, correct: bool) -> list[AntiAssistSignal]:
    """Вызывается на каждый принятый ответ (POST …/answer). Записывает улику; флагов нет (вес 0)."""
    db = object_session(instance)
    if db is None:
        return []
    from app.services.anticheat import runtime  # noqa: PLC0415

    runtime.record_answer(db, instance, answer)
    return []
