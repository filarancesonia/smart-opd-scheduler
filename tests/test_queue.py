"""Room 5 — tokens, live flow, ETAs and the corridor board."""

from datetime import time, timedelta

import pytest

from app.core.config import settings
from app.core.timeutil import local_now, local_today
from app.modules.queue import service
from app.modules.queue.models import QueueEntry

DEVICE_HEADERS = {"X-Device-Key": settings.device_api_key}


@pytest.fixture
def clinic(client, admin, doctor):
    """A clinic whose duty window covers the current hour, so 'today' works."""
    _, admin_headers = admin
    profile, _ = doctor
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 3, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)

    resp = client.post(
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
    assert resp.status_code == 201, resp.text
    return profile


@pytest.fixture
def present_clinic(client, admin, clinic):
    """...with the doctor actually in the room."""
    _, admin_headers = admin
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )
    return clinic


@pytest.fixture
def open_queue(client, admin, present_clinic):
    _, admin_headers = admin
    resp = client.post(
        f"/api/v1/queue/doctors/{present_clinic['id']}/open",
        json={"room": "OPD 12"},
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return present_clinic


def _make_patient(client, db_session, name, phone, doctor_id, age=30):
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
    return patient, appointment


def _join(client, headers, appointment_id):
    resp = client.post(
        "/api/v1/queue/join", json={"appointment_id": appointment_id}, headers=headers
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- session control -------------------------------------------------------


def test_opening_a_queue_is_idempotent(client, admin, present_clinic):
    _, headers = admin
    first = client.post(
        f"/api/v1/queue/doctors/{present_clinic['id']}/open", json={}, headers=headers
    ).json()
    second = client.post(
        f"/api/v1/queue/doctors/{present_clinic['id']}/open", json={}, headers=headers
    ).json()
    assert first["session_id"] == second["session_id"]


def test_queue_room_defaults_to_the_rostered_room(client, admin, present_clinic):
    _, headers = admin
    body = client.post(
        f"/api/v1/queue/doctors/{present_clinic['id']}/open", json={}, headers=headers
    ).json()
    assert body["room"] == "OPD 12"


def test_a_queue_with_people_waiting_cannot_be_closed(
    client, admin, open_queue, db_session
):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100001", open_queue["id"])
    _join(client, headers, appointment.id)

    resp = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/close", headers=headers
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["details"]["waiting"] == 1


# --- tokens ----------------------------------------------------------------


def test_tokens_are_sequential_and_never_reused(
    client, admin, open_queue, db_session
):
    _, headers = admin
    tokens = []
    for i in range(3):
        _, appointment = _make_patient(
            client, db_session, f"Patient {i}", f"911110001{i}", open_queue["id"]
        )
        tokens.append(_join(client, headers, appointment.id)["token_number"])
    assert tokens == [1, 2, 3]


def test_joining_checks_the_patient_in(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100021", open_queue["id"])
    assert appointment.status == "booked"

    _join(client, headers, appointment.id)
    db_session.refresh(appointment)
    assert appointment.status == "checked_in"
    assert appointment.checked_in_at is not None


def test_joining_twice_returns_the_same_token(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100031", open_queue["id"])
    first = _join(client, headers, appointment.id)
    second = _join(client, headers, appointment.id)
    assert first["token_number"] == second["token_number"]


def test_cannot_join_a_queue_that_was_never_opened(client, admin, clinic, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100041", clinic["id"])
    resp = client.post(
        "/api/v1/queue/join", json={"appointment_id": appointment.id}, headers=headers
    )
    assert resp.status_code == 404


# --- flow ------------------------------------------------------------------


def test_patients_are_not_called_into_an_empty_room(
    client, admin, clinic, db_session
):
    """The exact failure this project exists to prevent."""
    _, headers = admin
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=headers)
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100051", clinic["id"])
    _join(client, headers, appointment.id)

    resp = client.post(
        f"/api/v1/queue/doctors/{clinic['id']}/call-next", headers=headers
    )
    assert resp.status_code == 409
    assert "not present" in resp.json()["error"]["message"]


def test_full_consultation_cycle(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100061", open_queue["id"])
    entry = _join(client, headers, appointment.id)

    called = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers
    ).json()
    assert called["called"]["token_number"] == entry["token_number"]
    assert called["called"]["status"] == "called"

    started = client.post(
        f"/api/v1/queue/entries/{entry['id']}/start", headers=headers
    ).json()
    assert started["status"] == "in_progress"

    completed = client.post(
        f"/api/v1/queue/entries/{entry['id']}/complete",
        json={"note": "Prescribed paracetamol"},
        headers=headers,
    ).json()
    assert completed["status"] == "completed"
    assert completed["note"] == "Prescribed paracetamol"

    db_session.refresh(appointment)
    assert appointment.status == "completed"


def test_completion_feeds_a_training_row_back_to_room_4(
    client, admin, open_queue, db_session
):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100071", open_queue["id"])
    entry = _join(client, headers, appointment.id)

    client.post(f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers)
    client.post(f"/api/v1/queue/entries/{entry['id']}/start", headers=headers)
    client.post(
        f"/api/v1/queue/entries/{entry['id']}/complete", json={}, headers=headers
    )

    status = client.get("/api/v1/scheduling/status", headers=headers).json()
    assert status["training_rows"] == 1
    assert status["synthetic_rows"] == 0


def test_only_one_patient_is_called_at_a_time(client, admin, open_queue, db_session):
    _, headers = admin
    for i in range(2):
        _, appointment = _make_patient(
            client, db_session, f"Patient {i}", f"911110008{i}", open_queue["id"]
        )
        _join(client, headers, appointment.id)

    client.post(f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers)
    second = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers
    )
    assert second.status_code == 409
    assert "already been called" in second.json()["error"]["message"]


def test_call_next_on_an_empty_queue_reports_it(client, admin, open_queue):
    _, headers = admin
    body = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers
    ).json()
    assert body["called"] is None
    assert "Nobody is waiting" in body["reason"]


def test_a_skipped_patient_keeps_their_token(client, admin, open_queue, db_session):
    """Someone who stepped out for water should not lose their morning."""
    _, headers = admin
    _, first_appt = _make_patient(client, db_session, "Asha Devi", "9111100091", open_queue["id"])
    _, second_appt = _make_patient(client, db_session, "Ravi Kumar", "9111100092", open_queue["id"])
    first = _join(client, headers, first_appt.id)
    _join(client, headers, second_appt.id)

    client.post(f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers)
    skipped = client.post(
        f"/api/v1/queue/entries/{first['id']}/skip", headers=headers
    ).json()

    assert skipped["status"] == "skipped"
    assert skipped["skip_count"] == 1
    assert skipped["token_number"] == first["token_number"]

    # Still in the running, just later.
    queue = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=headers
    ).json()
    assert any(e["status"] == "skipped" for e in queue["entries"])


def test_repeated_skips_become_a_no_show(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100101", open_queue["id"])
    entry = _join(client, headers, appointment.id)

    for _ in range(service.MAX_SKIPS + 1):
        client.post(
            f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers
        )
        body = client.post(
            f"/api/v1/queue/entries/{entry['id']}/skip", headers=headers
        ).json()

    assert body["status"] == "no_show"
    db_session.refresh(appointment)
    assert appointment.status == "no_show"


def test_no_show_is_recorded_for_the_models(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100111", open_queue["id"])
    entry = _join(client, headers, appointment.id)
    client.post(f"/api/v1/queue/entries/{entry['id']}/no-show", headers=headers)

    from app.modules.scheduling.models import ConsultationRecord

    record = db_session.query(ConsultationRecord).one()
    assert record.was_no_show is True
    assert record.duration_minutes is None


# --- ordering and estimates ------------------------------------------------


def test_priority_patient_is_called_before_an_earlier_token(
    client, admin, open_queue, db_session
):
    _, headers = admin
    _, young_appt = _make_patient(
        client, db_session, "Young Patient", "9111100121", open_queue["id"], age=25
    )
    _, elder_appt = _make_patient(
        client, db_session, "Elder Patient", "9111100122", open_queue["id"], age=74
    )
    young = _join(client, headers, young_appt.id)
    elder = _join(client, headers, elder_appt.id)

    assert young["token_number"] < elder["token_number"]

    called = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers
    ).json()
    # The senior citizen goes first despite holding the later token.
    assert called["called"]["token_number"] == elder["token_number"]
    assert called["called"]["priority_tier"] == 1


def test_waiting_patients_get_an_eta(client, admin, open_queue, db_session):
    _, headers = admin
    for i in range(3):
        _, appointment = _make_patient(
            client, db_session, f"Patient {i}", f"911110013{i}", open_queue["id"]
        )
        _join(client, headers, appointment.id)

    queue = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=headers
    ).json()
    waits = [e["estimated_wait_minutes"] for e in queue["entries"]]
    assert all(w is not None for w in waits)
    assert waits[0] == 0  # first in line is seen now
    assert waits == sorted(waits)  # and each subsequent patient waits longer


def test_no_eta_is_invented_while_the_doctor_is_absent(
    client, admin, clinic, db_session
):
    """Better to say 'not arrived' than to print a number that is a guess."""
    _, headers = admin
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=headers)
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100141", clinic["id"])
    _join(client, headers, appointment.id)

    queue = client.get(f"/api/v1/queue/doctors/{clinic['id']}", headers=headers).json()
    assert queue["doctor_present"] is False
    assert queue["entries"][0]["estimated_wait_minutes"] is None


