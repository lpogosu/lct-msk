"""«Качество заданий» в лаборатории: дрейф сложности и экспозиция по живым ответам, DIF — по прогону на синтетике.

DIF по реальным людям не считается: пол и возраст платформа не собирает, а группы (ФСП, регион, режим доступности) —
персональные признаки, которые не должны попадать в аналитику без согласия. Поэтому в лаборатории — результат проверки
метода на синтетической популяции (eval/out/quality.json, `uv run python -m eval.fairness`), с пометкой «синтетика».
Дрейф и экспозиция — по ответам кандидатов с согласием «calibration» (экспозиция — по всем решённым сессиям).
Формулы — app/testing/monitoring.py; пороги — config/testing.yaml → quality.
"""

from __future__ import annotations

import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.enums import EXCLUSIVE_SESSION_KINDS, FamilyStatus
from app.models.testing import ItemFamilyParams, TestResponse
from app.models.testing import TestSession as SessionRow
from app.services.testing import labels as L
from app.services.testing.lab import CALIBRATION_STATUSES, _observations
from app.testing.bank.registry import all_families
from app.testing.irt import ItemParams, load_scale
from app.testing.monitoring import estimate_drift, load_quality_config

SYNTHETIC_JSON = "eval/out/quality.json"


class DriftRowOut(BaseModel):
    family_id: str = Field(description="Семейство заданий")
    specialization: str
    n: int = Field(description="Ответов после последней калибровки семейства")
    observed_p: float | None = Field(description="Доля верных")
    expected_p: float | None = Field(description="Ожидаемая доля верных при калиброванной сложности")
    d_b: float | None = Field(description="Сдвиг сложности, логиты; < 0 — стало легче")
    ci_lo: float | None
    ci_hi: float | None
    flag: str = Field(description="drift | watch | ok | n/a")


class ExposureRowOut(BaseModel):
    family_id: str
    specialization: str
    sessions: int = Field(description="Сессий, где семейство показано")
    exposure: float = Field(description="Доля решённых сессий шкалы, где показано семейство")


class QualityOut(BaseModel):
    generated_at: datetime
    drift_threshold_b: float
    drift_min_n: int
    drift: list[DriftRowOut]
    drift_flagged: int
    exposure: list[ExposureRowOut]
    exposure_r_max: float
    synthetic: dict[str, Any] | None = Field(
        description="Прогон eval.fairness на синтетике (DIF по классам ETS, дрейф с внедрённой утечкой, Симпсон–Хеттер); null — прогона нет"
    )
    synthetic_note: str


def synthetic_result() -> dict[str, Any] | None:
    path = Path(__file__).resolve().parents[3] / SYNTHETIC_JSON
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    dif = data.get("dif", {})
    dif.pop("real_flagged", None)  # дубль строк rows, отчёту хватает таблицы
    return data


def drift_rows(db: Session, spec: str) -> list[DriftRowOut]:
    """Эмпирическая сложность по ответам после калибровки против калиброванной b (monitoring.estimate_drift)."""
    cfg = load_quality_config().drift
    scale = load_scale()
    fams = all_families()
    params = {p.family_id: p for p in db.scalars(select(ItemFamilyParams).where(ItemFamilyParams.specialization == spec))}
    per: dict[str, tuple[list[float], list[bool]]] = defaultdict(lambda: ([], []))
    for o in _observations(db, spec):
        p = params.get(o.family_id)
        if p is None or (p.calibrated_at is not None and o.answered_at <= p.calibrated_at):
            continue
        per[o.family_id][0].append(o.theta)
        per[o.family_id][1].append(o.correct)
    out: list[DriftRowOut] = []
    for fid in sorted(params):
        p, fam = params[fid], fams.get(fid)
        if fam is None or p.status != FamilyStatus.operational.value:
            continue
        thetas, correct = per.get(fid, ([], []))
        est = estimate_drift(fid, thetas, correct, ItemParams.from_family(p.b, p.sigma_b, float(fam.c), scale), cfg)
        out.append(
            DriftRowOut(
                family_id=fid, specialization=spec, n=est.n, observed_p=est.observed_p, expected_p=est.expected_p,
                d_b=est.d_b, ci_lo=est.lo, ci_hi=est.hi, flag=est.flag,
            )
        )
    return out


def drift_flag_count(db: Session) -> int:
    """Для ночной задачи: сколько семейств сейчас с флагом «дрейф» (в аудит)."""
    n = 0
    for spec in _testable_specs():
        n += sum(1 for r in drift_rows(db, spec) if r.flag == "drift")
    return n


def exposure_rows(db: Session, spec: str) -> list[ExposureRowOut]:
    base = (
        select(TestResponse.family_id, func.count(func.distinct(TestResponse.session_id)))
        .join(SessionRow, SessionRow.id == TestResponse.session_id)
        .where(
            SessionRow.specialization == spec,
            SessionRow.kind.in_(EXCLUSIVE_SESSION_KINDS),
            SessionRow.demo_assisted.is_(False),
            SessionRow.status.in_(CALIBRATION_STATUSES),
        )
        .group_by(TestResponse.family_id)
    )
    total = db.scalar(
        select(func.count(func.distinct(TestResponse.session_id)))
        .join(SessionRow, SessionRow.id == TestResponse.session_id)
        .where(
            SessionRow.specialization == spec,
            SessionRow.kind.in_(EXCLUSIVE_SESSION_KINDS),
            SessionRow.demo_assisted.is_(False),
            SessionRow.status.in_(CALIBRATION_STATUSES),
        )
    ) or 0
    if total == 0:
        return []
    rows = [
        ExposureRowOut(family_id=fid, specialization=spec, sessions=int(n), exposure=round(int(n) / total, 4))
        for fid, n in db.execute(base).all()
    ]
    return sorted(rows, key=lambda r: (-r.exposure, r.family_id))


def _testable_specs() -> list[str]:
    return [s["code"] for s in L.taxonomy()["specializations"] if L.spec_testable(s["code"])]


def quality_out(db: Session) -> QualityOut:
    qc = load_quality_config()
    drift: list[DriftRowOut] = []
    exposure: list[ExposureRowOut] = []
    for spec in _testable_specs():
        drift += drift_rows(db, spec)
        exposure += exposure_rows(db, spec)
    return QualityOut(
        generated_at=datetime.now(UTC),
        drift_threshold_b=qc.drift.threshold_b,
        drift_min_n=qc.drift.min_n,
        drift=drift,
        drift_flagged=sum(1 for r in drift if r.flag == "drift"),
        exposure=exposure,
        exposure_r_max=qc.exposure.r_max,
        synthetic=synthetic_result(),
        synthetic_note=(
            "DIF посчитан на синтетической популяции с заданными нами группами: это проверка метода, а не вывод о людях. "
            "По реальным кандидатам группы не собираются."
        ),
    )
