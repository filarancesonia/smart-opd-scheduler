"""Room 8 aggregation.

Everything here is read-only and derived. Percentiles are computed in Python
rather than SQL so the same code works on SQLite in a demo and on Postgres in
a district hospital.
"""

from __future__ import annotations

import statistics
from datetime import date, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.timeutil import as_utc, combine_local, local_today, minutes_between, to_local
from app.modules.booking.models import Appointment, AppointmentStatus, BookingChannel
from app.modules.doctors import service as doctors_service
from app.modules.doctors.models import Department, Doctor
from app.modules.emergency.models import CaseStatus, EmergencyCase
from app.modules.identity.models import User
from app.modules.notifications.models import Notification, NotificationStatus
from app.modules.presence import service as presence_service
from app.modules.presence.models import PresenceEvent, PresenceStatus, RosterDeviation
from app.modules.queue.models import QueueEntry, QueueSession
from app.modules.analytics.schemas import (
    AbsencePatternRow,
    AttendanceReport,
    AttendanceRow,
    ChannelMixRow,
    DepartmentLoadRow,
    HealthDeptSummary,
    LiveDoctorRow,
    LiveOverview,
    NotificationStatsRow,
    WaitTimeReport,
    WaitTimeRow,
)

WEEKDAY_NAMES = [
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
]

#: Above this, the Health Department summary raises a flag.
ALERT_NO_SHOW_RATE = 0.25
ALERT_AVG_WAIT_MINUTES = 45
ALERT_ATTENDANCE_RATE = 0.85


def _pct(part: int, whole: int) -> float:
    return round(part / whole * 100, 1) if whole else 0.0


def _rate(part: int, whole: int) -> float:
    return round(part / whole, 3) if whole else 0.0


# --- live overview ---------------------------------------------------------


def live_overview(db: Session, department_id: int | None = None) -> LiveOverview:
    doctors = doctors_service.list_doctors(db, department_id=department_id)
    today = local_today()

    rows: list[LiveDoctorRow] = []
    present = absent_rostered = on_leave = 0
    total_waiting = 0
    longest_overall: int | None = None

    for doctor in doctors:
        presence = presence_service.get_presence(db, doctor.id)
        department = db.get(Department, doctor.department_id)

        if presence.status == PresenceStatus.PRESENT:
            present += 1
        if presence.deviation == RosterDeviation.ABSENT_WHILE_ROSTERED:
            absent_rostered += 1
        if presence.deviation == RosterDeviation.ON_APPROVED_LEAVE:
            on_leave += 1

        waiting, longest, serving = _queue_snapshot(db, doctor.id, today)
        total_waiting += waiting
        if longest is not None:
            longest_overall = max(longest_overall or 0, longest)

        rows.append(
            LiveDoctorRow(
                doctor_id=doctor.id,
                doctor_name=presence.doctor_name,
                department_name=department.name if department else None,
                presence_status=presence.status,
                room=presence.room,
                deviation=presence.deviation,
                minutes_late=presence.minutes_late,
                waiting_count=waiting,
                longest_wait_minutes=longest,
                now_serving=serving,
            )
        )

    emergencies = db.execute(
        select(func.count(EmergencyCase.id)).where(
            EmergencyCase.status == CaseStatus.ACTIVE
        )
    ).scalar_one()

    return LiveOverview(
        generated_at=utcnow(),
        doctors_total=len(doctors),
        doctors_present=present,
        doctors_absent_while_rostered=absent_rostered,
        doctors_on_leave=on_leave,
        patients_waiting=total_waiting,
        longest_wait_minutes=longest_overall,
        active_emergencies=int(emergencies),
        doctors=rows,
    )


def _queue_snapshot(
    db: Session, doctor_id: int, on_date: date
) -> tuple[int, int | None, int | None]:
    from app.modules.queue.models import QueueEntryStatus

    session = db.execute(
        select(QueueSession).where(
            QueueSession.doctor_id == doctor_id, QueueSession.session_date == on_date
        )
    ).scalar_one_or_none()
    if session is None:
        return 0, None, None

    entries = list(
        db.execute(
            select(QueueEntry).where(QueueEntry.session_id == session.id)
        ).scalars()
    )
    waiting = [e for e in entries if e.status == QueueEntryStatus.WAITING]
    now = utcnow()
    longest = (
        max(minutes_between(as_utc(e.joined_at), now) for e in waiting)
        if waiting
        else None
    )
    serving = next(
        (
            e.token_number
            for e in entries
            if e.status in (QueueEntryStatus.IN_PROGRESS, QueueEntryStatus.CALLED)
        ),
        None,
    )
    return len(waiting), longest, serving


