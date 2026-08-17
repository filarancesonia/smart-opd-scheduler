"""Room 8 response shapes.

This module owns no tables. Every figure is derived from what the other rooms
already recorded — which is the point: a dashboard that keeps its own copy of
the numbers is a dashboard that will eventually disagree with the system.
"""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel


class LiveDoctorRow(BaseModel):
    doctor_id: int
    doctor_name: str | None
    department_name: str | None
    presence_status: str
    room: str | None
    deviation: str | None
    minutes_late: int | None
    waiting_count: int
    longest_wait_minutes: int | None
    now_serving: int | None


class LiveOverview(BaseModel):
    """What a hospital administrator sees on the wall screen right now."""

    generated_at: datetime
    doctors_total: int
    doctors_present: int
    doctors_absent_while_rostered: int
    doctors_on_leave: int
    patients_waiting: int
    longest_wait_minutes: int | None
    active_emergencies: int
    doctors: list[LiveDoctorRow] = []


class AttendanceRow(BaseModel):
    doctor_id: int
    doctor_name: str | None
    department_name: str | None
    days_rostered: int
    days_present: int
    days_absent: int
    days_on_leave: int
    attendance_rate: float
    average_minutes_late: float
    days_late: int
    punctuality_rate: float


class AttendanceReport(BaseModel):
    start_date: date
    end_date: date
    rows: list[AttendanceRow] = []


class AbsencePatternRow(BaseModel):
    """Absence by weekday — the shape that tells you it is not random."""

    doctor_id: int
    doctor_name: str | None
    weekday: int
    weekday_name: str
    days_rostered: int
    days_absent: int
    absence_rate: float


class WaitTimeRow(BaseModel):
    label: str
    sample_size: int
    mean_minutes: float
    median_minutes: float
    p90_minutes: float
    max_minutes: int


class WaitTimeReport(BaseModel):
    start_date: date
    end_date: date
    overall: WaitTimeRow | None = None
    by_department: list[WaitTimeRow] = []
    by_doctor: list[WaitTimeRow] = []


class DepartmentLoadRow(BaseModel):
    department_id: int
    department_name: str
    doctors: int
    appointments: int
    completed: int
    cancelled: int
    no_shows: int
    no_show_rate: float
    emergencies: int
    utilisation_pct: float


class ChannelMixRow(BaseModel):
    channel: str
    bookings: int
    share_pct: float
    no_show_rate: float


class NotificationStatsRow(BaseModel):
    channel: str
    queued: int
    sent: int
    failed: int
    delivery_rate: float


class HealthDeptSummary(BaseModel):
    """The rollup a state Health Department actually asks for."""

    start_date: date
    end_date: date
    departments: int
    doctors: int
    total_appointments: int
    completed: int
    no_show_rate: float
    average_wait_minutes: float
    doctor_attendance_rate: float
    doctor_punctuality_rate: float
    emergencies_handled: int
    bookings_by_channel: list[ChannelMixRow] = []
    #: Flags for anything that needs a human to look at it.
    alerts: list[str] = []
