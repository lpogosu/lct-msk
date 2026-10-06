"""Покрытие банка двойниками (экран «Банк заданий» лаборатории): по семействам — механизм (свой Family.twin() или общий
мутатор), сколько свободных экземпляров, у скольких двойник готов, ждёт расчёта или построить не удалось, сколько выдано.

Форма семейства (подходит ли оно под двойник) определяется по экземпляру seed 0 (генерация детерминирована и быстра) и
кэшируется в процессе. Ключей и текстов подмены здесь нет — только счётчики и коды причин."""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.testing import ItemInstance
from app.services.anticheat import precompute, store
from app.services.anticheat.models import AnticheatTwin
from app.testing.anticheat.config import load_anticheat_config
from app.testing.anticheat.dispatch import overrides_twin, twin_applicable
from app.testing.anticheat.twins import reason_text, structural_reason
from app.testing.bank.registry import all_families

_FORM: dict[str, tuple[str, str, str | None]] = {}  # family_id → (механизм, язык, причина «нет по форме»)


def form_of(family_id: str) -> tuple[str, str, str | None]:
    """(own | generic | none, язык, причина по форме)."""
    if family_id in _FORM:
        return _FORM[family_id]
    fam = all_families()[family_id]
    cfg = load_anticheat_config()
    try:
        inst = fam.generate(0)
    except Exception:  # noqa: BLE001 — семейство не собрало экземпляр: двойника нет
        res = ("none", "", "generate_failed")
    else:
        lang = inst.rendered.language or ""
        if overrides_twin(fam):
            res = ("own", lang, None)
        elif twin_applicable(fam, inst, cfg):
            res = ("generic", lang, None)
        else:
            res = ("none", lang, structural_reason(fam, inst, cfg))
    _FORM[family_id] = res
    return res


def coverage(db: Session) -> dict[str, Any]:
    reg = all_families()
    cfg = load_anticheat_config()
    free = dict(
        db.execute(
            select(ItemInstance.family_id, func.count()).where(ItemInstance.issued_at.is_(None)).group_by(ItemInstance.family_id)
        ).all()
    )
    twins: dict[str, dict[str, int]] = {}
    for fid, status, n in db.execute(
        select(AnticheatTwin.family_id, AnticheatTwin.status, func.count()).group_by(AnticheatTwin.family_id, AnticheatTwin.status)
    ):
        twins.setdefault(fid, {})[status] = int(n)
    shown = dict(
        db.execute(
            select(AnticheatTwin.family_id, func.count())
            .where(AnticheatTwin.shown_in_session_id.is_not(None))
            .group_by(AnticheatTwin.family_id)
        ).all()
    )
    pending = dict(
        db.execute(
            select(ItemInstance.family_id, func.count())
            .outerjoin(AnticheatTwin, AnticheatTwin.instance_id == ItemInstance.id)
            .where(ItemInstance.issued_at.is_(None), AnticheatTwin.instance_id.is_(None))
            .group_by(ItemInstance.family_id)
        ).all()
    )
    reasons: dict[str, dict[str, int]] = {}
    for fid, reason, n in db.execute(
        select(AnticheatTwin.family_id, AnticheatTwin.reason, func.count())
        .where(AnticheatTwin.status == store.STATUS_NONE)
        .group_by(AnticheatTwin.family_id, AnticheatTwin.reason)
    ):
        reasons.setdefault(fid, {})[str(reason)] = int(n)

    fams: list[dict[str, Any]] = []
    for fid in sorted(reg):
        fam = reg[fid]
        mech, lang, why = form_of(fid)
        t = twins.get(fid, {})
        ok, none = t.get(store.STATUS_OK, 0), t.get(store.STATUS_NONE, 0)
        fams.append(
            {
                "family_id": fid,
                "title": str(getattr(fam, "title", fid)),
                "format": str(fam.format),
                "language": lang,
                "mechanism": mech,
                "reason_ru": reason_text(why or "", cfg) if why else None,
                "pool_free": int(free.get(fid, 0)),
                "twins_ok": ok,
                "twins_none": none,
                "pending": int(pending.get(fid, 0)) if mech != "none" else 0,
                "shown": int(shown.get(fid, 0)),
                "share": round(ok / (ok + none), 3) if (ok + none) else None,
                "top_reasons": [
                    {"reason": k, "reason_ru": reason_text(k, cfg), "count": v}
                    for k, v in sorted(reasons.get(fid, {}).items(), key=lambda kv: -kv[1])[:3]
                ],
            }
        )
    with_twin = [f for f in fams if f["mechanism"] != "none"]
    totals = {
        "families": len(fams),
        "families_with_twin": len(with_twin),
        "families_own_twin": sum(1 for f in fams if f["mechanism"] == "own"),
        "pool_free": sum(f["pool_free"] for f in fams),
        "twins_ok": sum(f["twins_ok"] for f in fams),
        "twins_none": sum(f["twins_none"] for f in fams),
        "pending": sum(f["pending"] for f in fams),
        "shown": sum(f["shown"] for f in fams),
        "running": 1 if precompute.is_running() else 0,
    }
    return {
        "families": fams,
        "totals": totals,
        "note_ru": (
            "Двойник считается заранее: после генерации пула запустите расчёт (кнопка «Посчитать двойники» или "
            "`uv run python -m app.services.anticheat.precompute`). Задание без готового двойника выдаётся без подмены: "
            "это пропуск, а не ошибка."
        ),
        "version": cfg.twins.version,
    }
