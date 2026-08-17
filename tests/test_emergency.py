"""Room 7 — triage, safe overrides, the audit trail and anti-starvation."""

from datetime import time, timedelta

import pytest

from app.core.timeutil import local_now, local_today
from app.modules.emergency import service
from app.modules.emergency.models import PriorityTier
from app.modules.emergency.schemas import TriageRequest


@pytest.fixture
def clinic(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 3, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)
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
def running_clinic(client, admin, clinic):
    """Doctor present, queue open."""
    _, admin_headers = admin
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=admin_headers
    )
    return clinic


def _routine_patient(client, admin, db_session, name, phone, doctor_id, age=30):
    _, admin_headers = admin
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import AppointmentCreate, PatientCreate

    patient = booking_service.create_patient(
        db_session, PatientCreate(full_name=name, phone=phone, age=age)
    )
    appointment = booking_service.book(
        db_session,
        patient_id=patient.id,
        payload=AppointmentCreate(doctor_id=doctor_id, appointment_date=local_today()),
        channel="website",
    )
    entry = client.post(
        "/api/v1/queue/join",
        json={"appointment_id": appointment.id},
        headers=admin_headers,
    ).json()
    return patient, appointment, entry


# --- vulnerability ---------------------------------------------------------


def test_vulnerability_rules(client, admin, db_session):
    _, headers = admin
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import PatientCreate

    elder = booking_service.create_patient(
        db_session, PatientCreate(full_name="Elder", phone="9333300001", age=72)
    )
    pregnant = booking_service.create_patient(
        db_session,
        PatientCreate(full_name="Expecting", phone="9333300002", age=28, is_pregnant=True),
    )
    routine = booking_service.create_patient(
        db_session, PatientCreate(full_name="Routine", phone="9333300003", age=30)
    )

    for patient, expected in [(elder, 1), (pregnant, 1), (routine, 0)]:
        body = client.get(
            f"/api/v1/emergency/patients/{patient.id}/vulnerability", headers=headers
        ).json()
        assert body["tier"] == expected

    elder_reasons = client.get(
        f"/api/v1/emergency/patients/{elder.id}/vulnerability", headers=headers
    ).json()["reasons"]
    assert "Senior citizen (age 72)" in elder_reasons


def test_room_4_defers_to_room_7_for_the_vulnerability_rule(db_session):
    """One definition of 'vulnerable', not two that can drift apart."""
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import PatientCreate
    from app.modules.scheduling import service as scheduling_service

    patient = booking_service.create_patient(
        db_session,
        PatientCreate(full_name="Disabled Patient", phone="9333300004", has_disability=True),
    )
    assert scheduling_service.priority_tier_for(patient) == service.priority_tier_for(
        patient
    )
    assert scheduling_service.priority_tier_for(patient) == PriorityTier.VULNERABLE


# --- triage ----------------------------------------------------------------


def test_red_triage_jumps_the_whole_queue(
    client, admin, running_clinic, department, db_session
):
    _, admin_headers = admin
    for i in range(3):
        _routine_patient(
            client, admin, db_session, f"Routine {i}", f"933330001{i}", running_clinic["id"]
        )

    case = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "Chest pain, breathless",
            "department_id": department["id"],
            "patient": {"full_name": "Ambulance Case", "phone": "9333300020", "age": 58},
        },
        headers=admin_headers,
    )
    assert case.status_code == 201, case.text
    body = case.json()
    assert body["priority_tier"] == PriorityTier.EMERGENCY
    assert body["displaced_count"] == 3
    assert body["token_number"] is not None

    called = client.post(
        f"/api/v1/queue/doctors/{running_clinic['id']}/call-next", headers=admin_headers
    ).json()
    # The emergency is seen first despite holding the last token.
    assert called["called"]["token_number"] == body["token_number"]
    assert called["called"]["priority_tier"] == PriorityTier.EMERGENCY


def test_yellow_triage_outranks_routine_but_not_emergency(
    client, admin, running_clinic, department, db_session
):
    _, admin_headers = admin
    _routine_patient(
        client, admin, db_session, "Routine", "9333300030", running_clinic["id"]
    )

    urgent = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "yellow",
            "complaint": "Deep cut, bleeding controlled",
            "department_id": department["id"],
            "patient": {"full_name": "Urgent Case", "phone": "9333300031", "age": 40},
        },
        headers=admin_headers,
    ).json()
    assert urgent["priority_tier"] == PriorityTier.URGENT

    emergency = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "Unconscious",
            "department_id": department["id"],
            "patient": {"full_name": "Critical Case", "phone": "9333300032", "age": 60},
        },
        headers=admin_headers,
    ).json()

    called = client.post(
        f"/api/v1/queue/doctors/{running_clinic['id']}/call-next", headers=admin_headers
    ).json()
    assert called["called"]["token_number"] == emergency["token_number"]


