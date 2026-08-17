"""Room 7 logic: triage, safe overrides, and anti-starvation escalation."""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.timeutil import as_utc, local_today, minutes_between
from app.modules.booking import service as booking_service
from app.modules.booking.models import Patient
from app.modules.booking.schemas import AppointmentCreate
from app.modules.doctors import service as doctors_service
from app.modules.emergency.models import (
    TRIAGE_TIERS,
    CaseStatus,
    EmergencyCase,
    OverrideSource,
    PriorityOverride,
    PriorityTier,
    TriageLevel,
)
from app.modules.emergency.schemas import (
    AgingResult,
    TriageRequest,
    VulnerabilityAssessment,
)

#: A routine patient waiting longer than this is escalated one tier.
AGING_THRESHOLD_MINUTES = 45

#: Automatic escalation never reaches EMERGENCY — that is a clinical judgement,
#: not something waiting time is allowed to confer.
MAX_AGING_TIER = PriorityTier.URGENT


# --- vulnerability ---------------------------------------------------------


def assess_vulnerability(patient: Patient | None) -> VulnerabilityAssessment:
    """The standing entitlement of people who should not be left standing.

    This is Room 7's canonical rule; Room 4 and Room 5 both defer to it.
    """
    if patient is None:
        return VulnerabilityAssessment(patient_id=0, tier=PriorityTier.ROUTINE)

    reasons: list[str] = []
    if patient.is_senior_citizen:
        reasons.append(f"Senior citizen (age {patient.age})")
    if patient.is_pregnant:
        reasons.append("Pregnant")
    if patient.has_disability:
        reasons.append("Disability")

    return VulnerabilityAssessment(
        patient_id=patient.id,
        tier=PriorityTier.VULNERABLE if reasons else PriorityTier.ROUTINE,
        reasons=reasons,
    )


def priority_tier_for(patient: Patient | None) -> int:
    return assess_vulnerability(patient).tier


# --- audit -----------------------------------------------------------------


def _log_override(
    db: Session,
    *,
    from_tier: int,
    to_tier: int,
    source: OverrideSource,
    reason: str,
    actor_user_id: int | None,
    queue_entry_id: int | None = None,
    appointment_id: int | None = None,
    patient_id: int | None = None,
    displaced_count: int = 0,
) -> PriorityOverride:
    override = PriorityOverride(
        queue_entry_id=queue_entry_id,
        appointment_id=appointment_id,
        patient_id=patient_id,
        from_tier=int(from_tier),
        to_tier=int(to_tier),
        source=str(source),
        reason=reason,
        actor_user_id=actor_user_id,
        occurred_at=utcnow(),
        displaced_count=displaced_count,
    )
    db.add(override)
    return override


def list_overrides(
    db: Session, *, queue_entry_id: int | None = None, limit: int = 200
) -> list[PriorityOverride]:
    stmt = select(PriorityOverride).order_by(PriorityOverride.occurred_at.desc())
    if queue_entry_id is not None:
        stmt = stmt.where(PriorityOverride.queue_entry_id == queue_entry_id)
    return list(db.execute(stmt.limit(limit)).scalars())


# --- triage ----------------------------------------------------------------


def _pick_doctor(db: Session, department_id: int, doctor_id: int | None):
    if doctor_id is not None:
        return doctors_service.get_doctor(db, doctor_id)

    candidates = doctors_service.list_doctors(db, department_id=department_id)
    if not candidates:
        raise ConflictError("No doctor is registered in that department")

    # Prefer someone Room 1 can confirm is physically in the building.
    from app.modules.presence import service as presence_service

    present = [d for d in candidates if presence_service.is_doctor_present(db, d.id)]
    return (present or candidates)[0]