# --- attendance ------------------------------------------------------------


def _first_arrival(db: Session, doctor_id: int, on_date: date):
    """The first moment Room 1 saw this doctor on a given date."""
    events = list(
        db.execute(
            select(PresenceEvent)
            .where(
                PresenceEvent.doctor_id == doctor_id,
                PresenceEvent.to_status == PresenceStatus.PRESENT,
            )
            .order_by(PresenceEvent.occurred_at)
        ).scalars()
    )
    for event in events:
        if to_local(event.occurred_at).date() == on_date:
            return as_utc(event.occurred_at)
    return None


def attendance_report(
    db: Session,
    start_date: date,
    end_date: date,
    department_id: int | None = None,
) -> AttendanceReport:
    doctors = doctors_service.list_doctors(db, department_id=department_id)
    rows: list[AttendanceRow] = []

    for doctor in doctors:
        user = db.get(User, doctor.user_id)
        department = db.get(Department, doctor.department_id)

        rostered = present = leave = 0
        late_days = 0
        lateness: list[int] = []

        for day in _each_day(start_date, end_date):
            availability = doctors_service.get_day_availability(db, doctor.id, day)
            if availability.is_on_leave:
                leave += 1
                continue
            if not availability.windows:
                continue

            rostered += 1
            arrival = _first_arrival(db, doctor.id, day)
            if arrival is None:
                continue

            present += 1
            expected = combine_local(day, availability.windows[0].start_time)
            minutes = minutes_between(expected, arrival)
            if minutes > 0:
                late_days += 1
                lateness.append(minutes)

        rows.append(
            AttendanceRow(
                doctor_id=doctor.id,
                doctor_name=user.full_name if user else None,
                department_name=department.name if department else None,
                days_rostered=rostered,
                days_present=present,
                days_absent=rostered - present,
                days_on_leave=leave,
                attendance_rate=_rate(present, rostered),
                average_minutes_late=round(statistics.fmean(lateness), 1)
                if lateness
                else 0.0,
                days_late=late_days,
                punctuality_rate=_rate(present - late_days, present),
            )
        )

    rows.sort(key=lambda r: r.attendance_rate)
    return AttendanceReport(start_date=start_date, end_date=end_date, rows=rows)


def absence_patterns(
    db: Session, start_date: date, end_date: date, min_absence_rate: float = 0.3
) -> list[AbsencePatternRow]:
    """Absence broken down by weekday.

    A doctor absent 8% of the time overall but 60% of Saturdays is not an
    attendance problem, it is a rostering problem — and only the weekday split
    makes that visible.
    """
    results: list[AbsencePatternRow] = []

    for doctor in doctors_service.list_doctors(db):
        user = db.get(User, doctor.user_id)
        buckets: dict[int, list[bool]] = {i: [] for i in range(7)}

        for day in _each_day(start_date, end_date):
            availability = doctors_service.get_day_availability(db, doctor.id, day)
            if availability.is_on_leave or not availability.windows:
                continue
            buckets[day.weekday()].append(_first_arrival(db, doctor.id, day) is None)

        for weekday, flags in buckets.items():
            if not flags:
                continue
            absences = sum(flags)
            rate = _rate(absences, len(flags))
            if rate >= min_absence_rate:
                results.append(
                    AbsencePatternRow(
                        doctor_id=doctor.id,
                        doctor_name=user.full_name if user else None,
                        weekday=weekday,
                        weekday_name=WEEKDAY_NAMES[weekday],
                        days_rostered=len(flags),
                        days_absent=absences,
                        absence_rate=rate,
                    )
                )

    results.sort(key=lambda r: r.absence_rate, reverse=True)
    return results


def _each_day(start_date: date, end_date: date):
    day = start_date
    while day <= end_date:
        yield day
        day += timedelta(days=1)


# --- waiting times ---------------------------------------------------------


def _waits_for(entries: list[QueueEntry]) -> list[int]:
    """Minutes between taking a token and being called."""
    waits = []
    for entry in entries:
        if entry.called_at is None:
            continue
        minutes = minutes_between(as_utc(entry.joined_at), as_utc(entry.called_at))
        if minutes >= 0:
            waits.append(minutes)
    return waits


