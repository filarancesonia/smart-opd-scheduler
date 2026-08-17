"""Timezone helpers.

Everything is *stored* in UTC. But a duty roster says "09:00", and that means
09:00 on the hospital's wall clock — so any comparison between a stored instant
and a rostered time has to happen in hospital-local time. These helpers are the
only place that conversion is allowed to happen.
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

from app.core.config import settings


def hospital_tz() -> ZoneInfo:
    return ZoneInfo(settings.hospital_timezone)


def as_utc(dt: datetime) -> datetime:
    """Attach UTC to a naive datetime; convert an aware one."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def to_local(dt: datetime) -> datetime:
    """Render a stored instant on the hospital's wall clock."""
    return as_utc(dt).astimezone(hospital_tz())


def local_now() -> datetime:
    return datetime.now(hospital_tz())


def local_today() -> date:
    return local_now().date()


def combine_local(on_date: date, clock: time) -> datetime:
    """A rostered wall-clock time on a given date, as an aware instant."""
    return datetime.combine(on_date, clock, tzinfo=hospital_tz())


def minutes_between(earlier: datetime, later: datetime) -> int:
    """Whole minutes from ``earlier`` to ``later``; negative if reversed."""
    return int((as_utc(later) - as_utc(earlier)).total_seconds() // 60)
