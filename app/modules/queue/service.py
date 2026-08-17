"""Room 5 logic: tokens, live ordering, and honest waiting-time estimates."""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.timeutil import as_utc, local_today, minutes_between
from app.modules.booking.models import Appointment, AppointmentStatus, Patient
from app.modules.doctors import service as doctors_service
from app.modules.identity.models import User
from app.modules.presence import service as presence_service
from app.modules.presence.models import PresenceStatus
from app.modules.queue.models import QueueEntry, QueueEntryStatus, QueueSession
from app.modules.queue.schemas import (
    BoardRow,
    DisplayBoard,
    MyPosition,
    QueueEntryOut,
    QueueOut,
)
from app.modules.scheduling import predictors
from app.modules.scheduling import service as scheduling_service
from app.modules.scheduling.optimizer import PlanItem, optimise

#: A patient called twice who is still not at the door is marked absent.
MAX_SKIPS = 2

#: How fast today's observed durations displace the model's prediction.
CALIBRATION_FULL_WEIGHT_AT = 5

logger = logging.getLogger("queue")


def _notify(action: str, *args, **kwargs) -> None:
    """Fire a Room 6 message without letting it disrupt the queue."""
    try:
        from app.modules.notifications import service as notifications

        getattr(notifications, action)(*args, **kwargs)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Notification %s failed", action)


# --- sessions --------------------------------------------------------------


def get_session(
    db: Session, doctor_id: int, session_date: date | None = None
) -> QueueSession | None:
    return db.execute(
        select(QueueSession).where(
            QueueSession.doctor_id == doctor_id,
            QueueSession.session_date == (session_date or local_today()),
        )
    ).scalar_one_or_none()


def require_session(
    db: Session, doctor_id: int, session_date: date | None = None
) -> QueueSession:
    session = get_session(db, doctor_id, session_date)
    if session is None:
        raise NotFoundError("No queue has been opened for this doctor today")
    return session


