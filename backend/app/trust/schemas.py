"""DTO метки «Проверенная компания», жалоб и сигналов модерации."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class VerificationIn(_In):
    inn: str = Field(min_length=10, max_length=12, description="ИНН: 10 цифр (организация) или 12 (ИП)", examples=["7701123451"])
    ogrn: str = Field(min_length=13, max_length=15, description="ОГРН: 13 цифр (организация) или 15 (ОГРНИП)", examples=["1027701123450"])


class VerificationCheckOut(BaseModel):
    code: str
    label: str
    ok: bool


class VerificationOut(BaseModel):
    level: Literal["none", "requisites", "domain"]
    label: str
    hint: str | None = None
    verified: bool
    submitted: bool = Field(description="Реквизиты подавались")
    inn: str | None = None
    ogrn: str | None = None
    registry_name: str | None = Field(default=None, description="Название по реестру (если запись найдена)")
    checks: list[VerificationCheckOut] = Field(default_factory=list)
    revoked: bool = False
    revoke_reason: str | None = None


class ComplaintCreateIn(_In):
    reason: str = Field(description="Код причины из config/contact.yaml -> reasons.complaint")
    comment: str | None = Field(default=None, max_length=2000)


class ComplaintOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    target_type: Literal["invitation", "vacancy"]
    target_id: uuid.UUID
    target_title: str | None = None
    reason: str
    reason_label: str
    comment: str | None = None
    status: Literal["open", "upheld", "dismissed"]
    status_label: str
    created_at: datetime
    resolved_at: datetime | None = None
    resolution_note: str | None = None
    company_open_reporters: int = Field(description="Сколько разных кандидатов жаловались на компанию в окне")
    verification_level: str


class ComplaintResolveIn(_In):
    decision: Literal["upheld", "dismissed"]
    note: str | None = Field(default=None, max_length=300)


class RevokeIn(_In):
    reason: str = Field(description="complaints | wrong_data | other")
    note: str | None = Field(default=None, max_length=300)


class TrustFlagOut(BaseModel):
    code: str
    label: str
    detail: str


class CompanyFlagsOut(BaseModel):
    company_id: uuid.UUID
    company_name: str
    verification_level: str
    flags: list[TrustFlagOut]


class RevokeOut(BaseModel):
    company_id: uuid.UUID
    level: str
    revoked: bool
