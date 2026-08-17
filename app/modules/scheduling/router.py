"""Room 4 endpoints — predictions, optimisation and model management."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.core.deps import DbSession, get_current_user, require_roles
from app.core.security import Role
from app.modules.doctors import service as doctors_service
from app.modules.scheduling import service
from app.modules.scheduling.schemas import (
    AppointmentPrediction,
    EngineStatus,
    OptimisationOut,
    OptimiseRequest,
    SchedulePlanOut,
    TrainRequest,
)
from app.modules.scheduling.train import generate_synthetic, train_all

router = APIRouter(prefix="/scheduling", tags=["Room 4 - AI Scheduling Engine"])

AdminOnly = Depends(require_roles(Role.ADMIN))
ClinicStaff = Depends(require_roles(Role.ADMIN, Role.STAFF, Role.DOCTOR))


@router.get("/status", response_model=EngineStatus)
def engine_status(db: DbSession, _=Depends(get_current_user)) -> EngineStatus:
    """Whether each model is trained or still on its heuristic fallback."""
    return service.engine_status(db)


@router.get(
    "/appointments/{appointment_id}/prediction",
    response_model=AppointmentPrediction,
    dependencies=[ClinicStaff],
)
def predict(appointment_id: int, db: DbSession) -> AppointmentPrediction:
    """Predicted consultation length and no-show probability, with provenance."""
    return service.predict_for_appointment(db, appointment_id)


@router.post(
    "/doctors/{doctor_id}/optimise",
    response_model=OptimisationOut,
    dependencies=[ClinicStaff],
)
def optimise(
    doctor_id: int, payload: OptimiseRequest, db: DbSession
) -> OptimisationOut:
    """Re-plan a doctor's session to minimise total patient waiting time."""
    return service.optimise_day(
        db,
        doctor_id,
        payload.plan_date,
        available_from=payload.available_from,
        available_until=payload.available_until,
        save=payload.save,
    )


@router.get(
    "/doctors/{doctor_id}/optimise",
    response_model=OptimisationOut,
    dependencies=[ClinicStaff],
)
def preview_optimisation(
    doctor_id: int,
    db: DbSession,
    on_date: date | None = Query(default=None, alias="date"),
) -> OptimisationOut:
    """Read-only preview using the live presence window."""
    return service.optimise_day(db, doctor_id, on_date)


@router.get(
    "/doctors/{doctor_id}/plans",
    response_model=list[SchedulePlanOut],
    dependencies=[ClinicStaff],
)
def list_plans(doctor_id: int, db: DbSession, limit: int = 20) -> list[SchedulePlanOut]:
    doctors_service.get_doctor(db, doctor_id)
    return [
        SchedulePlanOut.model_validate(p) for p in service.list_plans(db, doctor_id, limit)
    ]


@router.post("/train", dependencies=[AdminOnly])
def train(payload: TrainRequest, db: DbSession) -> dict:
    """Retrain both models, optionally seeding synthetic history first.

    Synthetic rows exist so a fresh deployment has something to demonstrate;
    anything trained on them is flagged all the way through to /status.
    """
    generated = 0
    if payload.synthetic_rows:
        generated = generate_synthetic(db, payload.synthetic_rows)
    return {"synthetic_rows_generated": generated, **train_all(db)}
