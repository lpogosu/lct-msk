"""Перечисления статусов и видов. В БД хранятся строками (VARCHAR), не PG ENUM:
так новые значения не требуют миграции типа. Жёсткие CHECK — только для роли и цели согласия
(безопасность и 152-ФЗ). Источники: концепция §3–§5, spec-testing §2.4/§4, spec-contact §2.3/§4.
"""

from __future__ import annotations

from enum import StrEnum


class UserRole(StrEnum):
    candidate = "candidate"  # «Ищу работу»
    employer = "employer"  # «Нанимаю»
    methodist = "methodist"  # лаборатория: банк заданий, флаги
    admin = "admin"


SELF_REGISTER_ROLES = (UserRole.candidate, UserRole.employer)


class UserStatus(StrEnum):
    pending_email = "pending_email"  # до подтверждения email можно только смотреть лендинг
    active = "active"
    blocked = "blocked"


class EmailCodePurpose(StrEnum):
    verify_email = "verify_email"
    password_reset = "password_reset"


class ConsentPurpose(StrEnum):
    """Отдельные согласия (152-ФЗ): каждое — своя запись в журнале с версией текста."""

    processing = "processing"  # обработка ПДн (при регистрации, обязательное)
    blind_card = "blind_card"  # показ слепой карточки работодателям (без него кандидата нет в банке)
    fsp_enrichment = "fsp_enrichment"  # обогащение профиля данными ФСП
    contact_disclosure = "contact_disclosure"  # передача контактов конкретной компании
    test_logging = "test_logging"  # журналирование событий теста (уход со вкладки, вставка)
    calibration = "calibration"  # обезличенные ответы для калибровки заданий (spec-testing §2.2)
    interest_signal = "interest_signal"  # «сигнал интереса» компании (DECISIONS 24): видна только после открытия карточки


class SearchMode(StrEnum):
    active = "active"  # активно ищу
    open = "open"  # открыт к предложениям
    hidden = "hidden"  # не ищу — скрыт из банка


class SalaryVisibility(StrEnum):
    signal = "signal"  # только «в вилке ✓ / выше» (по умолчанию)
    exact = "exact"  # число


class SalaryType(StrEnum):
    net = "net"  # на руки
    gross = "gross"  # до НДФЛ


class WorkFormat(StrEnum):
    office = "office"
    hybrid = "hybrid"
    remote = "remote"


# --- Тестирование (spec-testing §2.4) ---


class FamilyStatus(StrEnum):
    draft = "draft"
    reviewed = "reviewed"
    pretest = "pretest"
    operational = "operational"
    quarantine = "quarantine"
    retired = "retired"


class SessionKind(StrEnum):
    initial = "initial"
    retake = "retake"
    upgrade = "upgrade"
    ext_up = "ext_up"
    ext_down = "ext_down"
    regular = "regular"
    preview = "preview"
    sim = "sim"


# Виды сессий, для которых действует правило «одна активная сессия на аккаунт»
EXCLUSIVE_SESSION_KINDS = ("initial", "retake", "upgrade", "ext_up", "ext_down")


# Статусы сессии, решения и квалификации — ЗАГЛАВНЫМИ, как в доменном модуле app/testing (session.py,
# cat.py) и в машинах состояний spec-testing §2.4: слой хранения пишет значения домена как есть.
# Частичный уникальный индекс «одна активная сессия» сравнивает строки буквально — при другом регистре
# гарантия молча исчезла бы, поэтому test_sessions.status ещё и под CHECK.


class SessionStatus(StrEnum):
    created = "CREATED"
    in_progress = "IN_PROGRESS"
    paused = "PAUSED"
    decided = "DECIDED"
    extension_offered = "EXTENSION_OFFERED"
    final = "FINAL"
    abandoned = "ABANDONED"
    under_review = "UNDER_REVIEW"
    invalidated = "INVALIDATED"


ACTIVE_SESSION_STATUSES = ("CREATED", "IN_PROGRESS", "PAUSED")


