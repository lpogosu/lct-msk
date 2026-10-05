"""Точки расширения защиты от подсказчика/ИИ-ассистента (DECISIONS п. 13, spec-testing §3.10,
_internal/design/spec-antiassist.md §7 «API»).

Сейчас — пустые реализации: отдельная функция заполнит их, не меняя сервис тестирования.
Сервис вызывает их в трёх местах:
- clipboard_variant(instance) — один раз, когда экземпляр выдан кандидату (до первого показа задания);
  может, например, пометить текст для распознавания копирования. Изменения сохраняются вместе с
  экземпляром, и повторные GET …/next отдают уже изменённый вариант. instance.rendered — JSONB без
  отслеживания вложенных правок: присваивайте новый словарь (instance.rendered = {...}), а не меняйте
  его на месте. Ключ, key_hash и теги дистракторов трогать нельзя.
  Данные для обработчика копирования на клиенте («двойник в буфере»: текст X′, карта подмены,
  render_id) кладите в instance.rendered["clip"] (JSON-объект) — API отдаёт его как ItemOut.clip без
  изменений. Ключ двойника key(X′) туда класть НЕЛЬЗЯ: он остаётся на сервере (spec-antiassist §7).
- on_client_event(session, event) — на каждое принятое событие фронта (blur/focus/paste/copy/…);
  возвращает список сигналов.
- on_answer(session, instance, answer, *, normalized, correct) — на каждый принятый (не повторный)
  ответ, после его проверки; возвращает список сигналов (например, «ответ совпал с ключом двойника
  X′»). Сигналы не меняют верность ответа и оценку уровня — только доверие. instance — строка
  ItemInstance (ключ расшифровывает app.services.testing.keystore.decrypt_key).

Каждый сигнал становится флагом сессии (severity soft | hard; details сохраняются в flags.details.signal
и видны методисту, не кандидату) и учитывается в доверии при закрытии сессии, как остальные детекторы.
Исключение в хуке не ломает тест: сервис пишет его в журнал и продолжает без сигналов.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class AntiAssistSignal:
    kind: str  # короткий код (≤ 16 символов), например "clipboard"
    message_ru: str  # что увидит кандидат в «Мой грейд» рядом с кнопкой «оспорить»
    severity: str = "soft"  # soft | hard
    value: float | None = None
    details: dict[str, Any] | None = None  # карточка доказательства для методиста (JSON)


def clipboard_variant(instance: Any) -> None:
    """Вызывается при рендере задания (GET …/next). Ничего не делает."""
    return None


def on_client_event(session: Any, event: Any) -> list[AntiAssistSignal]:
    """Вызывается на каждое принятое событие клиента (POST …/events). Сигналов нет."""
    return []


def on_answer(session: Any, instance: Any, answer: Any, *, normalized: Any, correct: bool) -> list[AntiAssistSignal]:
    """Вызывается на каждый принятый ответ (POST …/answer). Сигналов нет."""
    return []
