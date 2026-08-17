"""Room 1 fusion logic.

Readers are noisy and unreliable: they fire duplicates, they buffer while the
network is down and flush minutes late, and a Bluetooth beacon can sit in a
drawer all day. The rules below turn that stream into one trustworthy answer.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.timeutil import as_utc, combine_local, minutes_between, to_local
from app.modules.doctors import service as doctors_service
from app.modules.doctors.models import CredentialType, Department, Doctor
from app.modules.identity.models import User
from app.modules.presence.models import (
    Device,
    Direction,
    PresenceEvent,
    PresenceSignal,
    PresenceState,
    PresenceStatus,
    RosterDeviation,
)
from app.modules.presence.schemas import (
    DeviceCreate,
    ManualPresence,
    PresenceOut,
    SignalIn,
    SignalResult,
)

#: How much each kind of reader is trusted when it has no opinion of its own.
#: BLE is deliberately low — a phone in a drawer still advertises.
DEFAULT_CONFIDENCE: dict[str, float] = {
    CredentialType.RFID: 0.95,
    CredentialType.FACE: 0.90,
    CredentialType.BLE: 0.60,
    CredentialType.MANUAL: 1.00,
}

#: Below this, a signal is recorded for audit but does not move the state.
MIN_ACTIONABLE_CONFIDENCE = 0.50

#: Repeat sightings inside this window are treated as one continuous presence
#: rather than a leave-and-return, so `since` does not reset on every ping.
DEBOUNCE_SECONDS = 90


# --- devices ---------------------------------------------------------------


def register_device(db: Session, payload: DeviceCreate) -> Device:
    existing = db.execute(
        select(Device).where(Device.device_uid == payload.device_uid)
    ).scalar_one_or_none()
    if existing:
        raise ConflictError("A device with this uid is already registered")
    if payload.department_id is not None:
        doctors_service.get_department(db, payload.department_id)

    device = Device(
        device_uid=payload.device_uid,
        device_type=str(payload.device_type),
        room=payload.room,
        department_id=payload.department_id,
        location_note=payload.location_note,
    )
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def list_devices(db: Session, *, active_only: bool = True) -> list[Device]:
    stmt = select(Device).order_by(Device.room, Device.device_uid)
    if active_only:
        stmt = stmt.where(Device.is_active.is_(True))
    return list(db.execute(stmt).scalars())


def deactivate_device(db: Session, device_id: int) -> Device:
    device = db.get(Device, device_id)
    if device is None:
        raise NotFoundError("Device not found")
    device.is_active = False
    db.commit()
    db.refresh(device)
    return device


# --- state helpers ---------------------------------------------------------


def _get_or_create_state(db: Session, doctor_id: int) -> PresenceState:
    state = db.execute(
        select(PresenceState).where(PresenceState.doctor_id == doctor_id)
    ).scalar_one_or_none()
    if state is None:
        state = PresenceState(doctor_id=doctor_id, status=PresenceStatus.UNKNOWN)
        db.add(state)
        db.flush()
    return state


def _log_event(
    db: Session,
    doctor_id: int,
    from_status: str,
    to_status: str,
    room: str | None,
    occurred_at: datetime,
    source: str,
    note: str = "",
) -> None:
    db.add(
        PresenceEvent(
            doctor_id=doctor_id,
            from_status=from_status,
            to_status=to_status,
            room=room,
            occurred_at=occurred_at,
            source=source,
            note=note,
        )
    )


def _is_stale(state: PresenceState, now: datetime) -> bool:
    if state.last_signal_at is None:
        return True
    age = (as_utc(now) - as_utc(state.last_signal_at)).total_seconds()
    return age > settings.presence_ttl_seconds


# --- ingest ----------------------------------------------------------------


def ingest_signal(db: Session, payload: SignalIn) -> SignalResult:
    """Record one observation and update the doctor's fused state."""
    device = db.execute(
        select(Device).where(Device.device_uid == payload.device_uid)
    ).scalar_one_or_none()
    if device is None:
        raise NotFoundError(f"Unknown device '{payload.device_uid}'")
    if not device.is_active:
        raise ValidationError("This device has been decommissioned")

    observed_at = as_utc(payload.observed_at or utcnow())
    if observed_at > as_utc(utcnow()) + timedelta(minutes=5):
        # A reader with a badly-set clock must not park presence in the future.
        raise ValidationError("observed_at is too far in the future")

    confidence = payload.confidence
    if confidence is None:
        confidence = DEFAULT_CONFIDENCE.get(str(payload.credential_type), 0.5)

    doctor = doctors_service.resolve_credential(
        db, str(payload.credential_type), payload.raw_value
    )

    signal = PresenceSignal(
        device_id=device.id,
        doctor_id=doctor.id if doctor else None,
        credential_type=str(payload.credential_type),
        direction=str(payload.direction),
        room=device.room,
        observed_at=observed_at,
        confidence=confidence,
        matched=doctor is not None,
    )
    db.add(signal)
    device.last_seen_at = observed_at

    if doctor is None:
        db.commit()
        # Kept, not discarded: an unrecognised tag at a door is a security signal.
        return SignalResult(
            accepted=True, matched=False, reason="No doctor matches this credential"
        )

    if confidence < MIN_ACTIONABLE_CONFIDENCE:
        db.commit()
        return SignalResult(
            accepted=True,
            matched=True,
            doctor_id=doctor.id,
            reason="Confidence too low to change presence state",
        )

    state = _apply_to_state(
        db,
        doctor_id=doctor.id,
        direction=str(payload.direction),
        room=device.room,
        observed_at=observed_at,
        confidence=confidence,
        credential_type=str(payload.credential_type),
        source="device",
    )
    db.commit()
    db.refresh(state)
    return SignalResult(
        accepted=True,
        matched=True,
        doctor_id=doctor.id,
        status=state.status,
        room=state.room,
    )


