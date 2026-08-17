"""Room 1 endpoints.

Door hardware authenticates with a provisioned device key (readers in corridors
cannot hold user credentials); everything else uses a normal JWT.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, status

from app.core.deps import DbSession, get_current_user, require_roles, verify_device_key
from app.core.security import Role
from app.modules.presence import service
from app.modules.presence.models import PresenceStatus
from app.modules.presence.schemas import (
    BatchResult,
    DeviceCreate,
    DeviceOut,
    ManualPresence,
    PresenceEventOut,
    PresenceOut,
    SignalBatch,
    SignalIn,
    SignalResult,
)

router = APIRouter(prefix="/presence", tags=["Room 1 - Presence Detection"])

AdminOnly = Depends(require_roles(Role.ADMIN))
DeviceAuth = Depends(verify_device_key)


# --- device registry -------------------------------------------------------


@router.post(
    "/devices",
    response_model=DeviceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminOnly],
)
def register_device(payload: DeviceCreate, db: DbSession) -> DeviceOut:
    return DeviceOut.model_validate(service.register_device(db, payload))


@router.get("/devices", response_model=list[DeviceOut], dependencies=[AdminOnly])
def list_devices(db: DbSession, include_inactive: bool = False) -> list[DeviceOut]:
    devices = service.list_devices(db, active_only=not include_inactive)
    return [DeviceOut.model_validate(d) for d in devices]


@router.delete(
    "/devices/{device_id}", response_model=DeviceOut, dependencies=[AdminOnly]
)
def deactivate_device(device_id: int, db: DbSession) -> DeviceOut:
    return DeviceOut.model_validate(service.deactivate_device(db, device_id))


# --- signal ingest ---------------------------------------------------------


@router.post("/signals", response_model=SignalResult, dependencies=[DeviceAuth])
def ingest_signal(payload: SignalIn, db: DbSession) -> SignalResult:
    """A single observation from a door reader."""
    return service.ingest_signal(db, payload)


@router.post("/signals/batch", response_model=BatchResult, dependencies=[DeviceAuth])
def ingest_batch(payload: SignalBatch, db: DbSession) -> BatchResult:
    """A backlog flushed by a reader after its network link returned."""
    # Order matters: replaying oldest-first keeps the fused state coherent.
    ordered = sorted(
        payload.signals, key=lambda s: (s.observed_at is None, s.observed_at)
    )
    results = [service.ingest_signal(db, signal) for signal in ordered]
    matched = sum(1 for r in results if r.matched)
    return BatchResult(
        received=len(results),
        matched=matched,
        unmatched=len(results) - matched,
        results=results,
    )


@router.post("/manual", response_model=PresenceOut)
def set_manual_presence(
    payload: ManualPresence,
    db: DbSession,
    user=Depends(require_roles(Role.ADMIN, Role.STAFF)),
) -> PresenceOut:
    """Reception marks presence by hand when a reader fails."""
    service.set_manual_presence(db, payload, user.id)
    return service.get_presence(db, payload.doctor_id)


# --- reading presence ------------------------------------------------------


@router.get("/live", response_model=list[PresenceOut])
def live_board(
    db: DbSession,
    department_id: int | None = None,
    presence_status: PresenceStatus | None = None,
    _=Depends(get_current_user),
) -> list[PresenceOut]:
    """Who is actually in the building right now, with roster deviations."""
    return service.list_presence(
        db, department_id=department_id, status=presence_status
    )


@router.get("/doctors/{doctor_id}", response_model=PresenceOut)
def get_presence(
    doctor_id: int, db: DbSession, _=Depends(get_current_user)
) -> PresenceOut:
    return service.get_presence(db, doctor_id)


@router.get("/doctors/{doctor_id}/events", response_model=list[PresenceEventOut])
def list_events(
    doctor_id: int, db: DbSession, limit: int = 100, _=Depends(get_current_user)
) -> list[PresenceEventOut]:
    return [
        PresenceEventOut.model_validate(e)
        for e in service.list_events(db, doctor_id, limit=limit)
    ]


@router.post("/sweep", dependencies=[AdminOnly])
def sweep_stale(db: DbSession) -> dict[str, int]:
    """Demote presences with no recent signal. Called by a scheduled job."""
    return {"demoted": service.sweep_stale(db)}


@router.get("/unmatched", dependencies=[AdminOnly])
def unmatched_signals(db: DbSession, limit: int = 50) -> list[dict]:
    """Unrecognised credentials presented at doors, for security review."""
    return [
        {
            "id": s.id,
            "device_id": s.device_id,
            "credential_type": s.credential_type,
            "room": s.room,
            "observed_at": s.observed_at,
        }
        for s in service.recent_unmatched(db, limit=limit)
    ]