def triage(db: Session, payload: TriageRequest, actor_user_id: int) -> EmergencyCase:
    """Register an emergency and insert it into the right queue immediately."""
    doctors_service.get_department(db, payload.department_id)

    if payload.patient_id is not None:
        patient = booking_service.get_patient(db, payload.patient_id)
    elif payload.patient is not None:
        existing = booking_service.find_patients_by_phone(db, payload.patient.phone)
        match = next(
            (
                p
                for p in existing
                if p.full_name.lower() == payload.patient.full_name.lower()
            ),
            None,
        )
        patient = match or booking_service.create_patient(db, payload.patient)
    else:
        raise ValidationError("Provide either patient_id or a patient record")

    doctor = _pick_doctor(db, payload.department_id, payload.doctor_id)
    tier = TRIAGE_TIERS[payload.triage_level]

    # An emergency is not subject to the day's booking capacity. Someone
    # bleeding in the corridor does not become less urgent because the clinic
    # is notionally full.
    appointment = booking_service.book(
        db,
        patient_id=patient.id,
        payload=AppointmentCreate(
            doctor_id=doctor.id,
            appointment_date=local_today(),
            reason=f"[{payload.triage_level.upper()}] {payload.complaint}",
        ),
        channel="staff",
        booked_by_user_id=actor_user_id,
        bypass_capacity=True,
    )

    case = EmergencyCase(
        patient_id=patient.id,
        department_id=payload.department_id,
        doctor_id=doctor.id,
        appointment_id=appointment.id,
        triage_level=str(payload.triage_level),
        complaint=payload.complaint,
        status=str(CaseStatus.ACTIVE),
        arrived_at=utcnow(),
        triaged_by_user_id=actor_user_id,
    )
    db.add(case)
    db.commit()
    db.refresh(case)

    displaced = _insert_into_queue(db, case, appointment, tier, actor_user_id)
    _log_override(
        db,
        from_tier=PriorityTier.ROUTINE,
        to_tier=tier,
        source=OverrideSource.TRIAGE,
        reason=f"{payload.triage_level.upper()}: {payload.complaint}",
        actor_user_id=actor_user_id,
        queue_entry_id=case.queue_entry_id,
        appointment_id=appointment.id,
        patient_id=patient.id,
        displaced_count=displaced,
    )
    db.commit()
    db.refresh(case)
    return case


def _insert_into_queue(
    db: Session, case: EmergencyCase, appointment, tier: int, actor_user_id: int
) -> int:
    """Put the case in the live queue if one is running. Returns people displaced."""
    from app.modules.queue import service as queue_service
    from app.modules.queue.models import QueueEntryStatus

    session = queue_service.get_session(db, appointment.doctor_id)
    if session is None or not session.is_open:
        # No queue open yet; the appointment alone carries the priority when
        # one is opened later.
        return 0

    before = [
        e
        for e in queue_service.open_entries(db, session.id)
        if e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
        and e.priority_tier < tier
    ]

    entry = queue_service.join(db, appointment.id)
    entry.priority_tier = int(tier)
    case.queue_entry_id = entry.id
    db.commit()

    queue_service.reorder(db, session)
    return len(before)


def resolve(
    db: Session, case_id: int, status: CaseStatus, outcome: str = ""
) -> EmergencyCase:
    case = db.get(EmergencyCase, case_id)
    if case is None:
        raise NotFoundError("Emergency case not found")
    if case.status != CaseStatus.ACTIVE:
        raise ConflictError(f"This case is already {case.status}")

    case.status = str(status)
    case.outcome = outcome
    case.resolved_at = utcnow()
    db.commit()
    db.refresh(case)
    return case


def get_case(db: Session, case_id: int) -> EmergencyCase:
    case = db.get(EmergencyCase, case_id)
    if case is None:
        raise NotFoundError("Emergency case not found")
    return case


def list_cases(
    db: Session, *, status: CaseStatus | None = None, department_id: int | None = None
) -> list[EmergencyCase]:
    stmt = select(EmergencyCase).order_by(EmergencyCase.arrived_at.desc())
    if status is not None:
        stmt = stmt.where(EmergencyCase.status == str(status))
    if department_id is not None:
        stmt = stmt.where(EmergencyCase.department_id == department_id)
    return list(db.execute(stmt).scalars())


