"""Central model registry.

SQLAlchemy only knows about a table once its class has been imported. Every
module's models are re-exported here so ``Base.metadata`` is complete before
``create_all`` runs. Each new room appends its import below.
"""

from app.modules.booking.models import (
    Appointment,
    IVRSession,
    Patient,
)
from app.modules.doctors.models import (
    Department,
    Doctor,
    DoctorCredential,
    DutySlot,
    Leave,
)
from app.modules.identity.models import User
from app.modules.queue.models import QueueEntry, QueueSession
from app.modules.scheduling.models import (
    ConsultationRecord,
    ModelArtifact,
    SchedulePlan,
)
from app.modules.presence.models import (
    Device,
    PresenceEvent,
    PresenceSignal,
    PresenceState,
)

__all__ = [
    "User",
    "Department",
    "Doctor",
    "DutySlot",
    "Leave",
    "DoctorCredential",
    "Device",
    "PresenceSignal",
    "PresenceState",
    "PresenceEvent",
    "Patient",
    "Appointment",
    "IVRSession",
    "ConsultationRecord",
    "ModelArtifact",
    "SchedulePlan",
    "QueueSession",
    "QueueEntry",
]
