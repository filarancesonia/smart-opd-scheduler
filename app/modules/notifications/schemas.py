"""Room 6 request/response shapes."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.modules.notifications.models import Channel, NotificationStatus, TemplateCode


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    patient_id: int | None
    user_id: int | None
    appointment_id: int | None
    channel: str
    template_code: str
    language: str
    recipient: str
    body: str
    status: str
    scheduled_for: datetime
    sent_at: datetime | None
    attempts: int
    provider: str
    last_error: str


class SendTestRequest(BaseModel):
    """Lets an administrator prove the pipeline works end to end."""

    channel: Channel = Channel.SMS
    template_code: TemplateCode = TemplateCode.BOOKING_CONFIRMED
    language: str = "hi"
    recipient: str
    context: dict = Field(default_factory=dict)


class DispatchResult(BaseModel):
    processed: int
    sent: int
    failed: int
    skipped: int


class ReminderSweepResult(BaseModel):
    day_before_queued: int
    turn_soon_queued: int


class NotificationFilter(BaseModel):
    status: NotificationStatus | None = None
    channel: Channel | None = None
    appointment_id: int | None = None
