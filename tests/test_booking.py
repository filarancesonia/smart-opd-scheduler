"""Room 3 — slot allocation, the four booking channels, and role scoping."""

from datetime import time, timedelta

import pytest

from app.core.config import settings
from app.core.timeutil import local_now, local_today
from app.modules.booking import service

DEVICE_HEADERS = {"X-Device-Key": settings.device_api_key}


@pytest.fixture
def clinic(client, admin, doctor):
    """Dr. Sharma running a 09:00-13:00 clinic every day of this month."""
    _, admin_headers = admin
    profile, _ = doctor
    for weekday in range(7):
        resp = client.post(
            f"/api/v1/doctors/{profile['id']}/duty-slots",
            json={
                "day_of_week": weekday,
                "start_time": "09:00:00",
                "end_time": "13:00:00",
                "room": "OPD 12",
                "valid_from": (local_today() - timedelta(days=1)).isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
    return profile


@pytest.fixture
def patient_client(client, register_user):
    _, headers = register_user(phone="9123456780", role="patient", full_name="Asha Devi")
    return headers


# --- slots -----------------------------------------------------------------


def test_slots_are_generated_from_the_roster(client, clinic, patient_client):
    resp = client.get(
        f"/api/v1/booking/doctors/{clinic['id']}/slots?date={local_today().isoformat()}",
        headers=patient_client,
    )
    assert resp.status_code == 200
    body = resp.json()
    # 09:00-13:00 at 10 minutes each = 24 slots.
    assert len(body["slots"]) == 24
    assert body["capacity"] == 24
    assert body["remaining"] == 24
    assert body["slots"][0]["start"] == "09:00:00"
    assert body["slots"][0]["end"] == "09:10:00"
    assert all(s["available"] for s in body["slots"])


def test_slots_empty_on_a_day_with_no_clinic(client, doctor, patient_client):
    profile, _ = doctor
    body = client.get(
        f"/api/v1/booking/doctors/{profile['id']}/slots?date={local_today().isoformat()}",
        headers=patient_client,
    ).json()
    assert body["slots"] == []
    assert body["remaining"] == 0


# --- booking core ----------------------------------------------------------


def test_patient_books_the_next_free_slot(client, clinic, patient_client):
    resp = client.post(
        "/api/v1/booking/appointments?channel=mobile_app",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "reason": "Fever for three days",
        },
        headers=patient_client,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["slot_start"] == "09:00:00"
    assert body["room"] == "OPD 12"
    assert body["channel"] == "mobile_app"
    assert body["status"] == "booked"
    assert body["doctor_name"] == "Dr. Sharma"
    assert body["patient_name"] == "Asha Devi"
    assert body["booking_reference"].startswith("OPD")


def test_second_booking_takes_the_following_slot(
    client, clinic, patient_client, register_user
):
    client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    _, other = register_user(phone="9123456781", role="patient", full_name="Ravi Kumar")
    second = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=other,
    )
    assert second.json()["slot_start"] == "09:10:00"


def test_booking_references_are_unambiguous(client, clinic, patient_client):
    ref = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    ).json()["booking_reference"]
    # Read aloud on a phone line, so no O/0 or I/1 confusion.
    assert not set(ref[3:]) & set("O0I1")


def test_preferred_slot_is_honoured(client, clinic, patient_client):
    resp = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "preferred_start": "11:30:00",
        },
        headers=patient_client,
    )
    assert resp.json()["slot_start"] == "11:30:00"


def test_taken_slot_is_rejected(client, clinic, patient_client, register_user):
    payload = {
        "doctor_id": clinic["id"],
        "appointment_date": local_today().isoformat(),
        "preferred_start": "11:30:00",
    }
    client.post("/api/v1/booking/appointments", json=payload, headers=patient_client)
    _, other = register_user(phone="9123456782", role="patient")
    clash = client.post("/api/v1/booking/appointments", json=payload, headers=other)
    assert clash.status_code == 409


def test_slot_outside_the_clinic_is_rejected(client, clinic, patient_client):
    resp = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "preferred_start": "17:00:00",
        },
        headers=patient_client,
    )
    assert resp.status_code == 422


def test_past_date_is_rejected(client, clinic, patient_client):
    resp = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": (local_today() - timedelta(days=1)).isoformat(),
        },
        headers=patient_client,
    )
    assert resp.status_code == 422


