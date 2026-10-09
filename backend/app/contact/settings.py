"""Типизированный и проверенный config/contact.yaml.

Ошибка в конфиге (неизвестное правило видимости, ПДн в слепой проекции, битый regex) падает при загрузке,
а не в рантайме приглашения.
"""

from __future__ import annotations

import re
from datetime import date
from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.configs import load_config

from .statuses import ApplicationStatus, InvitationStatus


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class SalaryCfg(_Frozen):
    currency: str
    period: str
    min_from: int
    step: int
    soft_max_ratio: float
    hard_max_ratio: float
    types: dict[str, str]

    @model_validator(mode="after")
    def _ratios(self) -> SalaryCfg:
        if not 1.0 < self.soft_max_ratio <= self.hard_max_ratio:
            raise ValueError("salary: требуется 1 < soft_max_ratio ≤ hard_max_ratio")
        return self


class InvitationCfg(_Frozen):
    title_max_len: int
    description_min_len: int
    description_max_len: int
    why_you_max_items: int
    promises: dict[str, str] = {}  # «обещание процесса»: код → текст (config/contact.yaml)
    contact_methods: dict[str, str]
    formats: dict[str, str]


class RemindersCfg(_Frozen):
    after_sent_hours: int
    before_deadline_hours: int


class ResponseCfg(_Frozen):
    windows_days: list[int]
    default_days: int
    reminders: RemindersCfg

    @model_validator(mode="after")
    def _default_in_windows(self) -> ResponseCfg:
        if self.default_days not in self.windows_days:
            raise ValueError("response.default_days должен быть одним из windows_days")
        return self


class CounterCfg(_Frozen):
    max_rounds: int
    max_conditions: int
    comment_max_len: int
    salary_max_ratio_to: float
    salary_step: int
    auto_disclose_default: bool
    conditions: dict[str, str]


class EmployerCfg(_Frozen):
    counter_response_days: int
    first_contact_business_days: int
    outcome_check_after_days: int
    contact_reminder_before_hours: int


class BusinessDaysCfg(_Frozen):
    weekend: list[int]
    holidays: list[date]


class ReasonsCfg(_Frozen):
    decline: dict[str, str]
    decline_suggest_counter: list[str]
    withdraw: dict[str, str]
    counter_decline: dict[str, str]
    outcome: dict[str, str]
    not_matched: dict[str, str]
    outcome_check: dict[str, str]
    application_reject: dict[str, str]
    complaint: dict[str, str] = Field(default_factory=dict)


class StatusLabelsCfg(_Frozen):
    invitation: dict[str, str]
    application: dict[str, str]
    stages: dict[str, str]

    @model_validator(mode="after")
    def _complete(self) -> StatusLabelsCfg:
        missing = {s.value for s in InvitationStatus} - set(self.invitation)
        missing |= {s.value for s in ApplicationStatus} - set(self.application)
        if missing:
            raise ValueError(f"status_labels: нет подписей для {sorted(missing)}")
        return self


class LowReliabilityCfg(_Frozen):
    r_below: float
    min_events: int
    label: str


class QuotasCfg(_Frozen):
    active_per_need: int
    new_per_owner: int
    window_days: int
    batch_max: int
    rate_limit_per_minute: int
    reduced_factor: float
    low_reliability: LowReliabilityCfg
    personal_email_label: str
    personal_email_domains: list[str]


class CandidateLimitsCfg(_Frozen):
    weekly_options: list[int | None]
    default_weekly: int | None

    @model_validator(mode="after")
    def _default_in_options(self) -> CandidateLimitsCfg:
        if self.default_weekly not in self.weekly_options:
            raise ValueError("candidate_limits.default_weekly должен быть одним из weekly_options")
        return self


class InterestCfg(_Frozen):
    per_window: int
    window_days: int
    lifetime_days: int
    consent_text_version: str
    badge: str
    badge_short: str
    badge_hint: str
    consent_text: str

    @model_validator(mode="after")
    def _positive(self) -> InterestCfg:
        if min(self.per_window, self.window_days, self.lifetime_days) < 1:
            raise ValueError("interest: per_window, window_days, lifetime_days должны быть положительными")
        return self


class PairCfg(_Frozen):
    one_active_per_pair: bool
    pause_days: dict[str, int]
    salary_exception_factor: float
    salary_exception_reasons: list[str]
    # DECISIONS 22: пауза после отзыва приглашения работодателем и отзыва согласия кандидатом
    withdrawal_pause_days: dict[str, int] = Field(default_factory=dict)


class MatchingCfg(_Frozen):
    weak_score_below: float


