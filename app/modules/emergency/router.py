"""Room 7 endpoints — triage, priority overrides, and the override audit log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.deps import DbSession, require_roles
from app.core.security import Role
from app.modules.booking import service as booking_service
from app.modules.emergency import service
from app.modules.emergency.models import CaseStatus
from app.modules.emergency.schemas import (
    AgingResult,
    EmergencyCaseOut,
    OverrideOut,
    ResolveRequest,
    SetPriorityRequest,
    TriageRequest,
    VulnerabilityAssessment,
)
from app.modules.queue import service as queue_service
from app.modules.queue.schemas import QueueEntryOut

router = APIRouter(prefix="/emergency", tags=["Room 7 - Emergency & Priority"])

#: Triage is a clinical act. Patients can never raise their own priority.
ClinicalStaff = Depends(require_roles(Role.ADMIN, Role.STAFF, Role.DOCTOR))
AdminOnly = Depends(require_roles(Role.ADMIN))


@router.post(
    "/triage", response_model=EmergencyCaseOut, status_code=status.HTTP_201_CREATED
)
def triage(payload: TriageRequest, db: DbSession, user=ClinicalStaff) -> EmergencyCaseOut:
    """Register an emergency arrival and insert it into the live queue."""
    case = service.triage(db, payload, user.id)
    return EmergencyCaseOut.model_validate(service.decorate(db, case))


@router.get("/cases", response_model=list[EmergencyCaseOut], dependencies=[ClinicalStaff])
def list_cases(
    db: DbSession,
    case_status: CaseStatus | None = None,
    department_id: int | None = None,
) -> list[EmergencyCaseOut]:
    cases = service.list_cases(db, status=case_status, department_id=department_id)
    return [EmergencyCaseOut.model_validate(service.decorate(db, c)) for c in cases]


@router.get(
    "/cases/{case_id}", response_model=EmergencyCaseOut, dependencies=[ClinicalStaff]
)
def get_case(case_id: int, db: DbSession) -> EmergencyCaseOut:
    return EmergencyCaseOut.model_validate(
        service.decorate(db, service.get_case(db, case_id))
    )


@router.post(
    "/cases/{case_id}/resolve",
    response_model=EmergencyCaseOut,
    dependencies=[ClinicalStaff],
)
def resolve(case_id: int, payload: ResolveRequest, db: DbSession) -> EmergencyCaseOut:
    case = service.resolve(db, case_id, payload.status, payload.outcome)
    return EmergencyCaseOut.model_validate(service.decorate(db, case))


@router.post("/queue-entries/{entry_id}/priority", response_model=QueueEntryOut)
def set_priority(
    entry_id: int,
    payload: SetPriorityRequest,
    db: DbSession,
    user=ClinicalStaff,
) -> QueueEntryOut:
    """Raise or lower a waiting patient's tier — e.g. someone collapses.

    A reason is mandatory: jumping a queue in a public hospital is a decision
    someone has to be able to account for later.
    """
    entry = service.set_entry_priority(db, entry_id, payload.tier, payload.reason, user.id)
    return queue_service.entry_out(db, entry)


@router.get("/overrides", response_model=list[OverrideOut], dependencies=[ClinicalStaff])
def list_overrides(
    db: DbSession, queue_entry_id: int | None = None, limit: int = 200
) -> list[OverrideOut]:
    """The append-only record of who moved whom, and why."""
    return [
        OverrideOut.model_validate(o)
        for o in service.list_overrides(db, queue_entry_id=queue_entry_id, limit=limit)
    ]


@router.post(
    "/doctors/{doctor_id}/apply-aging",
    response_model=AgingResult,
    dependencies=[ClinicalStaff],
)
def apply_aging(doctor_id: int, db: DbSession) -> AgingResult:
    """Escalate anyone displaced past the waiting threshold."""
    session = queue_service.require_session(db, doctor_id)
    return service.apply_aging(db, session.id)


@router.get(
    "/patients/{patient_id}/vulnerability",
    response_model=VulnerabilityAssessment,
    dependencies=[ClinicalStaff],
)
def assess(patient_id: int, db: DbSession) -> VulnerabilityAssessment:
    """Why this patient does or does not hold a standing priority."""
    return service.assess_vulnerability(booking_service.get_patient(db, patient_id))
