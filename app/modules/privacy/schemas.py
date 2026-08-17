"""Room 10 request/response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.privacy.models import (
    ConsentPurpose,
    ConsentStatus,
    RequestStatus,
    RequestType,
)


# --- consent ---------------------------------------------------------------


class ConsentGrant(BaseModel):
    purpose: ConsentPurpose
    user_id: int | None = None
    patient_id: int | None = None
    notice_version: str = "1.0"
    notice_language: str = "hi"
    note: str = ""


class ConsentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    patient_id: int | None
    purpose: str
    status: str
    notice_version: str
    notice_language: str
    granted_at: datetime
    withdrawn_at: datetime | None
    note: str


class ConsentNotice(BaseModel):
    """The plain-language notice shown before consent is taken."""

    purpose: str
    version: str
    title_hi: str
    title_en: str
    body_hi: str
    body_en: str
    retention_note_hi: str
    retention_note_en: str


# --- face templates --------------------------------------------------------


class FaceEnrolRequest(BaseModel):
    doctor_id: int
    #: The embedding produced by the camera's recognition model. The image
    #: itself is never uploaded and there is nowhere to store one.
    embedding: list[float] = Field(min_length=8, max_length=2048)
    algorithm: str = "facenet-512"


class FaceTemplateOut(BaseModel):
    """Deliberately excludes the embedding — it never leaves the server."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    consent_id: int
    dimensions: int
    algorithm: str
    template_digest: str
    is_active: bool
    retired_at: datetime | None


class FaceMatchRequest(BaseModel):
    embedding: list[float] = Field(min_length=8, max_length=2048)
    threshold: float = Field(default=0.75, ge=0.0, le=1.0)


class FaceMatchResult(BaseModel):
    matched: bool
    doctor_id: int | None = None
    similarity: float | None = None
    template_digest: str | None = None
    reason: str | None = None


# --- audit -----------------------------------------------------------------


class AuditLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor_user_id: int | None
    actor_role: str
    action: str
    resource_type: str
    resource_id: str | None
    method: str
    path: str
    status_code: int | None
    occurred_at: datetime
    detail: str


# --- data subject rights ---------------------------------------------------


class DataSubjectRequestCreate(BaseModel):
    request_type: RequestType
    detail: str = ""


class DataSubjectRequestOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_type: str
    status: str
    user_id: int | None
    patient_id: int | None
    requested_at: datetime
    completed_at: datetime | None
    detail: str
    outcome: str


class PersonalDataExport(BaseModel):
    """Everything held about one person, in one response."""

    generated_at: datetime
    account: dict
    patient_record: dict | None
    appointments: list[dict] = []
    consents: list[dict] = []
    notifications: list[dict] = []
    external_links: list[dict] = []
    note: str


class ErasureResult(BaseModel):
    request_id: int
    anonymised_patient_records: int
    deleted_consents: int
    retired_face_templates: int
    revoked_external_links: int
    retained: list[str] = []


class PrivacyStatus(BaseModel):
    """A compliance summary an administrator can actually read."""

    encryption_enabled: bool
    encryption_key_configured: bool
    audit_log_entries: int
    consents_granted: int
    consents_withdrawn: int
    face_templates_active: int
    face_images_stored: int
    pending_data_requests: int
    dpdp_notes: list[str] = []
