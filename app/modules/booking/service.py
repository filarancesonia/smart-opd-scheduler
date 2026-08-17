"""Room 3 business logic: patients, slot allocation, booking, and the IVR."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import date, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.timeutil import local_today
from app.modules.booking.models import (
    Appointment,
    AppointmentStatus,
    BookingChannel,
    IVRSession,
    Patient,
)
from app.modules.booking.schemas import (
    AppointmentCreate,
    DaySlots,
    IVROption,
    IVRPrompt,
    PatientCreate,
    SlotOut,
)
from app.modules.doctors import service as doctors_service
from app.modules.doctors.models import Department, Doctor
from app.modules.identity.models import User

#: Unambiguous alphabet — no O/0 or I/1, because these get read aloud on a
#: phone line and copied off a printed kiosk slip by hand.
_REF_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

#: How far ahead booking is allowed to open.
MAX_ADVANCE_DAYS = 30


# --- patients --------------------------------------------------------------


def create_patient(
    db: Session, payload: PatientCreate, *, user_id: int | None = None
) -> Patient:
    patient = Patient(**payload.model_dump(exclude={"gender"}), user_id=user_id)
    patient.gender = str(payload.gender) if payload.gender else None
    db.add(patient)
    db.commit()
    db.refresh(patient)
    return patient


def get_patient(db: Session, patient_id: int) -> Patient:
    patient = db.get(Patient, patient_id)
    if patient is None:
        raise NotFoundError("Patient record not found")
    return patient


def find_patients_by_phone(db: Session, phone: str) -> list[Patient]:
    """One phone can cover a whole family, so this returns every match."""
    return list(
        db.execute(
            select(Patient).where(Patient.phone == phone).order_by(Patient.full_name)
        ).scalars()
    )


def patient_for_user(db: Session, user: User) -> Patient:
    """The patient record belonging to a logged-in account, created on demand."""
    existing = db.execute(
        select(Patient).where(Patient.user_id == user.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    return create_patient(
        db,
        PatientCreate(
            full_name=user.full_name,
            phone=user.phone,
            preferred_language=user.preferred_language,
        ),
        user_id=user.id,
    )


# --- slot allocation -------------------------------------------------------


def _add_minutes(clock: time, minutes: int) -> time:
    total = clock.hour * 60 + clock.minute + minutes
    total = min(total, 23 * 60 + 59)
    return time(total // 60, total % 60)


def active_appointments(
    db: Session, doctor_id: int, on_date: date
) -> list[Appointment]:
    return list(
        db.execute(
            select(Appointment)
            .where(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_date == on_date,
                Appointment.status.in_(AppointmentStatus.active()),
            )
            .order_by(Appointment.slot_start)
        ).scalars()
    )


def build_day_slots(db: Session, doctor_id: int, on_date: date) -> DaySlots:
    """Every slot the roster allows for a date, flagged free or taken."""
    doctor = doctors_service.get_doctor(db, doctor_id)
    availability = doctors_service.get_day_availability(db, doctor_id, on_date)
    taken = {a.slot_start for a in active_appointments(db, doctor_id, on_date)}

    if availability.is_on_leave:
        return DaySlots(
            doctor_id=doctor_id,
            date=on_date,
            is_on_leave=True,
            capacity=0,
            booked=len(taken),
            remaining=0,
        )

    step = max(doctor.avg_consultation_minutes, 1)
    slots: list[SlotOut] = []
    for window in availability.windows:
        cursor = window.start_time
        while _add_minutes(cursor, step) <= window.end_time:
            end = _add_minutes(cursor, step)
            slots.append(
                SlotOut(
                    start=cursor,
                    end=end,
                    room=window.room,
                    available=cursor not in taken,
                )
            )
            cursor = end

    capacity = min(len(slots), doctor.max_patients_per_day)
    return DaySlots(
        doctor_id=doctor_id,
        date=on_date,
        is_on_leave=False,
        capacity=capacity,
        booked=len(taken),
        remaining=max(capacity - len(taken), 0),
        slots=slots,
        presence_warning=_presence_warning(db, doctor_id, on_date),
    )


def _presence_warning(db: Session, doctor_id: int, on_date: date) -> str | None:
    """Surface a live Room 1 deviation when booking for today.

    A patient about to book at 11:00 deserves to know the doctor has not
    arrived yet — that is the entire point of the project.
    """
    if on_date != local_today():
        return None
    # Imported lazily: Room 1 already imports Room 2, and this keeps the
    # booking module usable even if presence hardware is not deployed.
    from app.modules.presence import service as presence_service
    from app.modules.presence.models import RosterDeviation

    presence = presence_service.get_presence(db, doctor_id)
    if presence.deviation == RosterDeviation.ABSENT_WHILE_ROSTERED:
        late = presence.minutes_late or 0
        return f"Doctor has not arrived yet (expected {late} minutes ago)"
    if presence.deviation == RosterDeviation.ON_APPROVED_LEAVE:
        return "Doctor is on approved leave today"
    return None


def _pick_slot(
    db: Session, doctor_id: int, on_date: date, preferred: time | None
) -> SlotOut:
    day = build_day_slots(db, doctor_id, on_date)

    if day.is_on_leave:
        raise ConflictError("The doctor is on leave on this date")
    if not day.slots:
        raise ConflictError("The doctor has no clinic scheduled on this date")
    if day.remaining <= 0:
        raise ConflictError(
            "This clinic is fully booked",
            details={"capacity": day.capacity, "booked": day.booked},
        )

    if preferred is not None:
        match = next((s for s in day.slots if s.start == preferred), None)
        if match is None:
            raise ValidationError("That time is not part of this doctor's clinic")
        if not match.available:
            raise ConflictError("That slot has already been taken")
        return match

    free = next((s for s in day.slots if s.available), None)
    if free is None:
        raise ConflictError("This clinic is fully booked")
    return free


# --- booking ---------------------------------------------------------------


def _generate_reference(db: Session) -> str:
    for _ in range(10):
        candidate = "OPD" + "".join(secrets.choice(_REF_ALPHABET) for _ in range(7))
        clash = db.execute(
            select(Appointment).where(Appointment.booking_reference == candidate)
        ).scalar_one_or_none()
        if clash is None:
            return candidate
    raise ConflictError("Could not allocate a booking reference, please retry")


def _validate_date(on_date: date) -> None:
    today = local_today()
    if on_date < today:
        raise ValidationError("Appointments cannot be booked for a past date")
    if on_date > today + timedelta(days=MAX_ADVANCE_DAYS):
        raise ValidationError(
            f"Bookings open only {MAX_ADVANCE_DAYS} days in advance",
            details={"latest_date": (today + timedelta(days=MAX_ADVANCE_DAYS)).isoformat()},
        )


def book(
    db: Session,
    *,
    patient_id: int,
    payload: AppointmentCreate,
    channel: BookingChannel,
    booked_by_user_id: int | None = None,
) -> Appointment:
    patient = get_patient(db, patient_id)
    doctor = doctors_service.get_doctor(db, payload.doctor_id)
    _validate_date(payload.appointment_date)

    if not doctor.is_accepting_patients:
        raise ConflictError("This doctor is not accepting appointments at present")

    duplicate = db.execute(
        select(Appointment).where(
            Appointment.patient_id == patient_id,
            Appointment.doctor_id == payload.doctor_id,
            Appointment.appointment_date == payload.appointment_date,
            Appointment.status.in_(AppointmentStatus.active()),
        )
    ).scalar_one_or_none()
    if duplicate is not None:
        raise ConflictError(
            "This patient already has an appointment with this doctor that day",
            details={"booking_reference": duplicate.booking_reference},
        )

    slot = _pick_slot(db, payload.doctor_id, payload.appointment_date, payload.preferred_start)

    appointment = Appointment(
        booking_reference=_generate_reference(db),
        patient_id=patient.id,
        doctor_id=doctor.id,
        department_id=doctor.department_id,
        appointment_date=payload.appointment_date,
        slot_start=slot.start,
        slot_end=slot.end,
        room=slot.room,
        status=str(AppointmentStatus.BOOKED),
        channel=str(channel),
        reason=payload.reason,
        is_follow_up=payload.is_follow_up,
        booked_by_user_id=booked_by_user_id,
    )
    db.add(appointment)
    db.commit()
    db.refresh(appointment)
    return appointment


def get_appointment(db: Session, appointment_id: int) -> Appointment:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise NotFoundError("Appointment not found")
    return appointment


def get_by_reference(db: Session, reference: str) -> Appointment:
    appointment = db.execute(
        select(Appointment).where(
            Appointment.booking_reference == reference.strip().upper()
        )
    ).scalar_one_or_none()
    if appointment is None:
        raise NotFoundError("No appointment found for that reference")
    return appointment


def cancel(db: Session, appointment_id: int, reason: str = "") -> Appointment:
    appointment = get_appointment(db, appointment_id)
    if appointment.status not in AppointmentStatus.active():
        raise ConflictError(f"This appointment is already {appointment.status}")
    appointment.status = str(AppointmentStatus.CANCELLED)
    appointment.cancelled_reason = reason
    db.commit()
    db.refresh(appointment)
    return appointment


def reschedule(
    db: Session, appointment_id: int, new_date: date, preferred: time | None = None
) -> Appointment:
    """Cancel-and-rebook, keeping the old row for the audit trail."""
    old = get_appointment(db, appointment_id)
    if old.status not in AppointmentStatus.active():
        raise ConflictError(f"This appointment is already {old.status}")
    _validate_date(new_date)

    slot = _pick_slot(db, old.doctor_id, new_date, preferred)

    old.status = str(AppointmentStatus.RESCHEDULED)
    replacement = Appointment(
        booking_reference=_generate_reference(db),
        patient_id=old.patient_id,
        doctor_id=old.doctor_id,
        department_id=old.department_id,
        appointment_date=new_date,
        slot_start=slot.start,
        slot_end=slot.end,
        room=slot.room,
        status=str(AppointmentStatus.BOOKED),
        channel=old.channel,
        reason=old.reason,
        is_follow_up=old.is_follow_up,
        booked_by_user_id=old.booked_by_user_id,
    )
    db.add(replacement)
    db.commit()
    db.refresh(replacement)
    return replacement


def check_in(db: Session, appointment_id: int) -> Appointment:
    """Patient has physically arrived — Room 5 picks them up from here."""
    appointment = get_appointment(db, appointment_id)
    if appointment.status == AppointmentStatus.CHECKED_IN:
        return appointment
    if appointment.status != AppointmentStatus.BOOKED:
        raise ConflictError(f"Cannot check in an appointment that is {appointment.status}")
    appointment.status = str(AppointmentStatus.CHECKED_IN)
    appointment.checked_in_at = utcnow()
    db.commit()
    db.refresh(appointment)
    return appointment


def list_appointments(
    db: Session,
    *,
    doctor_id: int | None = None,
    patient_id: int | None = None,
    on_date: date | None = None,
    status: AppointmentStatus | None = None,
    channel: BookingChannel | None = None,
    limit: int = 200,
) -> list[Appointment]:
    stmt = select(Appointment).order_by(
        Appointment.appointment_date.desc(), Appointment.slot_start
    )
    if doctor_id is not None:
        stmt = stmt.where(Appointment.doctor_id == doctor_id)
    if patient_id is not None:
        stmt = stmt.where(Appointment.patient_id == patient_id)
    if on_date is not None:
        stmt = stmt.where(Appointment.appointment_date == on_date)
    if status is not None:
        stmt = stmt.where(Appointment.status == str(status))
    if channel is not None:
        stmt = stmt.where(Appointment.channel == str(channel))
    return list(db.execute(stmt.limit(limit)).scalars())


def decorate(db: Session, appointment: Appointment) -> dict:
    patient = db.get(Patient, appointment.patient_id)
    doctor = db.get(Doctor, appointment.doctor_id)
    doctor_user = db.get(User, doctor.user_id) if doctor else None
    department = db.get(Department, appointment.department_id)
    return {
        **{c.name: getattr(appointment, c.name) for c in appointment.__table__.columns},
        "patient_name": patient.full_name if patient else None,
        "doctor_name": doctor_user.full_name if doctor_user else None,
        "department_name": department.name if department else None,
    }


# --- IVR state machine -----------------------------------------------------
#
# The caller has twelve keys and no screen. Every state therefore offers a
# short numbered menu, and the whole conversation is parked in the session row
# between keypresses.

WELCOME = "welcome"
CHOOSE_DEPARTMENT = "choose_department"
CHOOSE_DOCTOR = "choose_doctor"
CHOOSE_DATE = "choose_date"
CONFIRM = "confirm"
COMPLETE = "complete"


def _ctx(session: IVRSession) -> dict:
    return json.loads(session.context or "{}")


def _save_ctx(session: IVRSession, data: dict) -> None:
    session.context = json.dumps(data)


def _prompt(
    session: IVRSession,
    prompt_hi: str,
    prompt_en: str,
    options: list[IVROption] | None = None,
    *,
    complete: bool = False,
    reference: str | None = None,
) -> IVRPrompt:
    return IVRPrompt(
        session_id=session.session_id,
        state=session.state,
        prompt_hi=prompt_hi,
        prompt_en=prompt_en,
        options=options or [],
        expects_input=not complete,
        call_complete=complete,
        booking_reference=reference,
    )


def start_ivr(db: Session, caller_phone: str, language: str = "hi") -> IVRPrompt:
    session = IVRSession(
        session_id=str(uuid.uuid4()),
        caller_phone=caller_phone,
        state=WELCOME,
        language=language,
        context="{}",
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return _prompt(
        session,
        "नमस्ते। अस्पताल अपॉइंटमेंट सेवा में आपका स्वागत है। "
        "नया अपॉइंटमेंट बुक करने के लिए 1 दबाएँ। "
        "अपनी मौजूदा बुकिंग सुनने के लिए 2 दबाएँ।",
        "Welcome to the hospital appointment line. "
        "Press 1 to book a new appointment. Press 2 to hear your existing booking.",
        [
            IVROption(key="1", label_hi="नया अपॉइंटमेंट", label_en="New appointment"),
            IVROption(key="2", label_hi="मौजूदा बुकिंग", label_en="Existing booking"),
        ],
    )


def _get_session(db: Session, session_id: str) -> IVRSession:
    session = db.execute(
        select(IVRSession).where(IVRSession.session_id == session_id)
    ).scalar_one_or_none()
    if session is None:
        raise NotFoundError("This call session has expired")
    if session.is_complete:
        raise ConflictError("This call has already finished")
    return session


def _department_menu(db: Session) -> tuple[list[IVROption], list[Department]]:
    departments = doctors_service.list_departments(db)[:9]
    options = [
        IVROption(key=str(i + 1), label_hi=d.name, label_en=d.name)
        for i, d in enumerate(departments)
    ]
    return options, departments


def _doctor_menu(db: Session, department_id: int) -> tuple[list[IVROption], list[Doctor]]:
    doctors = doctors_service.list_doctors(
        db, department_id=department_id, accepting_only=True
    )[:9]
    options = []
    for i, doctor in enumerate(doctors):
        user = db.get(User, doctor.user_id)
        name = user.full_name if user else f"Doctor {doctor.id}"
        options.append(IVROption(key=str(i + 1), label_hi=name, label_en=name))
    return options, doctors


def _invalid(session: IVRSession) -> IVRPrompt:
    return _prompt(
        session,
        "क्षमा करें, यह विकल्प उपलब्ध नहीं है। कृपया दोबारा दबाएँ।",
        "Sorry, that option is not available. Please try again.",
    )


def handle_ivr_input(db: Session, session_id: str, digits: str) -> IVRPrompt:
    session = _get_session(db, session_id)
    context = _ctx(session)
    choice = digits.strip()

    if session.state == WELCOME:
        if choice == "1":
            options, departments = _department_menu(db)
            if not options:
                session.is_complete = True
                db.commit()
                return _prompt(
                    session,
                    "इस समय कोई विभाग उपलब्ध नहीं है। कृपया बाद में कॉल करें।",
                    "No departments are available right now. Please call back later.",
                    complete=True,
                )
            _save_ctx(session, {"department_ids": [d.id for d in departments]})
            session.state = CHOOSE_DEPARTMENT
            db.commit()
            return _prompt(
                session,
                "विभाग चुनने के लिए संबंधित नंबर दबाएँ।",
                "Press the number for the department you need.",
                options,
            )
        if choice == "2":
            return _existing_booking(db, session)
        return _invalid(session)

    if session.state == CHOOSE_DEPARTMENT:
        department_ids = context.get("department_ids", [])
        index = _menu_index(choice, len(department_ids))
        if index is None:
            return _invalid(session)
        department_id = department_ids[index]
        options, doctors = _doctor_menu(db, department_id)
        if not options:
            session.is_complete = True
            db.commit()
            return _prompt(
                session,
                "इस विभाग में इस समय कोई डॉक्टर उपलब्ध नहीं है।",
                "No doctors are available in that department right now.",
                complete=True,
            )
        _save_ctx(
            session,
            {"department_id": department_id, "doctor_ids": [d.id for d in doctors]},
        )
        session.state = CHOOSE_DOCTOR
        db.commit()
        return _prompt(
            session,
            "डॉक्टर चुनने के लिए संबंधित नंबर दबाएँ।",
            "Press the number for the doctor you want.",
            options,
        )

    if session.state == CHOOSE_DOCTOR:
        doctor_ids = context.get("doctor_ids", [])
        index = _menu_index(choice, len(doctor_ids))
        if index is None:
            return _invalid(session)
        context["doctor_id"] = doctor_ids[index]
        _save_ctx(session, context)
        session.state = CHOOSE_DATE
        db.commit()
        return _prompt(
            session,
            "आज के लिए 1 दबाएँ। कल के लिए 2 दबाएँ।",
            "Press 1 for today. Press 2 for tomorrow.",
            [
                IVROption(key="1", label_hi="आज", label_en="Today"),
                IVROption(key="2", label_hi="कल", label_en="Tomorrow"),
            ],
        )

    if session.state == CHOOSE_DATE:
        if choice not in {"1", "2"}:
            return _invalid(session)
        target = local_today() + timedelta(days=0 if choice == "1" else 1)
        context["appointment_date"] = target.isoformat()
        _save_ctx(session, context)
        session.state = CONFIRM
        db.commit()

        day = build_day_slots(db, context["doctor_id"], target)
        if day.remaining <= 0:
            session.is_complete = True
            db.commit()
            return _prompt(
                session,
                "क्षमा करें, उस दिन सभी अपॉइंटमेंट भर चुके हैं। कृपया दूसरे दिन कोशिश करें।",
                "Sorry, that day is fully booked. Please try another day.",
                complete=True,
            )
        return _prompt(
            session,
            "पुष्टि करने के लिए 1 दबाएँ। रद्द करने के लिए 2 दबाएँ।",
            "Press 1 to confirm. Press 2 to cancel.",
            [
                IVROption(key="1", label_hi="पुष्टि करें", label_en="Confirm"),
                IVROption(key="2", label_hi="रद्द करें", label_en="Cancel"),
            ],
        )

    if session.state == CONFIRM:
        if choice == "2":
            session.state = COMPLETE
            session.is_complete = True
            db.commit()
            return _prompt(
                session,
                "आपका अनुरोध रद्द कर दिया गया है। धन्यवाद।",
                "Your request has been cancelled. Thank you.",
                complete=True,
            )
        if choice != "1":
            return _invalid(session)

        appointment = _book_from_ivr(db, session, context)
        session.state = COMPLETE
        session.is_complete = True
        session.appointment_id = appointment.id
        db.commit()

        spoken = " ".join(appointment.booking_reference)
        return _prompt(
            session,
            f"आपका अपॉइंटमेंट बुक हो गया है। आपका बुकिंग नंबर है {spoken}। "
            f"समय {appointment.slot_start.strftime('%H:%M')} बजे, कमरा {appointment.room}। "
            "यह जानकारी आपको SMS पर भी भेजी जाएगी। धन्यवाद।",
            f"Your appointment is booked. Your booking number is {spoken}. "
            f"Time {appointment.slot_start.strftime('%H:%M')}, room {appointment.room}. "
            "You will also receive this by SMS. Thank you.",
            complete=True,
            reference=appointment.booking_reference,
        )

    return _invalid(session)


def _menu_index(choice: str, size: int) -> int | None:
    if not choice.isdigit():
        return None
    index = int(choice) - 1
    return index if 0 <= index < size else None


def _existing_booking(db: Session, session: IVRSession) -> IVRPrompt:
    patients = find_patients_by_phone(db, session.caller_phone)
    upcoming = []
    for patient in patients:
        upcoming.extend(
            a
            for a in list_appointments(db, patient_id=patient.id)
            if a.status in AppointmentStatus.active()
            and a.appointment_date >= local_today()
        )
    session.is_complete = True
    db.commit()

    if not upcoming:
        return _prompt(
            session,
            "इस नंबर पर कोई आगामी बुकिंग नहीं मिली। धन्यवाद।",
            "No upcoming booking was found for this number. Thank you.",
            complete=True,
        )

    nearest = min(upcoming, key=lambda a: (a.appointment_date, a.slot_start))
    spoken = " ".join(nearest.booking_reference)
    return _prompt(
        session,
        f"आपकी बुकिंग {nearest.appointment_date.strftime('%d-%m-%Y')} को "
        f"{nearest.slot_start.strftime('%H:%M')} बजे, कमरा {nearest.room} में है। "
        f"बुकिंग नंबर {spoken}। धन्यवाद।",
        f"Your booking is on {nearest.appointment_date.strftime('%d-%m-%Y')} at "
        f"{nearest.slot_start.strftime('%H:%M')} in room {nearest.room}. "
        f"Booking number {spoken}. Thank you.",
        complete=True,
        reference=nearest.booking_reference,
    )


def _book_from_ivr(db: Session, session: IVRSession, context: dict) -> Appointment:
    """Reuse the caller's existing record, or open a provisional one.

    A keypad cannot spell a name, so a first-time caller gets a placeholder that
    reception completes at the desk.
    """
    existing = find_patients_by_phone(db, session.caller_phone)
    if existing:
        patient = existing[0]
    else:
        patient = create_patient(
            db,
            PatientCreate(
                full_name=f"Phone booking {session.caller_phone[-4:]}",
                phone=session.caller_phone,
                preferred_language=session.language,
            ),
        )

    return book(
        db,
        patient_id=patient.id,
        payload=AppointmentCreate(
            doctor_id=context["doctor_id"],
            appointment_date=date.fromisoformat(context["appointment_date"]),
            reason="Booked over IVR",
        ),
        channel=BookingChannel.IVR,
    )
