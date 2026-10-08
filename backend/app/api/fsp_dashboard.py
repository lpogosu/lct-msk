"""Панель ФСП «Кадровый резерв»: агрегаты по участникам соревнований, их пути до найма и спросу работодателей.

Только для ролей «методист» и «администратор» (в демо — кнопка «Панель ФСП» на странице жюри). Ответ не содержит ПДн:
ячейки с числом людей меньше k скрыты (config/fsp_dashboard.yaml → privacy). Подробнее — docs/FSP_INTEGRATION.md.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field

from app.api.deps import DbSession, require_roles
from app.core.errors import error_responses
from app.models.enums import UserRole
from app.models.users import User
from app.services.fsp_dashboard import service
from app.services.fsp_dashboard.kanon import Cell

router = APIRouter(prefix="/fsp/dashboard", tags=["ФСП"])

Viewer = Annotated[User, Depends(require_roles(UserRole.methodist, UserRole.admin))]


class StageRow(BaseModel):
    key: str
    label: str
    fsp: Cell = Field(description="Спортсмены ФСП (привязан ФСП ID)")
    other: Cell = Field(description="Остальные кандидаты платформы — для сравнения")
    fsp_rate: float | None = Field(description="Доля от предыдущей стадии, %; null, если ячейка или знаменатель скрыты")
    other_rate: float | None


class BreakRow(BaseModel):
    key: str
    label: str
    stages: dict[str, Cell]


class RegionBlock(BaseModel):
    rows: list[BreakRow]
    other: BreakRow | None
    regions_total: int


class GradeCol(BaseModel):
    key: str
    label: str


class SpecGradeRow(BaseModel):
    key: str
    label: str
    cells: dict[str, Cell]


class SpecGradeBlock(BaseModel):
    grades: list[GradeCol]
    rows: list[SpecGradeRow]


class Timeline(BaseModel):
    weeks: list[str] = Field(description="Понедельники начала недель, ISO-дата")
    series: dict[str, list[Cell]]


class SalaryCohort(BaseModel):
    n: Cell
    median_net: int | None = Field(description="Медиана, руб./мес «на руки»; null, если скрыта")
    median_gross: int | None = Field(description="Та же медиана «до НДФЛ»")


class SalaryRow(BaseModel):
    key: str
    label: str
    fsp: SalaryCohort
    other: SalaryCohort


class SalaryBlock(BaseModel):
    rows: list[SalaryRow]
    ndfl_rate: float
    basis: str


class DemandRow(BaseModel):
    spec: str
    spec_label: str
    grade: str
    grade_label: str
    demand_companies: Cell = Field(description="Компаний с потребностью за период или опубликованной вакансией в категории")
    supply_fsp: Cell = Field(description="Спортсменов ФСП с подтверждённой категорией, открытых к предложениям")
    supply_all: Cell = Field(description="Все кандидаты платформы с этой категорией (для масштаба)")
    status: str = Field(description="deficit | balance | surplus | few_data | no_demand")


class DashboardOut(BaseModel):
    as_of: str
    k_min: int
    synthetic: bool = Field(description="Данные демонстрационные (мок реестра ФСП)")
    note: str | None
    funnel: list[StageRow]
    by_discipline: list[BreakRow]
    by_district: list[BreakRow]
    by_region: RegionBlock
    spec_grade: SpecGradeBlock
    timeline: Timeline
    companies: dict[str, Cell]
    salary: SalaryBlock
    demand_supply: list[DemandRow]
    privacy_note: str


@router.get(
    "",
    response_model=DashboardOut,
    summary="Кадровый резерв ФСП: агрегаты",
    description=(
        "Воронка «участник соревнований → тест → категория → подборки → приглашение → «Взаимно» → нанят» в сравнении с остальными "
        "кандидатами, разрезы по дисциплинам, округам и регионам, специализации × грейду, динамика по неделям, число компаний, "
        "медианы оферт «ФСП / без ФСП» и спрос против предложения. Ячейки с числом людей меньше k скрыты («<5»). Роли: методист, админ."
    ),
    responses=error_responses(401, 403),
)
def get_dashboard(db: DbSession, _user: Viewer, weeks: Annotated[int | None, Query(ge=4, le=52, description="Недель в графике")] = None) -> dict[str, Any]:
    return service.build_dashboard(db, weeks=weeks)


@router.get(
    "/export.csv",
    summary="Кадровый резерв ФСП: выгрузка CSV",
    description="Те же агрегаты плоской таблицей (раздел; строка; показатель; значение). Скрытые ячейки остаются «<5». UTF-8 с BOM, разделитель «;».",
    responses={200: {"content": {"text/csv": {}}, "description": "CSV"}, **error_responses(401, 403)},
    response_class=Response,
)
def export_csv(db: DbSession, _user: Viewer) -> Response:
    data = service.build_dashboard(db)
    return Response(
        content=service.to_csv(data).encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="fsp-reserve.csv"', "Cache-Control": "no-store"},
    )
