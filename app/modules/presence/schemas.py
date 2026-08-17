"""Room 1 request/response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.doctors.models import CredentialType
from app.modules.presence.models import (
    DeviceType,
    Direction,
    PresenceStatus,
    RosterDeviation,
)


# --- devices ---------------------------------------------------------------


class DeviceCreate(BaseModel):
    device_uid: str = Field(min_length=3, max_length=64)
    device_type: DeviceType
    room: str = Field(min_length=1, max_length=50)
    department_id: int | None = None
    location_note: str = ""


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    device_uid: str
    device_type: str
    room: str
    department_id: int | None
    location_note: str
    is_active: bool
    last_seen_at: datetime | None


# --- signal ingest ---------------------------------------------------------


class SignalIn(BaseModel):
    """What a door reader posts to the gateway."""

    device_uid: str
    credential_type: CredentialType
    # Raw tag id / beacon id / face-template digest. Fingerprinted, never stored.
    raw_value: str = Field(min_length=4, max_length=512)
    direction: Direction = Direction.SEEN
    observed_at: datetime | None = None
    # Reader-reported match quality, 0-1. Face cameras report their similarity
    # score here; RFID readers report 1.0.
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class SignalBatch(BaseModel):
    """Readers buffer while offline, then flush a batch when the link returns."""

    signals: list[SignalIn] = Field(min_length=1, max_length=500)


class SignalResult(BaseModel):
    accepted: bool
    matched: bool
    doctor_id: int | None = None
    status: str | None = None
    room: str | None = None
    reason: str | None = None


class BatchResult(BaseModel):
    received: int
    matched: int
    unmatched: int
    results: list[SignalResult]


class ManualPresence(BaseModel):
    """Reception marking a doctor present when hardware fails."""

    doctor_id: int
    status: PresenceStatus
    room: str | None = None
    note: str = ""


# --- current state ---------------------------------------------------------


class PresenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    doctor_id: int
    doctor_name: str | None = None
    department_name: str | None = None
    status: str
    room: str | None
    since: datetime | None
    last_signal_at: datetime | None
    last_credential_type: str | None
    confidence: float

    # How long the doctor has been present, in whole minutes.
    present_minutes: int | None = None

    # Comparison against the Room 2 roster.
    deviation: RosterDeviation | None = None
    expected_room: str | None = None
    expected_until: datetime | None = None
    minutes_late: int | None = None


class PresenceEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    from_status: str
    to_status: str
    room: str | None
    occurred_at: datetime
    source: str
    note: str