class GuardianCfg(_Frozen):
    token_ttl_hours: int
    resend_cooldown_minutes: int
    name_min_len: int
    name_max_len: int


class MinorsCfg(_Frozen):
    registration_min_age: int
    hidden_below_age: int
    guardian_consent_below_age: int
    internship_only_below_age: int
    employer_notice: str
    invite_restriction: str
    guardian: GuardianCfg


class DiscrepancyCfg(_Frozen):
    window_days: int
    min_distinct_candidates: int
    kinds: list[str]
    badge: str


class ReliabilityCfg(_Frozen):
    prior_mean: float
    prior_strength: float
    min_events_for_value: int
    low_data_label: str
    discrepancy: DiscrepancyCfg
    good: dict[str, str]
    bad: dict[str, str]

    @model_validator(mode="after")
    def _disjoint(self) -> ReliabilityCfg:
        if set(self.good) & set(self.bad):
            raise ValueError("reliability: событие не может быть одновременно good и bad")
        if not set(self.discrepancy.kinds) <= set(self.bad):
            raise ValueError("reliability.discrepancy.kinds должны быть bad-событиями")
        return self


class ApplicationCfg(_Frozen):
    response_days: int


class FraudRuleCfg(_Frozen):
    code: str
    reason: str
    patterns: list[str]


class AntifraudCfg(_Frozen):
    label: str
    rules: list[FraudRuleCfg]
    short_link_domains: list[str]

    @model_validator(mode="after")
    def _compile(self) -> AntifraudCfg:
        for rule in self.rules:
            for p in rule.patterns:
                re.compile(p)
        return self


class PseudonymCfg(_Frozen):
    prefix: str
    digits: int = Field(ge=3, le=8)
    max_collision_attempts: int


class DisclosureCfg(_Frozen):
    consent_text_version: str
    default_fields: list[str]
    contact_fields: dict[str, str] = Field(min_length=1)  # код поля → подпись для кандидата

    @model_validator(mode="after")
    def _default_has_channel(self) -> DisclosureCfg:
        if not set(self.contact_fields) & set(self.default_fields):
            raise ValueError("disclosure.default_fields должны включать хотя бы один канал связи")
        return self


Rule = Literal["show", "hide", "never", "if_allowed", "test_band", "fsp_public", "salary_signal"]


class FieldRule(_Frozen):
    blind: Rule
    disclosed: Rule


class TestBand(_Frozen):
    min_percentile: float
    label: str


class PlaceBand(_Frozen):
    max_place: int
    label: str


class SalarySignalCfg(_Frozen):
    within: str
    above: str


class VisibilityCfg(_Frozen):
    fields: dict[str, FieldRule]
    pii_fields: list[str]
    never_fields: list[str]
    test_public_keys: list[str]
    test_bands: list[TestBand]
    test_band_default: str
    fsp_public_keys: list[str]
    fsp_place_bands: list[PlaceBand]
    fsp_place_default: str
    salary_signal: SalarySignalCfg

    @model_validator(mode="after")
    def _privacy(self) -> VisibilityCfg:
        # §4.5: ПДн до раскрытия — никогда. Конфиг, который это нарушает, не загружается.
        for name in self.pii_fields:
            rule = self.fields.get(name)
            if rule is not None and rule.blind not in ("hide", "never"):
                raise ValueError(f"visibility: ПДн-поле {name} открыто в слепой проекции")
        for name in self.never_fields:
            rule = self.fields.get(name)
            if rule is not None and (rule.blind, rule.disclosed) != ("never", "never"):
                raise ValueError(f"visibility: поле {name} должно быть скрыто всегда (never/never)")
        if not set(self.never_fields) <= set(self.pii_fields):
            raise ValueError("visibility.never_fields должны входить в pii_fields")
        leaked = (set(self.test_public_keys) | set(self.fsp_public_keys)) & set(self.pii_fields)
        if leaked:
            raise ValueError(f"visibility: публичные ключи содержат ПДн {sorted(leaked)}")
        return self


class AdvancePreset(_Frozen):
    label: str
    days: int


class DemoCfg(_Frozen):
    advance_presets: list[AdvancePreset]


class CompanyProfileCfg(_Frozen):
    name_max_len: int
    field_max_len: int
    description_min_len: int
    description_max_len: int
    inn_lengths: list[int]
    required: dict[str, str]


