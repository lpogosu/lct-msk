"""ORM-модели «Взаимно». Импорт пакета регистрирует все таблицы в Base.metadata (нужно Alembic)."""

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
