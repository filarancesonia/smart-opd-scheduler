"""Room 7 — Emergency & Priority (the ambulance siren).

Overriding a queue is easy. Overriding it *safely* is the hard part, and it is
what this module is for:

  - Only clinical staff can raise a priority, never the patient themselves.
  - Every override is written to an append-only log with an actor and a reason.
  - Routine patients cannot be displaced indefinitely: waiting time escalates
    a person's tier automatically, so a morning of emergencies does not leave
    the same people sitting on the bench until the clinic closes.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class PriorityTier(IntEnum):
    """Higher wins. The optimiser treats these as absolute."""

    ROUTINE = 0
    VULNERABLE = 1  # senior citizen, pregnant, or disabled
    URGENT = 2  # needs to be seen soon, not life-threatening
    EMERGENCY = 3  # immediate


class TriageLevel(StrEnum):
    """Simplified five-level triage, as used in Indian casualty departments."""

    RED = "red"  # resuscitation — immediate
    ORANGE = "orange"  # very urgent
    YELLOW = "yellow"  # urgent
    GREEN = "green"  # standard
    BLUE = "blue"  # non-urgent


#: Triage colour to queue tier.
TRIAGE_TIERS: dict[str, int] = {
    TriageLevel.RED: PriorityTier.EMERGENCY,
    TriageLevel.ORANGE: PriorityTier.EMERGENCY,
    TriageLevel.YELLOW: PriorityTier.URGENT,
    TriageLevel.GREEN: PriorityTier.ROUTINE,
    TriageLevel.BLUE: PriorityTier.ROUTINE,
}


class CaseStatus(StrEnum):
    ACTIVE = "active"
    RESOLVED = "resolved"
    TRANSFERRED = "transferred"  # sent to casualty / another facility
    CANCELLED = "cancelled"


class OverrideSource(StrEnum):
    TRIAGE = "triage"
    MANUAL = "manual"
    VULNERABILITY = "vulnerability"
    AGING = "aging"  # automatic escalation for waiting too long


class EmergencyCase(TimestampMixin, Base):
    __tablename__ = "emergency_cases"

    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)
    doctor_id: Mapped[int | None] = mapped_column(
        ForeignKey("doctors.id"), nullable=True, index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id"), nullable=True
    )
    queue_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("queue_entries.id"), nullable=True
    )

    triage_level: Mapped[str] = mapped_column(String(10), index=True)
    complaint: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default=CaseStatus.ACTIVE, index=True)

    arrived_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: The clinician accountable for this triage decision.
    triaged_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    outcome: Mapped[str] = mapped_column(Text, default="")


class PriorityOverride(TimestampMixin, Base):
    """Append-only record of every queue-order override.

    Jumping a queue in a public hospital is a decision someone has to be able
    to account for later.
    """

    __tablename__ = "priority_overrides"

    queue_entry_id: Mapped[int | None] = mapped_column(
        ForeignKey("queue_entries.id", ondelete="SET NULL"), nullable=True, index=True
    )
    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True
    )

    from_tier: Mapped[int] = mapped_column(Integer)
    to_tier: Mapped[int] = mapped_column(Integer, index=True)
    source: Mapped[str] = mapped_column(String(20), index=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    #: Null only for automatic escalations, which have no human author.
    actor_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    #: How many people this override moved down the queue.
    displaced_count: Mapped[int] = mapped_column(Integer, default=0)