def _apply_to_state(
    db: Session,
    *,
    doctor_id: int,
    direction: str,
    room: str | None,
    observed_at: datetime,
    confidence: float,
    credential_type: str,
    source: str,
    note: str = "",
) -> PresenceState:
    state = _get_or_create_state(db, doctor_id)

    # Readers buffer while offline and flush late. A stale observation must not
    # overwrite a more recent one.
    if state.last_signal_at is not None and observed_at < as_utc(state.last_signal_at):
        return state

    previous_status = state.status
    previous_room = state.room

    if direction == Direction.OUT:
        new_status = PresenceStatus.ABSENT
        new_room = None
    else:
        new_status = PresenceStatus.PRESENT
        new_room = room

    # `since` marks the start of an uninterrupted stretch of presence. It is
    # preserved across repeat pings and room moves, and reset on a real return.
    if new_status == PresenceStatus.PRESENT:
        gap_ok = (
            state.last_signal_at is not None
            and (observed_at - as_utc(state.last_signal_at)).total_seconds()
            <= max(settings.presence_ttl_seconds, DEBOUNCE_SECONDS)
        )
        if previous_status != PresenceStatus.PRESENT or not gap_ok:
            state.since = observed_at
    else:
        state.since = None

    state.status = str(new_status)
    state.room = new_room
    state.last_signal_at = observed_at
    state.last_credential_type = credential_type
    state.confidence = confidence

    if previous_status != str(new_status):
        _log_event(
            db, doctor_id, previous_status, str(new_status), new_room, observed_at,
            source, note,
        )
    elif new_status == PresenceStatus.PRESENT and previous_room != new_room:
        _log_event(
            db, doctor_id, previous_status, str(new_status), new_room, observed_at,
            source, f"Moved from {previous_room or 'unknown'} to {new_room}",
        )

    return state


def set_manual_presence(
    db: Session, payload: ManualPresence, actor_user_id: int
) -> PresenceState:
    """Reception override for when the hardware is down."""
    doctors_service.get_doctor(db, payload.doctor_id)
    now = utcnow()

    direction = (
        Direction.OUT if payload.status == PresenceStatus.ABSENT else Direction.IN
    )
    state = _apply_to_state(
        db,
        doctor_id=payload.doctor_id,
        direction=str(direction),
        room=payload.room,
        observed_at=now,
        confidence=DEFAULT_CONFIDENCE[CredentialType.MANUAL],
        credential_type=str(CredentialType.MANUAL),
        source="manual",
        note=payload.note or f"Set manually by user {actor_user_id}",
    )
    # ON_BREAK has no device equivalent, so it is applied after the fusion step.
    if payload.status == PresenceStatus.ON_BREAK:
        state.status = str(PresenceStatus.ON_BREAK)

    db.commit()
    db.refresh(state)
    return state


def sweep_stale(db: Session) -> int:
    """Demote presences whose last signal has aged out. Run on a schedule."""
    now = utcnow()
    demoted = 0
    states = db.execute(
        select(PresenceState).where(PresenceState.status == PresenceStatus.PRESENT)
    ).scalars()
    for state in states:
        if _is_stale(state, now):
            _log_event(
                db, state.doctor_id, state.status, str(PresenceStatus.STALE),
                state.room, now, "sweep", "No signal within the trust window",
            )
            state.status = str(PresenceStatus.STALE)
            state.since = None
            demoted += 1
    db.commit()
    return demoted


