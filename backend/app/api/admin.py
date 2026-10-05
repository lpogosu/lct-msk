"""Раздел «Администрирование»: импорт данных жюри, ранжирование с компонентами Score, метрики по размеченным парам,
последний прогон валидации (концепция §7.3; DECISIONS п. 5; 02-compliance G3). Только роль «администратор».

Формат импорта — строки набора пар `backend/eval/pairs_test.jsonl` (схема `eval/pairs_test.schema.json`). Кандидаты без
теста попадают в группу «категория по опросу, не подтверждена» и ранжируются ниже подтверждённых.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import DbSession, require_roles
from app.core.errors import ApiError, bad_request, error_responses, not_found
from app.models.enums import UserRole
from app.models.users import User
from app.services.demo import jury_admin as svc
from app.services.demo import models as demo_models  # noqa: F401 — регистрирует таблицы демо в Base.metadata
from app.services.demo.cfg import section
from app.services.demo.models import JuryBrief

router = APIRouter(prefix="/admin", tags=["Администрирование"], responses=error_responses())

Admin = Annotated[User, Depends(require_roles(UserRole.admin))]


# --------------------------------------------------------------------------- схемы


class ImportIn(BaseModel):
    name: str | None = Field(default=None, max_length=200, description="Название загрузки (для списка импортов)")
    pairs: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Строки как в eval/pairs_test.jsonl: brief_id, brief{text, gold, form}, candidate_id, candidate{features|null, "
        "self_description, claimed_spec, claimed_grade, survey}, rel 0–3",
    )
    briefs: list[dict[str, Any]] = Field(
        default_factory=list, description="Только брифы: {brief_id, text, gold?, form?} или {brief_id, brief: {…}}"
    )
    candidates: list[dict[str, Any]] = Field(
        default_factory=list,
        description="Только кандидаты: {candidate_id, features|null, self_description, claimed_spec, claimed_grade, survey}",
    )


class ImportOut(BaseModel):
    batch_id: uuid.UUID
    name: str
    briefs: int
    candidates: int
    confirmed: int = Field(description="Кандидатов с тестом (полный Score)")
    unconfirmed: int = Field(description="Кандидатов без теста: группа «категория по опросу, не подтверждена»")
    pairs: int
    warnings: list[str]
    note: str


class BatchOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    briefs: int
    candidates: int
    unconfirmed: int
    pairs: int


class DeletedOut(BaseModel):
    deleted: bool
    batch_id: uuid.UUID


class RankIn(BaseModel):
    brief_id: str | None = Field(default=None, max_length=64, description="Бриф из импорта")
    batch_id: uuid.UUID | None = Field(default=None, description="Импорт (по умолчанию — последний)")
    text: str | None = Field(default=None, max_length=5000, description="Или текст брифа прямо в запросе")
    salary_from: int | None = Field(default=None, ge=0, description="Вилка от, ₽/мес (если её нет в тексте)")
    salary_to: int | None = Field(default=None, ge=0, description="Вилка до, ₽/мес")
    salary_net: bool | None = Field(default=None, description="true — на руки, false — до НДФЛ")
    source: Literal["import", "platform", "both"] = Field(
        default="import", description="Откуда кандидаты: загрузка жюри, слепой банк платформы или оба"
    )
    top: int | None = Field(default=None, ge=1, description="Сколько строк вернуть (по умолчанию из config/demo.yaml)")
    use_gold: bool = Field(default=False, description="Брать эталонный разбор брифа из импорта вместо разбора текста")
    all_categories: bool = Field(default=False, description="Все категории пула, а не рекомендованные для брифа")


class RankReasonOut(BaseModel):
    kind: str = Field(description="pro — «за», con — «против»")
    code: str
    component: str | None = None
    text: str


class RankRowOut(BaseModel):
    rank: int
    candidate_id: str
    source: str = Field(description="import — из загрузки жюри, platform — из банка платформы")
    group: Literal["confirmed", "unconfirmed"]
    group_label: str
    category: str
    score: float
    components: dict[str, float] = Field(description="Нормированные компоненты Score: T, K, F, A, U")
    contributions: dict[str, float] = Field(description="Вклад компонентов в баллах (Σ = Score)")
    parts: dict[str, float]
    reasons: list[RankReasonOut]
    flags: list[str]
    has_fsp: bool


class RankOut(BaseModel):
    brief_id: str
    brief: dict[str, Any] = Field(description="Как разобран бриф: специализация, грейд, навыки, вилка, формат")
    categories: list[dict[str, Any]]
    pool_size: int
    total: int
    confirmed: int
    unconfirmed: int
    items: list[RankRowOut]
    scoring_config_version: str


class PairsIn(BaseModel):
    pairs: list[dict[str, Any]] = Field(
        default_factory=list, description="Размеченные пары (формат eval/pairs_test.jsonl); не заданы — берутся пары из batch_id"
    )
    batch_id: uuid.UUID | None = Field(default=None, description="Импорт с разметкой (по умолчанию — последний)")
    use_gold: bool = False
    systems: list[str] | None = Field(default=None, description="Какие системы из ranks_top20 сравнивать (по умолчанию все)")


class MetricOut(BaseModel):
    mean: float | None
    ci_low: float | None = None
    ci_high: float | None = None
    n_briefs: int


class PairsOut(BaseModel):
    n_briefs: int
    n_pairs: int
    n_candidates: int
    n_unconfirmed: int
    ours: dict[str, MetricOut] = Field(description="P@5, P@10, nDCG@10 нашей системы: среднее по брифам и 95% бутстреп-ДИ")
    reported_systems: dict[str, dict[str, MetricOut]] = Field(
        description="Те же метрики по местам из ranks_top20 (наш набор пар: ours, b0–b3)"
    )
    per_brief: list[dict[str, Any]]
    notes: list[str]
    scoring_config_version: str


class ValidationOut(BaseModel):
    source: str = Field(description="Файл прогона относительно backend/")
    file_time: datetime | str = Field(description="Время файла прогона")
    sections: list[str] = Field(description="Разделы результата")
    meta: dict[str, Any]
    result: dict[str, Any] = Field(description="Полный JSON последнего `make validate`")


# --------------------------------------------------------------------------- маршруты


@router.post(
    "/import",
    status_code=201,
    response_model=ImportOut,
    summary="Импорт кандидатов и брифов жюри",
    description=(
        "Принимает строки набора пар (формат `eval/pairs_test.jsonl`, схема `eval/pairs_test.schema.json`): бриф, кандидат с "
        "признаками подбора и разметка rel 0–3; можно отдельно `briefs` и `candidates`. Кандидат с `features: null` — без теста: "
        "он попадает в группу «категория по опросу, не подтверждена» (заявленные `claimed_spec`/`claimed_grade`, навыки из "
        "`survey`/`declared_skills` или самоописания) и ранжируется ниже подтверждённых. Данные не попадают в банк платформы. "
        "400 — формат строки неверен (в сообщении путь до поля)."
    ),
)
def import_pairs(body: ImportIn, user: Admin, db: DbSession) -> dict[str, Any]:
    out = svc.store_import(
        db, name=body.name, created_by=user.id, pairs=body.pairs, briefs=body.briefs, candidates=body.candidates
    )
    db.commit()
    return out


@router.get(
    "/import",
    response_model=list[BatchOut],
    summary="Список импортов жюри",
    description="Загрузки жюри (новые сверху) с числом брифов, кандидатов, кандидатов без теста и размеченных пар.",
)
def list_imports(user: Admin, db: DbSession) -> list[dict[str, Any]]:  # noqa: ARG001
    return [
        {"id": b.id, "name": b.name, "created_at": b.created_at, "briefs": b.n_briefs, "candidates": b.n_candidates,
         "unconfirmed": b.n_unconfirmed, "pairs": b.n_pairs}
        for b in svc.list_batches(db)
    ]


@router.delete(
    "/import/{batch_id}",
    response_model=DeletedOut,
    summary="Удалить импорт жюри",
    description="Удаляет загрузку вместе с её брифами, кандидатами и разметкой. Данные платформы не затрагиваются.",
)
def delete_import(batch_id: uuid.UUID, user: Admin, db: DbSession) -> dict[str, Any]:  # noqa: ARG001
    svc.delete_batch(db, batch_id)
    db.commit()
    return {"deleted": True, "batch_id": batch_id}


@router.post(
    "/eval/rank",
    response_model=RankOut,
    summary="Ранжирование по брифу (топ-N с компонентами Score)",
    description=(
        "Бриф (из импорта по `brief_id` или текстом с вилкой) → топ-N кандидатов тем же движком, что подборка работодателя: Score, "
        "нормированные компоненты T, K, F, A, U, вклады и «за»/«против». Кандидаты без теста — группа «категория по опросу, не "
        "подтверждена» ниже подтверждённых. `format=csv` отдаёт тот же топ в CSV. `source`: импорт жюри, слепой банк платформы или "
        "оба. 404 — нет импорта или брифа, 422 — у брифа нет вилки."
    ),
)
def eval_rank(
    body: RankIn,
    user: Admin,  # noqa: ARG001
    db: DbSession,
    format: Annotated[Literal["json", "csv"], Query(description="Формат ответа")] = "json",  # noqa: A002
) -> Any:
    admin_cfg = section("admin")["rank"]
    top = min(int(body.top or admin_cfg["default_top"]), int(admin_cfg["max_top"]))
    pool: list[Any] = []
    labels: dict[str, str] = {}
    payload: dict[str, Any] | None = None
    brief_id = body.brief_id or "inline"
    uses_import = body.source in ("import", "both")
    batch = None
    if uses_import or body.brief_id:
        try:
            batch = svc.batch_or_404(db, body.batch_id)
        except ApiError:
            # «both» без загрузки жюри и без брифа из импорта — просто банк платформы
            if body.brief_id or body.batch_id is not None or body.source == "import":
                raise
    if batch is not None:
        if body.brief_id:
            stored = db.get(JuryBrief, (batch.id, body.brief_id))
            if stored is None:
                raise not_found("brief_not_found", f"Бриф «{body.brief_id}» не найден в импорте")
            payload = dict(stored.payload)
        if uses_import:
            imported = svc.batch_pool(db, batch.id)
            labels.update({f.candidate_id: "import" for f in imported})
            pool.extend(imported)
    form = {k: v for k, v in (("salary_from", body.salary_from), ("salary_to", body.salary_to), ("salary_net", body.salary_net))
            if v is not None}
    if body.text:
        payload = {"text": body.text, "form": form}
    elif payload is not None and form:
        payload["form"] = {**(payload.get("form") or {}), **form}
    if payload is None:
        raise bad_request("brief_required", "Укажите brief_id из импорта или text брифа")
    if body.source in ("platform", "both"):
        plat = svc.platform_pool(db)
        labels.update({f.candidate_id: "platform" for f in plat})
        pool.extend(plat)
    result = svc.rank_pool(brief_id, payload, pool, top=top, use_gold=body.use_gold, all_categories=body.all_categories, labels=labels)
    db.commit()
    if format == "csv":
        return Response(
            content="﻿" + svc.rank_csv(result),
            media_type="text/csv; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="rank-{brief_id}.csv"'},
        )
    return result


@router.post(
    "/eval/pairs",
    response_model=PairsOut,
    summary="Метрики по размеченным парам (P@5, P@10, nDCG@10)",
    description=(
        "Загрузите размеченные пары «бриф × кандидат» (формат eval/pairs_test.jsonl, rel 0–3; релевантен rel ≥ 2) — или не "
        "передавайте `pairs`, и возьмутся пары последнего импорта. Для каждого брифа ранжируются размеченные кандидаты нашим "
        "движком; считаются P@5, P@10, nDCG@10: среднее по брифам и 95% бутстреп-ДИ (тот же код, что в `make validate`). Если в "
        "строках есть `ranks_top20` (ours, b0–b3), по ним считаются те же метрики для сравнения."
    ),
)
def eval_pairs(body: PairsIn, user: Admin, db: DbSession) -> dict[str, Any]:  # noqa: ARG001
    rows = body.pairs
    if not rows:
        batch = svc.batch_or_404(db, body.batch_id)
        rows = svc.rows_from_batch(db, batch.id)
        if not rows:
            raise bad_request("pairs_required", "В импорте нет размеченных пар: передайте `pairs` с полем rel")
    return svc.pairs_metrics(rows, use_gold=body.use_gold, systems=body.systems)


@router.get(
    "/validation",
    response_model=ValidationOut,
    summary="Последний прогон валидации",
    description=(
        "JSON последнего `make validate` (`backend/eval/out/validation.json`): тестирование, категоризация, подбор, набор пар, "
        "плитки. 404 — прогона ещё нет (выполните `make validate`)."
    ),
)
def validation(user: Admin, db: DbSession) -> dict[str, Any]:  # noqa: ARG001
    found = svc.validation_result()
    if found is None:
        raise not_found("validation_missing", "Прогона валидации ещё нет — выполните `make validate`")
    data, rel, stamp = found
    return {"source": rel, "file_time": stamp, "sections": sorted(data), "meta": data.get("meta", {}), "result": data}