def open_queue(
    db: Session, doctor_id: int, session_date: date | None = None, room: str = ""
) -> QueueSession:
    doctors_service.get_doctor(db, doctor_id)
    on_date = session_date or local_today()

    existing = get_session(db, doctor_id, on_date)
    if existing is not None:
        if not existing.is_open:
            existing.is_open = True
            existing.closed_at = None
            db.commit()
            db.refresh(existing)
        return existing

    if not room:
        availability = doctors_service.get_day_availability(db, doctor_id, on_date)
        room = availability.windows[0].room if availability.windows else ""

    session = QueueSession(
        doctor_id=doctor_id,
        session_date=on_date,
        room=room,
        is_open=True,
        opened_at=utcnow(),
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def close_queue(db: Session, doctor_id: int, session_date: date | None = None) -> QueueSession:
    session = require_session(db, doctor_id, session_date)
    still_waiting = [
        e for e in _entries(db, session.id) if e.status == QueueEntryStatus.WAITING
    ]
    if still_waiting:
        raise ConflictError(
            "Patients are still waiting in this queue",
            details={"waiting": len(still_waiting)},
        )
    session.is_open = False
    session.closed_at = utcnow()
    db.commit()
    db.refresh(session)
    return session


# --- entries ---------------------------------------------------------------


def _entries(db: Session, session_id: int) -> list[QueueEntry]:
    return list(
        db.execute(
            select(QueueEntry)
            .where(QueueEntry.session_id == session_id)
            .order_by(QueueEntry.position, QueueEntry.token_number)
        ).scalars()
    )


def open_entries(db: Session, session_id: int) -> list[QueueEntry]:
    return [
        e for e in _entries(db, session_id) if e.status in QueueEntryStatus.open_states()
    ]


def join(db: Session, appointment_id: int) -> QueueEntry:
    """A checked-in patient takes a token."""
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise NotFoundError("Appointment not found")
    if appointment.status not in (
        AppointmentStatus.BOOKED,
        AppointmentStatus.CHECKED_IN,
    ):
        raise ConflictError(f"Cannot join the queue while {appointment.status}")

    session = require_session(db, appointment.doctor_id, appointment.appointment_date)
    if not session.is_open:
        raise ConflictError("This queue has been closed")

    existing = db.execute(
        select(QueueEntry).where(QueueEntry.appointment_id == appointment_id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    patient = db.get(Patient, appointment.patient_id)
    ctx = scheduling_service.build_context(db, appointment)
    predicted = predictors.duration_predictor.predict(ctx).value

    entry = QueueEntry(
        session_id=session.id,
        appointment_id=appointment.id,
        patient_id=appointment.patient_id,
        token_number=session.next_token,
        position=session.next_token,
        priority_tier=scheduling_service.priority_tier_for(patient),
        status=str(QueueEntryStatus.WAITING),
        joined_at=utcnow(),
        predicted_duration=predicted,
    )
    session.next_token += 1

    if appointment.status == AppointmentStatus.BOOKED:
        appointment.status = str(AppointmentStatus.CHECKED_IN)
        appointment.checked_in_at = utcnow()

    db.add(entry)
    db.commit()
    db.refresh(entry)

    reorder(db, session)
    db.refresh(entry)
    return entry


def get_entry(db: Session, entry_id: int) -> QueueEntry:
    entry = db.get(QueueEntry, entry_id)
    if entry is None:
        raise NotFoundError("Queue entry not found")
    return entry


# --- ordering --------------------------------------------------------------


def _session_end(db: Session, session: QueueSession) -> datetime:
    """When the doctor's clinic is rostered to finish today."""
    from app.core.timeutil import combine_local

    availability = doctors_service.get_day_availability(
        db, session.doctor_id, session.session_date
    )
    if availability.windows:
        return combine_local(session.session_date, availability.windows[-1].end_time)
    return as_utc(utcnow()) + timedelta(hours=4)


def reorder(db: Session, session: QueueSession) -> dict:
    """Re-plan the live queue through the Room 4 optimiser.

    Called whenever the queue changes — someone joins, an emergency arrives,
    a consultation finishes — because an ordering computed at 09:00 is worth
    very little at 11:30.
    """
    from app.core.timeutil import to_local

    # Room 7 first: anyone displaced past the threshold moves up a tier before
    # the ordering is computed, so a morning of emergencies cannot leave the
    # same people on the bench until closing time.
    _apply_aging(db, session.id)

    waiting = [
        e
        for e in open_entries(db, session.id)
        if e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
    ]
    if not waiting:
        return {"reordered": 0, "improvement_pct": 0.0, "notes": []}

    now_local = to_local(utcnow())
    end_local = to_local(_session_end(db, session))

    items = [
        PlanItem(
            appointment_id=entry.id,  # optimise over queue entries, not bookings
            patient_id=entry.patient_id,
            patient_name="",
            booked_start=to_local(entry.joined_at).time(),
            expected_duration=_effective_duration(session, entry),
            # Someone already standing in the corridor will not go home; the
            # no-show question was settled when they walked through the door.
            no_show_probability=0.0,
            priority_tier=entry.priority_tier,
        )
        for entry in waiting
    ]

    result = optimise(
        items,
        doctor_id=session.doctor_id,
        available_from=now_local.time(),
        available_until=end_local.time(),
    )

    by_id = {e.id: e for e in waiting}
    for assignment in result.assignments:
        by_id[assignment.appointment_id].position = assignment.position

    db.commit()
    recompute_etas(db, session)

    return {
        "reordered": len(result.assignments),
        "improvement_pct": result.improvement_pct,
        "notes": result.notes,
    }


def _apply_aging(db: Session, session_id: int) -> None:
    """Run Room 7's anti-starvation sweep, if that module is deployed."""
    try:
        from app.modules.emergency import service as emergency_service

        emergency_service.apply_aging(db, session_id)
    except ImportError:  # pragma: no cover - defensive
        pass


def _effective_duration(session: QueueSession, entry: QueueEntry) -> float:
    """Blend the model's prediction with what this clinic is actually doing.

    A prediction is a prior. Ten consultations into the morning, the doctor's
    real pace today is better evidence than any model trained on last year.
    """
    if session.observed_avg_minutes is None or session.completed_count == 0:
        return entry.predicted_duration
    weight = min(session.completed_count / CALIBRATION_FULL_WEIGHT_AT, 1.0)
    return (1 - weight) * entry.predicted_duration + weight * session.observed_avg_minutes


# --- estimates -------------------------------------------------------------


def recompute_etas(db: Session, session: QueueSession) -> None:
    """Set 'your turn in N minutes' for everyone still waiting."""
    now = utcnow()
    present = _doctor_present(db, session.doctor_id)

    entries = sorted(
        [
            e
            for e in open_entries(db, session.id)
            if e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED, QueueEntryStatus.CALLED)
        ],
        key=lambda e: e.position,
    )

    in_progress = next(
        (
            e
            for e in open_entries(db, session.id)
            if e.status == QueueEntryStatus.IN_PROGRESS
        ),
        None,
    )

    cursor = now
    if in_progress is not None and in_progress.started_at is not None:
        expected_end = as_utc(in_progress.started_at) + timedelta(
            minutes=_effective_duration(session, in_progress)
        )
        # If the current consultation has already overrun, assume it is nearly
        # done rather than reporting a negative wait.
        cursor = max(expected_end, now + timedelta(minutes=1))

    for entry in entries:
        if not present and in_progress is None:
            # The doctor is not here and nothing is running. Any number we
            # printed would be a guess dressed up as information.
            entry.estimated_wait_minutes = None
            continue
        entry.estimated_wait_minutes = max(minutes_between(now, cursor), 0)
        cursor += timedelta(minutes=_effective_duration(session, entry))

    db.commit()


def _doctor_present(db: Session, doctor_id: int) -> bool:
    return (
        presence_service.get_presence(db, doctor_id).status == PresenceStatus.PRESENT
    )


# --- flow control ----------------------------------------------------------


def call_next(db: Session, doctor_id: int) -> tuple[QueueEntry | None, str | None, int]:
    session = require_session(db, doctor_id)
    if not session.is_open:
        raise ConflictError("This queue has been closed")
    if not _doctor_present(db, doctor_id):
        # Calling patients into an empty room is precisely the failure this
        # system exists to prevent.
        raise ConflictError(
            "The doctor is not present — patients cannot be called yet",
            details={"hint": "Mark presence manually if the reader is offline"},
        )

    already = [
        e
        for e in open_entries(db, session.id)
        if e.status in (QueueEntryStatus.CALLED, QueueEntryStatus.IN_PROGRESS)
    ]
    if already:
        raise ConflictError(
            "A patient has already been called",
            details={"token_number": already[0].token_number},
        )

    candidates = sorted(
        [
            e
            for e in open_entries(db, session.id)
            if e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
        ],
        key=lambda e: (-e.priority_tier, e.position),
    )
    waiting_count = len(candidates)
    if not candidates:
        return None, "Nobody is waiting in this queue", 0

    entry = candidates[0]
    entry.status = str(QueueEntryStatus.CALLED)
    entry.called_at = utcnow()
    db.commit()
    db.refresh(entry)
    recompute_etas(db, session)

    _notify("notify_now_calling", db, entry)
    return entry, None, waiting_count - 1


def start_consultation(db: Session, entry_id: int) -> QueueEntry:
    entry = get_entry(db, entry_id)
    if entry.status not in (QueueEntryStatus.CALLED, QueueEntryStatus.WAITING):
        raise ConflictError(f"Cannot start a consultation that is {entry.status}")

    entry.status = str(QueueEntryStatus.IN_PROGRESS)
    entry.started_at = utcnow()

    appointment = db.get(Appointment, entry.appointment_id)
    if appointment is not None:
        appointment.status = str(AppointmentStatus.IN_PROGRESS)

    db.commit()
    db.refresh(entry)
    recompute_etas(db, db.get(QueueSession, entry.session_id))
    return entry


def complete_consultation(db: Session, entry_id: int, note: str = "") -> QueueEntry:
    """Finish a consultation and feed the outcome back to Room 4."""
    entry = get_entry(db, entry_id)
    if entry.status != QueueEntryStatus.IN_PROGRESS:
        raise ConflictError(f"Cannot complete a consultation that is {entry.status}")

    now = utcnow()
    entry.status = str(QueueEntryStatus.COMPLETED)
    entry.completed_at = now
    entry.estimated_wait_minutes = None
    entry.note = note

    session = db.get(QueueSession, entry.session_id)
    actual = max(minutes_between(entry.started_at, now), 1)
    total = (session.observed_avg_minutes or 0.0) * session.completed_count + actual
    session.completed_count += 1
    session.observed_avg_minutes = round(total / session.completed_count, 2)

    appointment = db.get(Appointment, entry.appointment_id)
    if appointment is not None:
        appointment.status = str(AppointmentStatus.COMPLETED)

    db.commit()

    if appointment is not None:
        scheduling_service.record_consultation(
            db,
            appointment,
            actual_start=entry.started_at,
            actual_end=now,
            was_no_show=False,
            slot_index=entry.position,
        )

    db.refresh(entry)
    reorder(db, session)
    db.refresh(entry)
    return entry


def skip(db: Session, entry_id: int) -> QueueEntry:
    """Called but not at the door. One more chance, then marked absent."""
    entry = get_entry(db, entry_id)
    if entry.status != QueueEntryStatus.CALLED:
        raise ConflictError("Only a called patient can be skipped")

    entry.skip_count += 1
    if entry.skip_count > MAX_SKIPS:
        db.commit()
        return mark_no_show(db, entry_id)

    entry.status = str(QueueEntryStatus.SKIPPED)
    entry.called_at = None
    db.commit()

    session = db.get(QueueSession, entry.session_id)
    # Move to the back of the current waiting group rather than losing the
    # token: someone who stepped out for water should not lose their morning.
    others = [
        e
        for e in open_entries(db, session.id)
        if e.id != entry.id and e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
    ]
    entry.position = max((e.position for e in others), default=0) + 1
    db.commit()
    db.refresh(entry)
    recompute_etas(db, session)
    return entry


def mark_no_show(db: Session, entry_id: int) -> QueueEntry:
    entry = get_entry(db, entry_id)
    if entry.status in (QueueEntryStatus.COMPLETED, QueueEntryStatus.NO_SHOW):
        raise ConflictError(f"This entry is already {entry.status}")

    entry.status = str(QueueEntryStatus.NO_SHOW)
    entry.estimated_wait_minutes = None

    appointment = db.get(Appointment, entry.appointment_id)
    if appointment is not None:
        appointment.status = str(AppointmentStatus.NO_SHOW)
    db.commit()

    if appointment is not None:
        scheduling_service.record_consultation(
            db, appointment, was_no_show=True, slot_index=entry.position
        )

    session = db.get(QueueSession, entry.session_id)
    db.refresh(entry)
    reorder(db, session)
    db.refresh(entry)
    return entry


# --- views -----------------------------------------------------------------


def entry_out(db: Session, entry: QueueEntry) -> QueueEntryOut:
    patient = db.get(Patient, entry.patient_id)
    return QueueEntryOut.model_validate(
        {
            **{c.name: getattr(entry, c.name) for c in entry.__table__.columns},
            "patient_name": patient.full_name if patient else None,
        }
    )


def get_queue(db: Session, doctor_id: int, session_date: date | None = None) -> QueueOut:
    session = require_session(db, doctor_id, session_date)
    entries = _entries(db, session.id)
    doctor = doctors_service.get_doctor(db, doctor_id)
    user = db.get(User, doctor.user_id)

    serving = next(
        (e for e in entries if e.status == QueueEntryStatus.IN_PROGRESS), None
    ) or next((e for e in entries if e.status == QueueEntryStatus.CALLED), None)

    return QueueOut(
        session_id=session.id,
        doctor_id=doctor_id,
        doctor_name=user.full_name if user else None,
        session_date=session.session_date,
        room=session.room,
        is_open=session.is_open,
        doctor_present=_doctor_present(db, doctor_id),
        waiting_count=sum(1 for e in entries if e.status == QueueEntryStatus.WAITING),
        completed_count=session.completed_count,
        observed_avg_minutes=session.observed_avg_minutes,
        now_serving=serving.token_number if serving else None,
        entries=[entry_out(db, e) for e in entries],
    )


def _mask(name: str | None) -> str:
    """First name plus a surname initial — enough to recognise yourself."""
    if not name:
        return "—"
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]} {parts[-1][0]}."


