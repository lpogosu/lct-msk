"""Раздел «Администрирование»: импорт данных жюри, ранжирование с компонентами Score, метрики по размеченным парам,
последний прогон валидации и страница «Проверка подбора на ваших данных» (концепция §7.3; DECISIONS п. 5; 02-compliance G3).

Доступ — администратор и методист (вход жюри «Лаборатория» — это методист, staff.jury в config/demo.yaml). Методист
видит, ранжирует и удаляет только свои загрузки (created_by), администратор — все; чужая загрузка неотличима от
несуществующей (404), как в app.core.access.

Форматы импорта: строки набора пар `backend/eval/pairs_test.jsonl` (схема `eval/pairs_test.schema.json`) или простой
формат из трёх таблиц CSV/JSON — вакансии, кандидаты, разметка (app/services/demo/jury_simple.py). Кандидаты без теста
попадают в группу «категория по опросу, не подтверждена» и ранжируются ниже подтверждённых.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import DbSession, require_roles
from app.core.errors import ApiError, bad_request, error_responses, not_found
from app.models.enums import UserRole
from app.models.users import User
from app.services.demo import jury_admin as svc
from app.services.demo import jury_check as check
from app.services.demo import jury_simple as simple
from app.services.demo import models as demo_models  # noqa: F401 — регистрирует таблицы демо в Base.metadata
from app.services.demo.cfg import section
from app.services.demo.models import JuryBrief

router = APIRouter(prefix="/admin", tags=["Администрирование"], responses=error_responses())

# Импорт, ранжирование и метрики: администратор и методист (вход жюри «Лаборатория»)
Staff = Annotated[User, Depends(require_roles(UserRole.admin, UserRole.methodist))]


def owner_of(user: User) -> uuid.UUID | None:
    """Чьи загрузки видны: администратору — все (None), методисту — только свои."""
    return None if user.role == UserRole.admin.value else user.id


def limit_body(request: Request) -> None:
    """Размер тела по Content-Length (config/demo.yaml → admin.import.max_bytes): большой файл — 413 до разбора JSON."""
    raw = request.headers.get("content-length")
    limit = int(section("admin")["import"]["max_bytes"])
    if raw and raw.isdigit() and int(raw) > limit:
        raise ApiError(
            413, "import_too_large", f"Загрузка больше {limit // 1_000_000} МБ: разделите набор на несколько файлов"
        )


BodyLimit = Depends(limit_body)


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


class SimpleImportIn(BaseModel):
    name: str | None = Field(default=None, max_length=200, description="Название загрузки (для списка импортов)")
    vacancies: list[dict[str, Any]] | str | None = Field(
        default=None,
        description="Вакансии: CSV-текст с заголовком или JSON-массив. Поля: id, text, salary_from, salary_to (₽/мес), "
        "salary_net (true — на руки). Вилка обязательна: в столбцах или в тексте",
    )
    candidates: list[dict[str, Any]] | str | None = Field(
        default=None,
        description="Кандидаты: id, spec, grade, skills (через «;»), experience_months, self_description; необязательно fsp "
        "(«algo:70; product:40» или число — VSS продуктового), salary_min, salary_net, formats (office;hybrid;remote), city",
    )
    labels: list[dict[str, Any]] | str | None = Field(
        default=None, description="Разметка: vacancy_id, candidate_id, rel (0–3; релевантен ⇔ rel ≥ 2)"
    )
    unlabeled_as_irrelevant: bool | None = Field(
        default=None,
        description="У размеченной вакансии неразмеченные кандидаты набора — rel 0 (по умолчанию из config/demo.yaml: да)",
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


class SimpleImportOut(ImportOut):
    stats: dict[str, int] = Field(
        description="vacancies, candidates, labels — строк в таблицах; labeled_vacancies; pairs_added — пар с rel 0 из неразмеченных"
    )


class BatchOut(BaseModel):
    id: uuid.UUID
    name: str
    created_at: datetime
    briefs: int
    candidates: int
    unconfirmed: int
    pairs: int
    format: str | None = Field(default=None, description="pairs — строки набора пар, simple — три таблицы CSV/JSON")


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


class ChipOut(BaseModel):
    name: str
    kind: str = Field(description="must — обязательный навык брифа, nice — желательный")
    state: str = Field(description="confirmed — подтверждён, weak — проверен слабо, declared — только заявлен, no_data")
    label: str


class RankRowOut(BaseModel):
    rank: int
    candidate_id: str = Field(description="id из загрузки; у кандидата банка платформы — псевдоним «К-1234» (слепая выдача)")
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
    chips: list[ChipOut] = Field(default_factory=list, description="Навыки брифа у кандидата: подтверждён / заявлено / нет данных")


class RankBriefOut(BaseModel):
    spec: str
    grade: str
    category: str = Field(description="Категория брифа по-русски: «Backend (Python) · Middle»")
    must: list[str]
    nice: list[str]
    must_names: list[str]
    nice_names: list[str]
    salary_from: int
    salary_to: int
    salary_net: bool
    formats: list[str]
    city: str | None = None
    parsed: dict[str, Any]


class RankCategoryOut(BaseModel):
    spec: str
    grade: str
    n: int
    name: str


class RankOut(BaseModel):
    brief_id: str
    brief: RankBriefOut = Field(description="Как разобран бриф: специализация, грейд, навыки, вилка, формат")
    categories: list[RankCategoryOut]
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


# ---- страница «Проверка подбора на ваших данных»


class CheckMetricsOut(BaseModel):
    ours: dict[str, MetricOut] = Field(description="Пересчёт сейчас: P@5, P@10, nDCG@10 со средним и 95% бутстреп-ДИ")
    reported_systems: dict[str, dict[str, MetricOut]] = Field(description="Те же метрики по местам из ranks_top20 (если есть)")
    n_briefs: int


class CheckBriefRowOut(BaseModel):
    brief_id: str
    preview: str = Field(description="Начало текста брифа")
    category: str | None = Field(description="Как разобран: «Backend (Python) · Middle»")
    gold_category: str | None = Field(description="Эталон разбора из набора (если есть)")
    salary_from: int | None
    salary_to: int | None
    salary_net: bool | None
    parse_error: str | None = Field(description="Бриф не разобран (нет вилки и т. п.) — в метрики не вошёл")
    n_pairs: int = Field(description="Размечено кандидатов")
    n_relevant: int | None = Field(description="Из них релевантных (rel ≥ 2)")
    metrics: dict[str, float] | None = Field(description="p@5, p@10, ndcg@10 по брифу")


class CheckSummaryOut(BaseModel):
    source: Literal["repo", "batch"]
    title: str
    batch_id: uuid.UUID | None
    created_at: datetime | None
    n_briefs: int
    n_labeled_briefs: int
    n_pairs: int
    n_candidates: int
    n_unconfirmed: int = Field(description="Кандидатов без теста: группа «категория по опросу, не подтверждена»")
    relevant_from: int = Field(description="Релевантен ⇔ rel ≥ этого значения")
    ks: list[int]
    ndcg_k: int
    metrics: CheckMetricsOut | None = Field(description="null — в наборе нет разметки")
    system_labels: dict[str, str] = Field(description="Подписи систем из ranks_top20: ours, b0–b3")
    briefs: list[CheckBriefRowOut]
    warnings: list[str] = Field(description="Предупреждения импорта")
    notes: list[str]
    scoring_config_version: str


class SkillRefOut(BaseModel):
    id: str
    name: str


class BriefParseOut(BaseModel):
    spec: str | None
    grade: str | None
    category: str
    must: list[SkillRefOut]
    nice: list[SkillRefOut]
    salary_from: int | None
    salary_to: int | None
    salary_net: bool | None
    formats: list[str]
    city: str | None
    source: str | None = Field(default=None, description="parser — разбор текста правилами, gold — эталон набора")


class BriefViewOut(BaseModel):
    brief_id: str
    text: str
    form: dict[str, Any] | None = Field(description="Вилка из формы (если её нет в тексте)")
    gold: BriefParseOut | None = Field(description="Эталон разбора, написанный авторами набора")
    parsed: BriefParseOut | None = Field(description="Как бриф разобрал продукт")
    parse_error: str | None


class CheckProfileOut(BaseModel):
    confirmed: bool = Field(description="Категория подтверждена тестом")
    claimed_spec: str | None
    claimed_grade: str | None
    experience_months: int | None
    self_description: str = Field(description="Самоописание — в Score не входит")
    declared_skills: list[str] = Field(description="Заявленные навыки — серые чипы, в Score не входят")
    salary_min: int | None
    salary_net: bool | None
    formats: list[str]
    city: str | None
    fsp_line: str | None


class CheckRowOut(RankRowOut):
    rel: int | None = Field(description="Релевантность по разметке набора 0–3 (null — пара не размечена)")
    relevant: bool | None = Field(description="rel ≥ 2")
    profile: CheckProfileOut | None


class RelevantBelowOut(BaseModel):
    rank: int
    candidate_id: str
    rel: int
    score: float
    category: str
    group: Literal["confirmed", "unconfirmed"]


class CategoryRefOut(BaseModel):
    spec: str
    grade: str
    name: str


class CheckBriefOut(BaseModel):
    source: Literal["repo", "batch"]
    batch_id: uuid.UUID | None
    brief: BriefViewOut
    mode: Literal["labeled", "pool"] = Field(
        description="labeled — ранжируются размеченные для брифа кандидаты (как в метриках), pool — все кандидаты загрузки "
        "в рекомендованных категориях (разметки нет)"
    )
    categories: list[CategoryRefOut]
    pool_size: int
    ranked: int
    n_labeled: int
    n_relevant: int
    relevant_from: int
    metrics: dict[str, float] | None
    items: list[CheckRowOut]
    relevant_below: list[RelevantBelowOut] = Field(description="Релевантные кандидаты ниже топа — с местом, для ручной проверки")
    notes: list[str]
    scoring_config_version: str


# --------------------------------------------------------------------------- маршруты


def _batch_out(b: Any) -> dict[str, Any]:
    return {"id": b.id, "name": b.name, "created_at": b.created_at, "briefs": b.n_briefs, "candidates": b.n_candidates,
            "unconfirmed": b.n_unconfirmed, "pairs": b.n_pairs, "format": (b.meta or {}).get("format")}


@router.post(
    "/import",
    status_code=201,
    response_model=ImportOut,
    summary="Импорт кандидатов и брифов жюри",
    dependencies=[BodyLimit],
    description=(
        "Принимает строки набора пар (формат `eval/pairs_test.jsonl`, схема `eval/pairs_test.schema.json`): бриф, кандидат с "
        "признаками подбора и разметка rel 0–3; можно отдельно `briefs` и `candidates`. Кандидат с `features: null` — без теста: "
        "он попадает в группу «категория по опросу, не подтверждена» (заявленные `claimed_spec`/`claimed_grade`, навыки из "
        "`survey`/`declared_skills` или самоописания) и ранжируется ниже подтверждённых. Данные не попадают в банк платформы. "
        "Роли: администратор, методист (вход жюри). 400 — формат строки неверен (в сообщении путь до поля), 413 — файл больше лимита."
    ),
)
def import_pairs(body: ImportIn, user: Staff, db: DbSession) -> dict[str, Any]:
    out = svc.store_import(
        db, name=body.name, created_by=user.id, pairs=body.pairs, briefs=body.briefs, candidates=body.candidates,
        meta={"format": "pairs"},
    )
    db.commit()
    return out


@router.post(
    "/import/simple",
    status_code=201,
    response_model=SimpleImportOut,
    summary="Импорт жюри в простом формате (вакансии, кандидаты, разметка — CSV или JSON)",
    dependencies=[BodyLimit],
    description=(
        "Три таблицы: вакансии (`id, text, salary_from, salary_to, salary_net`), кандидаты (`id, spec, grade, skills` через «;», "
        "`experience_months, self_description`, необязательно `fsp`, `salary_min`, `salary_net`, `formats`, `city`), разметка "
        "(`vacancy_id, candidate_id, rel` 0–3). Каждая — CSV-текст с заголовком (разделитель «,» или «;») или JSON-массив. "
        "Кандидаты без теста → группа «категория по опросу, не подтверждена». У размеченной вакансии неразмеченные кандидаты "
        "набора по умолчанию считаются нерелевантными (rel 0). 400 — ошибка в таблице: в сообщении таблица, номер строки и поле; "
        "шаблоны — GET /api/admin/check/templates/{name}."
    ),
)
def import_simple(body: SimpleImportIn, user: Staff, db: DbSession) -> dict[str, Any]:
    data = simple.adapt(body.vacancies, body.candidates, body.labels, unlabeled_as_irrelevant=body.unlabeled_as_irrelevant)
    out = svc.store_import(
        db, name=body.name, created_by=user.id, pairs=data["pairs"], briefs=data["briefs"], candidates=data["candidates"],
        extra_warnings=data["warnings"], meta={"format": "simple", "stats": data["stats"]},
    )
    db.commit()
    return {**out, "stats": data["stats"]}


@router.get(
    "/import",
    response_model=list[BatchOut],
    summary="Список импортов жюри",
    description=(
        "Загрузки жюри (новые сверху) с числом брифов, кандидатов, кандидатов без теста и размеченных пар. Администратор видит "
        "все загрузки, методист — только свои."
    ),
)
def list_imports(user: Staff, db: DbSession) -> list[dict[str, Any]]:
    return [_batch_out(b) for b in svc.list_batches(db, owner_of(user))]


@router.delete(
    "/import/{batch_id}",
    response_model=DeletedOut,
    summary="Удалить импорт жюри",
    description=(
        "Удаляет загрузку вместе с её брифами, кандидатами и разметкой. Данные платформы не затрагиваются. Методист удаляет "
        "только свои загрузки (чужая — 404)."
    ),
)
def delete_import(batch_id: uuid.UUID, user: Staff, db: DbSession) -> dict[str, Any]:
    svc.delete_batch(db, batch_id, owner_of(user))
    db.commit()
    return {"deleted": True, "batch_id": batch_id}


@router.post(
    "/eval/rank",
    response_model=RankOut,
    summary="Ранжирование по брифу (топ-N с компонентами Score)",
    dependencies=[BodyLimit],
    description=(
        "Бриф (из импорта по `brief_id` или текстом с вилкой) → топ-N кандидатов тем же движком, что подборка работодателя: Score, "
        "нормированные компоненты T, K, F, A, U, вклады и «за»/«против». Кандидаты без теста — группа «категория по опросу, не "
        "подтверждена» ниже подтверждённых. `format=csv` отдаёт тот же топ в CSV. `source`: импорт жюри, слепой банк платформы или "
        "оба; кандидаты банка платформы — под псевдонимами, без ПДн. 404 — нет импорта или брифа, 422 — у брифа нет вилки."
    ),
)
def eval_rank(
    body: RankIn,
    user: Staff,
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
            batch = svc.batch_or_404(db, body.batch_id, owner_of(user))
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
    svc.blind_platform_ids(result)
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
    dependencies=[BodyLimit],
    description=(
        "Загрузите размеченные пары «бриф × кандидат» (формат eval/pairs_test.jsonl, rel 0–3; релевантен rel ≥ 2) — или не "
        "передавайте `pairs`, и возьмутся пары последнего импорта (методист — своего). Для каждого брифа ранжируются размеченные "
        "кандидаты нашим движком; считаются P@5, P@10, nDCG@10: среднее по брифам и 95% бутстреп-ДИ (тот же код, что в "
        "`make validate`). Если в строках есть `ranks_top20` (ours, b0–b3), по ним считаются те же метрики для сравнения."
    ),
)
def eval_pairs(body: PairsIn, user: Staff, db: DbSession) -> dict[str, Any]:
    rows = body.pairs
    if not rows:
        batch = svc.batch_or_404(db, body.batch_id, owner_of(user))
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
def validation(user: Staff, db: DbSession) -> dict[str, Any]:  # noqa: ARG001
    found = svc.validation_result()
    if found is None:
        raise not_found("validation_missing", "Прогона валидации ещё нет — выполните `make validate`")
    data, rel, stamp = found
    return {"source": rel, "file_time": stamp, "sections": sorted(data), "meta": data.get("meta", {}), "result": data}


# --------------------------------------------------------------------------- страница /lab/check

UseGold = Annotated[bool, Query(description="Эталонный разбор брифа из набора вместо разбора текста (по умолчанию — разбор, как в продукте)")]
Top = Annotated[int | None, Query(ge=1, description="Сколько мест показать (по умолчанию из config/demo.yaml)")]
TemplateName = Literal["vacancies.csv", "candidates.csv", "labels.csv", "example.json"]


def _top(top: int | None, key: str = "default_top") -> int:
    cfg = section("admin")["check"]
    return min(int(top or cfg[key]), int(cfg["max_top"]))


def _csv(content: str, filename: str) -> Response:
    return Response(
        content="﻿" + content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


class SimpleSetOut(BaseModel):
    """Весь набор простого формата одним JSON-файлом (example.json)."""

    vacancies: list[dict[str, Any]]
    candidates: list[dict[str, Any]]
    labels: list[dict[str, Any]]


@router.get(
    "/check/templates/{name}",
    summary="Шаблон простого формата для жюри",
    response_class=Response,
    responses={200: {"model": SimpleSetOut, "content": {"text/csv": {}}, "description": "Файл шаблона с примером на 3 вакансии"}},
    description=(
        "Шаблоны таблиц простого формата с примером: `vacancies.csv`, `candidates.csv`, `labels.csv` или всё одним "
        "`example.json` (ключи vacancies, candidates, labels). Компании и кандидаты в примере вымышленные."
    ),
)
def check_template(name: Annotated[TemplateName, Path(description="Имя файла шаблона")], user: Staff) -> Response:  # noqa: ARG001
    data = simple.template(name)
    media = "application/json" if name.endswith(".json") else "text/csv; charset=utf-8"
    return Response(content=data, media_type=media, headers={"Content-Disposition": f'attachment; filename="{name}"'})


@router.get(
    "/check/repo",
    response_model=CheckSummaryOut,
    summary="Наш набор пар: метрики и список брифов",
    description=(
        "Набор пар из репозитория (`eval/pairs_test.jsonl`, 20 test-брифов) → P@5, P@10, nDCG@10 со средним по брифам и 95% "
        "бутстреп-ДИ, сравнение с местами систем из `ranks_top20` (наш сквозной, B0–B3) и P@k по каждому брифу."
    ),
)
def check_repo(user: Staff, use_gold: UseGold = False) -> dict[str, Any]:  # noqa: ARG001
    return check.repo_summary(use_gold=use_gold)


@router.get(
    "/check/repo/briefs/{brief_id}",
    response_model=CheckBriefOut,
    summary="Наш набор пар: бриф и топ для ручной проверки",
    description=(
        "Текст брифа, как он разобран (специализация, грейд, must/nice, вилка) и эталон набора; топ-N размеченных кандидатов в "
        "том же порядке, по которому посчитаны P@k: место, Score, категория, «за»/«против», релевантность по оракулу 0–3."
    ),
)
def check_repo_brief(
    brief_id: Annotated[str, Path(max_length=64)], user: Staff, top: Top = None, use_gold: UseGold = False  # noqa: ARG001
) -> dict[str, Any]:
    return check.brief_detail(check.repo_dataset(), brief_id, top=_top(top), use_gold=use_gold)


@router.get(
    "/check/repo/tops.csv",
    summary="Наш набор пар: топы по всем брифам в CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV (UTF-8 с BOM)"}},
    description=(
        "Топ-N по каждому брифу набора из репозитория одной таблицей: бриф, место, кандидат, группа, категория, Score, "
        "компоненты T, K, F, A, U, релевантность по оракулу (rel, relevant), «за», «против», флаги."
    ),
)
def check_repo_csv(user: Staff, top: Top = None, use_gold: UseGold = False) -> Response:  # noqa: ARG001
    return _csv(check.tops_csv(check.repo_dataset(), top=_top(top, "csv_top"), use_gold=use_gold), "tops-pairs_test.csv")


@router.get(
    "/check/batches/{batch_id}",
    response_model=CheckSummaryOut,
    summary="Своя загрузка: метрики и список брифов",
    description="То же, что для набора из репозитория, по своей загрузке (методист — только свои). Без разметки метрик нет.",
)
def check_batch(batch_id: uuid.UUID, user: Staff, db: DbSession, use_gold: UseGold = False) -> dict[str, Any]:
    batch = svc.batch_or_404(db, batch_id, owner_of(user))
    return check.summary(check.batch_dataset(db, batch), use_gold=use_gold)


@router.get(
    "/check/batches/{batch_id}/briefs/{brief_id}",
    response_model=CheckBriefOut,
    summary="Своя загрузка: бриф и топ",
    description=(
        "Бриф с разметкой — топ размеченных кандидатов (как в метриках) с rel и ✓/✗; без разметки — топ по всем кандидатам "
        "загрузки в рекомендованных категориях, как в подборке работодателя."
    ),
)
def check_batch_brief(
    batch_id: uuid.UUID,
    brief_id: Annotated[str, Path(max_length=64)],
    user: Staff,
    db: DbSession,
    top: Top = None,
    use_gold: UseGold = False,
) -> dict[str, Any]:
    batch = svc.batch_or_404(db, batch_id, owner_of(user))
    return check.brief_detail(check.batch_dataset(db, batch), brief_id, top=_top(top), use_gold=use_gold)


@router.get(
    "/check/batches/{batch_id}/tops.csv",
    summary="Своя загрузка: топы по всем брифам в CSV",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}, "description": "CSV (UTF-8 с BOM)"}},
    description=(
        "Топ-N по каждому брифу своей загрузки одной таблицей (методист — только свои): место, кандидат, Score, компоненты, "
        "rel и relevant (если пара размечена), «за», «против», флаги."
    ),
)
def check_batch_csv(
    batch_id: uuid.UUID, user: Staff, db: DbSession, top: Top = None, use_gold: UseGold = False
) -> Response:
    batch = svc.batch_or_404(db, batch_id, owner_of(user))
    return _csv(check.tops_csv(check.batch_dataset(db, batch), top=_top(top, "csv_top"), use_gold=use_gold), f"tops-{batch.id}.csv")
