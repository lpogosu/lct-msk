"""ORM-модели «Взаимно». Импорт пакета регистрирует все таблицы в Base.metadata (нужно Alembic)."""

from app.credentials import models as _credentials  # noqa: F401 — паспорт навыков: в metadata, но вне __all__
from app.models import integration as _integration  # noqa: F401 — чат и ATS: в metadata, но вне __all__
from app.models import trust as _trust  # noqa: F401 — метка компании и жалобы: в metadata, но вне __all__
from app.models.candidate import (
    DEFAULT_FIELD_VISIBILITY,
    DISCLOSURE_FIELD_COLUMNS,
    CandidateFeatures,
    CandidateProfile,
    SurveyResponse,
)
from app.models.company import Company, ReliabilitySnapshot, WebhookDelivery
from app.models.contact import (
    Application,
    ContactDisclosure,
    Cooldown,
    Feedback,
    InterestSignal,
    Invitation,
    InvitationEvent,
    OfferTerms,
    OutcomeCheck,
    Vacancy,
)
from app.models.fsp import FspAchievementCache, FspLink
from app.models.matching import ImpressionLog, Need, NeedVersion, ProfileView, Shortlist
from app.models.system import DemoClock
from app.models.tasks import EmployerTask, TaskAssignment
from app.models.testing import (
    CalibrationRun,
    DriftEvent,
    Flag,
    GradeChange,
    ItemFamilyParams,
    ItemInstance,
    Qualification,
    SessionEvent,
    TestResponse,
    TestSession,
    ValidationRun,
)
from app.models.users import AuditLog, Consent, EmailCode, RefreshToken, User
from app.services.anticheat import (
    models as _anticheat,  # noqa: F401, E402 — антиподсказчик: в metadata, вне __all__
)

__all__ = [
    "DEFAULT_FIELD_VISIBILITY",
    "DISCLOSURE_FIELD_COLUMNS",
    "Application",
    "AuditLog",
    "CalibrationRun",
    "CandidateFeatures",
    "CandidateProfile",
    "Company",
    "Consent",
    "ContactDisclosure",
    "Cooldown",
    "DemoClock",
    "DriftEvent",
    "EmailCode",
    "EmployerTask",
    "Feedback",
    "Flag",
    "FspAchievementCache",
    "FspLink",
    "GradeChange",
    "ImpressionLog",
    "Invitation",
    "InterestSignal",
    "InvitationEvent",
    "ItemFamilyParams",
    "ItemInstance",
    "Need",
    "NeedVersion",
    "OfferTerms",
    "OutcomeCheck",
    "ProfileView",
    "Qualification",
    "RefreshToken",
    "ReliabilitySnapshot",
    "SessionEvent",
    "Shortlist",
    "SurveyResponse",
    "TaskAssignment",
    "TestResponse",
    "TestSession",
    "User",
    "ValidationRun",
    "Vacancy",
    "WebhookDelivery",
]
