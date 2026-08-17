"""Room 7 request/response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.booking.schemas import PatientCreate
from app.modules.emergency.models import CaseStatus, PriorityTier, TriageLevel


class TriageRequest(BaseModel):
    """Register an emergency arrival at the OPD desk."""

    triage_level: TriageLevel
    complaint: str = Field(min_length=3, max_length=500)
    department_id: int
    doctor_id: int | None = None
    # Existing patient, or a new record for someone who just walked in.
    patient_id: int | None = None
    patient: PatientCreate | None = None


class EmergencyCaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int
    patient_name: str | None = None
    department_id: int
    doctor_id: int | None
    appointment_id: int | None
    queue_entry_id: int | None
    triage_level: str
    priority_tier: int
    complaint: str
    status: str
    arrived_at: datetime
    resolved_at: datetime | None
    triaged_by_user_id: int
    outcome: str

    token_number: int | None = None
    displaced_count: int = 0


class ResolveRequest(BaseModel):
    status: CaseStatus = CaseStatus.RESOLVED
    outcome: str = ""


class SetPriorityRequest(BaseModel):
    """Raise or lower a waiting patient's tier — e.g. someone collapses."""

    tier: PriorityTier
    reason: str = Field(min_length=3, max_length=500)


class OverrideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    queue_entry_id: int | None
    appointment_id: int | None
    patient_id: int | None
    from_tier: int
    to_tier: int
    source: str
    reason: str
    actor_user_id: int | None
    occurred_at: datetime
    displaced_count: int


class AgingResult(BaseModel):
    """Outcome of the automatic anti-starvation sweep."""

    escalated: int
    checked: int
    details: list[str] = []


class VulnerabilityAssessment(BaseModel):
    patient_id: int
    tier: int
    reasons: list[str] = []
