"""Room 3 request/response shapes."""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.modules.booking.models import AppointmentStatus, BookingChannel, Gender
from app.modules.identity.schemas import normalise_phone


# --- patients --------------------------------------------------------------


class PatientCreate(BaseModel):
    full_name: str = Field(min_length=2, max_length=150)
    phone: str
    age: int | None = Field(default=None, ge=0, le=130)
    gender: Gender | None = None
    address: str = ""
    abha_id: str | None = None
    preferred_language: str = Field(default="hi", max_length=5)
    is_pregnant: bool = False
    has_disability: bool = False

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)


class PatientOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    full_name: str
    phone: str
    age: int | None
    gender: str | None
    abha_id: str | None
    preferred_language: str
    is_pregnant: bool
    has_disability: bool
    is_senior_citizen: bool


# --- slots -----------------------------------------------------------------


class SlotOut(BaseModel):
    start: time
    end: time
    room: str
    available: bool


class DaySlots(BaseModel):
    doctor_id: int
    date: date
    is_on_leave: bool
    capacity: int
    booked: int
    remaining: int
    slots: list[SlotOut] = []
    # Set when Room 1 says the doctor is not where the roster expects them.
    presence_warning: str | None = None


# --- appointments ----------------------------------------------------------


class AppointmentCreate(BaseModel):
    doctor_id: int
    appointment_date: date
    reason: str = ""
    is_follow_up: bool = False
    # Omit to take the next free slot; supply to request a specific one.
    preferred_start: time | None = None


class BookForPatient(AppointmentCreate):
    """Kiosk and reception book on behalf of someone who has no account."""

    patient: PatientCreate


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    booking_reference: str
    patient_id: int
    doctor_id: int
    department_id: int
    appointment_date: date
    slot_start: time
    slot_end: time
    room: str
    status: str
    channel: str
    reason: str
    is_follow_up: bool
    checked_in_at: datetime | None

    patient_name: str | None = None
    doctor_name: str | None = None
    department_name: str | None = None


class CancelRequest(BaseModel):
    reason: str = ""


class RescheduleRequest(BaseModel):
    appointment_date: date
    preferred_start: time | None = None


# --- kiosk -----------------------------------------------------------------


class KioskLookup(BaseModel):
    phone: str

    @field_validator("phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)


class KioskTicket(BaseModel):
    """What the kiosk prints on the slip."""

    booking_reference: str
    patient_name: str
    doctor_name: str
    department_name: str
    room: str
    appointment_date: date
    slot_start: time
    message_hi: str
    message_en: str


# --- IVR -------------------------------------------------------------------


class IVRStart(BaseModel):
    caller_phone: str
    language: str = "hi"

    @field_validator("caller_phone")
    @classmethod
    def _phone(cls, v: str) -> str:
        return normalise_phone(v)


class IVRInput(BaseModel):
    session_id: str
    # A single keypress from the phone keypad.
    digits: str = Field(min_length=1, max_length=12)


class IVROption(BaseModel):
    key: str
    label_hi: str
    label_en: str


class IVRPrompt(BaseModel):
    """One turn of the call: what to read out and what to accept next."""

    session_id: str
    state: str
    prompt_hi: str
    prompt_en: str
    options: list[IVROption] = []
    expects_input: bool = True
    call_complete: bool = False
    booking_reference: str | None = None


# --- filters ---------------------------------------------------------------


class AppointmentFilter(BaseModel):
    doctor_id: int | None = None
    patient_id: int | None = None
    on_date: date | None = None
    status: AppointmentStatus | None = None
    channel: BookingChannel | None = None
