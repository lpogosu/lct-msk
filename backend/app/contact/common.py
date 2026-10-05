"""Общие типы машин состояний контакта: правило перехода, побочный эффект, ошибка, итог действия."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from .rules import Check


@dataclass(frozen=True)
class Rule:
    """Строка таблицы (from, action, actor) → (to, guards, side_effects). Guards и effects — имена функций."""

    to: str
    guards: tuple[str, ...] = ()
    effects: tuple[str, ...] = ()


@dataclass(frozen=True)
class SideEffect:
    """Что должен сделать внешний слой: записать Cooldown, событие надёжности, отправить уведомление, …

    kind: cooldown | reliability | disclosure_granted | disclosure_revoked | new_terms | quota_slot_used |
          quota_slot_returned | feedback | notify
    """

    kind: str
    data: Mapping[str, Any] = field(default_factory=dict)


class ContactError(Exception):
    """Отказ в действии с понятной пользователю причиной (message — по-русски)."""

    def __init__(self, code: str, message: str, *, check: Check | None = None, prior: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.check = check
        # Итог ленивого применения сроков, случившегося ДО отказа: его тоже надо сохранить.
        self.prior = prior


@dataclass
class Outcome:
    events: list[Any] = field(default_factory=list)
    side_effects: list[SideEffect] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def extend(self, other: Outcome) -> Outcome:
        self.events.extend(other.events)
        self.side_effects.extend(other.side_effects)
        self.warnings.extend(other.warnings)
        self.notes.extend(other.notes)
        return self

    def effects_of(self, kind: str) -> list[SideEffect]:
        return [e for e in self.side_effects if e.kind == kind]
