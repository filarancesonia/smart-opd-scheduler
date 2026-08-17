"""Room 4 — AI Scheduling Engine: training data and model bookkeeping.

ConsultationRecord is the ground truth the two predictors learn from: one row
per finished (or missed) appointment, with the features frozen as they were at
booking time. Freezing matters — asking "how many prior no-shows did this
patient have?" today would leak the future into a model trained on the past.
"""

from __future__ import annotations

from datetime import date, datetime, time
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    Time,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base, TimestampMixin


class ModelKind(StrEnum):
    DURATION = "duration"
    NO_SHOW = "no_show"


class ConsultationRecord(TimestampMixin, Base):
    """One completed or missed consultation, as a training example."""

    __tablename__ = "consultation_records"

    appointment_id: Mapped[int | None] = mapped_column(
        ForeignKey("appointments.id", ondelete="SET NULL"), nullable=True, index=True
    )
    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    patient_id: Mapped[int | None] = mapped_column(
        ForeignKey("patients.id", ondelete="SET NULL"), nullable=True
    )
    department_id: Mapped[int] = mapped_column(ForeignKey("departments.id"), index=True)

    record_date: Mapped[date] = mapped_column(Date, index=True)
    scheduled_start: Mapped[time] = mapped_column(Time)
    actual_start: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    actual_end: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # --- targets ---
    duration_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    was_no_show: Mapped[bool] = mapped_column(Boolean, default=False, index=True)

    # --- features, frozen at booking time ---
    patient_age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_follow_up: Mapped[bool] = mapped_column(Boolean, default=False)
    channel: Mapped[str] = mapped_column(String(20), default="website")
    lead_time_days: Mapped[int] = mapped_column(Integer, default=0)
    prior_visits: Mapped[int] = mapped_column(Integer, default=0)
    prior_no_shows: Mapped[int] = mapped_column(Integer, default=0)
    day_of_week: Mapped[int] = mapped_column(Integer, default=0)
    hour_of_day: Mapped[int] = mapped_column(Integer, default=9)
    slot_index: Mapped[int] = mapped_column(Integer, default=0)
    is_senior: Mapped[bool] = mapped_column(Boolean, default=False)
    has_priority_flag: Mapped[bool] = mapped_column(Boolean, default=False)

    #: Synthetic rows bootstrap a cold start. Flagged so a demo can never be
    #: mistaken for evidence drawn from real patients.
    is_synthetic: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class ModelArtifact(TimestampMixin, Base):
    """Bookkeeping for one trained model file."""

    __tablename__ = "model_artifacts"

    kind: Mapped[str] = mapped_column(String(20), index=True)
    version: Mapped[str] = mapped_column(String(40))
    path: Mapped[str] = mapped_column(String(255))
    n_samples: Mapped[int] = mapped_column(Integer, default=0)
    trained_on_synthetic: Mapped[bool] = mapped_column(Boolean, default=False)
    metrics: Mapped[str] = mapped_column(Text, default="{}")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class SchedulePlan(TimestampMixin, Base):
    """A saved optimiser run, so a plan can be audited after the fact."""

    __tablename__ = "schedule_plans"

    doctor_id: Mapped[int] = mapped_column(ForeignKey("doctors.id"), index=True)
    plan_date: Mapped[date] = mapped_column(Date, index=True)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    available_from: Mapped[time] = mapped_column(Time)
    available_until: Mapped[time] = mapped_column(Time)
    total_expected_wait: Mapped[float] = mapped_column(Float, default=0.0)
    baseline_wait: Mapped[float] = mapped_column(Float, default=0.0)
    improvement_pct: Mapped[float] = mapped_column(Float, default=0.0)
    # The ordered assignment list, as JSON.
    assignments: Mapped[str] = mapped_column(Text, default="[]")