def _summarise(label: str, waits: list[int]) -> WaitTimeRow | None:
    if not waits:
        return None
    ordered = sorted(waits)
    index = max(int(round(0.9 * (len(ordered) - 1))), 0)
    return WaitTimeRow(
        label=label,
        sample_size=len(ordered),
        mean_minutes=round(statistics.fmean(ordered), 1),
        median_minutes=round(statistics.median(ordered), 1),
        p90_minutes=float(ordered[index]),
        max_minutes=ordered[-1],
    )


def wait_time_report(
    db: Session, start_date: date, end_date: date
) -> WaitTimeReport:
    sessions = list(
        db.execute(
            select(QueueSession).where(
                QueueSession.session_date >= start_date,
                QueueSession.session_date <= end_date,
            )
        ).scalars()
    )
    session_ids = [s.id for s in sessions]
    entries = (
        list(
            db.execute(
                select(QueueEntry).where(QueueEntry.session_id.in_(session_ids))
            ).scalars()
        )
        if session_ids
        else []
    )

    by_doctor: dict[int, list[QueueEntry]] = {}
    session_doctor = {s.id: s.doctor_id for s in sessions}
    for entry in entries:
        by_doctor.setdefault(session_doctor[entry.session_id], []).append(entry)

    doctor_rows = []
    department_buckets: dict[int, list[QueueEntry]] = {}
    for doctor_id, doctor_entries in by_doctor.items():
        doctor = db.get(Doctor, doctor_id)
        user = db.get(User, doctor.user_id) if doctor else None
        row = _summarise(user.full_name if user else f"Doctor {doctor_id}", _waits_for(doctor_entries))
        if row:
            doctor_rows.append(row)
        if doctor:
            department_buckets.setdefault(doctor.department_id, []).extend(doctor_entries)

    department_rows = []
    for department_id, dept_entries in department_buckets.items():
        department = db.get(Department, department_id)
        row = _summarise(
            department.name if department else f"Department {department_id}",
            _waits_for(dept_entries),
        )
        if row:
            department_rows.append(row)

    return WaitTimeReport(
        start_date=start_date,
        end_date=end_date,
        overall=_summarise("All departments", _waits_for(entries)),
        by_department=sorted(department_rows, key=lambda r: -r.mean_minutes),
        by_doctor=sorted(doctor_rows, key=lambda r: -r.mean_minutes),
    )


# --- load and channels -----------------------------------------------------


def department_load(
    db: Session, start_date: date, end_date: date
) -> list[DepartmentLoadRow]:
    rows = []
    for department in doctors_service.list_departments(db):
        appointments = list(
            db.execute(
                select(Appointment).where(
                    Appointment.department_id == department.id,
                    Appointment.appointment_date >= start_date,
                    Appointment.appointment_date <= end_date,
                )
            ).scalars()
        )
        doctors = doctors_service.list_doctors(db, department_id=department.id)

        completed = sum(1 for a in appointments if a.status == AppointmentStatus.COMPLETED)
        cancelled = sum(1 for a in appointments if a.status == AppointmentStatus.CANCELLED)
        no_shows = sum(1 for a in appointments if a.status == AppointmentStatus.NO_SHOW)
        emergencies = db.execute(
            select(func.count(EmergencyCase.id)).where(
                EmergencyCase.department_id == department.id
            )
        ).scalar_one()

        capacity = 0
        for doctor in doctors:
            for day in _each_day(start_date, end_date):
                capacity += doctors_service.get_day_availability(
                    db, doctor.id, day
                ).capacity_estimate

        rows.append(
            DepartmentLoadRow(
                department_id=department.id,
                department_name=department.name,
                doctors=len(doctors),
                appointments=len(appointments),
                completed=completed,
                cancelled=cancelled,
                no_shows=no_shows,
                no_show_rate=_rate(no_shows, len(appointments)),
                emergencies=int(emergencies),
                utilisation_pct=_pct(len(appointments), capacity),
            )
        )

    rows.sort(key=lambda r: -r.appointments)
    return rows


