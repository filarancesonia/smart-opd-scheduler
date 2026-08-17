"""Room 5 endpoints — queue control, corridor boards, and 'where am I?'."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, status

from app.core.deps import DbSession, get_current_user, require_roles, verify_device_key
from app.core.errors import PermissionError_
from app.core.security import Role
from app.modules.booking import service as booking_service
from app.modules.doctors import service as doctors_service
from app.modules.queue import service
from app.modules.queue.schemas import (
    CallNextResult,
    CompleteRequest,
    DisplayBoard,
    JoinRequest,
    MyPosition,
    OpenQueueRequest,
    QueueEntryOut,
    QueueOut,
    ReorderResult,
)

router = APIRouter(prefix="/queue", tags=["Room 5 - Live Queue"])

ClinicStaff = Depends(require_roles(Role.ADMIN, Role.STAFF, Role.DOCTOR))
DeviceAuth = Depends(verify_device_key)


def _assert_can_run_queue(db: DbSession, user, doctor_id: int) -> None:
    """Doctors run their own queue; staff and admins run anyone's."""
    role = str(user.role)
    if role in {Role.ADMIN, Role.STAFF}:
        return
    if role == Role.DOCTOR:
        if doctors_service.get_doctor_by_user(db, user.id).id == doctor_id:
            return
    raise PermissionError_("You may only manage your own queue")


# --- session control -------------------------------------------------------


@router.post(
    "/doctors/{doctor_id}/open",
    response_model=QueueOut,
    status_code=status.HTTP_201_CREATED,
)
def open_queue(
    doctor_id: int,
    payload: OpenQueueRequest,
    db: DbSession,
    user=ClinicStaff,
) -> QueueOut:
    _assert_can_run_queue(db, user, doctor_id)
    service.open_queue(db, doctor_id, payload.session_date, payload.room)
    return service.get_queue(db, doctor_id, payload.session_date)


@router.post("/doctors/{doctor_id}/close", response_model=QueueOut)
def close_queue(
    doctor_id: int, db: DbSession, on_date: date | None = None, user=ClinicStaff
) -> QueueOut:
    _assert_can_run_queue(db, user, doctor_id)
    service.close_queue(db, doctor_id, on_date)
    return service.get_queue(db, doctor_id, on_date)


@router.post(
    "/join", response_model=QueueEntryOut, status_code=status.HTTP_201_CREATED
)
def join_queue(payload: JoinRequest, db: DbSession, user=ClinicStaff) -> QueueEntryOut:
    """Reception hands a token to a patient who has arrived."""
    entry = service.join(db, payload.appointment_id)
    return service.entry_out(db, entry)


# --- flow ------------------------------------------------------------------


@router.post("/doctors/{doctor_id}/call-next", response_model=CallNextResult)
def call_next(doctor_id: int, db: DbSession, user=ClinicStaff) -> CallNextResult:
    _assert_can_run_queue(db, user, doctor_id)
    entry, reason, remaining = service.call_next(db, doctor_id)
    return CallNextResult(
        called=service.entry_out(db, entry) if entry else None,
        reason=reason,
        remaining_waiting=remaining,
    )


@router.post("/entries/{entry_id}/start", response_model=QueueEntryOut)
def start_consultation(
    entry_id: int, db: DbSession, user=ClinicStaff
) -> QueueEntryOut:
    entry = service.start_consultation(db, entry_id)
    return service.entry_out(db, entry)


@router.post("/entries/{entry_id}/complete", response_model=QueueEntryOut)
def complete_consultation(
    entry_id: int, payload: CompleteRequest, db: DbSession, user=ClinicStaff
) -> QueueEntryOut:
    """Ends the consultation and feeds the real duration back to Room 4."""
    entry = service.complete_consultation(db, entry_id, payload.note)
    return service.entry_out(db, entry)


@router.post("/entries/{entry_id}/skip", response_model=QueueEntryOut)
def skip(entry_id: int, db: DbSession, user=ClinicStaff) -> QueueEntryOut:
    """Called but not at the door — keeps their token for one more round."""
    return service.entry_out(db, service.skip(db, entry_id))


@router.post("/entries/{entry_id}/no-show", response_model=QueueEntryOut)
def mark_no_show(entry_id: int, db: DbSession, user=ClinicStaff) -> QueueEntryOut:
    return service.entry_out(db, service.mark_no_show(db, entry_id))


@router.post("/doctors/{doctor_id}/reorder", response_model=ReorderResult)
def reorder(doctor_id: int, db: DbSession, user=ClinicStaff) -> ReorderResult:
    """Re-plan the live queue — used after a late arrival or an emergency."""
    _assert_can_run_queue(db, user, doctor_id)
    session = service.require_session(db, doctor_id)
    result = service.reorder(db, session)
    return ReorderResult(
        **result, entries=[service.entry_out(db, e) for e in service.open_entries(db, session.id)]
    )


# --- views -----------------------------------------------------------------


@router.get("/doctors/{doctor_id}", response_model=QueueOut, dependencies=[ClinicStaff])
def get_queue(doctor_id: int, db: DbSession, on_date: date | None = None) -> QueueOut:
    return service.get_queue(db, doctor_id, on_date)


@router.get(
    "/doctors/{doctor_id}/board",
    response_model=DisplayBoard,
    dependencies=[DeviceAuth],
)
def display_board(doctor_id: int, db: DbSession) -> DisplayBoard:
    """Feed for a corridor screen. Names are reduced to initials."""
    return service.get_board(db, doctor_id)


@router.get("/doctors/{doctor_id}/my-position", response_model=MyPosition)
def my_position(
    doctor_id: int, db: DbSession, user=Depends(get_current_user)
) -> MyPosition:
    """The patient's own view: 'your turn in about 22 minutes'."""
    patient = booking_service.patient_for_user(db, user)
    return service.my_position(db, patient.id, doctor_id)