class SessionDecision(StrEnum):
    passed = "PASSED"
    failed = "FAILED"
    borderline_pass = "BORDERLINE_PASS"
    borderline_fail = "BORDERLINE_FAIL"


class SessionEventType(StrEnum):
    blur = "blur"
    focus = "focus"
    paste = "paste"
    reconnect = "reconnect"


class FlagKind(StrEnum):
    lz = "lz"  # person-fit
    fast_correct = "fast_correct"
    focus = "focus"
    paste = "paste"
    foreign_key = "foreign_key"  # «чужой ключ»


class FlagSeverity(StrEnum):
    soft = "soft"
    hard = "hard"


class FlagStatus(StrEnum):
    open = "open"
    dismissed = "dismissed"
    confirmed = "confirmed"


class QualificationStatus(StrEnum):
    none = "NONE"
    survey_done = "SURVEY_DONE"
    testing = "TESTING"
    confirmed = "CONFIRMED"
    not_confirmed = "NOT_CONFIRMED"
    upgrade_testing = "UPGRADE_TESTING"


class ValidationKind(StrEnum):
    sim = "sim"
    leak = "leak"
    drift = "drift"
    personas = "personas"
    pilot = "pilot"
    vcat = "vcat"
    vvac = "vvac"
    llm_baseline = "llm_baseline"
    matching = "matching"


# --- Подбор и контакт (spec-contact §2.3) ---


class NeedStatus(StrEnum):
    draft = "draft"
    parsed = "parsed"
    active = "active"
    paused = "paused"
    closed = "closed"


class InvitationStatus(StrEnum):
    sent = "sent"
    viewed = "viewed"
    countered = "countered"
    accepted = "accepted"
    declined = "declined"
    counter_declined = "counter_declined"
    expired = "expired"
    withdrawn = "withdrawn"
    closed = "closed"
    consent_revoked = "consent_revoked"


# Ждут ответа одной из сторон (занимают слот брифа)
ACTIVE_INVITATION_STATUSES = ("sent", "viewed", "countered")
# «Одно активное приглашение на пару»: открытый контакт (accepted) тоже занимает пару —
# как PAIR_ACTIVE_STATUSES в app/contact/statuses.py
PAIR_ACTIVE_INVITATION_STATUSES = (*ACTIVE_INVITATION_STATUSES, "accepted")


class OfferAuthor(StrEnum):
    employer = "employer"
    candidate = "candidate"


class DisclosureBasis(StrEnum):
    invitation = "invitation"
    application = "application"


class ApplicationStatus(StrEnum):
    submitted = "submitted"
    viewed = "viewed"
    interested = "interested"
    rejected = "rejected"
    withdrawn = "withdrawn"
    no_response = "no_response"


class VacancyStatus(StrEnum):
    draft = "draft"
    published = "published"
    closed = "closed"


class TaskKind(StrEnum):
    catalog = "catalog"  # тип A: откалиброванное семейство, свой экземпляр
    approach = "approach"  # тип B: «предложи подход», рубрика


class EmployerTaskStatus(StrEnum):
    draft = "draft"
    active = "active"
    paused = "paused"
    closed = "closed"


class AssignmentStatus(StrEnum):
    offered = "offered"
    opened = "opened"
    submitted = "submitted"
    auto_scored = "auto_scored"
    awaiting_review = "awaiting_review"
    reviewed = "reviewed"
    skipped = "skipped"
    expired = "expired"


class WebhookStatus(StrEnum):
    pending = "pending"
    delivered = "delivered"
    failed = "failed"


class FspSyncStatus(StrEnum):
    pending = "pending"
    ok = "ok"
    error = "error"


class AchievementStatus(StrEnum):
    confirmed = "confirmed"
    # «ожидает протокол» (концепция §6.4); значение — как ResultStatus в app/fsp/models.py
    awaiting_protocol = "awaiting_protocol"
