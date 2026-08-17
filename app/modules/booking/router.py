"""Room 3 endpoints — one core, four ways in.

App and website callers use a JWT. The kiosk and the telephony gateway are
provisioned machines with no human login, so they authenticate with the same
device key Room 1 uses.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query, status

from app.core.deps import DbSession, get_current_user, require_roles, verify_device_key
from app.core.errors import PermissionError_
from app.core.security import Role
from app.modules.booking import service
from app.modules.booking.models import AppointmentStatus, BookingChannel
from app.modules.booking.schemas import (
    AppointmentCreate,
    AppointmentOut,
    BookForPatient,
    CancelRequest,
    DaySlots,
    IVRInput,
    IVRPrompt,
    IVRStart,
    KioskLookup,
    KioskTicket,
    PatientCreate,
    PatientOut,
    RescheduleRequest,
)
from app.modules.doctors import service as doctors_service
from app.modules.identity.models import User

router = APIRouter(prefix="/booking", tags=["Room 3 - Patient Booking"])

StaffOrAdmin = Depends(require_roles(Role.ADMIN, Role.STAFF))
DeviceAuth = Depends(verify_device_key)

#: Channels a human client may claim for itself. Kiosk and IVR bookings are
#: only ever stamped by their own endpoints, so a phone app cannot pose as one.
SELF_SERVICE_CHANNELS = {BookingChannel.MOBILE_APP, BookingChannel.WEBSITE}


def _out(db: DbSession, appointment) -> AppointmentOut:
    return AppointmentOut.model_validate(service.decorate(db, appointment))


# --- patient records -------------------------------------------------------


@router.get("/me/patient", response_model=PatientOut)
def my_patient_record(db: DbSession, user=Depends(get_current_user)) -> PatientOut:
    """The logged-in user's own patient record, created on first access."""
    return PatientOut.model_validate(service.patient_for_user(db, user))


@router.post(
    "/patients",
    response_model=PatientOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[StaffOrAdmin],
)
def create_patient(payload: PatientCreate, db: DbSession) -> PatientOut:
    """Reception registers a patient who has no account (family member, walk-in)."""
    return PatientOut.model_validate(service.create_patient(db, payload))


# --- slots -----------------------------------------------------------------


@router.get("/doctors/{doctor_id}/slots", response_model=DaySlots)
def day_slots(
    doctor_id: int,
    db: DbSession,
    on_date: date = Query(default_factory=date.today, alias="date"),
) -> DaySlots:
    """Bookable slots for a date, with a live warning if the doctor is missing."""
    return service.build_day_slots(db, doctor_id, on_date)


# --- app / website booking -------------------------------------------------


@router.post(
    "/appointments", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED
)
def book_for_self(
    payload: AppointmentCreate,
    db: DbSession,
    channel: BookingChannel = BookingChannel.WEBSITE,
    user=Depends(get_current_user),
) -> AppointmentOut:
    if channel not in SELF_SERVICE_CHANNELS:
        raise PermissionError_(
            "This channel can only be used by its own endpoint",
            details={"allowed": sorted(str(c) for c in SELF_SERVICE_CHANNELS)},
        )
    patient = service.patient_for_user(db, user)
    appointment = service.book(
        db,
        patient_id=patient.id,
        payload=payload,
        channel=channel,
        booked_by_user_id=user.id,
    )
    return _out(db, appointment)


@router.post(
    "/appointments/for-patient",
    response_model=AppointmentOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[StaffOrAdmin],
)
def book_for_patient(
    payload: BookForPatient, db: DbSession, user=Depends(get_current_user)
) -> AppointmentOut:
    """Reception books on behalf of someone standing at the desk."""
    existing = service.find_patients_by_phone(db, payload.patient.phone)
    match = next(
        (p for p in existing if p.full_name.lower() == payload.patient.full_name.lower()),
        None,
    )
    patient = match or service.create_patient(db, payload.patient)
    appointment = service.book(
        db,
        patient_id=patient.id,
        payload=AppointmentCreate(**payload.model_dump(exclude={"patient"})),
        channel=BookingChannel.STAFF,
        booked_by_user_id=user.id,
    )
    return _out(db, appointment)


# --- reading and changing appointments -------------------------------------


def _visible_to(db: DbSession, user: User, appointment) -> bool:
    role = str(user.role)
    if role in {Role.ADMIN, Role.STAFF}:
        return True
    if role == Role.DOCTOR:
        profile = doctors_service.get_doctor_by_user(db, user.id)
        return appointment.doctor_id == profile.id
    patient = service.patient_for_user(db, user)
    return appointment.patient_id == patient.id