def test_green_triage_does_not_jump_anyone(
    client, admin, running_clinic, department, db_session
):
    _, admin_headers = admin
    body = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "green",
            "complaint": "Mild rash for two weeks",
            "department_id": department["id"],
            "patient": {"full_name": "Walk In", "phone": "9333300040", "age": 35},
        },
        headers=admin_headers,
    ).json()
    assert body["priority_tier"] == PriorityTier.ROUTINE
    assert body["displaced_count"] == 0


def test_emergency_is_admitted_to_a_fully_booked_clinic(
    client, admin, running_clinic, department, db_session
):
    """Capacity is not a reason to turn away an ambulance."""
    _, admin_headers = admin
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import AppointmentCreate, PatientCreate

    slots = client.get(
        f"/api/v1/booking/doctors/{running_clinic['id']}/slots?date={local_today().isoformat()}",
        headers=admin_headers,
    ).json()
    for i in range(slots["remaining"]):
        patient = booking_service.create_patient(
            db_session, PatientCreate(full_name=f"Filler {i}", phone="9333300050")
        )
        booking_service.book(
            db_session,
            patient_id=patient.id,
            payload=AppointmentCreate(
                doctor_id=running_clinic["id"], appointment_date=local_today()
            ),
            channel="website",
        )

    # The clinic is now genuinely full for a normal booking...
    full = client.get(
        f"/api/v1/booking/doctors/{running_clinic['id']}/slots?date={local_today().isoformat()}",
        headers=admin_headers,
    ).json()
    assert full["remaining"] == 0

    # ...but the emergency still gets in.
    resp = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "Road accident, head injury",
            "department_id": department["id"],
            "patient": {"full_name": "Accident Case", "phone": "9333300051", "age": 22},
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text


def test_triage_reuses_an_existing_patient_record(
    client, admin, running_clinic, department, db_session
):
    _, admin_headers = admin
    from app.modules.booking import service as booking_service

    payload = {
        "triage_level": "yellow",
        "complaint": "Severe abdominal pain",
        "department_id": department["id"],
        "patient": {"full_name": "Repeat Case", "phone": "9333300060", "age": 45},
    }
    client.post("/api/v1/emergency/triage", json=payload, headers=admin_headers)
    client.post("/api/v1/emergency/triage", json=payload, headers=admin_headers)

    assert len(booking_service.find_patients_by_phone(db_session, "9333300060")) == 1


def test_triage_needs_a_patient(client, admin, running_clinic, department):
    _, admin_headers = admin
    resp = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "Collapsed",
            "department_id": department["id"],
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_patients_cannot_triage_themselves(
    client, running_clinic, department, register_user
):
    """The single most important access rule in this module."""
    _, patient_headers = register_user(phone="9333300070", role="patient")
    resp = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "I am in a hurry",
            "department_id": department["id"],
            "patient": {"full_name": "Impatient", "phone": "9333300070"},
        },
        headers=patient_headers,
    )
    assert resp.status_code == 403


def test_case_can_be_resolved_once(client, admin, running_clinic, department):
    _, admin_headers = admin
    case = client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "orange",
            "complaint": "High fever with fits",
            "department_id": department["id"],
            "patient": {"full_name": "Fever Case", "phone": "9333300080", "age": 5},
        },
        headers=admin_headers,
    ).json()

    resolved = client.post(
        f"/api/v1/emergency/cases/{case['id']}/resolve",
        json={"status": "resolved", "outcome": "Stabilised, sent home"},
        headers=admin_headers,
    ).json()
    assert resolved["status"] == "resolved"
    assert resolved["resolved_at"] is not None

    again = client.post(
        f"/api/v1/emergency/cases/{case['id']}/resolve",
        json={"status": "resolved"},
        headers=admin_headers,
    )
    assert again.status_code == 409


# --- manual overrides ------------------------------------------------------


def test_a_waiting_patient_can_be_escalated_with_a_reason(
    client, admin, running_clinic, db_session
):
    _, admin_headers = admin
    _, _, entry = _routine_patient(
        client, admin, db_session, "Collapsed Patient", "9333300090", running_clinic["id"]
    )

    updated = client.post(
        f"/api/v1/emergency/queue-entries/{entry['id']}/priority",
        json={"tier": 3, "reason": "Collapsed in the waiting area"},
        headers=admin_headers,
    ).json()
    assert updated["priority_tier"] == PriorityTier.EMERGENCY