def test_an_eta_stops_being_shown_when_the_doctor_leaves(
    client, admin, open_queue, db_session
):
    """The estimates were honest when written. They stop being honest the
    moment the doctor walks out, and nothing writes to the queue to say so."""
    _, headers = admin
    _, appointment = _make_patient(
        client, db_session, "Asha Devi", "9111100161", open_queue["id"]
    )
    entry = _join(client, headers, appointment.id)

    before = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=headers
    ).json()
    assert before["entries"][0]["estimated_wait_minutes"] is not None

    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": open_queue["id"], "status": "absent"},
        headers=headers,
    )

    after = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=headers
    ).json()
    assert after["doctor_present"] is False
    assert after["entries"][0]["estimated_wait_minutes"] is None

    board = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}/board", headers=DEVICE_HEADERS
    ).json()
    assert board["next_tokens"][0]["estimated_wait_minutes"] is None

    # The number is still on disk — nothing rewrote it. It is the serialiser
    # that declines to repeat it, so single-entry responses are covered too.
    row = db_session.get(QueueEntry, entry["id"])
    assert row.estimated_wait_minutes is not None
    assert service.entry_out(db_session, row).estimated_wait_minutes is None


def test_an_eta_survives_the_doctor_stepping_out_mid_consultation(
    client, admin, open_queue, db_session
):
    """A consultation in progress is reason enough to keep estimating: the
    reader may simply have missed a doorway, and someone is plainly being seen."""
    _, headers = admin
    _, appointment = _make_patient(
        client, db_session, "Asha Devi", "9111100171", open_queue["id"]
    )
    first = _join(client, headers, appointment.id)
    _, second_appt = _make_patient(
        client, db_session, "Ramesh Yadav", "9111100172", open_queue["id"]
    )
    _join(client, headers, second_appt.id)

    client.post(f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers)
    client.post(f"/api/v1/queue/entries/{first['id']}/start", headers=headers)
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": open_queue["id"], "status": "absent"},
        headers=headers,
    )

    queue = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=headers
    ).json()
    waiting = [e for e in queue["entries"] if e["status"] == "waiting"]
    assert waiting[0]["estimated_wait_minutes"] is not None


