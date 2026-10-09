"""Типизированная загрузка config/fsp.yaml (pydantic-валидация весов VSS и матриц релевантности)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, model_validator

from app.core.configs import load_config
from app.fsp.models import Level, RankCode, ResultRole, Source, Stage, StatusType


class SportCfg(BaseModel):
    """Вид спорта по ВРВС: «спортивное программирование», код 1010001311Я (config/fsp.yaml: sport)."""

    code: str = Field(pattern=r"^\d{10}[А-ЯA-Z]$")
    name: str


class DisciplineCfg(BaseModel):
    code: str  # код ВРВС
    short: str  # algo | product | bas | robotics | infosec
    name: str  # наименование дисциплины в ВРВС — для выписки из протокола
    label: str  # подпись в интерфейсе: «алгоритмическое», «продуктовое программирование (хакатон)»
    scoring_kind: str


class LevelRule(BaseModel):
    level: Level
    status: list[StatusType] | None = None
    stage: list[Stage] | None = None
    weight: float = Field(gt=0, le=1)

    def matches(self, level: Level, status: StatusType, stage: Stage) -> bool:
        return (
            self.level == level
            and (self.status is None or status in self.status)
            and (self.stage is None or stage in self.stage)
        )


class PlaceCfg(BaseModel):
    exponent: float = Field(gt=0)
    field_size_ref: int = Field(gt=1)
    podium_bonus: dict[int, float]
    raw_score_weight: float = Field(ge=0, le=1)
    q_min: float = Field(ge=0, le=1)


class AggregationCfg(BaseModel):
    top_n: int = Field(ge=1)
    decay: float = Field(gt=0, le=1)


class ResultClassCfg(BaseModel):
    """Класс результата C: призёр или участник (DECISIONS п. 29, config/fsp.yaml: vss.result_class)."""

    podium_max_place: int = Field(ge=1)
    class_weights: dict[str, float]
    participation_max_counted: int = Field(ge=0)

    @model_validator(mode="after")
    def _classes(self) -> ResultClassCfg:
        if set(self.class_weights) != {"podium", "participant"}:
            raise ValueError("vss.result_class.class_weights: нужны классы podium и participant")
        if any(not 0.0 <= w <= 1.0 for w in self.class_weights.values()):
            raise ValueError("vss.result_class.class_weights: значения вне [0, 1]")
        return self


class VSSCfg(BaseModel):
    level_rules: list[LevelRule]
    result_class: ResultClassCfg
    place: PlaceCfg
    role_weights: dict[ResultRole, float]
    freshness_half_life_months: float = Field(gt=0)
    days_per_month: float = Field(gt=0)
    source_weights: dict[Source, float]
    aggregation: AggregationCfg
    # Разряды норм ЕВСК по спортивному программированию. Кода нет в словаре (MSMK) — разряд не учитывается.
    rank_weights: dict[RankCode, float]
    rank_validity_years: dict[RankCode, int] = Field(default_factory=dict)  # нет кода — бессрочно (МС)
    rank_factor: float = Field(ge=0)
    scale: float = Field(gt=0)
    rank_only_cap: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _complete(self) -> VSSCfg:
        for enum, mapping, name in (
            (ResultRole, self.role_weights, "role_weights"),
            (Source, self.source_weights, "source_weights"),
        ):
            missing = [m.value for m in enum if m not in mapping]
            if missing:
                raise ValueError(f"vss.{name}: нет весов для {missing}")
        # rank_weights — не все коды контракта: МСМК в нормах ЕВСК по спортивному программированию нет.
        if not self.rank_weights:
            raise ValueError("vss.rank_weights: нужен хотя бы один разряд")
        if any(not 0.0 < w <= 1.0 for w in self.rank_weights.values()):
            raise ValueError("vss.rank_weights: веса в (0, 1]")
        orphan = [c.value for c in self.rank_validity_years if c not in self.rank_weights]
        if orphan:
            raise ValueError(f"vss.rank_validity_years: срок для разрядов без веса {orphan}")
        if any(y < 1 for y in self.rank_validity_years.values()):
            raise ValueError("vss.rank_validity_years: срок — целое число лет ≥ 1")
        covered = {r.level for r in self.level_rules if r.status is None and r.stage is None}
        if covered != set(Level):
            raise ValueError("vss.level_rules: у каждого уровня должно быть правило по умолчанию")
        return self


class PlaceBand(BaseModel):
    label: str
    max_place: int | None = None
    max_share: float | None = None


class FSPConfig(BaseModel):
    version: int
    sport: SportCfg
    disciplines: list[DisciplineCfg]
    vss: VSSCfg
    skill_evidence_factor: float = Field(gt=0, le=1)
    spec_relevance: dict[str, dict[str, float]]
    module_relevance: dict[str, dict[str, float]]
    place_bands: list[PlaceBand]

    @model_validator(mode="after")
    def _consistent(self) -> FSPConfig:
        shorts = {d.short for d in self.disciplines}
        for name in ("spec_relevance", "module_relevance"):
            matrix: dict[str, dict[str, float]] = getattr(self, name)
            if set(matrix) != shorts:
                raise ValueError(f"{name}: строки должны совпадать с дисциплинами {sorted(shorts)}")
            for d, row in matrix.items():
                bad = {k: v for k, v in row.items() if not 0.0 <= v <= 1.0}
                if bad:
                    raise ValueError(f"{name}.{d}: значения вне [0, 1]: {bad}")
        return self

    def discipline_by_code(self, code: str) -> DisciplineCfg | None:
        return next((d for d in self.disciplines if d.code == code), None)

    def discipline_by_short(self, short: str) -> DisciplineCfg | None:
        return next((d for d in self.disciplines if d.short == short), None)


@lru_cache(maxsize=1)
def get_fsp_config() -> FSPConfig:
    return FSPConfig.model_validate(load_config("fsp"))
