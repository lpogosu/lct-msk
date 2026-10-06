"""Общие помощники API-тестов «Антиподсказчика». Тестов здесь нет.

Двойники считаются заранее (предрасчёт пула), поэтому тесты считают их для небольшого набора БЫСТРЫХ семейств Backend
(Python) и оставляют в бою только их: тогда выдача заданий детерминированно упирается в эти семейства, а тесты платформы
не запускают подпроцессы. Выдача заданий кандидату читает двойники только из таблицы (runtime.issue.lazy_budget_s = 0).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.testing import ItemInstance, TestResponse
from app.services.anticheat import models as _models  # noqa: F401 — регистрирует таблицы
from app.services.anticheat import store
from app.services.anticheat.models import AnticheatEvidence, AnticheatTwin
from app.services.testing.keystore import decrypt_key
from tests import test_api_testing_support as support

SPEC = "backend_python"
# Быстрые семейства (двойник считается за доли секунды): Python и SQL
FAST_FAMILIES = ["py.defaults.mutable", "py.aliasing.copy", "py.format.spec", "py.dict.basics", "sql.aggregate.null", "sql.group.having"]


def prepare_twins(db: Session, families: list[str] | None = None) -> list[str]:
    """Посчитать двойники пула и оставить в бою только семейства, у которых они получились."""
    fams = families or FAST_FAMILIES
    store.precompute_twins(db, family_ids=fams, per_family=support.POOL_K, workers=4)
    db.flush()
    ok = sorted({r.family_id for r in db.scalars(select(AnticheatTwin).where(AnticheatTwin.status == store.STATUS_OK))})
    assert len(ok) >= 3, f"двойники получились лишь у {ok}"
    support.restrict_bank(db, SPEC, set(ok))
    return ok


def twin_row_of(db: Session, inst: ItemInstance) -> AnticheatTwin | None:
    return db.get(AnticheatTwin, inst.id)


def twin_key_of(db: Session, inst: ItemInstance) -> str:
    row = twin_row_of(db, inst)
    assert row is not None and row.secret_enc
    return str(decrypt_key(row.secret_enc)["twin_key"])


def copy_policy(copy_all: bool = True) -> Callable[[Session, ItemInstance, dict[str, Any]], Any]:
    """Кандидат, который решает скопированный текст: на задание с clip отвечает ключом двойника, иначе — верно."""

    def policy(db: Session, inst: ItemInstance, item: dict[str, Any]) -> Any:
        if copy_all and item.get("clip"):
            return twin_key_of(db, inst)
        return decrypt_key(inst.key_enc)

    return policy


def screen_policy(db: Session, inst: ItemInstance, item: dict[str, Any]) -> Any:
    """Кандидат, который решает по экрану (или скриншоту): всегда верный ответ."""
    return decrypt_key(inst.key_enc)


def evidence_rows(db: Session, session_id: str) -> list[AnticheatEvidence]:
    db.flush()
    return list(
        db.scalars(
            select(AnticheatEvidence).where(AnticheatEvidence.session_id == uuid.UUID(session_id)).order_by(AnticheatEvidence.seq)
        )
    )


def responses_of(db: Session, session_id: str) -> list[TestResponse]:
    return list(
        db.scalars(select(TestResponse).where(TestResponse.session_id == uuid.UUID(session_id)).order_by(TestResponse.seq))
    )
