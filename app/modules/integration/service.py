"""Room 9 logic: link identities, push and pull, log everything."""

from __future__ import annotations

import json
import time as time_module
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.timeutil import local_today
from app.modules.booking import service as booking_service
from app.modules.booking.models import Appointment, Patient
from app.modules.doctors.models import Department, Doctor
from app.modules.identity.models import User
from app.modules.integration.clients import (
    abha_client,
    build_encounter_bundle,
    fhir_client,
    ors_client,
)
from app.modules.integration.models import (
    ExternalLink,
    ExternalSystem,
    LinkStatus,
    SyncDirection,
    SyncLog,
    SyncStatus,
)
from app.modules.integration.schemas import (
    IntegrationStatus,
    OrsPullResult,
    SyncResult,
)
from app.modules.integration.verhoeff import digits_only


# --- audit -----------------------------------------------------------------


def _log(
    db: Session,
    *,
    system: str,
    direction: SyncDirection,
    operation: str,
    status: SyncStatus,
    started: float,
    is_mock: bool = False,
    patient_id: int | None = None,
    appointment_id: int | None = None,
    summary: str = "",
    error: str = "",
) -> SyncLog:
    entry = SyncLog(
        system=str(system),
        direction=str(direction),
        operation=operation,
        status=str(status),
        patient_id=patient_id,
        appointment_id=appointment_id,
        occurred_at=utcnow(),
        duration_ms=int((time_module.perf_counter() - started) * 1000),
        is_mock=is_mock,
        summary=summary,
        error=error,
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry


def list_sync_logs(
    db: Session,
    *,
    system: ExternalSystem | None = None,
    status: SyncStatus | None = None,
    limit: int = 200,
) -> list[SyncLog]:
    stmt = select(SyncLog).order_by(SyncLog.occurred_at.desc())
    if system is not None:
        stmt = stmt.where(SyncLog.system == str(system))
    if status is not None:
        stmt = stmt.where(SyncLog.status == str(status))
    return list(db.execute(stmt.limit(limit)).scalars())


# --- links -----------------------------------------------------------------


def get_link(
    db: Session,
    system: ExternalSystem,
    *,
    patient_id: int | None = None,
    appointment_id: int | None = None,
) -> ExternalLink | None:
    stmt = select(ExternalLink).where(
        ExternalLink.system == str(system),
        ExternalLink.status != LinkStatus.REVOKED,
    )
    if patient_id is not None:
        stmt = stmt.where(ExternalLink.patient_id == patient_id)
    if appointment_id is not None:
        stmt = stmt.where(ExternalLink.appointment_id == appointment_id)
    return db.execute(stmt).scalars().first()


def list_links(
    db: Session, *, system: ExternalSystem | None = None, patient_id: int | None = None
) -> list[ExternalLink]:
    stmt = select(ExternalLink).order_by(ExternalLink.created_at.desc())
    if system is not None:
        stmt = stmt.where(ExternalLink.system == str(system))
    if patient_id is not None:
        stmt = stmt.where(ExternalLink.patient_id == patient_id)
    return list(db.execute(stmt).scalars())


def link_abha(
    db: Session, patient_id: int, abha_number: str, consent_reference: str | None
) -> ExternalLink:
    """Attach a national health ID to a local patient record."""
    patient = booking_service.get_patient(db, patient_id)
    digits = digits_only(abha_number)
    started = time_module.perf_counter()

    response = abha_client.verify_number(digits)
    if not response.ok:
        _log(
            db,
            system=ExternalSystem.ABHA,
            direction=SyncDirection.OUTBOUND,
            operation="verify_number",
            status=SyncStatus.FAILED,
            started=started,
            is_mock=response.is_mock,
            patient_id=patient_id,
            error=response.error,
        )
        raise ValidationError(response.error or "ABHA verification failed")

    clash = db.execute(
        select(ExternalLink).where(
            ExternalLink.system == ExternalSystem.ABHA,
            ExternalLink.external_id == digits,
            ExternalLink.status != LinkStatus.REVOKED,
        )
    ).scalar_one_or_none()
    if clash is not None and clash.patient_id != patient_id:
        raise ConflictError("That ABHA number is already linked to another patient")
    if clash is not None:
        return clash

    link = ExternalLink(
        system=str(ExternalSystem.ABHA),
        external_id=digits,
        patient_id=patient_id,
        status=str(LinkStatus.VERIFIED),
        verification_method=response.data.get("verificationMethod", "otp"),
        verified_at=utcnow(),
        consent_reference=consent_reference,
        is_mock=response.is_mock,
        extra=json.dumps({"abhaAddress": response.data.get("abhaAddress")}),
    )
    db.add(link)

    # Mirror onto the patient record so other modules do not need Room 9 loaded.
    patient.abha_id = digits
    db.commit()
    db.refresh(link)

    _log(
        db,
        system=ExternalSystem.ABHA,
        direction=SyncDirection.OUTBOUND,
        operation="verify_number",
        status=SyncStatus.SUCCESS,
        started=started,
        is_mock=response.is_mock,
        patient_id=patient_id,
        summary=f"Linked ABHA ending {digits[-4:]}",
    )
    return link


def revoke_link(db: Session, link_id: int) -> ExternalLink:
    """Withdraw a link. Room 10's consent withdrawal ends up here."""
    link = db.get(ExternalLink, link_id)
    if link is None:
        raise NotFoundError("External link not found")
    link.status = str(LinkStatus.REVOKED)

    if link.system == ExternalSystem.ABHA and link.patient_id:
        patient = db.get(Patient, link.patient_id)
        if patient is not None:
            patient.abha_id = None

    db.commit()
    db.refresh(link)
    return link


# --- ORS -------------------------------------------------------------------


def _ors_payload(db: Session, appointment: Appointment) -> dict:
    patient = db.get(Patient, appointment.patient_id)
    doctor = db.get(Doctor, appointment.doctor_id)
    doctor_user = db.get(User, doctor.user_id) if doctor else None
    department = db.get(Department, appointment.department_id)
    return {
        "bookingReference": appointment.booking_reference,
        "appointmentDate": appointment.appointment_date.isoformat(),
        "slotTime": appointment.slot_start.strftime("%H:%M"),
        "department": department.name if department else "",
        "doctorName": doctor_user.full_name if doctor_user else "",
        "patientName": patient.full_name if patient else "",
        "patientPhone": patient.phone if patient else "",
        "abhaNumber": patient.abha_id if patient else None,
    }


def push_appointment_to_ors(db: Session, appointment_id: int) -> SyncResult:
    """Publish a locally-made booking to the national portal."""
    appointment = booking_service.get_appointment(db, appointment_id)
    started = time_module.perf_counter()

    existing = get_link(db, ExternalSystem.ORS, appointment_id=appointment_id)
    if existing is not None:
        return SyncResult(
            ok=True,
            system=str(ExternalSystem.ORS),
            operation="push_appointment",
            is_mock=existing.is_mock,
            external_id=existing.external_id,
            detail="Already published",
        )

    response = ors_client.push_appointment(_ors_payload(db, appointment))
    if not response.ok:
        _log(
            db,
            system=ExternalSystem.ORS,
            direction=SyncDirection.OUTBOUND,
            operation="push_appointment",
            status=SyncStatus.FAILED,
            started=started,
            is_mock=response.is_mock,
            appointment_id=appointment_id,
            error=response.error,
        )
        return SyncResult(
            ok=False,
            system=str(ExternalSystem.ORS),
            operation="push_appointment",
            is_mock=response.is_mock,
            detail=response.error,
        )

    reference = response.data.get("orsReferenceId", "")
    link = ExternalLink(
        system=str(ExternalSystem.ORS),
        external_id=reference,
        appointment_id=appointment_id,
        patient_id=appointment.patient_id,
        status=str(LinkStatus.VERIFIED),
        verification_method="api",
        verified_at=utcnow(),
        is_mock=response.is_mock,
    )
    db.add(link)
    db.commit()

    _log(
        db,
        system=ExternalSystem.ORS,
        direction=SyncDirection.OUTBOUND,
        operation="push_appointment",
        status=SyncStatus.SUCCESS,
        started=started,
        is_mock=response.is_mock,
        appointment_id=appointment_id,
        summary=f"Published as {reference}",
    )
    return SyncResult(
        ok=True,
        system=str(ExternalSystem.ORS),
        operation="push_appointment",
        is_mock=response.is_mock,
        external_id=reference,
    )


def pull_ors_appointments(
    db: Session, facility_id: str, on_date: date | None = None
) -> OrsPullResult:
    """Import bookings citizens made on the portal instead of through us."""
    target = on_date or local_today()
    started = time_module.perf_counter()

    response = ors_client.fetch_appointments(facility_id, target)
    if not response.ok:
        _log(
            db,
            system=ExternalSystem.ORS,
            direction=SyncDirection.INBOUND,
            operation="fetch_appointments",
            status=SyncStatus.FAILED,
            started=started,
            is_mock=response.is_mock,
            error=response.error,
        )
        return OrsPullResult(
            fetched=0, imported=0, skipped=0, is_mock=response.is_mock
        )

    incoming = response.data.get("appointments", [])
    imported = skipped = 0
    for record in incoming:
        reference = record.get("orsReferenceId")
        if not reference:
            skipped += 1
            continue
        already = db.execute(
            select(ExternalLink).where(
                ExternalLink.system == ExternalSystem.ORS,
                ExternalLink.external_id == reference,
            )
        ).scalar_one_or_none()
        if already is not None:
            skipped += 1
            continue
        db.add(
            ExternalLink(
                system=str(ExternalSystem.ORS),
                external_id=reference,
                status=str(LinkStatus.UNVERIFIED),
                verification_method="api",
                is_mock=response.is_mock,
                extra=json.dumps(record),
            )
        )
        imported += 1
    db.commit()

    _log(
        db,
        system=ExternalSystem.ORS,
        direction=SyncDirection.INBOUND,
        operation="fetch_appointments",
        status=SyncStatus.SUCCESS,
        started=started,
        is_mock=response.is_mock,
        summary=f"{len(incoming)} fetched, {imported} imported",
    )
    return OrsPullResult(
        fetched=len(incoming),
        imported=imported,
        skipped=skipped,
        is_mock=response.is_mock,
    )


# --- HMIS / FHIR -----------------------------------------------------------


def build_bundle(db: Session, appointment_id: int) -> dict:
    """Assemble the FHIR bundle for an appointment without sending it."""
    appointment = booking_service.get_appointment(db, appointment_id)
    patient = db.get(Patient, appointment.patient_id)
    doctor = db.get(Doctor, appointment.doctor_id)
    doctor_user = db.get(User, doctor.user_id) if doctor else None
    department = db.get(Department, appointment.department_id)

    from app.modules.queue.models import QueueEntry

    entry = db.execute(
        select(QueueEntry).where(QueueEntry.appointment_id == appointment_id)
    ).scalar_one_or_none()

    return build_encounter_bundle(
        patient=patient,
        appointment=appointment,
        doctor_name=doctor_user.full_name if doctor_user else "",
        department_name=department.name if department else "",
        abha_number=patient.abha_id if patient else None,
        started_at=entry.started_at if entry else None,
        ended_at=entry.completed_at if entry else None,
    )


def push_encounter_to_hmis(db: Session, appointment_id: int) -> SyncResult:
    """Send a finished consultation into the hospital's own record system."""
    appointment = booking_service.get_appointment(db, appointment_id)
    started = time_module.perf_counter()
    bundle = build_bundle(db, appointment_id)

    response = fhir_client.push_encounter(bundle)
    status = SyncStatus.SUCCESS if response.ok else SyncStatus.FAILED
    _log(
        db,
        system=ExternalSystem.HMIS,
        direction=SyncDirection.OUTBOUND,
        operation="push_encounter",
        status=status,
        started=started,
        is_mock=response.is_mock,
        patient_id=appointment.patient_id,
        appointment_id=appointment_id,
        summary=f"Bundle {response.data.get('id', '')}" if response.ok else "",
        error=response.error,
    )

    if response.ok:
        existing = get_link(db, ExternalSystem.HMIS, appointment_id=appointment_id)
        if existing is None:
            db.add(
                ExternalLink(
                    system=str(ExternalSystem.HMIS),
                    external_id=str(response.data.get("id", appointment_id)),
                    appointment_id=appointment_id,
                    patient_id=appointment.patient_id,
                    status=str(LinkStatus.VERIFIED),
                    verification_method="api",
                    verified_at=utcnow(),
                    is_mock=response.is_mock,
                )
            )
            db.commit()

    return SyncResult(
        ok=response.ok,
        system=str(ExternalSystem.HMIS),
        operation="push_encounter",
        is_mock=response.is_mock,
        external_id=str(response.data.get("id")) if response.ok else None,
        detail=response.error,
    )


# --- status ----------------------------------------------------------------


def status(db: Session) -> IntegrationStatus:
    counts = dict(
        db.execute(
            select(ExternalLink.system, func.count(ExternalLink.id)).group_by(
                ExternalLink.system
            )
        ).all()
    )
    failures = db.execute(
        select(func.count(SyncLog.id)).where(SyncLog.status == SyncStatus.FAILED)
    ).scalar_one()

    def describe(client) -> dict:
        return {
            "mode": client.mode,
            "live": client.is_live,
            "note": (
                "Connected to the configured gateway"
                if client.is_live
                else "Running the offline stub — responses are flagged is_mock"
            ),
        }

    return IntegrationStatus(
        abha=describe(abha_client),
        ors=describe(ors_client),
        hmis=describe(fhir_client),
        links_by_system={str(k): int(v) for k, v in counts.items()},
        recent_failures=int(failures),
    )