def test_escalation_requires_a_reason(client, admin, running_clinic, db_session):
    _, admin_headers = admin
    _, _, entry = _routine_patient(
        client, admin, db_session, "Patient", "9333300100", running_clinic["id"]
    )
    resp = client.post(
        f"/api/v1/emergency/queue-entries/{entry['id']}/priority",
        json={"tier": 3, "reason": ""},
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_priority_cannot_be_changed_after_the_consultation_starts(
    client, admin, running_clinic, db_session
):
    _, admin_headers = admin
    _, _, entry = _routine_patient(
        client, admin, db_session, "Patient", "9333300110", running_clinic["id"]
    )
    client.post(
        f"/api/v1/queue/doctors/{running_clinic['id']}/call-next", headers=admin_headers
    )
    client.post(f"/api/v1/queue/entries/{entry['id']}/start", headers=admin_headers)

    resp = client.post(
        f"/api/v1/emergency/queue-entries/{entry['id']}/priority",
        json={"tier": 3, "reason": "Too late"},
        headers=admin_headers,
    )
    assert resp.status_code == 409


# --- audit trail -----------------------------------------------------------


def test_every_override_is_logged_with_an_actor_and_a_reason(
    client, admin, running_clinic, department, db_session
):
    _, admin_headers = admin
    admin_user, _ = admin

    client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "Severe bleeding",
            "department_id": department["id"],
            "patient": {"full_name": "Bleeding Case", "phone": "9333300120", "age": 33},
        },
        headers=admin_headers,
    )

    overrides = client.get("/api/v1/emergency/overrides", headers=admin_headers).json()
    assert len(overrides) == 1
    entry = overrides[0]
    assert entry["source"] == "triage"
    assert entry["to_tier"] == PriorityTier.EMERGENCY
    assert entry["actor_user_id"] == admin_user["id"]
    assert "Severe bleeding" in entry["reason"]


def test_override_log_is_staff_only(client, register_user):
    _, patient_headers = register_user(phone="9333300130", role="patient")
    resp = client.get("/api/v1/emergency/overrides", headers=patient_headers)
    assert resp.status_code == 403


# --- anti-starvation -------------------------------------------------------


def test_long_waiting_patients_are_escalated_automatically(
    client, admin, running_clinic, db_session
):
    """A morning of emergencies must not leave the same people on the bench."""
    _, admin_headers = admin
    _, _, entry = _routine_patient(
        client, admin, db_session, "Patient", "9333300140", running_clinic["id"]
    )

    from app.modules.queue.models import QueueEntry

    row = db_session.get(QueueEntry, entry["id"])
    assert row.priority_tier == PriorityTier.ROUTINE

    # Pretend they joined well past the threshold.
    row.joined_at = row.joined_at - timedelta(
        minutes=service.AGING_THRESHOLD_MINUTES + 10
    )
    db_session.commit()

    result = client.post(
        f"/api/v1/emergency/doctors/{running_clinic['id']}/apply-aging",
        headers=admin_headers,
    ).json()
    assert result["escalated"] == 1
    assert result["checked"] == 1

    db_session.refresh(row)
    assert row.priority_tier == PriorityTier.VULNERABLE


def test_aging_never_manufactures_an_emergency(client, admin, running_clinic, db_session):
    """Waiting a long time is not a clinical finding."""
    _, admin_headers = admin
    _, _, entry = _routine_patient(
        client, admin, db_session, "Patient", "9333300150", running_clinic["id"]
    )

    from app.modules.queue.models import QueueEntry

    row = db_session.get(QueueEntry, entry["id"])
    for _ in range(5):
        row.joined_at = row.joined_at - timedelta(
            minutes=service.AGING_THRESHOLD_MINUTES + 10
        )
        db_session.commit()
        client.post(
            f"/api/v1/emergency/doctors/{running_clinic['id']}/apply-aging",
            headers=admin_headers,
        )
        db_session.refresh(row)

    assert row.priority_tier == service.MAX_AGING_TIER
    assert row.priority_tier < PriorityTier.EMERGENCY


def test_automatic_escalations_are_logged_without_an_actor(
    client, admin, running_clinic, db_session
):
    _, admin_headers = admin
    _, _, entry = _routine_patient(
        client, admin, db_session, "Patient", "9333300160", running_clinic["id"]
    )

    from app.modules.queue.models import QueueEntry

    row = db_session.get(QueueEntry, entry["id"])
    row.joined_at = row.joined_at - timedelta(
        minutes=service.AGING_THRESHOLD_MINUTES + 10
    )
    db_session.commit()
    client.post(
        f"/api/v1/emergency/doctors/{running_clinic['id']}/apply-aging",
        headers=admin_headers,
    )

    overrides = client.get(
        f"/api/v1/emergency/overrides?queue_entry_id={entry['id']}",
        headers=admin_headers,
    ).json()
    aging = [o for o in overrides if o["source"] == "aging"]
    assert len(aging) == 1
    # No human authored this one, and the log says so honestly.
    assert aging[0]["actor_user_id"] is None
    assert "threshold" in aging[0]["reason"]
