"""Типизированная загрузка config/fsp.yaml (pydantic-валидация весов VSS и матриц релевантности)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import BaseModel, Field, model_validator

from app.core.configs import load_config
from app.fsp.models import Level, RankCode, ResultRole, Source, Stage, StatusType


class DisciplineCfg(BaseModel):
    code: str  # код ВРВС
    short: str  # algo | product | bas | robotics | infosec
    name: str
    label: str  # короткая подпись для карточки: «продуктовое»
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


class VSSCfg(BaseModel):
    level_rules: list[LevelRule]
    place: PlaceCfg
    role_weights: dict[ResultRole, float]
    freshness_half_life_months: float = Field(gt=0)
    days_per_month: float = Field(gt=0)
    source_weights: dict[Source, float]
    aggregation: AggregationCfg
    rank_weights: dict[RankCode, float]
    rank_factor: float = Field(ge=0)
    scale: float = Field(gt=0)
    rank_only_cap: float = Field(gt=0, le=100)

    @model_validator(mode="after")
    def _complete(self) -> VSSCfg:
        for enum, mapping, name in (
            (ResultRole, self.role_weights, "role_weights"),
            (Source, self.source_weights, "source_weights"),
            (RankCode, self.rank_weights, "rank_weights"),
        ):
            missing = [m.value for m in enum if m not in mapping]
            if missing:
                raise ValueError(f"vss.{name}: нет весов для {missing}")
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
