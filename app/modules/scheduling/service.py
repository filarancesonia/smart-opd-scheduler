"""Room 4 glue: assemble features, run the models, optimise the session."""

from __future__ import annotations

import json
from datetime import date, datetime, time

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import ConflictError, NotFoundError
from app.core.timeutil import as_utc, local_now, local_today
from app.modules.booking.models import Appointment, AppointmentStatus, Patient
from app.modules.doctors import service as doctors_service
from app.modules.identity.models import User
from app.modules.scheduling import optimizer, predictors
from app.modules.scheduling.models import ConsultationRecord, SchedulePlan
from app.modules.scheduling.optimizer import PlanItem
from app.modules.scheduling.predictors import PredictionContext
from app.modules.scheduling.schemas import (
    AppointmentPrediction,
    AssignmentOut,
    EngineStatus,
    OptimisationOut,
    PredictionOut,
)

#: Statuses that still need a place in today's plan.
PLANNABLE = (AppointmentStatus.BOOKED, AppointmentStatus.CHECKED_IN)


# --- feature assembly ------------------------------------------------------


def _history(db: Session, patient_id: int, before: date) -> tuple[int, int]:
    """Prior visits and prior no-shows, counted strictly before ``before``.

    Bounded by date so a model trained on history never sees an outcome that
    had not happened yet at booking time.
    """
    visits = db.execute(
        select(func.count(ConsultationRecord.id)).where(
            ConsultationRecord.patient_id == patient_id,
            ConsultationRecord.record_date < before,
        )
    ).scalar_one()
    no_shows = db.execute(
        select(func.count(ConsultationRecord.id)).where(
            ConsultationRecord.patient_id == patient_id,
            ConsultationRecord.record_date < before,
            ConsultationRecord.was_no_show.is_(True),
        )
    ).scalar_one()
    return int(visits), int(no_shows)


def priority_tier_for(patient: Patient | None) -> int:
    """Baseline vulnerability tier.

    Room 7 owns emergency triage and overrides this with higher tiers; what
    remains here is the standing entitlement of people who should not be left
    standing in a corridor.
    """
    if patient is None:
        return 0
    if patient.is_pregnant or patient.has_disability or patient.is_senior_citizen:
        return 1
    return 0


def build_context(
    db: Session, appointment: Appointment, *, slot_index: int = 0
) -> PredictionContext:
    patient = db.get(Patient, appointment.patient_id)
    doctor = doctors_service.get_doctor(db, appointment.doctor_id)
    prior_visits, prior_no_shows = _history(
        db, appointment.patient_id, appointment.appointment_date
    )
    lead_time = (appointment.appointment_date - appointment.created_at.date()).days

    return PredictionContext(
        doctor_avg_minutes=doctor.avg_consultation_minutes,
        patient_age=patient.age if patient else None,
        is_follow_up=appointment.is_follow_up,
        lead_time_days=max(lead_time, 0),
        prior_visits=prior_visits,
        prior_no_shows=prior_no_shows,
        day_of_week=appointment.appointment_date.weekday(),
        hour_of_day=appointment.slot_start.hour,
        slot_index=slot_index,
        is_senior=bool(patient and patient.is_senior_citizen),
        has_priority_flag=priority_tier_for(patient) > 0,
        channel=appointment.channel,
    )


def predict_for_appointment(
    db: Session, appointment_id: int
) -> AppointmentPrediction:
    appointment = db.get(Appointment, appointment_id)
    if appointment is None:
        raise NotFoundError("Appointment not found")
    patient = db.get(Patient, appointment.patient_id)
    ctx = build_context(db, appointment)

    duration = predictors.duration_predictor.predict(ctx)
    no_show = predictors.no_show_predictor.predict(ctx)
    return AppointmentPrediction(
        appointment_id=appointment.id,
        patient_name=patient.full_name if patient else "",
        predicted_duration_minutes=PredictionOut(
            value=round(duration.value, 1), source=duration.source, detail=duration.detail
        ),
        no_show_probability=PredictionOut(
            value=round(no_show.value, 3), source=no_show.source, detail=no_show.detail
        ),
    )


# --- session window --------------------------------------------------------


def session_window(
    db: Session, doctor_id: int, on_date: date
) -> tuple[time, time, bool]:
    """When can this doctor actually see patients today?

    The printed roster says 09:00. Room 1 may know they walked in at 11:14.
    Planning against the roster when the truth is available is exactly the
    failure this project exists to fix.
    """
    availability = doctors_service.get_day_availability(db, doctor_id, on_date)
    if availability.is_on_leave:
        raise ConflictError("The doctor is on approved leave on this date")
    if not availability.windows:
        raise ConflictError("The doctor has no clinic scheduled on this date")

    roster_from = availability.windows[0].start_time
    roster_until = availability.windows[-1].end_time
    used_live = False

    if on_date == local_today():
        from app.modules.presence import service as presence_service
        from app.modules.presence.models import PresenceStatus

        presence = presence_service.get_presence(db, doctor_id)
        now_clock = local_now().time()
        # Nothing can be scheduled into the past.
        start = max(roster_from, now_clock) if now_clock > roster_from else roster_from
        if presence.status == PresenceStatus.PRESENT:
            used_live = True
        return start, roster_until, used_live

    return roster_from, roster_until, used_live


# --- optimisation ----------------------------------------------------------


