"""Room 6 logic: queue, render, deliver, retry, audit."""

from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import utcnow
from app.core.errors import NotFoundError
from app.core.timeutil import as_utc, combine_local, local_today, to_local
from app.modules.booking.models import Appointment, AppointmentStatus, Patient
from app.modules.doctors.models import Doctor
from app.modules.identity.models import User
from app.modules.notifications.models import (
    Channel,
    Notification,
    NotificationStatus,
    TemplateCode,
)
from app.modules.notifications.providers import get_provider
from app.modules.notifications.templates import flatten_for_voice, render

#: Give up after this many delivery attempts.
MAX_ATTEMPTS = 3

#: Warn a patient when their turn is this close.
TURN_SOON_MINUTES = 20


# --- queueing --------------------------------------------------------------


def queue(
    db: Session,
    *,
    template_code: str,
    context: dict,
    recipient: str,
    dedupe_key: str,
    channel: Channel = Channel.SMS,
    language: str = "hi",
    patient_id: int | None = None,
    user_id: int | None = None,
    appointment_id: int | None = None,
    scheduled_for: datetime | None = None,
) -> Notification:
    """Render and queue one message. Idempotent on ``dedupe_key``."""
    existing = db.execute(
        select(Notification).where(Notification.dedupe_key == dedupe_key)
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    body = render(template_code, language, context)
    if channel == Channel.VOICE:
        body = flatten_for_voice(body)

    notification = Notification(
        dedupe_key=dedupe_key,
        patient_id=patient_id,
        user_id=user_id,
        appointment_id=appointment_id,
        channel=str(channel),
        template_code=str(template_code),
        language=language,
        recipient=recipient,
        body=body,
        status=str(NotificationStatus.QUEUED),
        scheduled_for=scheduled_for or utcnow(),
        provider=get_provider(str(channel)).name,
    )
    db.add(notification)
    db.commit()
    db.refresh(notification)
    return notification


# --- delivery --------------------------------------------------------------


def dispatch(db: Session, notification: Notification) -> bool:
    """Attempt delivery once, recording the outcome either way."""
    if notification.status in (
        NotificationStatus.SENT,
        NotificationStatus.DELIVERED,
        NotificationStatus.CANCELLED,
    ):
        return True

    provider = get_provider(notification.channel)
    notification.attempts += 1
    notification.provider = provider.name

    result = provider.send(notification.recipient, notification.body)
    if result.ok:
        notification.status = str(NotificationStatus.SENT)
        notification.sent_at = utcnow()
        notification.provider_message_id = result.provider_message_id
        notification.last_error = ""
    else:
        notification.last_error = result.error
        if notification.attempts >= MAX_ATTEMPTS:
            notification.status = str(NotificationStatus.FAILED)
        else:
            # Stay queued and back off, so a transient gateway outage does not
            # silently drop everyone's reminders.
            notification.scheduled_for = utcnow() + timedelta(
                minutes=5 * notification.attempts
            )

    db.commit()
    db.refresh(notification)
    return result.ok


def process_due(db: Session, limit: int = 200) -> dict[str, int]:
    """Send everything that is due. Called by a scheduled worker."""
    now = utcnow()
    due = list(
        db.execute(
            select(Notification)
            .where(
                Notification.status == NotificationStatus.QUEUED,
                Notification.scheduled_for <= now,
            )
            .order_by(Notification.scheduled_for)
            .limit(limit)
        ).scalars()
    )

    sent = failed = 0
    for notification in due:
        if dispatch(db, notification):
            sent += 1
        elif notification.status == NotificationStatus.FAILED:
            failed += 1

    return {
        "processed": len(due),
        "sent": sent,
        "failed": failed,
        "skipped": len(due) - sent - failed,
    }


# --- context helpers -------------------------------------------------------


def _appointment_context(db: Session, appointment: Appointment) -> dict:
    doctor = db.get(Doctor, appointment.doctor_id)
    doctor_user = db.get(User, doctor.user_id) if doctor else None
    return {
        "doctor_name": doctor_user.full_name if doctor_user else "the doctor",
        "date": appointment.appointment_date.strftime("%d-%m-%Y"),
        "time": appointment.slot_start.strftime("%H:%M"),
        "room": appointment.room or "—",
        "reference": appointment.booking_reference,
    }


def _recipient(db: Session, appointment: Appointment) -> tuple[Patient | None, str, str]:
    patient = db.get(Patient, appointment.patient_id)
    phone = patient.phone if patient else ""
    language = patient.preferred_language if patient else "hi"
    return patient, phone, language


# --- event triggers --------------------------------------------------------


def notify_booking_confirmed(db: Session, appointment: Appointment) -> Notification | None:
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    return queue(
        db,
        template_code=TemplateCode.BOOKING_CONFIRMED,
        context=_appointment_context(db, appointment),
        recipient=phone,
        dedupe_key=f"booking_confirmed:{appointment.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
    )


def notify_cancelled(
    db: Session, appointment: Appointment, reason: str = ""
) -> Notification | None:
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    context = _appointment_context(db, appointment)
    context["reason"] = reason or ("रद्द" if language == "hi" else "cancelled")
    return queue(
        db,
        template_code=TemplateCode.APPOINTMENT_CANCELLED,
        context=context,
        recipient=phone,
        dedupe_key=f"cancelled:{appointment.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
    )


def notify_rescheduled(db: Session, appointment: Appointment) -> Notification | None:
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    return queue(
        db,
        template_code=TemplateCode.APPOINTMENT_RESCHEDULED,
        context=_appointment_context(db, appointment),
        recipient=phone,
        dedupe_key=f"rescheduled:{appointment.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
    )


def notify_doctor_unavailable(
    db: Session, appointment: Appointment
) -> Notification | None:
    """The message that matters most: told before leaving home, not after."""
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    return queue(
        db,
        template_code=TemplateCode.DOCTOR_UNAVAILABLE,
        context=_appointment_context(db, appointment),
        recipient=phone,
        dedupe_key=f"doctor_unavailable:{appointment.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=appointment.patient_id,
        appointment_id=appointment.id,
    )


def notify_turn_soon(db: Session, entry, minutes: int) -> Notification | None:
    appointment = db.get(Appointment, entry.appointment_id)
    if appointment is None:
        return None
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    return queue(
        db,
        template_code=TemplateCode.TURN_SOON,
        context={
            "minutes": minutes,
            "room": appointment.room or "—",
            "token": entry.token_number,
        },
        recipient=phone,
        dedupe_key=f"turn_soon:{entry.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=entry.patient_id,
        appointment_id=appointment.id,
    )


def notify_now_calling(db: Session, entry) -> Notification | None:
    appointment = db.get(Appointment, entry.appointment_id)
    if appointment is None:
        return None
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    return queue(
        db,
        template_code=TemplateCode.NOW_CALLING,
        context={"token": entry.token_number, "room": appointment.room or "—"},
        recipient=phone,
        dedupe_key=f"now_calling:{entry.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=entry.patient_id,
        appointment_id=appointment.id,
    )


def notify_doctor_delayed(db: Session, entry, minutes: int) -> Notification | None:
    appointment = db.get(Appointment, entry.appointment_id)
    if appointment is None:
        return None
    patient, phone, language = _recipient(db, appointment)
    if not phone:
        return None
    doctor = db.get(Doctor, appointment.doctor_id)
    doctor_user = db.get(User, doctor.user_id) if doctor else None
    return queue(
        db,
        template_code=TemplateCode.DOCTOR_DELAYED,
        context={
            "doctor_name": doctor_user.full_name if doctor_user else "the doctor",
            "minutes": minutes,
            "token": entry.token_number,
        },
        recipient=phone,
        dedupe_key=f"doctor_delayed:{entry.id}",
        channel=Channel.SMS,
        language=language,
        patient_id=entry.patient_id,
        appointment_id=appointment.id,
    )


# --- sweeps ----------------------------------------------------------------


def sweep_day_before_reminders(db: Session) -> int:
    """Queue tomorrow's reminders. Run once a day by a scheduled job."""
    tomorrow = local_today() + timedelta(days=1)
    appointments = list(
        db.execute(
            select(Appointment).where(
                Appointment.appointment_date == tomorrow,
                Appointment.status == AppointmentStatus.BOOKED,
            )
        ).scalars()
    )

    queued = 0
    for appointment in appointments:
        patient, phone, language = _recipient(db, appointment)
        if not phone:
            continue
        # Sent at 18:00 local the evening before — late enough that the day is
        # settled, early enough to still rearrange things.
        send_at = combine_local(local_today(), _evening())
        before = db.execute(
            select(Notification).where(
                Notification.dedupe_key == f"reminder:{appointment.id}"
            )
        ).scalar_one_or_none()
        if before is not None:
            continue
        queue(
            db,
            template_code=TemplateCode.REMINDER_DAY_BEFORE,
            context=_appointment_context(db, appointment),
            recipient=phone,
            dedupe_key=f"reminder:{appointment.id}",
            channel=Channel.SMS,
            language=language,
            patient_id=appointment.patient_id,
            appointment_id=appointment.id,
            scheduled_for=as_utc(send_at),
        )
        queued += 1
    return queued


def _evening():
    from datetime import time

    return time(18, 0)


def sweep_turn_soon(
    db: Session, doctor_id: int, threshold_minutes: int = TURN_SOON_MINUTES
) -> int:
    """Tell everyone whose turn is close that they should start walking over."""
    from app.modules.queue import service as queue_service
    from app.modules.queue.models import QueueEntryStatus

    session = queue_service.get_session(db, doctor_id)
    if session is None:
        return 0

    queued = 0
    for entry in queue_service.open_entries(db, session.id):
        if entry.status != QueueEntryStatus.WAITING:
            continue
        wait = entry.estimated_wait_minutes
        if wait is None or wait > threshold_minutes:
            continue
        if notify_turn_soon(db, entry, wait) is not None:
            queued += 1
    return queued


# --- reads -----------------------------------------------------------------


def list_notifications(
    db: Session,
    *,
    status: NotificationStatus | None = None,
    channel: Channel | None = None,
    appointment_id: int | None = None,
    patient_id: int | None = None,
    limit: int = 200,
) -> list[Notification]:
    stmt = select(Notification).order_by(Notification.created_at.desc())
    if status is not None:
        stmt = stmt.where(Notification.status == str(status))
    if channel is not None:
        stmt = stmt.where(Notification.channel == str(channel))
    if appointment_id is not None:
        stmt = stmt.where(Notification.appointment_id == appointment_id)
    if patient_id is not None:
        stmt = stmt.where(Notification.patient_id == patient_id)
    return list(db.execute(stmt.limit(limit)).scalars())


def get_notification(db: Session, notification_id: int) -> Notification:
    notification = db.get(Notification, notification_id)
    if notification is None:
        raise NotFoundError("Notification not found")
    return notification


def cancel_pending_for_appointment(db: Session, appointment_id: int) -> int:
    """Stop reminders for an appointment that no longer exists.

    Texting someone a reminder for a visit they cancelled is how a system
    teaches people to ignore it.
    """
    pending = list(
        db.execute(
            select(Notification).where(
                Notification.appointment_id == appointment_id,
                Notification.status == NotificationStatus.QUEUED,
            )
        ).scalars()
    )
    for notification in pending:
        notification.status = str(NotificationStatus.CANCELLED)
    db.commit()
    return len(pending)
