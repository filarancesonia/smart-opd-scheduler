"""Room 6 endpoints — outbox, dispatch worker hooks, and reminder sweeps."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.deps import DbSession, get_current_user, require_roles
from app.core.security import Role
from app.modules.booking import service as booking_service
from app.modules.notifications import service
from app.modules.notifications.models import Channel, NotificationStatus
from app.modules.notifications.schemas import (
    DispatchResult,
    NotificationOut,
    ReminderSweepResult,
    SendTestRequest,
)

router = APIRouter(prefix="/notifications", tags=["Room 6 - Notifications"])

AdminOnly = Depends(require_roles(Role.ADMIN))
StaffOrAdmin = Depends(require_roles(Role.ADMIN, Role.STAFF))


@router.get("/", response_model=list[NotificationOut], dependencies=[StaffOrAdmin])
def list_notifications(
    db: DbSession,
    notification_status: NotificationStatus | None = None,
    channel: Channel | None = None,
    appointment_id: int | None = None,
    limit: int = 200,
) -> list[NotificationOut]:
    """The outbox — every message, whether it was delivered or not."""
    rows = service.list_notifications(
        db,
        status=notification_status,
        channel=channel,
        appointment_id=appointment_id,
        limit=limit,
    )
    return [NotificationOut.model_validate(n) for n in rows]


@router.get("/me", response_model=list[NotificationOut])
def my_notifications(
    db: DbSession, user=Depends(get_current_user), limit: int = 50
) -> list[NotificationOut]:
    patient = booking_service.patient_for_user(db, user)
    rows = service.list_notifications(db, patient_id=patient.id, limit=limit)
    return [NotificationOut.model_validate(n) for n in rows]


@router.post("/dispatch", response_model=DispatchResult, dependencies=[AdminOnly])
def dispatch(db: DbSession, limit: int = 200) -> DispatchResult:
    """Send everything that is due. Driven by a scheduled worker in production."""
    return DispatchResult(**service.process_due(db, limit))


@router.post(
    "/sweep-reminders", response_model=ReminderSweepResult, dependencies=[AdminOnly]
)
def sweep_reminders(db: DbSession) -> ReminderSweepResult:
    """Queue tomorrow's day-before reminders."""
    return ReminderSweepResult(
        day_before_queued=service.sweep_day_before_reminders(db),
        turn_soon_queued=0,
    )


@router.post("/doctors/{doctor_id}/turn-soon", dependencies=[StaffOrAdmin])
def sweep_turn_soon(doctor_id: int, db: DbSession, threshold: int = 20) -> dict:
    """Warn everyone in this queue whose turn is within `threshold` minutes."""
    return {"queued": service.sweep_turn_soon(db, doctor_id, threshold)}


@router.post("/test", response_model=NotificationOut, dependencies=[AdminOnly])
def send_test(payload: SendTestRequest, db: DbSession) -> NotificationOut:
    """Prove the pipeline end to end without touching a real appointment."""
    import uuid

    notification = service.queue(
        db,
        template_code=str(payload.template_code),
        context=payload.context,
        recipient=payload.recipient,
        dedupe_key=f"test:{uuid.uuid4().hex}",
        channel=payload.channel,
        language=payload.language,
    )
    service.dispatch(db, notification)
    db.refresh(notification)
    return NotificationOut.model_validate(notification)
