"""Схемы API регулярных задач (Р11, К11) и проверочного набора по вакансии (DECISIONS 3)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FamilyOut(BaseModel):
    family_id: str
    title: str
    module: str
    module_name: str | None = None
    specializations: list[str]
    grade: str
    grade_name: str | None = None
    minutes: int
    format: str
    calibrated: bool = Field(description="Бейдж «откалиброван»: у семейства есть рабочие параметры IRT")


class TaskCreateIn(_In):
    kind: Literal["catalog", "approach"] = Field(description="catalog — тип A из каталога; approach — тип B «предложи подход»")
    specialization: str = Field(description="Аудитория: специализация (грейд ±1)", examples=["backend_python"])
    grade: Literal["intern", "junior", "middle", "senior"]
    not_real_work_ack: bool = Field(description="Отметка «это не рабочая задача компании» — обязательна")
    title: str | None = Field(default=None, max_length=200)
    family_id: str | None = Field(default=None, max_length=64, description="Для типа A — семейство из каталога")
    prompt: str | None = Field(default=None, max_length=5000, description="Для типа B — текст задачи (≥ 50 символов)")
    time_limit_min: int | None = Field(default=None, ge=1, le=60, description="Для типа B, не больше 15 минут")
    tags: list[str] = Field(default_factory=list, max_length=10, description="Навыки задачи (id справочника)")


class AudienceOut(BaseModel):
    specialization: str | None = None
    specialization_name: str | None = None
    grade: str | None = None
    grade_name: str | None = None
    window: int


class TaskStatsOut(BaseModel):
    offered: int
    submitted: int
    awaiting_review: int
    skipped: int
    avg_score: float | None = None


class TaskOut(BaseModel):
    id: uuid.UUID
    kind: str
    kind_label: str
    title: str
    family_id: str | None = None
    module: str | None = None
    module_name: str | None = None
    prompt: str | None = None
    rubric: dict[str, Any] | None = None
    audience: AudienceOut
    tags: list[str]
    minutes: int
    status: str
    is_check_set: bool
    need_id: uuid.UUID | None = None
    not_real_work_label: str
    stats: TaskStatsOut


class SubmissionOut(BaseModel):
    """Решение обезличено: псевдоним в рамках компании и ссылка для «пригласить» (DECISIONS 20)."""

    assignment_id: uuid.UUID
    pseudonym: str
    candidate_ref: str | None = None
    status: str
    status_label: str
    submitted_text: str | None = None
    answer: Any | None = Field(default=None, description="Текст решения типа B")
    is_correct: bool | None = None
    score: float | None = Field(default=None, description="Доля 0..1")
    score_text: str | None = None
    rubric_scores: dict[str, int] | None = None
    review_comment: str | None = None
    can_review: bool


class ReviewIn(_In):
    scores: dict[str, int] = Field(description="Критерий рубрики → балл 0–2 (все 4 критерия)")
    comment: str | None = Field(default=None, max_length=2000)


class CheckSetIn(_In):
    need_id: uuid.UUID
    candidates: list[str] = Field(min_length=1, max_length=30, description="Псевдонимы или ссылки из шортлиста брифа")


class CheckSetSlotOut(BaseModel):
    module: str
    module_name: str
    family_id: str
    title: str
    minutes: int
    skills: list[str]


class CheckSetSentOut(BaseModel):
    ref: str | None = None
    pseudonym: str
    assignments: int


class CheckSetRejectedOut(BaseModel):
    ref: str | None = None
    pseudonym: str
    message: str


class CheckSetOut(BaseModel):
    need_id: uuid.UUID
    slots: list[CheckSetSlotOut]
    untestable: list[str] = Field(description="Обязательные навыки, которые тестом не проверяются")
    total_minutes: int = Field(description="Не больше 15 минут на кандидата")
    due_text: str | None = None
    sent: list[CheckSetSentOut]
    rejected: list[CheckSetRejectedOut]
    note: str | None = None


class CheckSetItemOut(BaseModel):
    module: str | None = None
    module_name: str | None = None
    title: str
    status: str
    status_label: str
    is_correct: bool | None = None
    finished_text: str | None = None


class CheckSetCandidateOut(BaseModel):
    pseudonym: str
    candidate_ref: str | None = None
    done: int
    total: int
    correct: int
    items: list[CheckSetItemOut]


class CheckSetResultsOut(BaseModel):
    need_id: uuid.UUID
    candidates: list[CheckSetCandidateOut]


class OriginOut(BaseModel):
    kind: str = Field(description="company | platform | check_set")
    label: str
    company: dict[str, Any] | None = None


class AssignmentResultOut(BaseModel):
    score: float | None = None
    score_text: str | None = None
    is_correct: bool | None = None
    rubric_scores: dict[str, int] | None = None
    review_comment: str | None = None


class AssignmentOut(BaseModel):
    id: uuid.UUID
    task_id: uuid.UUID
    kind: str
    kind_label: str
    title: str
    module: str | None = None
    module_name: str | None = None
    minutes: int
    effect: str = Field(description="На что влияет задача")
    origin: OriginOut
    source: str
    status: str
    status_label: str
    due_text: str | None = None
    solve_until_text: str | None = None
    not_real_work_label: str
    result: AssignmentResultOut | None = None


class TasksLimitsOut(BaseModel):
    offer_interval_days: int
    active_max: int
    skip_streak: int
    skip_pause_days: int


class TasksOverviewOut(BaseModel):
    active: list[AssignmentOut]
    history: list[AssignmentOut]
    limits: TasksLimitsOut
    paused_until_text: str | None = None
    next_offer_text: str | None = None
    empty_text: str | None = None


class AssignmentStartOut(AssignmentOut):
    item: dict[str, Any] | None = Field(default=None, description="Тип A: ваш экземпляр (без ключа) и код варианта")
    prompt: str | None = Field(default=None, description="Тип B: текст задачи")
    rubric: dict[str, Any] | None = None
    answer_max_len: int | None = None


class SubmitIn(_In):
    answer: Any = Field(description="Тип A — ответ в формате задания; тип B — текст подхода (без контактов)")


class OutcomeOut(BaseModel):
    message: str
    correct: bool | None = None
    timed_out: bool | None = None
    explanation: str | None = None
    correct_answer: Any | None = None
    misconception: str | None = None
    upgrade_hint: str | None = None
    paused_until_text: str | None = None


class AssignmentActionOut(AssignmentOut):
    outcome: OutcomeOut
