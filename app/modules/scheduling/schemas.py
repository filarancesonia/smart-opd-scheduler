"""Room 4 request/response shapes."""

from __future__ import annotations

from datetime import date, datetime, time

from pydantic import BaseModel, ConfigDict, Field


class PredictionOut(BaseModel):
    value: float
    source: str = Field(description="'model' if learned, 'heuristic' if cold start")
    detail: str = ""


class AppointmentPrediction(BaseModel):
    appointment_id: int
    patient_name: str
    predicted_duration_minutes: PredictionOut
    no_show_probability: PredictionOut


class AssignmentOut(BaseModel):
    appointment_id: int
    patient_id: int
    patient_name: str
    position: int
    booked_start: time
    predicted_start: time
    predicted_end: time
    expected_wait_minutes: float
    expected_duration: float
    no_show_probability: float
    priority_tier: int
    overruns_session: bool
    promoted_for_fairness: bool


class OptimisationOut(BaseModel):
    doctor_id: int
    doctor_name: str | None = None
    plan_date: date
    available_from: time
    available_until: time
    session_minutes: int

    assignments: list[AssignmentOut] = []

    total_expected_wait: float
    average_wait: float
    baseline_wait: float
    baseline_average_wait: float
    improvement_pct: float

    expected_no_shows: float
    recommended_overbooking: int
    projected_overrun_minutes: float
    notes: list[str] = []

    #: True when the doctor's window came from live Room 1 presence rather
    #: than from the printed timetable.
    used_live_presence: bool = False
    engine: dict = {}


class OptimiseRequest(BaseModel):
    plan_date: date | None = None
    # Override the session window; otherwise taken from presence and roster.
    available_from: time | None = None
    available_until: time | None = None
    save: bool = False


class TrainRequest(BaseModel):
    synthetic_rows: int = Field(default=0, ge=0, le=50_000)


class EngineStatus(BaseModel):
    duration: dict
    no_show: dict
    training_rows: int
    synthetic_rows: int


class SchedulePlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    doctor_id: int
    plan_date: date
    generated_at: datetime
    available_from: time
    available_until: time
    total_expected_wait: float
    baseline_wait: float
    improvement_pct: float
