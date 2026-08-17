"""Room 5 — Live Queue Management (the traffic police).

A queue session is opened per doctor per day. Patients who have checked in join
it and receive a token; the ordering comes from Room 4, the timing from Room 1,
and the answer everyone actually wants — "how much longer?" — is recomputed
from live progress rather than from the printed slot time.
"""

from __future__ import annotations

from datetime import date, datetime
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
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base, TimestampMixin


class QueueEntryStatus(StrEnum):
    WAITING = "waiting"
    CALLED = "called"  # announced, walking to the room
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    NO_SHOW = "no_show"
    SKIPPED = "skipped"  # called but absent; gets one more chance later
    LEFT = "left"  # gave up and went home

    @classmethod
    def open_states(cls) -> tuple[str, ...]:
        return (cls.WAITING, cls.CALLED, cls.IN_PROGRESS, cls.SKIPPED)


class QueueSession(TimestampMixin, Base):
    """One doctor's queue for one day."""

    __tablename__ = "queue_sessions"
    __table_args__ = (
        UniqueConstraint("doctor_id", "session_date", name="uq_queue_session"),
    )

    doctor_id: Mapped[int] = mapped_column(
        ForeignKey("doctors.id", ondelete="CASCADE"), index=True
    )
    session_date: Mapped[date] = mapped_column(Date, index=True)
    room: Mapped[str] = mapped_column(String(50), default="")

    is_open: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    #: Monotonic counter. Tokens are never reused within a session, because a
    #: reissued token number on a corridor board is worse than no board.
    next_token: Mapped[int] = mapped_column(Integer, default=1)

    #: Rolling mean of actual consultation lengths observed today. Beats any
    #: prediction once the clinic is under way.
    observed_avg_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)

    entries: Mapped[list[QueueEntry]] = relationship(
        back_populates="session", cascade="all, delete-orphan"
    )


class QueueEntry(TimestampMixin, Base):
    __tablename__ = "queue_entries"
    __table_args__ = (
        UniqueConstraint("session_id", "token_number", name="uq_session_token"),
        UniqueConstraint("appointment_id", name="uq_entry_appointment"),
    )

    session_id: Mapped[int] = mapped_column(
        ForeignKey("queue_sessions.id", ondelete="CASCADE"), index=True
    )
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"), index=True
    )
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)

    token_number: Mapped[int] = mapped_column(Integer, index=True)
    #: Live ordering, rewritten whenever the queue is re-planned.
    position: Mapped[int] = mapped_column(Integer, default=0, index=True)
    priority_tier: Mapped[int] = mapped_column(Integer, default=0, index=True)

    status: Mapped[str] = mapped_column(
        String(20), default=QueueEntryStatus.WAITING, index=True
    )

    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    called_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    predicted_duration: Mapped[float] = mapped_column(Float, default=10.0)
    #: Minutes from now until this patient is expected to be called.
    estimated_wait_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    skip_count: Mapped[int] = mapped_column(Integer, default=0)
    note: Mapped[str] = mapped_column(Text, default="")

    session: Mapped[QueueSession] = relationship(back_populates="entries")