def test_booking_too_far_ahead_is_rejected(client, clinic, patient_client):
    resp = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": (local_today() + timedelta(days=90)).isoformat(),
        },
        headers=patient_client,
    )
    assert resp.status_code == 422


def test_double_booking_same_doctor_same_day_is_rejected(
    client, clinic, patient_client
):
    payload = {
        "doctor_id": clinic["id"],
        "appointment_date": local_today().isoformat(),
    }
    first = client.post(
        "/api/v1/booking/appointments", json=payload, headers=patient_client
    )
    again = client.post(
        "/api/v1/booking/appointments", json=payload, headers=patient_client
    )
    assert again.status_code == 409
    assert (
        again.json()["error"]["details"]["booking_reference"]
        == first.json()["booking_reference"]
    )


def test_doctor_not_accepting_patients_is_rejected(
    client, admin, clinic, patient_client
):
    _, admin_headers = admin
    client.patch(
        f"/api/v1/doctors/{clinic['id']}",
        json={"is_accepting_patients": False},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    assert resp.status_code == 409


def test_leave_blocks_booking(client, admin, clinic, patient_client):
    _, admin_headers = admin
    leave = client.post(
        f"/api/v1/doctors/{clinic['id']}/leaves",
        json={
            "start_date": local_today().isoformat(),
            "end_date": local_today().isoformat(),
        },
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=admin_headers,
    )
    resp = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    assert resp.status_code == 409


def test_clinic_fills_up(client, clinic, db_session, patient_client):
    """Fill all 24 slots, then confirm the 25th is refused."""
    for i in range(24):
        patient = service.create_patient(
            db_session,
            service.PatientCreate(full_name=f"Patient {i}", phone="9000000123"),
        )
        service.book(
            db_session,
            patient_id=patient.id,
            payload=service.AppointmentCreate(
                doctor_id=clinic["id"], appointment_date=local_today()
            ),
            channel="website",
        )
    resp = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["capacity"] == 24


# --- cancel / reschedule / check-in ----------------------------------------


def test_cancel_frees_the_slot(client, clinic, patient_client, register_user):
    booked = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "preferred_start": "10:00:00",
        },
        headers=patient_client,
    ).json()

    client.post(
        f"/api/v1/booking/appointments/{booked['id']}/cancel",
        json={"reason": "Feeling better"},
        headers=patient_client,
    )

    _, other = register_user(phone="9123456783", role="patient")
    retaken = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "preferred_start": "10:00:00",
        },
        headers=other,
    )
    assert retaken.status_code == 201


def test_cancel_twice_is_rejected(client, clinic, patient_client):
    booked = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    ).json()
    client.post(
        f"/api/v1/booking/appointments/{booked['id']}/cancel",
        json={},
        headers=patient_client,
    )
    again = client.post(
        f"/api/v1/booking/appointments/{booked['id']}/cancel",
        json={},
        headers=patient_client,
    )
    assert again.status_code == 409


def test_reschedule_creates_a_new_reference_and_keeps_the_old_row(
    client, clinic, patient_client
):
    booked = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    ).json()

    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    moved = client.post(
        f"/api/v1/booking/appointments/{booked['id']}/reschedule",
        json={"appointment_date": tomorrow},
        headers=patient_client,
    ).json()

    assert moved["appointment_date"] == tomorrow
    assert moved["booking_reference"] != booked["booking_reference"]

    original = client.get(
        f"/api/v1/booking/appointments/{booked['booking_reference']}",
        headers=patient_client,
    ).json()
    assert original["status"] == "rescheduled"


def test_check_in_is_staff_only(client, clinic, patient_client, admin):
    booked = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    ).json()

    denied = client.post(
        f"/api/v1/booking/appointments/{booked['id']}/check-in", headers=patient_client
    )
    assert denied.status_code == 403

    _, admin_headers = admin
    allowed = client.post(
        f"/api/v1/booking/appointments/{booked['id']}/check-in", headers=admin_headers
    )
    assert allowed.status_code == 200
    assert allowed.json()["status"] == "checked_in"
    assert allowed.json()["checked_in_at"] is not None


# --- role scoping ----------------------------------------------------------


