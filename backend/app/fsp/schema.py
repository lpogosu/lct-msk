"""Нормализованная схема ФСП на стороне платформы (то, что хранится у кандидата и идёт в VSS).

Ответ реестра (app/fsp/models.py) приводится к ней адаптером FSPProvider (app/fsp/provider.py):
результат склеивается со своим соревнованием, дисциплина ВРВС переводится в короткий код из config/fsp.yaml,
добавляются платформенные флаги (скрыт кандидатом, подтверждён протоколом). Концепция §6.4–6.5.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.fsp.models import (
    AgeGroup,
    Athlete,
    CompetitionFormat,
    Level,
    Rank,
    ResultRole,
    RoleBadge,
    Source,
    Stage,
    StatusType,
)


class FSPState(StrEnum):
    """Состояние профиля ФСП кандидата (концепция §6.4)."""

    not_linked = "not_linked"  # ФСП ID не привязан
    linked_no_results = "linked_no_results"  # привязан, в реестре ничего нет
    awaiting_protocol = "awaiting_protocol"  # есть только результаты, ждущие протокола
    linked = "linked"  # есть подтверждённые результаты или разряды


class NormalizedResult(BaseModel):
    """Результат, склеенный со своим соревнованием."""

    result_id: str
    ekp: str
    group_key: str  # отбор и финал одного соревнования → один ключ (берём максимум, бриф §5.4)
    title: str
    discipline: str | None  # короткий код (algo, product…); None — дисциплина не из справочника
    discipline_code: str
    level: Level
    status_type: StatusType
    stage: Stage
    age_group: AgeGroup
    format: CompetitionFormat
    start_date: date
    end_date: date
    field_size: int
    place: int | None
    place_to: int | None
    score: float | None
    max_score: float | None
    role: ResultRole
    team_id: str | None = None
    team_name: str | None = None
    team_size: int | None = None
    team_function: str | None = None
    protocol_url: str | None = None
    source: Source
    confirmed: bool  # протокол опубликован, результат подтверждён
    hidden: bool = False  # кандидат скрыл результат: не участвует ни в F, ни в K, ни в выдаче

    @property
    def worst_place(self) -> int | None:
        if self.place is None:
            return None
        if self.place_to is not None and self.place_to >= self.place:
            return self.place_to
        return self.place


class FSPProfile(BaseModel):
    """Профиль ФСП кандидата после нормализации."""

    sub: str | None = None
    state: FSPState = FSPState.not_linked
    athlete: Athlete | None = None
    results: list[NormalizedResult] = Field(default_factory=list)
    ranks: list[Rank] = Field(default_factory=list)
    roles: list[RoleBadge] = Field(default_factory=list)
    source: Source | None = None
    fetched_at: datetime | None = None

    @classmethod
    def not_linked(cls) -> FSPProfile:
        return cls()

    @property
    def counted_results(self) -> list[NormalizedResult]:
        """Результаты, которые участвуют в VSS: подтверждены, не скрыты, дисциплина известна."""
        return [r for r in self.results if r.confirmed and not r.hidden and r.discipline is not None]

    @property
    def pending_results(self) -> list[NormalizedResult]:
        return [r for r in self.results if not r.confirmed]

    @property
    def pending_count(self) -> int:
        return len(self.pending_results)
