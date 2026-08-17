"""Room 2 — Doctor Profile & Duty Roster (the diary).

Holds the *expected* schedule: who works where, on which days, between which
hours, and when they are on leave. Room 1 compares live door signals against
this to decide whether a doctor is where the timetable claims.
"""

from __future__ import annotations

from datetime import date, time
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin


class CredentialType(StrEnum):
    RFID = "rfid"
    FACE = "face"
    BLE = "ble"  # Bluetooth beacon / phone MAC
    MANUAL = "manual"  # reception marks presence by hand


class LeaveType(StrEnum):
    CASUAL = "casual"
    SICK = "sick"
    EARNED = "earned"
    CONFERENCE = "conference"
    OFFICIAL_DUTY = "official_duty"  # surgery, VIP duty, government meeting


class LeaveStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class Department(TimestampMixin, Base):
    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    floor: Mapped[str | None] = mapped_column(String(30), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    doctors: Mapped[list[Doctor]] = relationship(back_populates="department")


class Doctor(TimestampMixin, Base):
    __tablename__ = "doctors"

    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), unique=True, index=True
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("departments.id"), index=True
    )

    # State medical council registration — the legal identity of a practitioner.
    registration_no: Mapped[str] = mapped_column(String(50), unique=True, index=True)
    qualification: Mapped[str] = mapped_column(String(200), default="")
    specialisation: Mapped[str] = mapped_column(String(150), default="")
    designation: Mapped[str] = mapped_column(String(100), default="")

    # Fallback used by Room 4 until enough history exists to predict per-doctor.
    avg_consultation_minutes: Mapped[int] = mapped_column(Integer, default=10)
    max_patients_per_day: Mapped[int] = mapped_column(Integer, default=60)

    is_accepting_patients: Mapped[bool] = mapped_column(Boolean, default=True)

    department: Mapped[Department] = relationship(back_populates="doctors")
    duty_slots: Mapped[list[DutySlot]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )
    leaves: Mapped[list[Leave]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )
    credentials: Mapped[list[DoctorCredential]] = relationship(
        back_populates="doctor", cascade="all, delete-orphan"
    )


class DutySlot(TimestampMixin, Base):
    """One recurring weekly window, e.g. 'Mon 09:00-13:00 in OPD 12'."""

    __tablename__ = "duty_slots"
    __table_args__ = (
        UniqueConstraint(
            "doctor_id", "day_of_week", "start_time", "valid_from", name="uq_duty_slot"
        ),
    )

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    day_of_week: Mapped[int] = mapped_column(Integer, index=True)  # Monday = 0
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    room: Mapped[str] = mapped_column(String(50))

    # A roster is versioned rather than overwritten, so historical analytics in
    # Room 8 can still ask "what was the timetable last March?".
    valid_from: Mapped[date] = mapped_column(Date, index=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    doctor: Mapped[Doctor] = relationship(back_populates="duty_slots")


class Leave(TimestampMixin, Base):
    __tablename__ = "leaves"

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    leave_type: Mapped[str] = mapped_column(String(20), default=LeaveType.CASUAL)
    status: Mapped[str] = mapped_column(
        String(20), default=LeaveStatus.PENDING, index=True
    )
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    approved_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )

    doctor: Mapped[Doctor] = relationship(back_populates="leaves")


class DoctorCredential(TimestampMixin, Base):
    """An identifier a door reader can observe.

    Only a keyed fingerprint of the raw value is stored — never the tag number
    or a face image. A database leak therefore yields nothing that can be
    replayed at a reader. Room 10 governs consent for the face variant.
    """

    __tablename__ = "doctor_credentials"
    __table_args__ = (
        UniqueConstraint("credential_type", "fingerprint", name="uq_credential"),
    )

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    credential_type: Mapped[str] = mapped_column(String(20), index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    doctor: Mapped[Doctor] = relationship(back_populates="credentials")
