"""Room 6 — templates, delivery, retries, dedupe and the event triggers."""

from datetime import time, timedelta

import pytest

from app.core.config import settings
from app.core.timeutil import local_now, local_today
from app.modules.notifications import providers, service
from app.modules.notifications.models import (
    Channel,
    Notification,
    NotificationStatus,
    TemplateCode,
)
from app.modules.notifications.templates import TEMPLATES, flatten_for_voice, render


@pytest.fixture
def clinic(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 3, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)
    # Every weekday, so both "today" and "tomorrow" bookings work.
    for weekday in range(7):
        resp = client.post(
            f"/api/v1/doctors/{profile['id']}/duty-slots",
            json={
                "day_of_week": weekday,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "room": "OPD 12",
                "valid_from": local_today().isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
    return profile


@pytest.fixture
def patient_headers(client, register_user):
    _, headers = register_user(
        phone="9222200001", role="patient", full_name="Asha Devi"
    )
    return headers


def _book(client, headers, doctor_id, on_date=None):
    resp = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": doctor_id,
            "appointment_date": (on_date or local_today()).isoformat(),
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- templates -------------------------------------------------------------


def test_every_template_exists_in_hindi_and_english():
    for code in TemplateCode:
        assert code in TEMPLATES, f"missing template: {code}"
        assert "hi" in TEMPLATES[code]
        assert "en" in TEMPLATES[code]


def test_render_fills_placeholders():
    text = render(
        TemplateCode.BOOKING_CONFIRMED,
        "en",
        {
            "doctor_name": "Dr. Sharma",
            "date": "18-08-2026",
            "time": "09:30",
            "room": "OPD 12",
            "reference": "OPDABC2345",
        },
    )
    assert "Dr. Sharma" in text
    assert "OPD 12" in text
    assert "OPDABC2345" in text
    assert "{" not in text


def test_render_falls_back_to_english_for_an_unknown_language():
    text = render(TemplateCode.NOW_CALLING, "ta", {"token": 7, "room": "OPD 3"})
    assert "Token 7" in text


def test_render_survives_a_missing_placeholder():
    """A missing value must not lose the whole message."""
    text = render(TemplateCode.NOW_CALLING, "en", {"token": 7})
    assert "7" in text
    assert "{room}" not in text


def test_voice_messages_are_flattened():
    multiline = "Line one\nLine two\n\nLine three"
    assert flatten_for_voice(multiline) == "Line one Line two Line three"


# --- providers -------------------------------------------------------------


def test_console_provider_never_leaks_a_full_phone_number(caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="notifications"):
        result = providers.ConsoleProvider("sms").send("9876543210", "hello")
    assert result.ok is True
    logged = caplog.text
    assert "9876543210" not in logged
    assert "3210" in logged


def test_an_unconfigured_gateway_fails_loudly_rather_than_pretending():
    original = settings.sms_provider
    settings.sms_provider = "twilio"
    try:
        provider = providers.get_provider("sms")
        result = provider.send("9876543210", "hello")
        assert result.ok is False
        assert "not configured" in result.error
    finally:
        settings.sms_provider = original


# --- queueing and delivery -------------------------------------------------


def test_booking_queues_a_confirmation(client, clinic, patient_headers, db_session):
    booked = _book(client, patient_headers, clinic["id"])

    notifications = service.list_notifications(db_session, appointment_id=booked["id"])
    assert len(notifications) == 1
    note = notifications[0]
    assert note.template_code == TemplateCode.BOOKING_CONFIRMED
    assert note.status == NotificationStatus.QUEUED
    assert booked["booking_reference"] in note.body
    # Asha Devi's account defaults to Hindi.
    assert note.language == "hi"
    assert "अपॉइंटमेंट" in note.body


def test_dispatch_sends_queued_messages(client, admin, clinic, patient_headers):
    _, admin_headers = admin
    _book(client, patient_headers, clinic["id"])

    result = client.post("/api/v1/notifications/dispatch", headers=admin_headers).json()
    assert result["processed"] == 1
    assert result["sent"] == 1
    assert result["failed"] == 0

    outbox = client.get("/api/v1/notifications/", headers=admin_headers).json()
    assert outbox[0]["status"] == "sent"
    assert outbox[0]["sent_at"] is not None
    assert outbox[0]["provider"] == "console"


def test_dispatch_is_idempotent(client, admin, clinic, patient_headers):
    _, admin_headers = admin
    _book(client, patient_headers, clinic["id"])
    client.post("/api/v1/notifications/dispatch", headers=admin_headers)
    again = client.post("/api/v1/notifications/dispatch", headers=admin_headers).json()
    assert again["processed"] == 0


def test_duplicate_queueing_is_deduped(db_session):
    first = service.queue(
        db_session,
        template_code=TemplateCode.NOW_CALLING,
        context={"token": 4, "room": "OPD 1"},
        recipient="9876543210",
        dedupe_key="same-key",
    )
    second = service.queue(
        db_session,
        template_code=TemplateCode.NOW_CALLING,
        context={"token": 4, "room": "OPD 1"},
        recipient="9876543210",
        dedupe_key="same-key",
    )
    assert first.id == second.id
    assert db_session.query(Notification).count() == 1


def test_a_failing_gateway_retries_then_gives_up(db_session):
    original = settings.sms_provider
    settings.sms_provider = "twilio"  # selected but unconfigured
    try:
        note = service.queue(
            db_session,
            template_code=TemplateCode.NOW_CALLING,
            context={"token": 1, "room": "OPD 1"},
            recipient="9876543210",
            dedupe_key="retry-test",
        )
        for attempt in range(1, service.MAX_ATTEMPTS + 1):
            note.scheduled_for = note.scheduled_for.replace(year=2020)
            db_session.commit()
            service.dispatch(db_session, note)
            assert note.attempts == attempt

        assert note.status == NotificationStatus.FAILED
        assert "not configured" in note.last_error
    finally:
        settings.sms_provider = original


# --- event triggers --------------------------------------------------------


def test_cancelling_stops_pending_reminders_and_sends_a_notice(
    client, admin, clinic, patient_headers, db_session
):
    _, admin_headers = admin
    booked = _book(client, patient_headers, clinic["id"])

    client.post(
        f"/api/v1/booking/appointments/{booked['id']}/cancel",
        json={"reason": "Feeling better"},
        headers=patient_headers,
    )

    notifications = service.list_notifications(db_session, appointment_id=booked["id"])
    by_code = {n.template_code: n for n in notifications}

    # The confirmation that had not gone out yet is stood down...
    assert by_code[TemplateCode.BOOKING_CONFIRMED].status == NotificationStatus.CANCELLED
    # ...and a cancellation notice takes its place.
    assert TemplateCode.APPOINTMENT_CANCELLED in by_code
    assert "Feeling better" in by_code[TemplateCode.APPOINTMENT_CANCELLED].body


def test_rescheduling_notifies_the_new_details(
    client, clinic, patient_headers, db_session
):
    booked = _book(client, patient_headers, clinic["id"])
    tomorrow = local_today() + timedelta(days=1)
    moved = client.post(
        f"/api/v1/booking/appointments/{booked['id']}/reschedule",
        json={"appointment_date": tomorrow.isoformat()},
        headers=patient_headers,
    ).json()

    notifications = service.list_notifications(db_session, appointment_id=moved["id"])
    reschedule = next(
        n for n in notifications if n.template_code == TemplateCode.APPOINTMENT_RESCHEDULED
    )
    assert moved["booking_reference"] in reschedule.body


def test_calling_a_patient_texts_them_their_room(
    client, admin, clinic, patient_headers, db_session
):
    _, admin_headers = admin
    booked = _book(client, patient_headers, clinic["id"])

    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=admin_headers
    )
    client.post(
        "/api/v1/queue/join",
        json={"appointment_id": booked["id"]},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/queue/doctors/{clinic['id']}/call-next", headers=admin_headers
    )

    notifications = service.list_notifications(db_session, appointment_id=booked["id"])
    calling = next(
        n for n in notifications if n.template_code == TemplateCode.NOW_CALLING
    )
    assert "OPD 12" in calling.body


def test_turn_soon_sweep_warns_only_those_who_are_close(
    client, admin, clinic, db_session
):
    _, admin_headers = admin
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import AppointmentCreate, PatientCreate

    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=admin_headers
    )

    for i in range(6):
        patient = booking_service.create_patient(
            db_session,
            PatientCreate(full_name=f"Patient {i}", phone=f"922220010{i}"),
        )
        appointment = booking_service.book(
            db_session,
            patient_id=patient.id,
            payload=AppointmentCreate(
                doctor_id=clinic["id"], appointment_date=local_today()
            ),
            channel="website",
        )
        client.post(
            "/api/v1/queue/join",
            json={"appointment_id": appointment.id},
            headers=admin_headers,
        )

    result = client.post(
        f"/api/v1/notifications/doctors/{clinic['id']}/turn-soon?threshold=20",
        headers=admin_headers,
    ).json()

    warned = service.list_notifications(db_session)
    turn_soon = [n for n in warned if n.template_code == TemplateCode.TURN_SOON]
    assert result["queued"] == len(turn_soon)
    # Not everyone: only those whose estimate is inside the window.
    assert 0 < len(turn_soon) <= 6


