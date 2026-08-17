"""Room 2 business logic, including expected-availability resolution.

``get_day_availability`` is the function the rest of the system leans on: it
turns a recurring weekly roster plus approved leaves into concrete duty windows
for one real calendar date.
"""

from __future__ import annotations

from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import Role, credential_fingerprint
from app.modules.doctors.models import (
    Department,
    Doctor,
    DoctorCredential,
    DutySlot,
    Leave,
    LeaveStatus,
)
from app.modules.doctors.schemas import (
    CredentialCreate,
    DayAvailability,
    DepartmentCreate,
    DoctorCreate,
    DoctorUpdate,
    DutySlotCreate,
    DutyWindow,
    LeaveCreate,
)
from app.modules.identity.models import User


# --- departments -----------------------------------------------------------


def create_department(db: Session, payload: DepartmentCreate) -> Department:
    code = payload.code.strip().upper()
    existing = db.execute(
        select(Department).where(Department.code == code)
    ).scalar_one_or_none()
    if existing:
        raise ConflictError(f"Department code '{code}' is already in use")

    dept = Department(name=payload.name.strip(), code=code, floor=payload.floor)
    db.add(dept)
    db.commit()
    db.refresh(dept)
    return dept


def list_departments(db: Session, *, active_only: bool = True) -> list[Department]:
    stmt = select(Department).order_by(Department.name)
    if active_only:
        stmt = stmt.where(Department.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def get_department(db: Session, department_id: int) -> Department:
    dept = db.get(Department, department_id)
    if dept is None:
        raise NotFoundError("Department not found")
    return dept


# --- doctors ---------------------------------------------------------------


def create_doctor(db: Session, payload: DoctorCreate) -> Doctor:
    user = db.get(User, payload.user_id)
    if user is None:
        raise NotFoundError("No user account with that id")
    if user.role != Role.DOCTOR:
        raise ValidationError(
            "The linked account must have the 'doctor' role",
            details={"current_role": user.role},
        )
    if db.execute(
        select(Doctor).where(Doctor.user_id == payload.user_id)
    ).scalar_one_or_none():
        raise ConflictError("This account already has a doctor profile")
    if db.execute(
        select(Doctor).where(Doctor.registration_no == payload.registration_no)
    ).scalar_one_or_none():
        raise ConflictError("That medical registration number is already recorded")

    get_department(db, payload.department_id)  # existence check

    doctor = Doctor(**payload.model_dump())
    db.add(doctor)
    db.commit()
    db.refresh(doctor)
    return doctor


def get_doctor(db: Session, doctor_id: int) -> Doctor:
    doctor = db.get(Doctor, doctor_id)
    if doctor is None:
        raise NotFoundError("Doctor not found")
    return doctor


def get_doctor_by_user(db: Session, user_id: int) -> Doctor:
    doctor = db.execute(
        select(Doctor).where(Doctor.user_id == user_id)
    ).scalar_one_or_none()
    if doctor is None:
        raise NotFoundError("No doctor profile linked to this account")
    return doctor


def list_doctors(
    db: Session, *, department_id: int | None = None, accepting_only: bool = False
) -> list[Doctor]:
    stmt = select(Doctor).order_by(Doctor.id)
    if department_id is not None:
        stmt = stmt.where(Doctor.department_id == department_id)
    if accepting_only:
        stmt = stmt.where(Doctor.is_accepting_patients.is_(True))
    return list(db.execute(stmt).scalars())


def update_doctor(db: Session, doctor_id: int, payload: DoctorUpdate) -> Doctor:
    doctor = get_doctor(db, doctor_id)
    changes = payload.model_dump(exclude_unset=True, exclude_none=True)
    if "department_id" in changes:
        get_department(db, changes["department_id"])
    for field, value in changes.items():
        setattr(doctor, field, value)
    db.commit()
    db.refresh(doctor)
    return doctor


def decorate(db: Session, doctor: Doctor) -> dict:
    """Flatten a doctor plus its joined names into a response dict."""
    user = db.get(User, doctor.user_id)
    dept = db.get(Department, doctor.department_id)
    return {
        **{
            c.name: getattr(doctor, c.name) for c in doctor.__table__.columns
        },
        "full_name": user.full_name if user else None,
        "department_name": dept.name if dept else None,
    }


# --- duty roster -----------------------------------------------------------


def _overlaps(a_start: time, a_end: time, b_start: time, b_end: time) -> bool:
    return a_start < b_end and b_start < a_end


def add_duty_slot(db: Session, doctor_id: int, payload: DutySlotCreate) -> DutySlot:
    get_doctor(db, doctor_id)

    # A doctor cannot be rostered into two rooms at the same hour. Compare only
    # against slots whose validity period overlaps the new one.
    for existing in list_duty_slots(db, doctor_id, active_only=True):
        if existing.day_of_week != payload.day_of_week:
            continue
        if not _validity_overlaps(
            existing.valid_from, existing.valid_to, payload.valid_from, payload.valid_to
        ):
            continue
        if _overlaps(
            existing.start_time, existing.end_time, payload.start_time, payload.end_time
        ):
            raise ConflictError(
                "This clashes with an existing duty slot",
                details={
                    "conflicting_slot_id": existing.id,
                    "room": existing.room,
                    "start_time": existing.start_time.isoformat(),
                    "end_time": existing.end_time.isoformat(),
                },
            )

    slot = DutySlot(doctor_id=doctor_id, **payload.model_dump())
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


def _validity_overlaps(
    a_from: date, a_to: date | None, b_from: date, b_to: date | None
) -> bool:
    """Do two open-ended validity periods intersect?"""
    if a_to is not None and a_to < b_from:
        return False
    if b_to is not None and b_to < a_from:
        return False
    return True


def list_duty_slots(
    db: Session, doctor_id: int, *, active_only: bool = True
) -> list[DutySlot]:
    stmt = (
        select(DutySlot)
        .where(DutySlot.doctor_id == doctor_id)
        .order_by(DutySlot.day_of_week, DutySlot.start_time)
    )
    if active_only:
        stmt = stmt.where(DutySlot.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def deactivate_duty_slot(db: Session, doctor_id: int, slot_id: int) -> DutySlot:
    slot = db.get(DutySlot, slot_id)
    if slot is None or slot.doctor_id != doctor_id:
        raise NotFoundError("Duty slot not found for this doctor")
    slot.is_active = False
    db.commit()
    db.refresh(slot)
    return slot


# --- leaves ----------------------------------------------------------------


def request_leave(db: Session, doctor_id: int, payload: LeaveCreate) -> Leave:
    get_doctor(db, doctor_id)
    leave = Leave(
        doctor_id=doctor_id,
        leave_type=str(payload.leave_type),
        start_date=payload.start_date,
        end_date=payload.end_date,
        reason=payload.reason,
    )
    db.add(leave)
    db.commit()
    db.refresh(leave)
    return leave


def decide_leave(
    db: Session, leave_id: int, status: LeaveStatus, approver_user_id: int
) -> Leave:
    leave = db.get(Leave, leave_id)
    if leave is None:
        raise NotFoundError("Leave request not found")
    if leave.status != LeaveStatus.PENDING:
        raise ConflictError(f"This request was already {leave.status}")
    leave.status = str(status)
    leave.approved_by_user_id = approver_user_id
    db.commit()
    db.refresh(leave)
    return leave


def list_leaves(
    db: Session, doctor_id: int | None = None, *, status: LeaveStatus | None = None
) -> list[Leave]:
    stmt = select(Leave).order_by(Leave.start_date.desc())
    if doctor_id is not None:
        stmt = stmt.where(Leave.doctor_id == doctor_id)
    if status is not None:
        stmt = stmt.where(Leave.status == str(status))
    return list(db.execute(stmt).scalars())


def leave_on(db: Session, doctor_id: int, on_date: date) -> Leave | None:
    """The approved leave covering ``on_date``, if any."""
    return db.execute(
        select(Leave).where(
            Leave.doctor_id == doctor_id,
            Leave.status == LeaveStatus.APPROVED,
            Leave.start_date <= on_date,
            Leave.end_date >= on_date,
        )
    ).scalars().first()


# --- credentials -----------------------------------------------------------


def add_credential(
    db: Session, doctor_id: int, payload: CredentialCreate
) -> DoctorCredential:
    get_doctor(db, doctor_id)
    fingerprint = credential_fingerprint(payload.raw_value)

    clash = db.execute(
        select(DoctorCredential).where(
            DoctorCredential.credential_type == str(payload.credential_type),
            DoctorCredential.fingerprint == fingerprint,
        )
    ).scalar_one_or_none()
    if clash is not None:
        if clash.doctor_id == doctor_id:
            raise ConflictError("This credential is already registered to this doctor")
        raise ConflictError("This credential is already registered to another doctor")

    credential = DoctorCredential(
        doctor_id=doctor_id,
        credential_type=str(payload.credential_type),
        fingerprint=fingerprint,
        label=payload.label,
    )
    db.add(credential)
    db.commit()
    db.refresh(credential)
    return credential


def list_credentials(db: Session, doctor_id: int) -> list[DoctorCredential]:
    return list(
        db.execute(
            select(DoctorCredential)
            .where(DoctorCredential.doctor_id == doctor_id)
            .order_by(DoctorCredential.id)
        ).scalars()
    )


def revoke_credential(
    db: Session, doctor_id: int, credential_id: int
) -> DoctorCredential:
    credential = db.get(DoctorCredential, credential_id)
    if credential is None or credential.doctor_id != doctor_id:
        raise NotFoundError("Credential not found for this doctor")
    credential.is_active = False
    db.commit()
    db.refresh(credential)
    return credential


def resolve_credential(
    db: Session, credential_type: str, raw_value: str
) -> Doctor | None:
    """Map a raw reader observation back to a doctor. Used by Room 1."""
    credential = db.execute(
        select(DoctorCredential).where(
            DoctorCredential.credential_type == credential_type,
            DoctorCredential.fingerprint == credential_fingerprint(raw_value),
            DoctorCredential.is_active.is_(True),
        )
    ).scalar_one_or_none()
    return db.get(Doctor, credential.doctor_id) if credential else None


# --- expected availability -------------------------------------------------


def _slot_applies_on(slot: DutySlot, on_date: date) -> bool:
    if not slot.is_active or slot.day_of_week != on_date.weekday():
        return False
    if slot.valid_from > on_date:
        return False
    return slot.valid_to is None or slot.valid_to >= on_date


def get_day_availability(
    db: Session, doctor_id: int, on_date: date
) -> DayAvailability:
    """Concrete duty windows for one date, after applying approved leave."""
    doctor = get_doctor(db, doctor_id)
    leave = leave_on(db, doctor_id, on_date)
    if leave is not None:
        return DayAvailability(
            doctor_id=doctor_id,
            date=on_date,
            is_on_leave=True,
            leave_type=leave.leave_type,
        )

    windows: list[DutyWindow] = []
    for slot in list_duty_slots(db, doctor_id, active_only=True):
        if not _slot_applies_on(slot, on_date):
            continue
        minutes = _minutes_between(slot.start_time, slot.end_time)
        windows.append(
            DutyWindow(
                doctor_id=doctor_id,
                date=on_date,
                start_time=slot.start_time,
                end_time=slot.end_time,
                room=slot.room,
                duration_minutes=minutes,
            )
        )

    windows.sort(key=lambda w: w.start_time)
    total = sum(w.duration_minutes for w in windows)
    per_patient = max(doctor.avg_consultation_minutes, 1)
    capacity = min(total // per_patient, doctor.max_patients_per_day)

    return DayAvailability(
        doctor_id=doctor_id,
        date=on_date,
        is_on_leave=False,
        windows=windows,
        total_minutes=total,
        capacity_estimate=capacity,
    )


def _minutes_between(start: time, end: time) -> int:
    return (end.hour * 60 + end.minute) - (start.hour * 60 + start.minute)


def rostered_window_at(db: Session, doctor_id: int, moment: datetime) -> DutyWindow | None:
    """The duty window covering ``moment``, or None if not rostered then."""
    availability = get_day_availability(db, doctor_id, moment.date())
    if availability.is_on_leave:
        return None
    clock = moment.time()
    for window in availability.windows:
        if window.start_time <= clock < window.end_time:
            return window
    return None
