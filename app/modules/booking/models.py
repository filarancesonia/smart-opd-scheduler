"""Room 3 — Patient Booking (the front door).

Four channels reach the same core: mobile app, website, in-hospital kiosk and a
Hindi phone/IVR line. The channel is recorded on every appointment because
"who actually books through the kiosk" is one of the questions Room 8 has to
answer — a channel nobody uses is a channel that failed its users.

A Patient is deliberately *not* the same thing as a User. A walk-in registering
at a kiosk, or an elderly caller on the IVR line, has no account and no
smartphone; they still need a record and a token.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin
from app.modules.privacy.crypto import EncryptedString


class BookingChannel(StrEnum):
    MOBILE_APP = "mobile_app"
    WEBSITE = "website"
    KIOSK = "kiosk"
    IVR = "ivr"
    STAFF = "staff"  # reception booking on someone's behalf


class AppointmentStatus(StrEnum):
    BOOKED = "booked"
    CHECKED_IN = "checked_in"  # patient has arrived and joined the queue
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    NO_SHOW = "no_show"
    RESCHEDULED = "rescheduled"

    @classmethod
    def active(cls) -> tuple[str, ...]:
        """Statuses that still occupy a slot."""
        return (cls.BOOKED, cls.CHECKED_IN, cls.IN_PROGRESS)


class Gender(StrEnum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class Patient(TimestampMixin, Base):
    __tablename__ = "patients"

    # Null for walk-ins registered at a kiosk or over the phone.
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    full_name: Mapped[str] = mapped_column(String(150))
    # Kept queryable: reception looks a patient up by phone dozens of times a
    # day. Room 10 ships a blind index for encrypting this without losing
    # exact-match lookup; that migration is not done yet, and saying so is
    # better than implying the column is protected when it is not.
    phone: Mapped[str] = mapped_column(String(20), index=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(10), nullable=True)
    # Free text, never searched, and genuinely identifying — encrypted at rest.
    address: Mapped[str] = mapped_column(EncryptedString, default="")
    abha_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    preferred_language: Mapped[str] = mapped_column(String(5), default="hi")

    # Declared at registration; Room 7 turns these into queue priority.
    is_pregnant: Mapped[bool] = mapped_column(Boolean, default=False)
    has_disability: Mapped[bool] = mapped_column(Boolean, default=False)

    appointments: Mapped[list[Appointment]] = relationship(back_populates="patient")

    @property
    def is_senior_citizen(self) -> bool:
        return self.age is not None and self.age >= 60


class Appointment(TimestampMixin, Base):
    __tablename__ = "appointments"
    __table_args__ = (
        UniqueConstraint("booking_reference", name="uq_booking_reference"),
    )

    booking_reference: Mapped[str] = mapped_column(String(12), index=True)
    patient_id: Mapped[int] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), index=True
    )
    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)

    appointment_date: Mapped[date] = mapped_column(Date, index=True)
    slot_start: Mapped[time] = mapped_column(Time)
    slot_end: Mapped[time] = mapped_column(Time)
    room: Mapped[str] = mapped_column(String(50), default="")

    status: Mapped[str] = mapped_column(
        String(20), default=AppointmentStatus.BOOKED, index=True
    )
    channel: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    is_follow_up: Mapped[bool] = mapped_column(Boolean, default=False)

    booked_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    cancelled_reason: Mapped[str] = mapped_column(Text, default="")
    checked_in_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    patient: Mapped[Patient] = relationship(back_populates="appointments")


class IVRSession(TimestampMixin, Base):
    """State for one in-progress phone call.

    The IVR is a state machine and the caller's phone line is stateless, so the
    conversation has to be parked somewhere between keypresses.
    """

    __tablename__ = "ivr_sessions"

    session_id: Mapped[str] = mapped_column(String(36), unique=True, index=True)
    caller_phone: Mapped[str] = mapped_column(String(20), index=True)
    state: Mapped[str] = mapped_column(String(30), default="welcome")
    language: Mapped[str] = mapped_column(String(5), default="hi")
    # Selections gathered so far, as JSON text.
    context: Mapped[str] = mapped_column(Text, default="{}")
    is_complete: Mapped[bool] = mapped_column(Boolean, default=False)
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id"), nullable=True
    )