class WebhooksCfg(_Frozen):
    timeout_s: float
    max_attempts: int = Field(ge=1)
    backoff_base_s: float
    backoff_factor: float
    log_size: int
    signature_header: str
    event_header: str
    event_id_header: str
    secret_bytes: int = Field(ge=16)
    allowed_schemes: list[str]
    # SSRF: внутренние адреса запрещены (true — только для локальной разработки)
    allow_private_hosts: bool = False
    blocked_host_suffixes: list[str] = Field(default_factory=list)


class RubricCfg(_Frozen):
    max_points: int
    criteria: dict[str, str] = Field(min_length=1)


class TasksCfg(_Frozen):
    employer_active_max: int
    max_minutes: int
    solve_grace_s: int = 30
    audience_grade_window: int
    offer_interval_days: int
    candidate_active_max: int
    skip_streak: int
    skip_pause_days: int
    assignment_due_days: int
    check_set_due_days: int
    title_max_len: int
    prompt_min_len: int
    prompt_max_len: int
    answer_max_len: int
    comment_max_len: int
    not_real_work_label: str
    rubric: RubricCfg
    training_label: str
    check_set_label: str
    kind_labels: dict[str, str]
    effects: dict[str, str]
    status_labels: dict[str, str]


class FeedbackCfg(_Frozen):
    task_link_text: str
    retake_link_text: str


class NotificationsCfg(_Frozen):
    list_limit: int
    kinds: dict[str, str]


class HttpLimitsCfg(_Frozen):
    respond_per_minute: int
    apply_per_minute: int
    task_submit_per_minute: int
    webhook_test_per_minute: int


class ChatCfg(_Frozen):
    max_len: int = Field(ge=1)
    page_size: int = Field(ge=1)
    rate_per_minute: int = Field(ge=1)
    thread_max_messages: int = Field(ge=1)
    email_notify: bool
    email_min_interval_min: int
    demo_reply_enabled: bool
    demo_replies: list[str] = Field(min_length=1)


class AtsHuntflowCfg(_Frozen):
    default_base_url: str
    timeout_s: float
    emulator_account_id: int
    stage_names: dict[str, str]
    emulator_stage_ids: dict[str, int]
    emulator_rejection_reasons: dict[str, int]


class AtsStatusMapCfg(_Frozen):
    invitation: list[str]
    application: list[str]


class AtsCfg(_Frozen):
    page_size_default: int = Field(ge=1)
    page_size_max: int = Field(ge=1)
    key_prefix: str
    key_secret_bytes: int = Field(ge=16)
    max_keys_per_company: int = Field(ge=1)
    rate_per_minute: int = Field(ge=1)
    log_size: int = Field(ge=1)
    idempotency_ttl_days: int = Field(ge=1)
    scopes: dict[str, str]
    types: dict[str, str]
    huntflow: AtsHuntflowCfg
    status_map: AtsStatusMapCfg


class ContactConfig(_Frozen):
    version: int
    timezone_offset_hours: int
    salary: SalaryCfg
    invitation: InvitationCfg
    response: ResponseCfg
    counter_offer: CounterCfg
    employer: EmployerCfg
    business_days: BusinessDaysCfg
    reasons: ReasonsCfg
    status_labels: StatusLabelsCfg
    quotas: QuotasCfg
    candidate_limits: CandidateLimitsCfg
    interest: InterestCfg
    pair: PairCfg
    matching: MatchingCfg
    minors: MinorsCfg
    reliability: ReliabilityCfg
    application: ApplicationCfg
    antifraud: AntifraudCfg
    pseudonym: PseudonymCfg
    disclosure: DisclosureCfg
    visibility: VisibilityCfg
    demo: DemoCfg
    # Слой API (профиль компании Р1, вебхуки ATS, регулярные задачи, обратная связь, уведомления)
    company_profile: CompanyProfileCfg
    webhooks: WebhooksCfg
    tasks: TasksCfg
    feedback: FeedbackCfg
    notifications: NotificationsCfg
    http_limits: HttpLimitsCfg
    chat: ChatCfg
    ats: AtsCfg

    @model_validator(mode="after")
    def _cross(self) -> ContactConfig:
        disclosable = {k for k, r in self.visibility.fields.items() if r.disclosed == "if_allowed"}
        extra = set(self.disclosure.default_fields) - disclosable
        if extra:
            raise ValueError(f"disclosure.default_fields: поля {sorted(extra)} не раскрываются по согласию")
        extra = set(self.disclosure.contact_fields) - disclosable
        if extra:
            raise ValueError(f"disclosure.contact_fields: поля {sorted(extra)} не раскрываются по согласию")
        return self


@lru_cache(maxsize=1)
def get_contact_config() -> ContactConfig:
    return ContactConfig.model_validate(load_config("contact"))
