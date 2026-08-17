"""Room 8 endpoints — live board and historical reports.

Read-only throughout. Hospital administrators see their own facility;
Health Department accounts get the same reports for oversight.
"""

from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, require_roles
from app.core.security import ANALYTICS_ROLES
from app.modules.analytics import service
from app.modules.analytics.schemas import (
    AbsencePatternRow,
    AttendanceReport,
    ChannelMixRow,
    DepartmentLoadRow,
    HealthDeptSummary,
    LiveOverview,
    NotificationStatsRow,
    WaitTimeReport,
)
from app.core.timeutil import local_today

router = APIRouter(prefix="/analytics", tags=["Room 8 - Admin & Analytics"])

Oversight = Depends(require_roles(*ANALYTICS_ROLES))

#: Default reporting window when none is given.
DEFAULT_WINDOW_DAYS = 30


def _window(
    start_date: date | None, end_date: date | None
) -> tuple[date, date]:
    end = end_date or local_today()
    start = start_date or (end - timedelta(days=DEFAULT_WINDOW_DAYS))
    return start, end


@router.get("/live", response_model=LiveOverview, dependencies=[Oversight])
def live(db: DbSession, department_id: int | None = None) -> LiveOverview:
    """Who is present, who is missing, and how many people are waiting."""
    return service.live_overview(db, department_id)


@router.get("/attendance", response_model=AttendanceReport, dependencies=[Oversight])
def attendance(
    db: DbSession,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    department_id: int | None = None,
) -> AttendanceReport:
    """Attendance and punctuality per doctor, worst first."""
    start, end = _window(start_date, end_date)
    return service.attendance_report(db, start, end, department_id)


@router.get(
    "/absence-patterns",
    response_model=list[AbsencePatternRow],
    dependencies=[Oversight],
)
def absence_patterns(
    db: DbSession,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
    min_absence_rate: float = 0.3,
) -> list[AbsencePatternRow]:
    """Absence by weekday — the split that shows it is not random."""
    start, end = _window(start_date, end_date)
    return service.absence_patterns(db, start, end, min_absence_rate)


@router.get("/wait-times", response_model=WaitTimeReport, dependencies=[Oversight])
def wait_times(
    db: DbSession,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> WaitTimeReport:
    """Mean, median and 90th percentile waits, overall and broken down."""
    start, end = _window(start_date, end_date)
    return service.wait_time_report(db, start, end)


@router.get(
    "/department-load",
    response_model=list[DepartmentLoadRow],
    dependencies=[Oversight],
)
def department_load(
    db: DbSession,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> list[DepartmentLoadRow]:
    start, end = _window(start_date, end_date)
    return service.department_load(db, start, end)


@router.get("/channels", response_model=list[ChannelMixRow], dependencies=[Oversight])
def channels(
    db: DbSession,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> list[ChannelMixRow]:
    """Booking mix by channel — did the kiosk and the phone line get used?"""
    start, end = _window(start_date, end_date)
    return service.channel_mix(db, start, end)


@router.get(
    "/notifications",
    response_model=list[NotificationStatsRow],
    dependencies=[Oversight],
)
def notifications(db: DbSession) -> list[NotificationStatsRow]:
    return service.notification_stats(db)


@router.get(
    "/health-department",
    response_model=HealthDeptSummary,
    dependencies=[Oversight],
)
def health_department(
    db: DbSession,
    start_date: date | None = Query(default=None),
    end_date: date | None = Query(default=None),
) -> HealthDeptSummary:
    """Facility rollup, with alerts for anything a human should look at."""
    start, end = _window(start_date, end_date)
    return service.health_dept_summary(db, start, end)
