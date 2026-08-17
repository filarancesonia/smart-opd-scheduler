"""Room 5 request/response shapes."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class OpenQueueRequest(BaseModel):
    session_date: date | None = None
    room: str = ""


class JoinRequest(BaseModel):
    appointment_id: int


class QueueEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    appointment_id: int
    patient_id: int
    patient_name: str | None = None
    token_number: int
    position: int
    priority_tier: int
    status: str
    joined_at: datetime
    called_at: datetime | None
    started_at: datetime | None
    completed_at: datetime | None
    predicted_duration: float
    estimated_wait_minutes: int | None
    skip_count: int
    note: str


class QueueOut(BaseModel):
    session_id: int
    doctor_id: int
    doctor_name: str | None = None
    session_date: date
    room: str
    is_open: bool
    doctor_present: bool
    waiting_count: int
    completed_count: int
    observed_avg_minutes: float | None
    now_serving: int | None = None
    entries: list[QueueEntryOut] = []


class BoardRow(BaseModel):
    """One line on a corridor display.

    Names are reduced to initials: a public screen in a government hospital
    corridor should not broadcast who is attending which clinic.
    """

    token_number: int
    display_name: str
    status: str
    estimated_wait_minutes: int | None
    is_priority: bool


class DisplayBoard(BaseModel):
    doctor_id: int
    doctor_name: str | None
    room: str
    doctor_present: bool
    status_line_hi: str
    status_line_en: str
    now_serving: int | None
    next_tokens: list[BoardRow] = []
    updated_at: datetime


class MyPosition(BaseModel):
    """What a patient sees in the app: the honest answer to 'how much longer?'"""

    token_number: int
    position: int
    people_ahead: int
    status: str
    estimated_wait_minutes: int | None
    estimated_call_time: datetime | None
    doctor_present: bool
    message_hi: str
    message_en: str


class CompleteRequest(BaseModel):
    note: str = ""


class ReorderResult(BaseModel):
    reordered: int
    improvement_pct: float
    notes: list[str] = []
    entries: list[QueueEntryOut] = []


class CallNextResult(BaseModel):
    called: QueueEntryOut | None = None
    reason: str | None = None
    remaining_waiting: int = Field(default=0)