def test_observed_durations_replace_predictions_as_the_clinic_runs(
    client, admin, open_queue, db_session
):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100151", open_queue["id"])
    entry = _join(client, headers, appointment.id)

    client.post(f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers)
    client.post(f"/api/v1/queue/entries/{entry['id']}/start", headers=headers)
    client.post(
        f"/api/v1/queue/entries/{entry['id']}/complete", json={}, headers=headers
    )

    queue = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=headers
    ).json()
    assert queue["completed_count"] == 1
    assert queue["observed_avg_minutes"] is not None


def test_reorder_returns_the_live_plan(client, admin, open_queue, db_session):
    _, headers = admin
    for i in range(4):
        _, appointment = _make_patient(
            client, db_session, f"Patient {i}", f"911110016{i}", open_queue["id"]
        )
        _join(client, headers, appointment.id)

    body = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/reorder", headers=headers
    ).json()
    assert body["reordered"] == 4
    assert len(body["entries"]) == 4
    positions = sorted(e["position"] for e in body["entries"])
    assert positions == [1, 2, 3, 4]


# --- display board ---------------------------------------------------------


def test_board_masks_patient_names(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(
        client, db_session, "Asha Devi Sharma", "9111100171", open_queue["id"]
    )
    _join(client, headers, appointment.id)

    board = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}/board", headers=DEVICE_HEADERS
    ).json()
    row = board["next_tokens"][0]
    # A corridor screen must not broadcast who is attending which clinic.
    assert row["display_name"] == "Asha S."
    assert "Devi" not in row["display_name"]