def decorate(db: Session, case: EmergencyCase) -> dict:
    from app.modules.queue.models import QueueEntry

    patient = db.get(Patient, case.patient_id)
    entry = db.get(QueueEntry, case.queue_entry_id) if case.queue_entry_id else None
    override = db.execute(
        select(PriorityOverride)
        .where(PriorityOverride.appointment_id == case.appointment_id)
        .order_by(PriorityOverride.occurred_at)
    ).scalars().first()

    return {
        **{c.name: getattr(case, c.name) for c in case.__table__.columns},
        "patient_name": patient.full_name if patient else None,
        "priority_tier": int(TRIAGE_TIERS[case.triage_level]),
        "token_number": entry.token_number if entry else None,
        "displaced_count": override.displaced_count if override else 0,
    }


# --- manual priority change ------------------------------------------------


def set_entry_priority(
    db: Session, entry_id: int, tier: PriorityTier, reason: str, actor_user_id: int
):
    """Change a waiting patient's tier — e.g. someone collapses in the corridor."""
    from app.modules.queue import service as queue_service
    from app.modules.queue.models import QueueEntryStatus, QueueSession

    entry = queue_service.get_entry(db, entry_id)
    if entry.status not in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED):
        raise ConflictError(
            f"Priority can only be changed while waiting, not once {entry.status}"
        )

    previous = entry.priority_tier
    if previous == int(tier):
        return entry

    session = db.get(QueueSession, entry.session_id)
    displaced = (
        len(
            [
                e
                for e in queue_service.open_entries(db, session.id)
                if e.id != entry.id
                and e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
                and previous < e.priority_tier <= int(tier)
            ]
        )
        if int(tier) > previous
        else 0
    )

    entry.priority_tier = int(tier)
    db.commit()

    _log_override(
        db,
        from_tier=previous,
        to_tier=int(tier),
        source=OverrideSource.MANUAL,
        reason=reason,
        actor_user_id=actor_user_id,
        queue_entry_id=entry.id,
        appointment_id=entry.appointment_id,
        patient_id=entry.patient_id,
        displaced_count=displaced,
    )
    db.commit()

    queue_service.reorder(db, session)
    db.refresh(entry)
    return entry


# --- anti-starvation -------------------------------------------------------


def apply_aging(db: Session, session_id: int) -> AgingResult:
    """Escalate anyone who has been displaced for too long.

    Without this, a morning of emergencies leaves the same routine patients on
    the bench until the clinic closes — the override is safe for the emergency
    and quietly unfair to everyone else.
    """
    from app.modules.queue import service as queue_service
    from app.modules.queue.models import QueueEntryStatus

    now = utcnow()
    entries = [
        e
        for e in queue_service.open_entries(db, session_id)
        if e.status in (QueueEntryStatus.WAITING, QueueEntryStatus.SKIPPED)
    ]

    escalated = 0
    details: list[str] = []
    for entry in entries:
        if entry.priority_tier >= MAX_AGING_TIER:
            continue
        waited = minutes_between(as_utc(entry.joined_at), now)
        if waited < AGING_THRESHOLD_MINUTES:
            continue

        previous = entry.priority_tier
        entry.priority_tier = min(previous + 1, int(MAX_AGING_TIER))
        _log_override(
            db,
            from_tier=previous,
            to_tier=entry.priority_tier,
            source=OverrideSource.AGING,
            reason=f"Waiting {waited} minutes, above the {AGING_THRESHOLD_MINUTES} minute threshold",
            actor_user_id=None,
            queue_entry_id=entry.id,
            appointment_id=entry.appointment_id,
            patient_id=entry.patient_id,
        )
        escalated += 1
        details.append(f"Token {entry.token_number} raised to tier {entry.priority_tier}")

    db.commit()
    return AgingResult(escalated=escalated, checked=len(entries), details=details)
