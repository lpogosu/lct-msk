"""Фоновый цикл DEMO_MODE: автоответчик синтетических кандидатов, освежение сессий «на середине теста», метка режима жюри.

Запускается в lifespan роутера /api/demo только при DEMO_MODE (см. app/api/demo.py). Один проход — в отдельном потоке и со
своей сессией БД; общие часы платформы на время прохода берутся под CLOCK_LOCK, чтобы не пересекаться со сбросом.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.core.clock import clock
from app.core.settings import Settings
from app.services.demo import autoresponder, clockctl, jury, personas
from app.services.demo.cfg import section

log = logging.getLogger("vzaimno.demo.loop")


def tick_once(session_factory: Any, settings: Settings, *, force: bool = False) -> dict[str, Any]:
    """Один проход цикла. force — автоответчик отвечает немедленно (без задержки 30–90 с)."""
    secret = settings.pseudonym_secret.get_secret_value()
    with session_factory() as db, clockctl.CLOCK_LOCK:
        res = autoresponder.tick(db, secret=secret, now=clock.now(), force=force)
        kept = personas.keep_alive(db)
        assisted = jury.mark_assisted(db)
        db.commit()
    return {**res.as_dict(), "kept_alive": kept, "marked_assisted": assisted}


def shift_clock(db: Any, settings: Settings, preset: str) -> dict[str, Any]:
    """Кнопки «+1 день / +3 дня / сброс смещения»: сдвиг общих часов, запись смещения, ленивые сроки для всех сразу
    (истёкшие приглашения, «нет ответа» по откликам, напоминания), освежение сессий персон."""
    from app.services.contact import sweep

    presets = section("clock")["presets"]
    with clockctl.CLOCK_LOCK:
        if preset == "reset":
            clock.set_offset(0)
        else:
            clock.advance(float(presets[preset]["days"]) * 86400)
        clockctl.persist_offset(db)
        kept = personas.keep_alive(db)
        swept = sweep(db, secret=settings.pseudonym_secret.get_secret_value())
        db.commit()
    return {**clockctl.describe(), "swept": swept, "kept_alive": kept}


async def run(app: Any, session_factory: Any) -> None:
    interval = float(section("autoresponder")["tick_interval_s"])
    settings: Settings = app.state.settings
    while True:
        try:
            await asyncio.to_thread(tick_once, session_factory, settings)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — демо-цикл не должен падать; следующий проход через interval
            log.exception("Проход демо-цикла не удался")
        await asyncio.sleep(interval)