def test_board_requires_a_device_key(client, open_queue):
    resp = client.get(f"/api/v1/queue/doctors/{open_queue['id']}/board")
    assert resp.status_code == 401


def test_board_announces_an_absent_doctor_in_hindi(client, admin, clinic):
    _, headers = admin
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=headers)
    board = client.get(
        f"/api/v1/queue/doctors/{clinic['id']}/board", headers=DEVICE_HEADERS
    ).json()
    assert board["doctor_present"] is False
    assert "नहीं पहुँचे" in board["status_line_hi"]
    assert "not arrived" in board["status_line_en"]


def test_board_shows_now_serving(client, admin, open_queue, db_session):
    _, headers = admin
    _, appointment = _make_patient(client, db_session, "Asha Devi", "9111100181", open_queue["id"])
    entry = _join(client, headers, appointment.id)
    client.post(f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=headers)

    board = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}/board", headers=DEVICE_HEADERS
    ).json()
    assert board["now_serving"] == entry["token_number"]


# --- the patient's own view ------------------------------------------------


def test_patient_sees_their_position_in_hindi_and_english(
    client, admin, open_queue, register_user
):
    _, admin_headers = admin
    _, patient_headers = register_user(
        phone="9111100191", role="patient", full_name="Asha Devi"
    )
    booked = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": open_queue["id"],
            "appointment_date": local_today().isoformat(),
        },
        headers=patient_headers,
    ).json()
    client.post(
        "/api/v1/queue/join",
        json={"appointment_id": booked["id"]},
        headers=admin_headers,
    )

    mine = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}/my-position", headers=patient_headers
    ).json()
    assert mine["token_number"] == 1
    assert mine["people_ahead"] == 0
    assert mine["doctor_present"] is True
    assert "मिनट" in mine["message_hi"]
    assert "minutes" in mine["message_en"]


def test_called_patient_is_told_which_room(client, admin, open_queue, register_user):
    _, admin_headers = admin
    _, patient_headers = register_user(phone="9111100201", role="patient")
    booked = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": open_queue["id"],
            "appointment_date": local_today().isoformat(),
        },
        headers=patient_headers,
    ).json()
    client.post(
        "/api/v1/queue/join",
        json={"appointment_id": booked["id"]},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=admin_headers
    )

    mine = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}/my-position", headers=patient_headers
    ).json()
    assert mine["status"] == "called"
    assert "OPD 12" in mine["message_en"]


def test_patient_not_in_the_queue_gets_a_clear_answer(
    client, open_queue, register_user
):
    _, patient_headers = register_user(phone="9111100211", role="patient")
    resp = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}/my-position", headers=patient_headers
    )
    assert resp.status_code == 404


def test_patients_cannot_read_the_whole_queue(client, open_queue, register_user):
    _, patient_headers = register_user(phone="9111100221", role="patient")
    resp = client.get(
        f"/api/v1/queue/doctors/{open_queue['id']}", headers=patient_headers
    )
    assert resp.status_code == 403


def test_a_doctor_cannot_run_another_doctors_queue(
    client, admin, open_queue, department, register_user
):
    _, admin_headers = admin
    other_user, other_headers = register_user(phone="9111100231", role="doctor")
    client.post(
        "/api/v1/doctors",
        json={
            "user_id": other_user["id"],
            "department_id": department["id"],
            "registration_no": "MH-2022-33333",
        },
        headers=admin_headers,
    )
    resp = client.post(
        f"/api/v1/queue/doctors/{open_queue['id']}/call-next", headers=other_headers
    )
    assert resp.status_code == 403