def _board_row(db: Session, entry: QueueEntry) -> BoardRow:
    patient = db.get(Patient, entry.patient_id)
    return BoardRow(
        token_number=entry.token_number,
        display_name=_mask(patient.full_name if patient else None),
        status=entry.status,
        estimated_wait_minutes=entry.estimated_wait_minutes,
        is_priority=entry.priority_tier > 0,
    )


def get_board(db: Session, doctor_id: int) -> DisplayBoard:
    session = require_session(db, doctor_id)
    doctor = doctors_service.get_doctor(db, doctor_id)
    user = db.get(User, doctor.user_id)
    present = _doctor_present(db, doctor_id)
    entries = _entries(db, session.id)

    serving = next(
        (e for e in entries if e.status == QueueEntryStatus.IN_PROGRESS), None
    ) or next((e for e in entries if e.status == QueueEntryStatus.CALLED), None)

    upcoming = sorted(
        [
            e
            for e in entries
            if e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
        ],
        key=lambda e: e.position,
    )[:10]

    if not session.is_open:
        hi, en = "यह क्लिनिक बंद हो चुका है।", "This clinic has closed."
    elif present:
        hi, en = "डॉक्टर उपलब्ध हैं।", "Doctor is available."
    else:
        hi, en = (
            "डॉक्टर अभी नहीं पहुँचे हैं। कृपया प्रतीक्षा करें।",
            "Doctor has not arrived yet. Please wait.",
        )

    return DisplayBoard(
        doctor_id=doctor_id,
        doctor_name=user.full_name if user else None,
        room=session.room,
        doctor_present=present,
        status_line_hi=hi,
        status_line_en=en,
        now_serving=serving.token_number if serving else None,
        next_tokens=[_board_row(db, e) for e in upcoming],
        updated_at=utcnow(),
    )


