"""Раздел «Лаборатория» (Л1–Л4, концепция §9.4, spec-testing §4–§5): только роли «методист» и «администратор».

Трасса сессии (уровень и погрешность по шагам — внутренняя шкала, подписи «уровень»/«погрешность»),
банк семейств с параметрами, экспозицией и статусом, «3 клона рядом», очередь флагов с решением,
последний прогон валидации, калибровка фазы 1 (MAP по сложности, app/testing/calibration.py).

«Антиподсказчик» (spec-antiassist, killer-features §2.1 п. 1–2): лента сверок «двойника в буфере» и акт сверки с решением
методиста («назначить подтверждение», «снять отметку» — автобана нет), комплект «Подтверждение за 5 минут» по сессии,
покрытие банка двойниками и предрасчёт, отчёт последнего `make validate` для экрана «Валидация».
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query

from app.api.deps import DbSession, LimiterDep, require_roles
from app.core.audit import audit
from app.core.errors import error_responses, not_found
from app.models.enums import UserRole
from app.models.users import User
from app.services.anticheat import coverage as twin_coverage
from app.services.anticheat import evidence as anticheat_evidence
from app.services.anticheat import kit as anticheat_kit
from app.services.anticheat import precompute as twin_precompute
from app.services.anticheat.cfg import runtime_config
from app.services.anticheat.schemas import (
    ActOut,
    LabVerificationKitOut,
    PrecomputeIn,
    PrecomputeOut,
    ReconciliationListOut,
    ReconciliationReviewIn,
    TwinCoverageOut,
    ValidationReportOut,
)
from app.services.demo.jury_admin import validation_result
from app.services.testing import lab as svc
from app.services.testing import limits
from app.services.testing import quality as quality_svc
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


# ---------------------------------------------------------------------------
# «Антиподсказчик»: сверки, комплект подтверждения, покрытие двойниками, отчёт валидации
# ---------------------------------------------------------------------------

_RC = runtime_config()


@router.get(
    "/reconciliations",
    response_model=ReconciliationListOut,
    summary="Лента сверок «двойника в буфере»",
    description=(
        "Ответы, в которых есть след копии из буфера обмена: совпадение с ключом копии (а не с ключом экрана) и слабые "
        "следы (значение, которого нет на экране; типичная ошибка на копии — только контекст). Ответ двойника уже засчитан "
        "в тесте неверным; вес улики в доверии 0 до калибровки; автоматического бана нет — решает методист. Работодатель "
        "этого не видит. Включает мини-тесты песочницы жюри (`source = sandbox`, сессии demo_assisted)."
    ),
)
def reconciliations(
    user: Methodist,  # noqa: ARG001
    db: DbSession,
    status: Annotated[
        Literal["open", "verification_assigned", "dismissed"] | None, Query(description="Фильтр по решению методиста")
    ] = None,
    session_id: Annotated[uuid.UUID | None, Query(description="Только сверки этой сессии")] = None,
    include_sandbox: Annotated[bool, Query(description="Добавить песочницу жюри «Попробуй списать»")] = True,
    limit: Annotated[int, Query(ge=1, le=_RC.list_limit_max, description="Сколько вернуть")] = _RC.list_limit,
) -> ReconciliationListOut:
    rows, counts = anticheat_evidence.list_reconciliations(
        db, status=status, include_sandbox=include_sandbox and session_id is None, limit=limit, session_id=session_id
    )
    return ReconciliationListOut(items=rows, counts=counts, note_ru=_RC.text("list_note_ru"))  # type: ignore[arg-type]


@router.get(
    "/reconciliations/{reconciliation_id}",
    response_model=ActOut,
    summary="Акт сверки",
    description=(
        "Показанная версия и копия из буфера рядом с подсвеченным литералом, оба ключа (получены исполнением), ответ и то, "
        "с чем он совпал, «до/после предупреждения», вероятность честного совпадения («ожидаемо N на 10 000 сессий»), "
        "чек-лист альтернативных объяснений и сводка по сессии. Формулировки — только наблюдаемые факты, без обвинений."
    ),
)
def reconciliation(reconciliation_id: str, user: Methodist, db: DbSession) -> ActOut:  # noqa: ARG001
    return ActOut.model_validate(anticheat_evidence.get_act(db, reconciliation_id))


@router.post(
    "/reconciliations/{reconciliation_id}/review",
    response_model=ActOut,
    summary="Решение по сверке",
    description=(
        "«Назначить подтверждение» (кандидату предлагается «Подтверждение за 5 минут»), «Снять отметку» или «Вернуть в "
        "очередь». Решает человек: автоматического бана и понижения нет, работодатель решение и причины не видит."
    ),
)
def reconciliation_review(reconciliation_id: str, body: ReconciliationReviewIn, user: Methodist, db: DbSession) -> ActOut:
    out = anticheat_evidence.review(db, reconciliation_id, body.action, user.id, body.note)
    audit(
        db,
        "lab.anticheat_review",
        actor_id=user.id,
        actor_role=user.role,
        entity_type="anticheat_reconciliation",
        entity_id=None,
        reconciliation=reconciliation_id,
        decision=body.action,
    )
    db.commit()
    return ActOut.model_validate(out)


@router.get(
    "/sessions/{session_id}/verification-kit",
    response_model=LabVerificationKitOut,
    summary="«Подтверждение за 5 минут» по сессии",
    description=(
        "Комплект из 3 вопросов по собственным заданиям кандидата (в каждом изменён один параметр, ожидаемый ответ "
        "вычислен исполнением, рубрика 0–2, «что слушать») и до 2 шаблонных вопросов по навыкам. Методисту — с причиной "
        "выбора заданий; работодателю доступна урезанная версия после раскрытия контакта "
        "(`/api/invitations/{id}/verification-kit`)."
    ),
)
def verification_kit(session_id: uuid.UUID, user: Methodist, db: DbSession) -> LabVerificationKitOut:  # noqa: ARG001
    kit, session = anticheat_kit.lab_kit(db, session_id)
    return LabVerificationKitOut.model_validate({**kit.to_dict("methodist"), "session_id": session.id})


@router.get(
    "/twins/coverage",
    response_model=TwinCoverageOut,
    summary="Покрытие банка двойниками",
    description=(
        "По семействам: свой двойник семейства или общий мутатор, сколько свободных экземпляров пула, у скольких двойник "
        "готов, ждёт расчёта или построить не удалось (с причиной), сколько выдано кандидатам. Ключей и текстов подмены нет."
    ),
)
def twins_coverage(user: Methodist, db: DbSession) -> TwinCoverageOut:  # noqa: ARG001
    return TwinCoverageOut.model_validate(twin_coverage.coverage(db))


@router.post(
    "/twins/precompute",
    response_model=PrecomputeOut,
    summary="Посчитать двойники пула",
    description=(
        "Запускает в фоне расчёт двойников для свободных экземпляров (одно семейство или все). Расчёт — запуски кода, у "
        "части семейств он занимает секунды, поэтому делается заранее, а не при выдаче задания. Прогресс — в «Покрытии»."
    ),
)
def twins_precompute(
    body: PrecomputeIn, user: Methodist, db: DbSession, background: BackgroundTasks, limiter: LimiterDep
) -> PrecomputeOut:
    limiter.check("lab_twins_precompute", str(user.id), limits.RL_LAB_CALIBRATE)
    known = twin_coverage.all_families()
    if body.family_id and body.family_id not in known:
        raise not_found("family_not_found", "Семейство не найдено")
    if twin_precompute.is_running():
        return PrecomputeOut(accepted=False, families=0, message=_RC.text("precompute_busy_ru"))
    families = [body.family_id] if body.family_id else None
    background.add_task(twin_precompute.run_background, db.get_bind(), families, body.per_family)
    return PrecomputeOut(accepted=True, families=1 if families else len(known), message=_RC.text("precompute_started_ru"))


@router.get(
    "/quality",
    response_model=quality_svc.QualityOut,
    summary="Качество заданий: DIF, дрейф, экспозиция",
    description=(
        "Монитор психометрического качества (docs/TESTING_MECHANISM.md): дрейф сложности семейств по ответам после калибровки "
        "(Δb с 95% ДИ, флаг при превышении порога из config/testing.yaml), экспозиция семейств по решённым сессиям и результат "
        "проверки DIF (Мантель–Хензель, классы ETS A/B/C) на синтетической популяции с внедрёнными смещёнными заданиями."
    ),
)
def quality(user: Methodist, db: DbSession) -> quality_svc.QualityOut:  # noqa: ARG001
    return quality_svc.quality_out(db)


@router.get(
    "/validation/report",
    response_model=ValidationReportOut,
    summary="Отчёт последней валидации",
    description=(
        "Полный JSON последнего `make validate` (`backend/eval/out/validation.json`): тестирование, категоризация, подбор, "
        "набор пар — для экрана «Валидация». 404 — прогона ещё нет."
    ),
)
def validation_report(user: Methodist, db: DbSession) -> dict[str, Any]:  # noqa: ARG001
    found = validation_result()
    if found is None:
        raise not_found("validation_missing", "Прогона валидации ещё нет — выполните `make validate`")
    data, rel, stamp = found
    return {"source": rel, "file_time": stamp, "sections": sorted(data), "meta": data.get("meta", {}), "result": data}