def _plan_items(db: Session, doctor_id: int, on_date: date) -> list[PlanItem]:
    appointments = list(
        db.execute(
            select(Appointment)
            .where(
                Appointment.doctor_id == doctor_id,
                Appointment.appointment_date == on_date,
                Appointment.status.in_([str(s) for s in PLANNABLE]),
            )
            .order_by(Appointment.slot_start)
        ).scalars()
    )

    items: list[PlanItem] = []
    for index, appointment in enumerate(appointments):
        patient = db.get(Patient, appointment.patient_id)
        ctx = build_context(db, appointment, slot_index=index)
        duration = predictors.duration_predictor.predict(ctx)
        no_show = predictors.no_show_predictor.predict(ctx)

        items.append(
            PlanItem(
                appointment_id=appointment.id,
                patient_id=appointment.patient_id,
                patient_name=patient.full_name if patient else "",
                booked_start=appointment.slot_start,
                expected_duration=duration.value,
                no_show_probability=no_show.value,
                priority_tier=priority_tier_for(patient),
                duration_source=duration.source,
                no_show_source=no_show.source,
            )
        )
    return items


def optimise_day(
    db: Session,
    doctor_id: int,
    on_date: date | None = None,
    *,
    available_from: time | None = None,
    available_until: time | None = None,
    save: bool = False,
) -> OptimisationOut:
    doctor = doctors_service.get_doctor(db, doctor_id)
    plan_date = on_date or local_today()

    window_from, window_until, used_live = session_window(db, doctor_id, plan_date)
    if available_from is not None:
        window_from, used_live = available_from, False
    if available_until is not None:
        window_until = available_until

    items = _plan_items(db, doctor_id, plan_date)
    result = optimizer.optimise(
        items,
        doctor_id=doctor_id,
        available_from=window_from,
        available_until=window_until,
    )

    user = db.get(User, doctor.user_id)
    out = OptimisationOut(
        doctor_id=doctor_id,
        doctor_name=user.full_name if user else None,
        plan_date=plan_date,
        available_from=result.available_from,
        available_until=result.available_until,
        session_minutes=result.session_minutes,
        assignments=[AssignmentOut(**vars(a)) for a in result.assignments],
        total_expected_wait=result.total_expected_wait,
        average_wait=result.average_wait,
        baseline_wait=result.baseline_wait,
        baseline_average_wait=result.baseline_average_wait,
        improvement_pct=result.improvement_pct,
        expected_no_shows=result.expected_no_shows,
        recommended_overbooking=result.recommended_overbooking,
        projected_overrun_minutes=result.projected_overrun_minutes,
        notes=result.notes,
        used_live_presence=used_live,
        engine=predictors.status(),
    )

    if save:
        db.add(
            SchedulePlan(
                doctor_id=doctor_id,
                plan_date=plan_date,
                generated_at=utcnow(),
                available_from=result.available_from,
                available_until=result.available_until,
                total_expected_wait=result.total_expected_wait,
                baseline_wait=result.baseline_wait,
                improvement_pct=result.improvement_pct,
                assignments=json.dumps(
                    [
                        {
                            "appointment_id": a.appointment_id,
                            "position": a.position,
                            "predicted_start": a.predicted_start.isoformat(),
                        }
                        for a in result.assignments
                    ]
                ),
            )
        )
        db.commit()

    return out


def list_plans(db: Session, doctor_id: int, limit: int = 20) -> list[SchedulePlan]:
    return list(
        db.execute(
            select(SchedulePlan)
            .where(SchedulePlan.doctor_id == doctor_id)
            .order_by(SchedulePlan.generated_at.desc())
            .limit(limit)
        ).scalars()
    )


# --- learning from outcomes ------------------------------------------------


def record_consultation(
    db: Session,
    appointment: Appointment,
    *,
    actual_start: datetime | None = None,
    actual_end: datetime | None = None,
    was_no_show: bool = False,
    slot_index: int = 0,
) -> ConsultationRecord:
    """Turn a finished appointment into a training example.

    Room 5 calls this when a consultation ends or a patient is marked absent;
    this is the feedback loop that lets both models improve on real data.
    """
    existing = db.execute(
        select(ConsultationRecord).where(
            ConsultationRecord.appointment_id == appointment.id
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    ctx = build_context(db, appointment, slot_index=slot_index)

    # SQLite hands back naive datetimes even for timezone-aware columns, so
    # anything that came out of the database is normalised before arithmetic.
    actual_start = as_utc(actual_start) if actual_start else None
    actual_end = as_utc(actual_end) if actual_end else None

    duration = None
    if actual_start and actual_end and not was_no_show:
        duration = max(int((actual_end - actual_start).total_seconds() // 60), 1)

    record = ConsultationRecord(
        appointment_id=appointment.id,
        doctor_id=appointment.doctor_id,
        patient_id=appointment.patient_id,
        department_id=appointment.department_id,
        record_date=appointment.appointment_date,
        scheduled_start=appointment.slot_start,
        actual_start=actual_start,
        actual_end=actual_end,
        duration_minutes=duration,
        was_no_show=was_no_show,
        patient_age=ctx.patient_age,
        is_follow_up=ctx.is_follow_up,
        channel=ctx.channel,
        lead_time_days=ctx.lead_time_days,
        prior_visits=ctx.prior_visits,
        prior_no_shows=ctx.prior_no_shows,
        day_of_week=ctx.day_of_week,
        hour_of_day=ctx.hour_of_day,
        slot_index=slot_index,
        is_senior=ctx.is_senior,
        has_priority_flag=ctx.has_priority_flag,
        is_synthetic=False,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


def engine_status(db: Session) -> EngineStatus:
    total = db.execute(select(func.count(ConsultationRecord.id))).scalar_one()
    synthetic = db.execute(
        select(func.count(ConsultationRecord.id)).where(
            ConsultationRecord.is_synthetic.is_(True)
        )
    ).scalar_one()
    state = predictors.status()
    return EngineStatus(
        duration=state["duration"],
        no_show=state["no_show"],
        training_rows=int(total),
        synthetic_rows=int(synthetic),
    )
