"""Room 2 endpoints — departments, doctors, roster, leaves, credentials."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import DbSession, get_current_user, require_roles
from app.core.errors import PermissionError_
from app.core.security import Role
from app.modules.doctors import service
from app.modules.doctors.models import LeaveStatus
from app.modules.doctors.schemas import (
    CredentialCreate,
    CredentialOut,
    DayAvailability,
    DepartmentCreate,
    DepartmentOut,
    DoctorCreate,
    DoctorOut,
    DoctorUpdate,
    DutySlotCreate,
    DutySlotOut,
    LeaveCreate,
    LeaveDecision,
    LeaveOut,
)

router = APIRouter(tags=["Room 2 - Doctors & Roster"])

AdminOnly = Depends(require_roles(Role.ADMIN))
AdminOrStaff = Depends(require_roles(Role.ADMIN, Role.STAFF))


def _assert_self_or_admin(db: DbSession, user, doctor_id: int) -> None:
    """A doctor may act on their own record; admins on anyone's."""
    if str(user.role) == Role.ADMIN:
        return
    if str(user.role) == Role.DOCTOR:
        profile = service.get_doctor_by_user(db, user.id)
        if profile.id == doctor_id:
            return
    raise PermissionError_("You may only manage your own doctor record")


# --- departments -----------------------------------------------------------


@router.post(
    "/departments",
    response_model=DepartmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminOnly],
)
def create_department(payload: DepartmentCreate, db: DbSession) -> DepartmentOut:
    return DepartmentOut.model_validate(service.create_department(db, payload))


@router.get("/departments", response_model=list[DepartmentOut])
def list_departments(
    db: DbSession, include_inactive: bool = False
) -> list[DepartmentOut]:
    departments = service.list_departments(db, active_only=not include_inactive)
    return [DepartmentOut.model_validate(d) for d in departments]


# --- doctors ---------------------------------------------------------------


@router.post(
    "/doctors",
    response_model=DoctorOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminOnly],
)
def create_doctor(payload: DoctorCreate, db: DbSession) -> DoctorOut:
    doctor = service.create_doctor(db, payload)
    return DoctorOut.model_validate(service.decorate(db, doctor))


@router.get("/doctors", response_model=list[DoctorOut])
def list_doctors(
    db: DbSession,
    department_id: int | None = None,
    accepting_only: bool = False,
) -> list[DoctorOut]:
    doctors = service.list_doctors(
        db, department_id=department_id, accepting_only=accepting_only
    )
    return [DoctorOut.model_validate(service.decorate(db, d)) for d in doctors]


@router.get("/doctors/{doctor_id}", response_model=DoctorOut)
def get_doctor(doctor_id: int, db: DbSession) -> DoctorOut:
    doctor = service.get_doctor(db, doctor_id)
    return DoctorOut.model_validate(service.decorate(db, doctor))


@router.patch("/doctors/{doctor_id}", response_model=DoctorOut)
def update_doctor(
    doctor_id: int,
    payload: DoctorUpdate,
    db: DbSession,
    user=Depends(get_current_user),
) -> DoctorOut:
    _assert_self_or_admin(db, user, doctor_id)
    doctor = service.update_doctor(db, doctor_id, payload)
    return DoctorOut.model_validate(service.decorate(db, doctor))


# --- duty roster -----------------------------------------------------------


@router.post(
    "/doctors/{doctor_id}/duty-slots",
    response_model=DutySlotOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminOnly],
)
def add_duty_slot(
    doctor_id: int, payload: DutySlotCreate, db: DbSession
) -> DutySlotOut:
    return DutySlotOut.model_validate(service.add_duty_slot(db, doctor_id, payload))


@router.get("/doctors/{doctor_id}/duty-slots", response_model=list[DutySlotOut])
def list_duty_slots(
    doctor_id: int, db: DbSession, include_inactive: bool = False
) -> list[DutySlotOut]:
    slots = service.list_duty_slots(db, doctor_id, active_only=not include_inactive)
    return [DutySlotOut.model_validate(s) for s in slots]


@router.delete(
    "/doctors/{doctor_id}/duty-slots/{slot_id}",
    response_model=DutySlotOut,
    dependencies=[AdminOnly],
)
def deactivate_duty_slot(
    doctor_id: int, slot_id: int, db: DbSession
) -> DutySlotOut:
    return DutySlotOut.model_validate(
        service.deactivate_duty_slot(db, doctor_id, slot_id)
    )


# --- leaves ----------------------------------------------------------------


@router.post(
    "/doctors/{doctor_id}/leaves",
    response_model=LeaveOut,
    status_code=status.HTTP_201_CREATED,
)
def request_leave(
    doctor_id: int,
    payload: LeaveCreate,
    db: DbSession,
    user=Depends(get_current_user),
) -> LeaveOut:
    _assert_self_or_admin(db, user, doctor_id)
    return LeaveOut.model_validate(service.request_leave(db, doctor_id, payload))


@router.get("/doctors/{doctor_id}/leaves", response_model=list[LeaveOut])
def list_leaves(
    doctor_id: int, db: DbSession, leave_status: LeaveStatus | None = None
) -> list[LeaveOut]:
    return [
        LeaveOut.model_validate(leave)
        for leave in service.list_leaves(db, doctor_id, status=leave_status)
    ]


@router.post("/leaves/{leave_id}/decision", response_model=LeaveOut)
def decide_leave(
    leave_id: int,
    payload: LeaveDecision,
    db: DbSession,
    admin=AdminOnly,
) -> LeaveOut:
    return LeaveOut.model_validate(
        service.decide_leave(db, leave_id, payload.status, admin.id)
    )


# --- credentials -----------------------------------------------------------


@router.post(
    "/doctors/{doctor_id}/credentials",
    response_model=CredentialOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminOnly],
)
def add_credential(
    doctor_id: int, payload: CredentialCreate, db: DbSession
) -> CredentialOut:
    return CredentialOut.model_validate(service.add_credential(db, doctor_id, payload))


@router.get(
    "/doctors/{doctor_id}/credentials",
    response_model=list[CredentialOut],
    dependencies=[AdminOnly],
)
def list_credentials(doctor_id: int, db: DbSession) -> list[CredentialOut]:
    return [
        CredentialOut.model_validate(c) for c in service.list_credentials(db, doctor_id)
    ]


@router.delete(
    "/doctors/{doctor_id}/credentials/{credential_id}",
    response_model=CredentialOut,
    dependencies=[AdminOnly],
)
def revoke_credential(
    doctor_id: int, credential_id: int, db: DbSession
) -> CredentialOut:
    return CredentialOut.model_validate(
        service.revoke_credential(db, doctor_id, credential_id)
    )


# --- availability ----------------------------------------------------------


@router.get("/doctors/{doctor_id}/availability", response_model=DayAvailability)
def get_availability(
    doctor_id: int,
    db: DbSession,
    on_date: date = Query(default_factory=date.today, alias="date"),
) -> DayAvailability:
    """Expected duty windows for a date, after subtracting approved leave."""
    return service.get_day_availability(db, doctor_id, on_date)
