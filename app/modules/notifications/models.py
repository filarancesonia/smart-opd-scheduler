"""Room 6 — Notification Module (the messenger).

Every message is persisted before it is sent. In a government hospital the
question "did anyone actually tell the patient their appointment moved?" has
to be answerable months later, and a fire-and-forget SMS call cannot answer it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class Channel(StrEnum):
    SMS = "sms"
    PUSH = "push"  # mobile app notification
    WHATSAPP = "whatsapp"
    VOICE = "voice"  # automated call, for patients who cannot read


class NotificationStatus(StrEnum):
    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TemplateCode(StrEnum):
    BOOKING_CONFIRMED = "booking_confirmed"
    REMINDER_DAY_BEFORE = "reminder_day_before"
    TURN_SOON = "turn_soon"
    NOW_CALLING = "now_calling"
    APPOINTMENT_CANCELLED = "appointment_cancelled"
    APPOINTMENT_RESCHEDULED = "appointment_rescheduled"
    DOCTOR_DELAYED = "doctor_delayed"
    DOCTOR_UNAVAILABLE = "doctor_unavailable"


class Notification(TimestampMixin, Base):
    __tablename__ = "notifications"
    __table_args__ = (
        # One logical message per recipient per event. Re-running a reminder
        # sweep must not text the same person five times.
        UniqueConstraint("dedupe_key", name="uq_notification_dedupe"),
    )

    dedupe_key: Mapped[str] = mapped_column(String(120), index=True)

    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True, index=True
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )

    channel: Mapped[str] = mapped_column(String(20), index=True)
    template_code: Mapped[str] = mapped_column(String(40), index=True)
    language: Mapped[str] = mapped_column(String(5), default="hi")

    recipient: Mapped[str] = mapped_column(String(40))
    body: Mapped[str] = mapped_column(Text)

    status: Mapped[str] = mapped_column(
        String(20), default=NotificationStatus.QUEUED, index=True
    )
    scheduled_for: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    provider: Mapped[str] = mapped_column(String(30), default="console")
    provider_message_id: Mapped[str | None] = mapped_column(String(80), nullable=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
