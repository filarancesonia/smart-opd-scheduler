"""Room 9 endpoints — ABHA linking, ORS sync, FHIR export."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status as http_status

from app.core.deps import DbSession, require_roles
from app.core.security import Role
from app.modules.integration import service
from app.modules.integration.models import ExternalSystem, SyncStatus
from app.modules.integration.schemas import (
    AbhaLinkRequest,
    ExternalLinkOut,
    FhirPreview,
    IntegrationStatus,
    OrsPullRequest,
    OrsPullResult,
    SyncLogOut,
    SyncResult,
)

router = APIRouter(prefix="/integration", tags=["Room 9 - Integration"])

StaffOrAdmin = Depends(require_roles(Role.ADMIN, Role.STAFF))
AdminOnly = Depends(require_roles(Role.ADMIN))


@router.get("/status", response_model=IntegrationStatus, dependencies=[StaffOrAdmin])
def integration_status(db: DbSession) -> IntegrationStatus:
    """Which bridges are live and which are running offline stubs."""
    return service.status(db)


# --- ABHA ------------------------------------------------------------------


@router.post(
    "/abha/link",
    response_model=ExternalLinkOut,
    status_code=http_status.HTTP_201_CREATED,
    dependencies=[StaffOrAdmin],
)
def link_abha(payload: AbhaLinkRequest, db: DbSession) -> ExternalLinkOut:
    """Attach a national health ID, check digit validated before any call out."""
    link = service.link_abha(
        db, payload.patient_id, payload.abha_number, payload.consent_reference
    )
    return ExternalLinkOut.model_validate(link)


@router.get(
    "/links", response_model=list[ExternalLinkOut], dependencies=[StaffOrAdmin]
)
def list_links(
    db: DbSession,
    system: ExternalSystem | None = None,
    patient_id: int | None = None,
) -> list[ExternalLinkOut]:
    return [
        ExternalLinkOut.model_validate(link)
        for link in service.list_links(db, system=system, patient_id=patient_id)
    ]


@router.delete(
    "/links/{link_id}", response_model=ExternalLinkOut, dependencies=[StaffOrAdmin]
)
def revoke_link(link_id: int, db: DbSession) -> ExternalLinkOut:
    """Withdraw a link — where Room 10's consent withdrawal ends up."""
    return ExternalLinkOut.model_validate(service.revoke_link(db, link_id))


# --- ORS -------------------------------------------------------------------


@router.post(
    "/ors/appointments/{appointment_id}/push",
    response_model=SyncResult,
    dependencies=[StaffOrAdmin],
)
def push_to_ors(appointment_id: int, db: DbSession) -> SyncResult:
    """Publish a locally-made booking to the national portal."""
    return service.push_appointment_to_ors(db, appointment_id)


@router.post("/ors/pull", response_model=OrsPullResult, dependencies=[StaffOrAdmin])
def pull_from_ors(payload: OrsPullRequest, db: DbSession) -> OrsPullResult:
    """Import bookings citizens made on the portal instead of through us."""
    return service.pull_ors_appointments(db, payload.facility_id, payload.on_date)


# --- HMIS / FHIR -----------------------------------------------------------


@router.get(
    "/hmis/appointments/{appointment_id}/bundle",
    response_model=FhirPreview,
    dependencies=[StaffOrAdmin],
)
def preview_bundle(appointment_id: int, db: DbSession) -> FhirPreview:
    """The exact FHIR bundle that would be sent, for inspection before it is."""
    return FhirPreview(
        appointment_id=appointment_id, bundle=service.build_bundle(db, appointment_id)
    )


@router.post(
    "/hmis/appointments/{appointment_id}/push",
    response_model=SyncResult,
    dependencies=[StaffOrAdmin],
)
def push_to_hmis(appointment_id: int, db: DbSession) -> SyncResult:
    """Send a finished consultation into the hospital's own record system."""
    return service.push_encounter_to_hmis(db, appointment_id)


# --- audit -----------------------------------------------------------------


@router.get("/sync-logs", response_model=list[SyncLogOut], dependencies=[StaffOrAdmin])
def sync_logs(
    db: DbSession,
    system: ExternalSystem | None = None,
    sync_status: SyncStatus | None = None,
    limit: int = 200,
) -> list[SyncLogOut]:
    """Every exchange with an external system, successful or not."""
    return [
        SyncLogOut.model_validate(entry)
        for entry in service.list_sync_logs(
            db, system=system, status=sync_status, limit=limit
        )
    ]
