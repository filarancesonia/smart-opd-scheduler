"""Room 2 request/response shapes."""

from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.doctors.models import CredentialType, LeaveStatus, LeaveType

DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


# --- departments -----------------------------------------------------------


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    code: str = Field(min_length=2, max_length=20)
    floor: str | None = None


class DepartmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    code: str
    floor: str | None
    is_active: bool


# --- doctors ---------------------------------------------------------------


class DoctorCreate(BaseModel):
    user_id: int
    department_id: int
    registration_no: str = Field(min_length=3, max_length=50)
    qualification: str = ""
    specialisation: str = ""
    designation: str = ""
    avg_consultation_minutes: int = Field(default=10, ge=1, le=180)
    max_patients_per_day: int = Field(default=60, ge=1, le=500)


class DoctorUpdate(BaseModel):
    department_id: int | None = None
    qualification: str | None = None
    specialisation: str | None = None
    designation: str | None = None
    avg_consultation_minutes: int | None = Field(default=None, ge=1, le=180)
    max_patients_per_day: int | None = Field(default=None, ge=1, le=500)
    is_accepting_patients: bool | None = None


class DoctorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    department_id: int
    registration_no: str
    qualification: str
    specialisation: str
    designation: str
    avg_consultation_minutes: int
    max_patients_per_day: int
    is_accepting_patients: bool

    full_name: str | None = None
    department_name: str | None = None


# --- duty slots ------------------------------------------------------------


class DutySlotCreate(BaseModel):
    day_of_week: int = Field(ge=0, le=6, description="Monday = 0")
    start_time: time
    end_time: time
    room: str = Field(min_length=1, max_length=50)
    valid_from: date
    valid_to: date | None = None

    @model_validator(mode="after")
    def _check_order(self) -> DutySlotCreate:
        if self.end_time <= self.start_time:
            raise ValueError("end_time must be after start_time")
        if self.valid_to and self.valid_to < self.valid_from:
            raise ValueError("valid_to must not be before valid_from")
        return self


class DutySlotOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    day_of_week: int
    start_time: time
    end_time: time
    room: str
    valid_from: date
    valid_to: date | None
    is_active: bool

    @property
    def day_name(self) -> str:
        return DAY_NAMES[self.day_of_week]


# --- leaves ----------------------------------------------------------------


class LeaveCreate(BaseModel):
    leave_type: LeaveType = LeaveType.CASUAL
    start_date: date
    end_date: date
    reason: str = ""

    @model_validator(mode="after")
    def _check_range(self) -> LeaveCreate:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class LeaveDecision(BaseModel):
    status: LeaveStatus


class LeaveOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    leave_type: str
    status: str
    start_date: date
    end_date: date
    reason: str
    approved_by_user_id: int | None


# --- credentials -----------------------------------------------------------


class CredentialCreate(BaseModel):
    credential_type: CredentialType
    # Raw tag id / beacon id / face-template digest. Never persisted as given.
    raw_value: str = Field(min_length=4, max_length=512)
    label: str = ""


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    credential_type: str
    label: str
    is_active: bool
    # `fingerprint` is deliberately absent — it never leaves the server.


# --- availability ----------------------------------------------------------


class DutyWindow(BaseModel):
    """One concrete stretch of duty on a real calendar date."""

    doctor_id: int
    date: date
    start_time: time
    end_time: time
    room: str
    duration_minutes: int


class DayAvailability(BaseModel):
    doctor_id: int
    date: date
    is_on_leave: bool
    leave_type: str | None = None
    windows: list[DutyWindow] = []
    total_minutes: int = 0
    # Room 4 caps its plan at this many patients.
    capacity_estimate: int = 0