def test_day_before_reminder_sweep(client, admin, clinic, patient_headers, db_session):
    _, admin_headers = admin
    tomorrow = local_today() + timedelta(days=1)
    _book(client, patient_headers, clinic["id"], on_date=tomorrow)

    result = client.post(
        "/api/v1/notifications/sweep-reminders", headers=admin_headers
    ).json()
    assert result["day_before_queued"] == 1

    # Running it again must not double up.
    again = client.post(
        "/api/v1/notifications/sweep-reminders", headers=admin_headers
    ).json()
    assert again["day_before_queued"] == 0

    reminders = [
        n
        for n in service.list_notifications(db_session)
        if n.template_code == TemplateCode.REMINDER_DAY_BEFORE
    ]
    assert len(reminders) == 1
    assert "कल" in reminders[0].body


def test_doctor_unavailable_message_offers_priority_rebooking(
    client, clinic, patient_headers, db_session
):
    booked = _book(client, patient_headers, clinic["id"])
    from app.modules.booking import service as booking_service

    appointment = booking_service.get_appointment(db_session, booked["id"])
    note = service.notify_doctor_unavailable(db_session, appointment)
    assert note is not None
    assert "प्राथमिकता" in note.body  # "you will be given priority"


# --- access control --------------------------------------------------------


