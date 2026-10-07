"""Экспресс-тест жюри: честная подача результата (DEMO_MODE; config/testing.yaml → express; DECISIONS п. 29).

Движок тот же, поэтому решение (PASSED / FAILED / BORDERLINE_*) считается как обычно. Меняется только то, что мы
вправе из него заявить: за ≤ 8 заданий категорию не присваиваем никогда, а когда уверенности нет (P между порогами) —
прямо говорим «грейд не определён точно, нужен полный тест» вместо вымученного грейда. Погрешность показываем числом.
"""

from __future__ import annotations

from datetime import timedelta

from app.core.configs import load_config
from app.services.testing.schemas import ExpressResultOut, ResultOut
from app.testing.cat import FAILED, PASSED, Decision, express_cat_config, load_express_config
from app.testing.irt import Scale
from app.testing.session import TestSession


def express_texts() -> dict:
    return load_config("testing")["express"]["texts"]


def apply_express(out: ResultOut, s: TestSession, d: Decision, scale: Scale) -> ResultOut:
    """Переписать «категорийные» поля результата экспресс-сессии: оценка, погрешность, чем отличается полный тест."""
    t = express_texts()
    cfg = express_cat_config()
    grade = scale.grade_name(d.target_grade)
    pct = min(99, round(100 * d.p_pass))  # «на 100% уверены» не говорим: оценка по ≤ 8 заданиям
    lo, hi = (out.interval80 + [0, 0])[:2]
    n = len(s.cat.responses)
    if d.code == PASSED:
        verdict, headline = "estimate_pass", t["pass_headline"].format(grade=grade, pct=pct)
        note = t["estimate_note"]
    elif d.code == FAILED:
        verdict, headline = "estimate_below", t["below_headline"].format(grade=grade, pct=min(99, 100 - round(100 * d.p_pass)))
        note = t["estimate_note"]
    else:
        verdict, headline = "inconclusive", t["inconclusive_headline"]
        note = t["inconclusive_note"].format(
            n=n, lo=round(100 * cfg.p_stop_low), hi=round(100 * cfg.p_stop_high)
        )
    block = ExpressResultOut(
        badge=t["badge"],
        verdict=verdict,
        headline=headline,
        note=" ".join(note.split()),
        margin_note=" ".join(t["margin_note"].split()).format(lo=lo, hi=hi, width=hi - lo),
        interval_width=hi - lo,
        n_items=n,
        stop_reason=d.reason,
        full_test_title="Что даёт полный тест",
        full_test=[x.format(n_max=cfg.n_max) for x in t["full_test"]],
    )
    return out.model_copy(
        update={
            "express": block,
            "headline": headline,
            "stop_text": None,
            "message": None,
            "confirmed_grade": None,
            "category": None,
            "offers": [],
            "offers_expire_at": None,
            "offers_expire_in": None,
            "blind_card_preview": None,
        }
    )


def express_session_fields(s: TestSession) -> dict:
    """Поля SessionOut для экспресса (бюджет времени и потолок заданий)."""
    if not s.express:
        return {"express": False}


    ex = load_express_config()
    started = s.started_at or s.created_at
    return {
        "express": True,
        "express_deadline_at": started + timedelta(seconds=ex.time_budget_s),
        "express_items_max": express_cat_config().n_max,
    }