@router.get("/appointments", response_model=list[AppointmentOut])
def list_appointments(
    db: DbSession,
    doctor_id: int | None = None,
    on_date: date | None = None,
    appointment_status: AppointmentStatus | None = None,
    channel: BookingChannel | None = None,
    user=Depends(get_current_user),
) -> list[AppointmentOut]:
    """Scoped by role: patients see only their own, doctors only their clinic."""
    role = str(user.role)
    patient_id = None
    if role == Role.PATIENT:
        patient_id = service.patient_for_user(db, user).id
    elif role == Role.DOCTOR:
        doctor_id = doctors_service.get_doctor_by_user(db, user.id).id

    rows = service.list_appointments(
        db,
        doctor_id=doctor_id,
        patient_id=patient_id,
        on_date=on_date,
        status=appointment_status,
        channel=channel,
    )
    return [_out(db, a) for a in rows]


@router.get("/appointments/{reference}", response_model=AppointmentOut)
def get_by_reference(
    reference: str, db: DbSession, user=Depends(get_current_user)
) -> AppointmentOut:
    appointment = service.get_by_reference(db, reference)
    if not _visible_to(db, user, appointment):
        raise PermissionError_("This appointment does not belong to you")
    return _out(db, appointment)


@router.post("/appointments/{appointment_id}/cancel", response_model=AppointmentOut)
def cancel(
    appointment_id: int,
    payload: CancelRequest,
    db: DbSession,
    user=Depends(get_current_user),
) -> AppointmentOut:
    appointment = service.get_appointment(db, appointment_id)
    if not _visible_to(db, user, appointment):
        raise PermissionError_("This appointment does not belong to you")
    return _out(db, service.cancel(db, appointment_id, payload.reason))


@router.post("/appointments/{appointment_id}/reschedule", response_model=AppointmentOut)
def reschedule(
    appointment_id: int,
    payload: RescheduleRequest,
    db: DbSession,
    user=Depends(get_current_user),
) -> AppointmentOut:
    appointment = service.get_appointment(db, appointment_id)
    if not _visible_to(db, user, appointment):
        raise PermissionError_("This appointment does not belong to you")
    replacement = service.reschedule(
        db, appointment_id, payload.appointment_date, payload.preferred_start
    )
    return _out(db, replacement)


@router.post(
    "/appointments/{appointment_id}/check-in",
    response_model=AppointmentOut,
    dependencies=[StaffOrAdmin],
)
def check_in(appointment_id: int, db: DbSession) -> AppointmentOut:
    return _out(db, service.check_in(db, appointment_id))


# --- kiosk -----------------------------------------------------------------


@router.post("/kiosk/lookup", response_model=list[PatientOut], dependencies=[DeviceAuth])
def kiosk_lookup(payload: KioskLookup, db: DbSession) -> list[PatientOut]:
    """Touchscreen: type a phone number, pick your name from the list.

    One number often covers a whole family, so every match is returned.
    """
    return [
        PatientOut.model_validate(p)
        for p in service.find_patients_by_phone(db, payload.phone)
    ]


@router.post(
    "/kiosk/book",
    response_model=KioskTicket,
    status_code=status.HTTP_201_CREATED,
    dependencies=[DeviceAuth],
)
def kiosk_book(payload: BookForPatient, db: DbSession) -> KioskTicket:
    """Books and returns exactly what the kiosk prints on the slip."""
    existing = service.find_patients_by_phone(db, payload.patient.phone)
    match = next(
        (p for p in existing if p.full_name.lower() == payload.patient.full_name.lower()),
        None,
    )
    patient = match or service.create_patient(db, payload.patient)
    appointment = service.book(
        db,
        patient_id=patient.id,
        payload=AppointmentCreate(**payload.model_dump(exclude={"patient"})),
        channel=BookingChannel.KIOSK,
    )
    decorated = service.decorate(db, appointment)
    when = appointment.slot_start.strftime("%H:%M")
    return KioskTicket(
        booking_reference=appointment.booking_reference,
        patient_name=decorated["patient_name"] or patient.full_name,
        doctor_name=decorated["doctor_name"] or "",
        department_name=decorated["department_name"] or "",
        room=appointment.room,
        appointment_date=appointment.appointment_date,
        slot_start=appointment.slot_start,
        message_hi=(
            f"आपका बुकिंग नंबर {appointment.booking_reference} है। "
            f"कृपया {when} बजे कमरा {appointment.room} पर पहुँचें। "
            "यह पर्ची सँभालकर रखें।"
        ),
        message_en=(
            f"Your booking number is {appointment.booking_reference}. "
            f"Please reach room {appointment.room} at {when}. Keep this slip."
        ),
    )


# --- IVR -------------------------------------------------------------------


@router.post("/ivr/start", response_model=IVRPrompt, dependencies=[DeviceAuth])
def ivr_start(payload: IVRStart, db: DbSession) -> IVRPrompt:
    """Telephony gateway opens a call. Returns the first prompt to read out."""
    return service.start_ivr(db, payload.caller_phone, payload.language)


@router.post("/ivr/input", response_model=IVRPrompt, dependencies=[DeviceAuth])
def ivr_input(payload: IVRInput, db: DbSession) -> IVRPrompt:
    """One keypress from the caller; returns the next prompt."""
    return service.handle_ivr_input(db, payload.session_id, payload.digits)
