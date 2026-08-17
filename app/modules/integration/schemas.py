"""Room 9 request/response shapes."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.integration.models import ExternalSystem, LinkStatus


class AbhaLinkRequest(BaseModel):
    patient_id: int
    abha_number: str = Field(min_length=14, max_length=20)
    #: Room 10 requires the patient to have agreed before anything is shared.
    consent_reference: str | None = None


class ExternalLinkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    system: str
    external_id: str
    patient_id: int | None
    appointment_id: int | None
    status: str
    verification_method: str
    verified_at: datetime | None
    consent_reference: str | None
    is_mock: bool


class SyncLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    system: str
    direction: str
    operation: str
    status: str
    patient_id: int | None
    appointment_id: int | None
    occurred_at: datetime
    duration_ms: int
    is_mock: bool
    summary: str
    error: str


class SyncResult(BaseModel):
    ok: bool
    system: str
    operation: str
    is_mock: bool
    external_id: str | None = None
    detail: str = ""


class OrsPullRequest(BaseModel):
    facility_id: str
    on_date: date | None = None


class OrsPullResult(BaseModel):
    fetched: int
    imported: int
    skipped: int
    is_mock: bool


class IntegrationStatus(BaseModel):
    """Which bridges are live and which are running offline stubs."""

    abha: dict
    ors: dict
    hmis: dict
    links_by_system: dict
    recent_failures: int


class FhirPreview(BaseModel):
    """The exact bundle that would be sent, for inspection before it is."""

    appointment_id: int
    bundle: dict
