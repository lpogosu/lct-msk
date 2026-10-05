"""Каталог откалиброванных семейств для задач типа A (экран Р11): модуль, время, грейд, бейдж «откалиброван»."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.enums import FamilyStatus
from app.models.testing import ItemFamilyParams

from .common import cfg, families, family_minutes, grade_name, module_name, withdrawn_family_ids


def calibrated_ids(db: Session) -> set[str]:
    """Семейство «откалибровано», если у него есть рабочие параметры IRT (статус operational)."""
    return set(
        db.scalars(
            select(ItemFamilyParams.family_id).where(ItemFamilyParams.status == FamilyStatus.operational.value)
        )
    )


def family_out(fam: Any, calibrated: set[str]) -> dict[str, Any]:
    return {
        "family_id": fam.id,
        "title": fam.title,
        "module": fam.module,
        "module_name": module_name(fam.module),
        "specializations": list(fam.specs),
        "grade": fam.target_grade,
        "grade_name": grade_name(fam.target_grade),
        "minutes": family_minutes(fam),
        "format": fam.format,
        "calibrated": fam.id in calibrated,
    }


def catalog(db: Session, *, specialization: str | None = None, module: str | None = None) -> list[dict[str, Any]]:
    calibrated = calibrated_ids(db)
    withdrawn = withdrawn_family_ids(db)
    limit = cfg().max_minutes
    out = []
    for fid, fam in sorted(families().items()):
        if fid in withdrawn:  # карантин детектора утечек или выведено из банка
            continue
        if specialization is not None and specialization not in fam.specs:
            continue
        if module is not None and fam.module != module:
            continue
        if family_minutes(fam) > limit:
            continue
        out.append(family_out(fam, calibrated))
    return out