def my_position(db: Session, patient_id: int, doctor_id: int) -> MyPosition:
    session = require_session(db, doctor_id)
    entry = db.execute(
        select(QueueEntry).where(
            QueueEntry.session_id == session.id, QueueEntry.patient_id == patient_id
        )
    ).scalar_one_or_none()
    if entry is None:
        raise NotFoundError("You are not in this queue")

    present = _doctor_present(db, doctor_id)
    ahead = sum(
        1
        for e in open_entries(db, session.id)
        if e.position < entry.position
        and e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED, QueueEntryStatus.CALLED)
    )

    wait = entry.estimated_wait_minutes
    call_time = (
        utcnow() + timedelta(minutes=wait)
        if wait is not None and entry.status == QueueEntryStatus.WAITING
        else None
    )

    if entry.status == QueueEntryStatus.CALLED:
        hi = f"आपका नंबर आ गया है। कृपया कमरा {session.room} पर जाएँ।"
        en = f"You are being called. Please go to room {session.room}."
    elif entry.status == QueueEntryStatus.IN_PROGRESS:
        hi, en = "आपकी जाँच चल रही है।", "Your consultation is in progress."
    elif entry.status == QueueEntryStatus.COMPLETED:
        hi, en = "आपकी जाँच पूरी हो चुकी है।", "Your consultation is complete."
    elif not present:
        hi = "डॉक्टर अभी नहीं पहुँचे हैं। पहुँचते ही आपको सूचित किया जाएगा।"
        en = "The doctor has not arrived yet. You will be told as soon as they do."
    elif wait is None:
        hi, en = "प्रतीक्षा समय की गणना की जा रही है।", "Calculating your waiting time."
    else:
        hi = f"आपकी बारी लगभग {wait} मिनट में है। आपसे {ahead} लोग आगे हैं।"
        en = f"Your turn is in about {wait} minutes. {ahead} people are ahead of you."

    return MyPosition(
        token_number=entry.token_number,
        position=entry.position,
        people_ahead=ahead,
        status=entry.status,
        estimated_wait_minutes=wait,
        estimated_call_time=call_time,
        doctor_present=present,
        message_hi=hi,
        message_en=en,
    )
