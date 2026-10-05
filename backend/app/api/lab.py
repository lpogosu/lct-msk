"""Раздел «Лаборатория» (Л1–Л4, концепция §9.4, spec-testing §4–§5): только роли «методист» и «администратор».

Трасса сессии (уровень и погрешность по шагам — внутренняя шкала, подписи «уровень»/«погрешность»),
банк семейств с параметрами, экспозицией и статусом, «3 клона рядом», очередь флагов с решением,
последний прогон валидации, калибровка фазы 1 (MAP по сложности, app/testing/calibration.py).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import DbSession, LimiterDep, require_roles
from app.core.audit import audit
from app.core.errors import error_responses
from app.models.enums import UserRole
from app.models.users import User
from app.services.testing import lab as svc
from app.services.testing import limits
from app.services.testing.schemas import (
    BankOut,
    CalibrateIn,
    CalibrateOut,
    ClonesOut,
    FlagResolveIn,
    FlagResolveOut,
    LabFlagOut,
    LabSessionOut,
    TraceOut,
    ValidationOut,
)

router = APIRouter(prefix="/lab", tags=["Лаборатория"], responses=error_responses())

# Роли лаборатории. Отдельной роли «жюри» в платформе нет: демо-логин жюри получает роль «методист».
LAB_ROLES = (UserRole.methodist, UserRole.admin)
Methodist = Annotated[User, Depends(require_roles(*LAB_ROLES))]


@router.get(
    "/sessions",
    response_model=list[LabSessionOut],
    summary="Сессии теста",
    description="Последние сессии (новые сверху) со статусом, решением, баллом, доверием и числом открытых флагов — вход в «Трассу».",
)
def sessions(
    user: Methodist,
    db: DbSession,
    status: Annotated[str | None, Query(max_length=24, description="Фильтр по статусу (FINAL, UNDER_REVIEW, …)")] = None,
    specialization: Annotated[str | None, Query(max_length=32, description="Фильтр по специализации")] = None,
    limit: Annotated[int, Query(ge=1, le=200, description="Сколько сессий вернуть")] = 50,
) -> list[LabSessionOut]:
    return svc.sessions_out(db, status=status, spec=specialization, limit=limit)


@router.get(
    "/sessions/{session_id}/trace",
    response_model=TraceOut,
    summary="Трасса сессии",
    description=(
        "Экран Л1: по шагам — задание (семейство, модуль, уровень по экспертам, сложность, код варианта), верность "
        "и время, уровень с погрешностью и вероятность «выше порога» после ответа, причина выбора (цель, модуль, "
        "дефицит квоты, top-K по информативности у порога). Плюс журнал движка и флаги сессии."
    ),
)
def trace(session_id: uuid.UUID, user: Methodist, db: DbSession) -> TraceOut:
    return svc.trace_out(db, str(session_id))


@router.get(
    "/bank",
    response_model=BankOut,
    summary="Банк заданий",
    description=(
        "Экран Л2: семейства с параметрами по шкалам (сложность ± неуверенность, источник — экспертная оценка или "
        "уточнение по ответам, экспозиция, CUSUM, статус) и состоянием пула одноразовых экземпляров."
    ),
)
def bank(
    user: Methodist,
    db: DbSession,
    specialization: Annotated[str | None, Query(max_length=32, description="Шкала (специализация)")] = None,
) -> BankOut:
    return svc.bank_out(db, specialization)


@router.get(
    "/bank/{family_id}/clones",
    response_model=ClonesOut,
    summary="Клоны рядом",
    description=(
        "Карточка ревью «3 клона рядом»: код и ключ у клонов разные, сложность одна на семейство. Клоны берутся из "
        "отдельного диапазона, который никогда не выдаётся кандидатам, поэтому ключи здесь показывать безопасно."
    ),
)
def clones(
    family_id: str,
    user: Methodist,
    db: DbSession,
    n: Annotated[int, Query(ge=1, le=svc.MAX_CLONES, description="Сколько клонов показать")] = 3,
) -> ClonesOut:
    return svc.clones_out(db, family_id, n)


@router.get(
    "/flags",
    response_model=list[LabFlagOut],
    summary="Очередь флагов",
    description=(
        "Экран Л3: флаги целостности — жёсткие и оспоренные кандидатом первыми. «Чужой ключ» показывает источник: "
        "сессию, в которой был выдан экземпляр с этим ключом."
    ),
)
def flags(
    user: Methodist,
    db: DbSession,
    status: Annotated[
        Literal["open", "dismissed", "confirmed"] | None, Query(description="Фильтр по статусу флага")
    ] = "open",
    kind: Annotated[str | None, Query(max_length=16, description="Вид флага")] = None,
    limit: Annotated[int, Query(ge=1, le=500, description="Сколько вернуть")] = 100,
) -> list[LabFlagOut]:
    return svc.flags_out(db, status=status, kind=kind, limit=limit)


@router.post(
    "/flags/{flag_id}/resolve",
    response_model=FlagResolveOut,
    summary="Решение по флагу",
    description=(
        "Подтвердить или снять флаг. Решение — только по завершённой сессии (идущая — 409 `session_not_closed`): "
        "иначе подтверждённый посреди теста флаг оставил бы сессию на проверке без открытых флагов. Когда у "
        "сессии на проверке не осталось открытых флагов, решается и сессия: результат остаётся с пониженным "
        "доверием или аннулируется (грейд откатывается). Снятие флага у закрытой сессии восстанавливает доверие."
    ),
)
def resolve(flag_id: uuid.UUID, body: FlagResolveIn, user: Methodist, db: DbSession) -> FlagResolveOut:
    out = svc.resolve_flag(db, user, str(flag_id), body.decision, body.comment)
    audit(
        db,
        "lab.flag_resolved",
        actor_id=user.id,
        actor_role=user.role,
        entity_type="flag",
        entity_id=flag_id,
        decision=body.decision,
    )
    db.commit()
    return out


@router.get(
    "/validation",
    response_model=ValidationOut,
    summary="Валидация",
    description="Экран Л4: последний прогон валидации (метрики, конфигурация, seed, коммит) — тот же код движков на синтетике.",
)
def validation(
    user: Methodist,
    db: DbSession,
    kind: Annotated[str | None, Query(max_length=32, description="Вид прогона (sim, leak, drift, …)")] = None,
) -> ValidationOut:
    return svc.validation_out(db, kind)


@router.post(
    "/calibrate",
    response_model=CalibrateOut,
    summary="Калибровка заданий",
    description=(
        "Фаза 1: уточнение сложности семейств по новым ответам (не меньше 30 на семейство) методом MAP при "
        "известных оценках уровня отвечавших; якоря шкалы не меняются, выданные грейды не пересчитываются. "
        "Используются только ответы кандидатов с согласием на калибровку."
    ),
)
def calibrate(body: CalibrateIn, user: Methodist, db: DbSession, limiter: LimiterDep) -> CalibrateOut:
    limiter.check("lab_calibrate", str(user.id), limits.RL_LAB_CALIBRATE)
    out = svc.calibrate(db, body.specialization)
    audit(
        db,
        "lab.calibrate",
        actor_id=user.id,
        actor_role=user.role,
        entity_type="calibration_run",
        entity_id=out.run_id,
        families_updated=out.families_updated,
    )
    db.commit()
    return out