def test_patients_only_see_their_own_appointments(
    client, clinic, patient_client, register_user
):
    client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    _, other = register_user(phone="9123456784", role="patient")
    client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=other,
    )

    mine = client.get("/api/v1/booking/appointments", headers=patient_client).json()
    assert len(mine) == 1
    theirs = client.get("/api/v1/booking/appointments", headers=other).json()
    assert len(theirs) == 1
    assert mine[0]["id"] != theirs[0]["id"]


def test_patient_cannot_read_another_patients_reference(
    client, clinic, patient_client, register_user
):
    booked = client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    ).json()
    _, other = register_user(phone="9123456785", role="patient")
    resp = client.get(
        f"/api/v1/booking/appointments/{booked['booking_reference']}", headers=other
    )
    assert resp.status_code == 403


def test_doctor_sees_only_their_own_clinic(client, clinic, patient_client, doctor):
    _, doc_headers = doctor
    client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    listed = client.get("/api/v1/booking/appointments", headers=doc_headers).json()
    assert len(listed) == 1
    assert listed[0]["doctor_id"] == clinic["id"]


def test_app_client_cannot_claim_the_kiosk_channel(client, clinic, patient_client):
    resp = client.post(
        "/api/v1/booking/appointments?channel=kiosk",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    assert resp.status_code == 403


# --- kiosk channel ---------------------------------------------------------


def test_kiosk_requires_its_device_key(client, clinic):
    resp = client.post("/api/v1/booking/kiosk/lookup", json={"phone": "9123456780"})
    assert resp.status_code == 401


def test_kiosk_books_a_walk_in_and_prints_a_slip(client, clinic):
    resp = client.post(
        "/api/v1/booking/kiosk/book",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "patient": {
                "full_name": "Ramesh Yadav",
                "phone": "9812345670",
                "age": 67,
            },
        },
        headers=DEVICE_HEADERS,
    )
    assert resp.status_code == 201, resp.text
    ticket = resp.json()
    assert ticket["room"] == "OPD 12"
    assert ticket["doctor_name"] == "Dr. Sharma"
    assert ticket["booking_reference"] in ticket["message_hi"]
    assert ticket["booking_reference"] in ticket["message_en"]
    assert "कमरा" in ticket["message_hi"]


def test_kiosk_lookup_returns_every_family_member_on_one_number(client, clinic):
    for name, age in [("Ramesh Yadav", 67), ("Sunita Yadav", 61)]:
        client.post(
            "/api/v1/booking/kiosk/book",
            json={
                "doctor_id": clinic["id"],
                "appointment_date": local_today().isoformat(),
                "patient": {"full_name": name, "phone": "9812345670", "age": age},
            },
            headers=DEVICE_HEADERS,
        )
    found = client.post(
        "/api/v1/booking/kiosk/lookup",
        json={"phone": "9812345670"},
        headers=DEVICE_HEADERS,
    ).json()
    assert {p["full_name"] for p in found} == {"Ramesh Yadav", "Sunita Yadav"}
    assert all(p["is_senior_citizen"] for p in found)


def test_kiosk_reuses_an_existing_patient_record(client, clinic, db_session):
    payload = {
        "doctor_id": clinic["id"],
        "appointment_date": local_today().isoformat(),
        "patient": {"full_name": "Ramesh Yadav", "phone": "9812345670", "age": 67},
    }
    client.post("/api/v1/booking/kiosk/book", json=payload, headers=DEVICE_HEADERS)
    payload["appointment_date"] = (local_today() + timedelta(days=1)).isoformat()
    client.post("/api/v1/booking/kiosk/book", json=payload, headers=DEVICE_HEADERS)

    assert len(service.find_patients_by_phone(db_session, "9812345670")) == 1


# --- IVR channel -----------------------------------------------------------


def _ivr(client, session_id, digits):
    return client.post(
        "/api/v1/booking/ivr/input",
        json={"session_id": session_id, "digits": digits},
        headers=DEVICE_HEADERS,
    ).json()


def test_ivr_books_end_to_end_in_hindi(client, clinic, department):
    started = client.post(
        "/api/v1/booking/ivr/start",
        json={"caller_phone": "9876500001"},
        headers=DEVICE_HEADERS,
    ).json()
    assert "नमस्ते" in started["prompt_hi"]
    session_id = started["session_id"]

    departments = _ivr(client, session_id, "1")
    assert departments["state"] == "choose_department"
    assert departments["options"][0]["label_en"] == "General Medicine"

    doctors = _ivr(client, session_id, "1")
    assert doctors["state"] == "choose_doctor"
    assert doctors["options"][0]["label_en"] == "Dr. Sharma"

    dates = _ivr(client, session_id, "1")
    assert dates["state"] == "choose_date"

    confirm = _ivr(client, session_id, "1")  # today
    assert confirm["state"] == "confirm"

    done = _ivr(client, session_id, "1")
    assert done["call_complete"] is True
    assert done["booking_reference"] is not None
    # The reference is spelled out letter by letter for the phone line.
    assert " ".join(done["booking_reference"]) in done["prompt_hi"]