def channel_mix(db: Session, start_date: date, end_date: date) -> list[ChannelMixRow]:
    """Did the kiosk and the phone line actually get used?

    A channel nobody uses is a channel that failed the people it was for.
    """
    appointments = list(
        db.execute(
            select(Appointment).where(
                Appointment.appointment_date >= start_date,
                Appointment.appointment_date <= end_date,
            )
        ).scalars()
    )
    total = len(appointments)

    rows = []
    for channel in BookingChannel:
        subset = [a for a in appointments if a.channel == str(channel)]
        if not subset:
            continue
        no_shows = sum(1 for a in subset if a.status == AppointmentStatus.NO_SHOW)
        rows.append(
            ChannelMixRow(
                channel=str(channel),
                bookings=len(subset),
                share_pct=_pct(len(subset), total),
                no_show_rate=_rate(no_shows, len(subset)),
            )
        )
    rows.sort(key=lambda r: -r.bookings)
    return rows


def notification_stats(db: Session) -> list[NotificationStatsRow]:
    rows = []
    channels = db.execute(select(Notification.channel).distinct()).scalars()
    for channel in channels:
        subset = list(
            db.execute(
                select(Notification).where(Notification.channel == channel)
            ).scalars()
        )
        sent = sum(
            1
            for n in subset
            if n.status in (NotificationStatus.SENT, NotificationStatus.DELIVERED)
        )
        failed = sum(1 for n in subset if n.status == NotificationStatus.FAILED)
        queued = sum(1 for n in subset if n.status == NotificationStatus.QUEUED)
        rows.append(
            NotificationStatsRow(
                channel=channel,
                queued=queued,
                sent=sent,
                failed=failed,
                delivery_rate=_rate(sent, len(subset)),
            )
        )
    return rows


# --- health department rollup ----------------------------------------------


def health_dept_summary(
    db: Session, start_date: date, end_date: date
) -> HealthDeptSummary:
    appointments = list(
        db.execute(
            select(Appointment).where(
                Appointment.appointment_date >= start_date,
                Appointment.appointment_date <= end_date,
            )
        ).scalars()
    )
    completed = sum(1 for a in appointments if a.status == AppointmentStatus.COMPLETED)
    no_shows = sum(1 for a in appointments if a.status == AppointmentStatus.NO_SHOW)
    no_show_rate = _rate(no_shows, len(appointments))

    waits = wait_time_report(db, start_date, end_date)
    average_wait = waits.overall.mean_minutes if waits.overall else 0.0

    attendance = attendance_report(db, start_date, end_date)
    rostered_days = sum(r.days_rostered for r in attendance.rows)
    present_days = sum(r.days_present for r in attendance.rows)
    late_days = sum(r.days_late for r in attendance.rows)

    attendance_rate = _rate(present_days, rostered_days)
    punctuality_rate = _rate(present_days - late_days, present_days)

    emergencies = db.execute(select(func.count(EmergencyCase.id))).scalar_one()

    alerts: list[str] = []
    if no_show_rate > ALERT_NO_SHOW_RATE:
        alerts.append(
            f"No-show rate is {no_show_rate:.0%} — above the {ALERT_NO_SHOW_RATE:.0%} threshold"
        )
    if average_wait > ALERT_AVG_WAIT_MINUTES:
        alerts.append(
            f"Average wait is {average_wait:.0f} minutes — above the "
            f"{ALERT_AVG_WAIT_MINUTES} minute threshold"
        )
    if rostered_days and attendance_rate < ALERT_ATTENDANCE_RATE:
        alerts.append(
            f"Doctor attendance is {attendance_rate:.0%} — below the "
            f"{ALERT_ATTENDANCE_RATE:.0%} threshold"
        )
    for pattern in absence_patterns(db, start_date, end_date)[:3]:
        alerts.append(
            f"{pattern.doctor_name} absent {pattern.absence_rate:.0%} of "
            f"{pattern.weekday_name}s"
        )

    return HealthDeptSummary(
        start_date=start_date,
        end_date=end_date,
        departments=len(doctors_service.list_departments(db)),
        doctors=len(doctors_service.list_doctors(db)),
        total_appointments=len(appointments),
        completed=completed,
        no_show_rate=no_show_rate,
        average_wait_minutes=average_wait,
        doctor_attendance_rate=attendance_rate,
        doctor_punctuality_rate=punctuality_rate,
        emergencies_handled=int(emergencies),
        bookings_by_channel=channel_mix(db, start_date, end_date),
        alerts=alerts,
    )
