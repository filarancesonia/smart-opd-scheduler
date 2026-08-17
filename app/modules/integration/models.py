"""Room 9 — Integration Module (the bridge).

The failure mode this module exists to prevent is the one every state health
IT project hits: a system that works beautifully and is a separate island, so
staff end up entering the same patient twice and the data never reaches the
national record.

Two tables:
  ExternalLink  our id <-> their id, per system, with how it was verified
  SyncLog       every call in or out, kept whether it succeeded or not
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class ExternalSystem(StrEnum):
    ABHA = "abha"  # Ayushman Bharat Health Account (national health ID)
    ORS = "ors"  # Online Registration System
    HMIS = "hmis"  # the hospital's own HMIS / EHR, over FHIR


class LinkStatus(StrEnum):
    VERIFIED = "verified"  # confirmed against the upstream system
    UNVERIFIED = "unverified"  # recorded but not yet confirmed
    REVOKED = "revoked"  # consent withdrawn, or the link was wrong


class SyncDirection(StrEnum):
    OUTBOUND = "outbound"
    INBOUND = "inbound"


class SyncStatus(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


class ExternalLink(TimestampMixin, Base):
    __tablename__ = "external_links"
    __table_args__ = (
        UniqueConstraint("system", "external_id", name="uq_external_identity"),
    )

    system: Mapped[str] = mapped_column(String(20), index=True)
    external_id: Mapped[str] = mapped_column(String(120), index=True)

    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="CASCADE"), nullable=True, index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), nullable=True, index=True
    )
    doctor_id: Mapped[int | None] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), nullable=True
    )

    status: Mapped[str] = mapped_column(
        String(20), default=LinkStatus.UNVERIFIED, index=True
    )
    #: How the link was established — "otp", "demographic", "manual", "mock".
    verification_method: Mapped[str] = mapped_column(String(30), default="manual")
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Room 10 requires explicit consent before anything leaves the building.
    consent_reference: Mapped[str | None] = mapped_column(String(80), nullable=True)
    #: True when produced by the offline stub rather than a real gateway.
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False)
    extra: Mapped[str] = mapped_column(Text, default="{}")


class SyncLog(TimestampMixin, Base):
    """Append-only record of every exchange with an external system."""

    __tablename__ = "sync_logs"

    system: Mapped[str] = mapped_column(String(20), index=True)
    direction: Mapped[str] = mapped_column(String(10), index=True)
    operation: Mapped[str] = mapped_column(String(60), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)

    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    is_mock: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    #: Request/response summary. Never the full payload — that would put
    #: health data into a log table Room 10 does not govern.
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str] = mapped_column(Text, default="")
