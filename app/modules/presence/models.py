"""Room 1 — Presence Detection (the eyes).

Door readers emit raw observations; this module fuses them into one answer:
"Dr. Sharma = PRESENT, OPD 12, since 9:14 AM" — and compares that answer to the
Room 2 roster so the hospital can see who is actually where they should be.

Three tables, three jobs:
  PresenceSignal  every raw observation, kept for audit (including unmatched ones)
  PresenceState   the current fused answer, one row per doctor
  PresenceEvent   the transition log (arrived / left / moved room)
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin, utcnow


class DeviceType(StrEnum):
    RFID_READER = "rfid_reader"
    FACE_CAMERA = "face_camera"
    BLE_GATEWAY = "ble_gateway"


class Direction(StrEnum):
    IN = "in"
    OUT = "out"
    SEEN = "seen"  # a passive sighting with no entry/exit semantics


class PresenceStatus(StrEnum):
    PRESENT = "present"
    ABSENT = "absent"
    ON_BREAK = "on_break"
    # Last signal is older than the trust window — we genuinely do not know.
    STALE = "stale"
    UNKNOWN = "unknown"


class RosterDeviation(StrEnum):
    """How the observed reality compares to the Room 2 timetable."""

    ON_DUTY_AS_ROSTERED = "on_duty_as_rostered"
    ABSENT_WHILE_ROSTERED = "absent_while_rostered"
    PRESENT_OFF_ROSTER = "present_off_roster"
    WRONG_ROOM = "wrong_room"
    ON_APPROVED_LEAVE = "on_approved_leave"
    NOT_ROSTERED = "not_rostered"


class Device(TimestampMixin, Base):
    """A physical reader mounted at a door or inside a consultation room."""

    __tablename__ = "devices"

    device_uid: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    device_type: Mapped[str] = mapped_column(String(30), index=True)
    room: Mapped[str] = mapped_column(String(50), index=True)
    department_id: Mapped[int | None] = mapped_column(
        ForeignKey("departments.id"), nullable=True
    )
    location_note: Mapped[str] = mapped_column(String(200), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    signals: Mapped[list[PresenceSignal]] = relationship(back_populates="device")


class PresenceSignal(TimestampMixin, Base):
    """One raw observation from one reader.

    Unmatched signals are kept with ``doctor_id = NULL``: an unknown tag at a
    door is exactly the kind of thing a security review needs to see.
    """

    __tablename__ = "presence_signals"

    device_id: Mapped[int | None] = mapped_column(
        ForeignKey("devices.id"), nullable=True, index=True
    )
    doctor_id: Mapped[int | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), nullable=True, index=True
    )
    credential_type: Mapped[str] = mapped_column(String(20))
    direction: Mapped[str] = mapped_column(String(10), default=Direction.SEEN)
    room: Mapped[str] = mapped_column(String(50))
    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    matched: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    device: Mapped[Device | None] = relationship(back_populates="signals")


class PresenceState(TimestampMixin, Base):
    """The current fused answer for one doctor."""

    __tablename__ = "presence_states"

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), unique=True, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default=PresenceStatus.UNKNOWN, index=True
    )
    room: Mapped[str | None] = mapped_column(String(50), nullable=True)
    # When this uninterrupted stretch of presence began — the "since 9:14 AM".
    since: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_signal_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_credential_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class PresenceEvent(TimestampMixin, Base):
    """An append-only log of state transitions, for Room 8 and audits."""

    __tablename__ = "presence_events"

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    from_status: Mapped[str] = mapped_column(String(20))
    to_status: Mapped[str] = mapped_column(String(20), index=True)
    room: Mapped[str | None] = mapped_column(String(50), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    source: Mapped[str] = mapped_column(String(20), default="device")
    note: Mapped[str] = mapped_column(Text, default="")
