"""Состояние демо-режима (одна строка demo_state): режим жюри, автоответчик, отметки сборки мира."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.core.clock import clock
from app.services.demo.cfg import section
from app.services.demo.models import DemoState

SEED_VERSION = "1"


def get_state(db: Session) -> DemoState:
    row = db.get(DemoState, 1)
    if row is None:
        row = DemoState(
            id=1,
            jury_mode=bool(section("jury_mode")["default"]),
            autoresponder=bool(section("autoresponder")["enabled"]),
        )
        db.add(row)
        db.flush()
    return row


def fence_scenario(db: Session) -> DemoState:
    """Начало сборки сценария: граница автоответчика уходит вперёд. Заготовки («отправлено», «просмотрено», «Да, если…»)
    создаются с датами в прошлом, и работающий демо-цикл (в другом процессе) ответил бы на них сразу — граница это исключает.
    Сборка заканчивается stamp_scenario: граница = «сейчас»."""
    row = get_state(db)
    row.scenario_built_at = clock.now() + timedelta(days=float(section("autoresponder")["build_fence_days"]))
    db.commit()
    return row


def stamp_scenario(db: Session, at: datetime | None = None) -> DemoState:
    """Мир собран (seed/reset): автоответчик начинает отвечать на приглашения, отправленные позже этого момента."""
    row = get_state(db)
    row.scenario_built_at = at or clock.now()
    row.seed_version = SEED_VERSION
    if row.seeded_at is None:
        row.seeded_at = row.scenario_built_at
    db.flush()
    return row