def test_ivr_creates_a_provisional_patient_for_a_new_caller(
    client, clinic, db_session
):
    started = client.post(
        "/api/v1/booking/ivr/start",
        json={"caller_phone": "9876500002"},
        headers=DEVICE_HEADERS,
    ).json()
    sid = started["session_id"]
    for key in ["1", "1", "1", "1", "1"]:
        _ivr(client, sid, key)

    patients = service.find_patients_by_phone(db_session, "9876500002")
    assert len(patients) == 1
    assert patients[0].full_name.endswith("0002")


def test_ivr_rejects_an_invalid_key(client, clinic):
    started = client.post(
        "/api/v1/booking/ivr/start",
        json={"caller_phone": "9876500003"},
        headers=DEVICE_HEADERS,
    ).json()
    bad = _ivr(client, started["session_id"], "7")
    assert "क्षमा करें" in bad["prompt_hi"]
    assert bad["call_complete"] is False


def test_ivr_caller_can_abandon_at_confirmation(client, clinic, db_session):
    started = client.post(
        "/api/v1/booking/ivr/start",
        json={"caller_phone": "9876500004"},
        headers=DEVICE_HEADERS,
    ).json()
    sid = started["session_id"]
    for key in ["1", "1", "1", "1"]:
        _ivr(client, sid, key)
    cancelled = _ivr(client, sid, "2")
    assert cancelled["call_complete"] is True
    assert cancelled["booking_reference"] is None
    assert service.list_appointments(db_session, doctor_id=clinic["id"]) == []


def test_ivr_reads_back_an_existing_booking(client, clinic, patient_client):
    client.post(
        "/api/v1/booking/appointments",
        json={"doctor_id": clinic["id"], "appointment_date": local_today().isoformat()},
        headers=patient_client,
    )
    started = client.post(
        "/api/v1/booking/ivr/start",
        json={"caller_phone": "9123456780"},
        headers=DEVICE_HEADERS,
    ).json()
    readback = _ivr(client, started["session_id"], "2")
    assert readback["call_complete"] is True
    assert readback["booking_reference"] is not None


def test_ivr_session_cannot_be_reused_after_the_call_ends(client, clinic):
    started = client.post(
        "/api/v1/booking/ivr/start",
        json={"caller_phone": "9876500005"},
        headers=DEVICE_HEADERS,
    ).json()
    sid = started["session_id"]
    for key in ["1", "1", "1", "1", "1"]:
        _ivr(client, sid, key)
    resp = client.post(
        "/api/v1/booking/ivr/input",
        json={"session_id": sid, "digits": "1"},
        headers=DEVICE_HEADERS,
    )
    assert resp.status_code == 409


# --- presence integration --------------------------------------------------


def test_slots_warn_when_the_doctor_has_not_arrived(
    client, admin, doctor, patient_client
):
    """Room 1 feeding Room 3: the booking screen says the doctor is missing.

    Uses its own duty window covering the current hour rather than the shared
    09:00-13:00 clinic, so the assertion holds whenever the suite runs.
    """
    _, admin_headers = admin
    profile, _ = doctor
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 2, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)

    created = client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": local_today().weekday(),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "room": "OPD 12",
            "valid_from": local_today().isoformat(),
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text

    body = client.get(
        f"/api/v1/booking/doctors/{profile['id']}/slots?date={local_today().isoformat()}",
        headers=patient_client,
    ).json()
    assert body["presence_warning"] is not None
    assert "not arrived" in body["presence_warning"]


def test_no_presence_warning_for_a_future_date(client, clinic, patient_client):
    tomorrow = (local_today() + timedelta(days=1)).isoformat()
    body = client.get(
        f"/api/v1/booking/doctors/{clinic['id']}/slots?date={tomorrow}",
        headers=patient_client,
    ).json()
    assert body["presence_warning"] is None