def test_patients_see_only_their_own_messages(
    client, clinic, patient_headers, register_user, db_session
):
    _book(client, patient_headers, clinic["id"])
    _, other = register_user(phone="9222200099", role="patient")
    _book(client, other, clinic["id"])

    mine = client.get("/api/v1/notifications/me", headers=patient_headers).json()
    assert len(mine) == 1


def test_outbox_is_staff_only(client, patient_headers):
    assert client.get("/api/v1/notifications/", headers=patient_headers).status_code == 403


def test_test_endpoint_is_admin_only(client, patient_headers):
    resp = client.post(
        "/api/v1/notifications/test",
        json={"recipient": "9876543210", "context": {}},
        headers=patient_headers,
    )
    assert resp.status_code == 403


def test_admin_can_send_a_test_message(client, admin):
    _, headers = admin
    resp = client.post(
        "/api/v1/notifications/test",
        json={
            "channel": "voice",
            "template_code": "booking_confirmed",
            "language": "hi",
            "recipient": "9876543210",
            "context": {
                "doctor_name": "Dr. Sharma",
                "date": "18-08-2026",
                "time": "09:30",
                "room": "OPD 12",
                "reference": "OPDABC2345",
            },
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "sent"
    # Voice messages are flattened for text-to-speech.
    assert "\n" not in body["body"]
