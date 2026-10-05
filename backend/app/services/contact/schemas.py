"""Схемы API механики контакта: профиль компании, приглашения, вакансии, отклики, уведомления.

Модели ответа — белый список: поле, которого нет в схеме, наружу не уйдёт (защита в глубину для DECISIONS 20).
Карточка кандидата для работодателя (`candidate`) — проекция матрицы видимости (CandidateBlind или
CandidateDisclosed) и поэтому описана как объект: её состав задаёт config/contact.yaml → visibility.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

# --- общее -------------------------------------------------------------------------------------


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IssueOut(BaseModel):
    code: str = Field(description="Машинный код причины")
    message: str = Field(description="Причина словами (рус.)")


class SalaryOut(BaseModel):
    salary_from: int
    salary_to: int
    salary_type: str = Field(description="net — на руки, gross — до НДФЛ")
    salary_type_label: str
    text: str = Field(description="«220 000–280 000 ₽ на руки»")


class QuotaOut(BaseModel):
    used: int
    limit: int
    remaining: int
    label: str = Field(description="«осталось 57/60»")
    reduced_reason: str | None = Field(default=None, description="Почему квота снижена ×0,5")
    next_slot_in: str | None = Field(default=None, description="Когда освободится слот (относительно)")


class ReliabilityOut(BaseModel):
    r: float = Field(description="R = (5·0,8 + good) / (5 + good + bad)")
    enough_data: bool
    display: str = Field(description="«Надёжность 0,92 · отвечает на отклики в срок 94% · …» или «мало данных»")
    breakdown: list[str]
    good: int
    bad: int
    discrepancy_badge: str | None = None
    quota_factor: float


class ContactMethodOut(BaseModel):
    method: str
    label: str
    value: str


class CompanyCardOut(BaseModel):
    """Что кандидат видит о компании всегда, до решения (концепция §4.5)."""

    id: uuid.UUID
    name: str
    field: str | None = None
    description: str | None = None
    website: str | None = None
    personal_email: bool
    personal_email_label: str | None = None
    contact_methods: list[ContactMethodOut]
    reliability: ReliabilityOut


class CompanyBriefOut(BaseModel):
    id: uuid.UUID
    name: str
    personal_email_label: str | None = None
    reliability: str = Field(description="Строка надёжности для карточки")
    r: float
    discrepancy_badge: str | None = None


class FeedbackLinkOut(BaseModel):
    kind: str = Field(description="task — короткая задача по модулю теста; interview — проверяется на интервью")
    skill: str
    module: str | None = None
    text: str


class FeedbackOut(BaseModel):
    reason: str
    label: str
    comment: str | None = None
    links: list[FeedbackLinkOut]


class EventOut(BaseModel):
    seq: int
    at: datetime
    action: str
    from_status: str | None = None
    to_status: str
    label: str = Field(description="Подпись статуса — одна и та же у обеих сторон")
    actor_role: str = Field(description="employer | candidate | system (без id участника — DECISIONS 20)")
    payload: dict[str, Any] | None = None


# --- профиль компании (Р1) ---------------------------------------------------------------------


class ContactMethodsIn(_In):
    email: str | None = Field(default=None, max_length=254)
    telegram: str | None = Field(default=None, max_length=64)
    phone: str | None = Field(default=None, max_length=32)


class CompanyProfileIn(_In):
    """Поля, не переданные в запросе, не меняются. Пустая строка очищает необязательное поле."""

    name: str | None = Field(default=None, max_length=200, examples=["ООО «Северный код»"])
    field: str | None = Field(default=None, max_length=200, examples=["Финтех: платёжный шлюз"])
    description: str | None = Field(default=None, max_length=3000)
    contact_methods: ContactMethodsIn | None = None
    inn: str | None = Field(default=None, max_length=12, description="Необязательно; 10 или 12 цифр")
    website: str | None = Field(default=None, max_length=300)
    webhook_url: str | None = Field(default=None, max_length=500, description="URL вебхука ATS [доп.]")


class WebhookDeliveryOut(BaseModel):
    event_id: uuid.UUID
    event_type: str
    target_url: str
    status: str = Field(description="pending | delivered | failed")
    attempts: int
    response_code: int | None = None
    response_excerpt: str | None = None
    created_text: str | None = None
    last_attempt_text: str | None = None
    next_attempt_text: str | None = None


class WebhookSettingsOut(BaseModel):
    url: str | None
    secret: str | None = Field(description="Ключ HMAC-SHA256 подписи (виден только сотрудникам компании)")
    signature_header: str
    event_header: str
    event_id_header: str


class CompanyMeOut(BaseModel):
    id: uuid.UUID
    email_domain: str
    personal_domain: bool
    name: str | None
    field: str | None
    description: str | None
    contact_methods: dict[str, str]
    inn: str | None
    website: str | None
    profile_complete: bool
    missing: list[str] = Field(description="Чего не хватает, чтобы отправлять приглашения")
    webhook: WebhookSettingsOut
    quota: QuotaOut
    reliability: ReliabilityOut
    complaints_90d: int = Field(description="Жалобы кандидатов на приглашения компании за 90 дней")
    preview: CompanyCardOut = Field(description="«Так видит кандидат»")


class WebhookLogOut(BaseModel):
    url: str | None
    deliveries: list[WebhookDeliveryOut]


# --- приглашения ------------------------------------------------------------------------------


class InvitationFormIn(_In):
    """Форма приглашения (Р7): пустые поля заполняются из брифа или вакансии."""

    need_id: uuid.UUID | None = None
    vacancy_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=500)
    description: str | None = Field(default=None, max_length=10000)
    salary_from: int | None = Field(default=None, ge=0)
    salary_to: int | None = Field(default=None, ge=0)
    salary_type: Literal["net", "gross"] | None = None
    format: str | None = Field(default=None, max_length=16)
    start_date: date | None = None
    contact_method: str | None = Field(default=None, max_length=16)
    contact_value: str | None = Field(default=None, max_length=254)
    response_days: int | None = Field(default=None, description="3, 5 или 7 дней (по умолчанию 5)")


class InvitationCreateIn(InvitationFormIn):
    candidate: str = Field(min_length=1, max_length=64, description="Псевдоним «К-4821» или ссылка ref из подборки")
    why_you: list[str] = Field(default_factory=list, max_length=3, description="Свой текст «почему вы» (до 3 пунктов)")


class InvitationBatchIn(InvitationFormIn):
    candidates: list[str] = Field(min_length=1, description="Псевдонимы или ссылки ref (не больше 10)")
    why_you: dict[str, list[str]] = Field(default_factory=dict, description="Кандидат → «почему вы»")


class TimersOut(BaseModel):
    respond_by: datetime | None = None
    respond_by_text: str | None = None
    counter_respond_by: datetime | None = None
    counter_respond_by_text: str | None = None
    contact_due_at: datetime | None = None
    contact_due_text: str | None = None
    outcome_check_at: datetime | None = None


class CounterOut(BaseModel):
    salary_from: int | None = None
    format: str | None = None
    start_date: date | None = None
    comment: str | None = None
    auto_disclose: bool
    summary: str = Field(description="Карточка разницы: «от 270 000 ₽ (ваша вилка …) · удалённо · в пределах вашей вилки»")
    within_range: bool
    countered_at: datetime | None = None


class TermsOut(BaseModel):
    ver: int
    author: str
    title: str
    description: str
    salary_from: int
    salary_to: int
    salary_type: str
    salary_type_label: str
    text: str
    format: str | None = None
    format_label: str | None = None
    start_date: date | None = None
    contact_method: str
    contact_method_label: str | None = None
    contact_value: str
    conditions: dict[str, Any]
    sha256: str = Field(description="Хеш неизменяемого снимка условий (антифейк §8.1)")
    verified: bool


class InvitationEmployerCard(BaseModel):
    id: uuid.UUID
    pseudonym: str
    candidate_ref: str | None = Field(default=None, description="Непрозрачная ссылка на кандидата в рамках компании")
    need_id: uuid.UUID | None = None
    vacancy_id: uuid.UUID | None = None
    status: str
    status_label: str
    title: str
    salary: SalaryOut
    sent_at: datetime
    sent_text: str | None = None
    viewed: bool
    contacts_open: bool
    contacted: bool
    contact_overdue: bool
    disputed: bool
    stage: str | None = None
    outcome: str | None = None
    flags: list[str]
    score: float | None = None
    rank_at_send: int | None = None
    counter: CounterOut | None = None
    decline_reason: str | None = None
    decline_reason_label: str | None = None
    decline_comment: str | None = None
    timers: TimersOut
    actions: list[str] = Field(description="Доступные работодателю действия (пути после /invitations/{id}/)")


class InvitationEmployerDetail(InvitationEmployerCard):
    terms: list[TermsOut]
    why_you: list[str]
    caution: list[str]
    score_snapshot: dict[str, Any] | None = None
    events: list[EventOut]
    candidate: dict[str, Any] = Field(
        description="CandidateBlind до взаимного «да», CandidateDisclosed — при действующем согласии"
    )


class KanbanColumnOut(BaseModel):
    status: str
    label: str
    count: int
    items: list[InvitationEmployerCard]


class DeclineSummaryOut(BaseModel):
    reason: str
    label: str
    count: int


class KanbanOut(BaseModel):
    columns: list[KanbanColumnOut] = Field(description="Встречные условия — первой колонкой")
    funnel: dict[str, int]
    decline_summary: list[DeclineSummaryOut]
    decline_hint: str | None = None
    avg_rank_at_send: float | None = None
    quota: QuotaOut


class RejectedOut(BaseModel):
    ref: str
    pseudonym: str
    errors: list[IssueOut]
    warnings: list[IssueOut] = Field(default_factory=list)
    notes: list[IssueOut] = Field(default_factory=list)


class SendResultOut(BaseModel):
    sent: list[InvitationEmployerCard]
    rejected: list[RejectedOut]
    warnings: list[str]
    notes: list[str]
    quota: QuotaOut


class PreviewCheckOut(BaseModel):
    ref: str
    pseudonym: str
    ok: bool
    errors: list[IssueOut]
    warnings: list[IssueOut]
    notes: list[IssueOut]
    why_you: list[str]


class InvitationPreviewOut(BaseModel):
    """«Так увидит кандидат» и результаты проверок квоты, лимита и паузы — без отправки."""

    title: str
    description: str
    salary: SalaryOut
    format_label: str | None = None
    contact_method_label: str | None = None
    respond_in_days: int
    company: CompanyCardOut
    caution: str | None = Field(default=None, description="Плашка «Требует осторожности», если сработали стоп-слова")
    caution_reasons: list[str]
    checks: list[PreviewCheckOut]
    quota: QuotaOut


class InvitationCandidateCard(BaseModel):
    id: uuid.UUID
    salary: SalaryOut = Field(description="Вилка — первой строкой")
    title: str
    company: CompanyBriefOut | None = None
    status: str
    status_label: str
    is_new: bool
    caution: str | None = None
    sent_text: str | None = None
    timers: TimersOut


class DeclineReasonOut(BaseModel):
    code: str
    label: str
    suggest_counter: bool
    hint: str | None = None


class ConsentInfoOut(BaseModel):
    text_version: str
    default_fields: list[str]
    contact_fields: dict[str, str]


class CounterLimitsOut(BaseModel):
    salary_min: int
    salary_max: int
    salary_step: int
    max_conditions: int
    comment_max_len: int
    conditions: dict[str, str]
    auto_disclose_default: bool


class InvitationCandidateDetail(BaseModel):
    id: uuid.UUID
    salary: SalaryOut
    title: str
    company: CompanyCardOut | None = None
    status: str
    status_label: str
    is_new: bool
    caution: str | None = None
    caution_reasons: list[str]
    sent_text: str | None = None
    timers: TimersOut
    terms: TermsOut
    terms_history: list[TermsOut]
    why_you: list[str]
    salary_vs_min: str | None = None
    counter: CounterOut | None = None
    counter_limits: CounterLimitsOut | None = None
    decline_reasons: list[DeclineReasonOut]
    consent: ConsentInfoOut
    contacts_open: bool
    contacted: bool
    can_dispute: bool
    outcome: str | None = None
    outcome_label: str | None = None
    outcome_check_options: dict[str, str] | None = None
    outcome_check: str | None = None
    feedback: list[FeedbackOut]
    actions: list[str]
    reported: bool = Field(description="Кандидат уже пожаловался на это приглашение")
    complaint_reasons: dict[str, str]
    events: list[EventOut]


class InvitationInboxOut(BaseModel):
    items: list[InvitationCandidateCard]
    new_count: int


class AcceptIn(_In):
    consent: bool = Field(description="Отдельная галочка «передать мои контакты компании X» (152-ФЗ)")
    fields: list[str] | None = Field(default=None, description="Какие поля раскрыть; по умолчанию — из настроек К13")


class DeclineIn(_In):
    reason: str = Field(description="salary | stack | format | company | not_looking | other_process | other")
    comment: str | None = Field(default=None, max_length=2000)


class CounterIn(_In):
    salary_from: int | None = Field(default=None, ge=0)
    format: str | None = Field(default=None, max_length=16)
    start_date: date | None = None
    comment: str | None = Field(default=None, max_length=2000)
    auto_disclose: bool | None = Field(default=None, description="«Раскрыть контакты, если примут» (по умолчанию да)")
    fields: list[str] | None = None


class ReasonIn(_In):
    reason: str
    comment: str | None = Field(default=None, max_length=2000)


class OutcomeIn(_In):
    outcome: Literal["hired", "not_matched"]
    reason: str | None = Field(default=None, description="Для «не сложилось»: skills_missing | position_closed | chose_other")
    skill_ids: list[str] = Field(default_factory=list, description="Каких навыков брифа не хватило")
    comment: str | None = Field(default=None, max_length=2000)


class DisputeIn(_In):
    comment: str | None = Field(default=None, max_length=2000)


class ReportIn(_In):
    reason: str = Field(description="fake_vacancy | payment_request | contacts_spam | misleading_terms | other")
    comment: str | None = Field(default=None, max_length=2000)


class CheckIn(_In):
    answer: str = Field(description="match | lower_salary | no_vacancy | other")


class GrantConsentIn(_In):
    consent: bool
    fields: list[str] | None = None


# --- вакансии -------------------------------------------------------------------------------


class VacancyCreateIn(_In):
    need_id: uuid.UUID = Field(description="Бриф, который публикуется как вакансия")
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    salary_from: int | None = Field(default=None, ge=0)
    salary_to: int | None = Field(default=None, ge=0)
    salary_type: Literal["net", "gross"] | None = None
    format: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)


class VacancyPatchIn(_In):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    salary_from: int | None = Field(default=None, ge=0)
    salary_to: int | None = Field(default=None, ge=0)
    salary_type: Literal["net", "gross"] | None = None
    format: str | None = Field(default=None, max_length=16)
    city: str | None = Field(default=None, max_length=120)


class VacancyOut(BaseModel):
    id: uuid.UUID
    need_id: uuid.UUID | None = None
    title: str
    description: str
    specialization: str | None = None
    grade: str | None = None
    salary: SalaryOut
    format: str | None = None
    format_label: str | None = None
    city: str | None = None
    status: str
    published_text: str | None = None


class VacancyEmployerOut(VacancyOut):
    applications_total: int
    applications_new: int
    how_we_check: dict[str, Any] | None = None


class VacancyPublishOut(BaseModel):
    vacancy: VacancyEmployerOut
    warnings: list[str]


class VacancyCandidateItem(VacancyOut):
    company: CompanyBriefOut | None = None
    score: float | None = Field(default=None, description="Тот же Score, что видит работодатель в подборке")
    parts: dict[str, float] | None = None
    category: str | None = None
    weak_match: bool
    application_status: str | None = None
    application_id: uuid.UUID | None = None


class VacancyCandidateDetail(VacancyOut):
    company: CompanyCardOut | None = None
    score: float | None = None
    parts: dict[str, float] | None = None
    category: str | None = None
    how_we_check: dict[str, Any] | None = None
    application_status: str | None = None
    application_id: uuid.UUID | None = None
    consent: dict[str, Any]
    response_days: int


# --- отклики --------------------------------------------------------------------------------


class ApplyIn(_In):
    vacancy_id: uuid.UUID
    consent: bool = Field(description="Отдельная галочка согласия на передачу контактов этой компании")
    fields: list[str] | None = None


class RejectIn(_In):
    reason: str = Field(description="skills_missing | salary | position_closed | chose_other | other")
    comment: str | None = Field(default=None, max_length=2000)
    skill_ids: list[str] = Field(default_factory=list)


class ApplicationTimersOut(BaseModel):
    sla_due_at: datetime | None = None
    sla_due_text: str | None = None


class ApplicationEmployerCard(BaseModel):
    id: uuid.UUID
    vacancy_id: uuid.UUID
    pseudonym: str
    candidate_ref: str | None = None
    status: str
    status_label: str
    score: float | None = None
    category: str | None = None
    submitted_text: str | None = None
    viewed: bool
    contacts_visible: bool
    reject_reason: str | None = None
    reject_reason_label: str | None = None
    timers: ApplicationTimersOut
    actions: list[str]


class ApplicationEmployerDetail(ApplicationEmployerCard):
    vacancy: VacancyOut
    score_snapshot: dict[str, Any] | None = None
    candidate: dict[str, Any] = Field(description="Карточка кандидата: контакты видны — он откликнулся сам")
    events: list[EventOut]
    reject_reasons: dict[str, str]


class ApplicationCompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    reliability: str
    r: float


class ApplicationCandidateCard(BaseModel):
    id: uuid.UUID
    vacancy: VacancyOut | None = None
    company: ApplicationCompanyOut | None = None
    status: str
    status_label: str
    submitted_text: str | None = None
    score: float | None = None
    contacts_shared: bool
    timers: ApplicationTimersOut
    actions: list[str]


class ApplicationCandidateDetail(ApplicationCandidateCard):
    reject_reason: str | None = None
    reject_reason_label: str | None = None
    feedback: list[FeedbackOut]
    disclosure_fields: list[str]
    events: list[EventOut]


# --- уведомления ----------------------------------------------------------------------------


class NotificationOut(BaseModel):
    id: uuid.UUID
    kind: str = Field(description="invitation | application | task | company | system")
    kind_label: str
    message: str
    entity_type: str | None = None
    entity_id: str | None = None
    payload: dict[str, Any]
    created_at: datetime
    created_text: str
    read: bool


class UnreadOut(BaseModel):
    total: int
    by_kind: dict[str, int]


class NotificationListOut(BaseModel):
    items: list[NotificationOut]
    unread: UnreadOut


class ReadAllIn(_In):
    kind: Literal["invitation", "application", "task", "company", "system"] | None = None


class ReadAllOut(BaseModel):
    marked: int
    unread: UnreadOut