# --- reading presence ------------------------------------------------------


def _deviation(
    db: Session, doctor_id: int, state: PresenceState, effective_status: str, now: datetime
) -> dict:
    """Compare the observed state against the Room 2 roster."""
    local_now_dt = to_local(now)
    availability = doctors_service.get_day_availability(
        db, doctor_id, local_now_dt.date()
    )
    result: dict = {
        "deviation": None,
        "expected_room": None,
        "expected_until": None,
        "minutes_late": None,
    }

    if availability.is_on_leave:
        result["deviation"] = RosterDeviation.ON_APPROVED_LEAVE
        return result

    window = next(
        (
            w
            for w in availability.windows
            if w.start_time <= local_now_dt.time() < w.end_time
        ),
        None,
    )

    if window is None:
        result["deviation"] = (
            RosterDeviation.PRESENT_OFF_ROSTER
            if effective_status == PresenceStatus.PRESENT
            else RosterDeviation.NOT_ROSTERED
        )
        return result

    window_start = combine_local(local_now_dt.date(), window.start_time)
    result["expected_room"] = window.room
    result["expected_until"] = combine_local(local_now_dt.date(), window.end_time)

    if effective_status != PresenceStatus.PRESENT:
        result["deviation"] = RosterDeviation.ABSENT_WHILE_ROSTERED
        result["minutes_late"] = max(minutes_between(window_start, now), 0)
        return result

    if state.since is not None:
        late = minutes_between(window_start, state.since)
        result["minutes_late"] = max(late, 0)

    result["deviation"] = (
        RosterDeviation.ON_DUTY_AS_ROSTERED
        if state.room == window.room
        else RosterDeviation.WRONG_ROOM
    )
    return result


def get_presence(db: Session, doctor_id: int) -> PresenceOut:
    doctor = doctors_service.get_doctor(db, doctor_id)
    state = db.execute(
        select(PresenceState).where(PresenceState.doctor_id == doctor_id)
    ).scalar_one_or_none()
    now = utcnow()

    if state is None:
        # Never observed. Column defaults only apply on INSERT, so this
        # transient stand-in has to spell them out.
        state = PresenceState(
            doctor_id=doctor_id,
            status=str(PresenceStatus.UNKNOWN),
            room=None,
            since=None,
            last_signal_at=None,
            last_credential_type=None,
            confidence=0.0,
        )

    # Staleness is judged at read time so an answer is never silently outdated,
    # even if the sweep job has not run recently.
    effective = state.status
    if state.status == PresenceStatus.PRESENT and _is_stale(state, now):
        effective = str(PresenceStatus.STALE)

    present_minutes = (
        minutes_between(state.since, now)
        if state.since is not None and effective == PresenceStatus.PRESENT
        else None
    )

    user = db.get(User, doctor.user_id)
    department = db.get(Department, doctor.department_id)

    return PresenceOut(
        doctor_id=doctor_id,
        doctor_name=user.full_name if user else None,
        department_name=department.name if department else None,
        status=effective,
        room=state.room,
        since=state.since,
        last_signal_at=state.last_signal_at,
        last_credential_type=state.last_credential_type,
        confidence=state.confidence,
        present_minutes=present_minutes,
        **_deviation(db, doctor_id, state, effective, now),
    )


def list_presence(
    db: Session,
    *,
    department_id: int | None = None,
    status: PresenceStatus | None = None,
) -> list[PresenceOut]:
    doctors = doctors_service.list_doctors(db, department_id=department_id)
    rows = [get_presence(db, d.id) for d in doctors]
    if status is not None:
        rows = [r for r in rows if r.status == status]
    return rows


def list_events(
    db: Session, doctor_id: int, *, limit: int = 100
) -> list[PresenceEvent]:
    return list(
        db.execute(
            select(PresenceEvent)
            .where(PresenceEvent.doctor_id == doctor_id)
            .order_by(PresenceEvent.occurred_at.desc())
            .limit(limit)
        ).scalars()
    )


def recent_unmatched(db: Session, *, limit: int = 50) -> list[PresenceSignal]:
    """Unrecognised credentials seen at doors — reviewed by security."""
    return list(
        db.execute(
            select(PresenceSignal)
            .where(PresenceSignal.matched.is_(False))
            .order_by(PresenceSignal.observed_at.desc())
            .limit(limit)
        ).scalars()
    )


def is_doctor_present(db: Session, doctor_id: int) -> bool:
    """Convenience used by Rooms 4 and 5 before opening a queue."""
    return get_presence(db, doctor_id).status == PresenceStatus.PRESENT
