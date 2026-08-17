"""Room 1 — signal ingest, fusion rules and roster comparison.

Duty windows are built around the *current* hospital-local hour so the suite
does not depend on when it happens to run.
"""

from datetime import datetime, time, timedelta, timezone

import pytest

from app.core.config import settings
from app.core.timeutil import local_now, local_today
from app.modules.presence import service

DEVICE_HEADERS = {"X-Device-Key": settings.device_api_key}
TAG = "TAG-0042-ABCD"


def _window_around_now() -> tuple[time, time]:
    """A duty window guaranteed to contain the current local time."""
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 2, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)
    return start, end


@pytest.fixture(autouse=True)
def _default_to_admin(client, admin):
    """Presence reads require a logged-in user.

    Every test here needs some caller identity, so the client defaults to the
    admin token. Requests that pass their own Authorization header still
    override this, which is how the role-rejection tests below work.
    """
    _, headers = admin
    client.headers.update(headers)


@pytest.fixture
def reader(client, admin, department):
    _, headers = admin
    resp = client.post(
        "/api/v1/presence/devices",
        json={
            "device_uid": "RDR-OPD12",
            "device_type": "rfid_reader",
            "room": "OPD 12",
            "department_id": department["id"],
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def tagged_doctor(client, admin, doctor):
    """A doctor with an RFID card already enrolled."""
    _, admin_headers = admin
    profile, _ = doctor
    client.post(
        f"/api/v1/doctors/{profile['id']}/credentials",
        json={"credential_type": "rfid", "raw_value": TAG, "label": "ID card"},
        headers=admin_headers,
    )
    return profile


@pytest.fixture
def rostered_doctor(client, admin, tagged_doctor):
    """...and rostered into OPD 12 for a window covering right now."""
    _, admin_headers = admin
    start, end = _window_around_now()
    resp = client.post(
        f"/api/v1/doctors/{tagged_doctor['id']}/duty-slots",
        json={
            "day_of_week": local_today().weekday(),
            "start_time": start.isoformat(),
            "end_time": end.isoformat(),
            "room": "OPD 12",
            "valid_from": (local_today() - timedelta(days=1)).isoformat(),
        },
        headers=admin_headers,
    )
    assert resp.status_code == 201, resp.text
    return tagged_doctor


def _signal(client, **overrides):
    payload = {
        "device_uid": "RDR-OPD12",
        "credential_type": "rfid",
        "raw_value": TAG,
        "direction": "in",
    }
    payload.update(overrides)
    return client.post(
        "/api/v1/presence/signals", json=payload, headers=DEVICE_HEADERS
    )


# --- device registry -------------------------------------------------------


def test_device_registration_requires_admin(client, doctor):
    _, doc_headers = doctor
    resp = client.post(
        "/api/v1/presence/devices",
        json={"device_uid": "X", "device_type": "rfid_reader", "room": "OPD 1"},
        headers=doc_headers,
    )
    assert resp.status_code == 403


def test_duplicate_device_uid_rejected(client, admin, reader):
    _, headers = admin
    dup = client.post(
        "/api/v1/presence/devices",
        json={"device_uid": "RDR-OPD12", "device_type": "face_camera", "room": "OPD 9"},
        headers=headers,
    )
    assert dup.status_code == 409


# --- ingest authentication -------------------------------------------------


def test_signal_requires_device_key(client, reader, tagged_doctor):
    resp = client.post(
        "/api/v1/presence/signals",
        json={"device_uid": "RDR-OPD12", "credential_type": "rfid", "raw_value": TAG},
    )
    assert resp.status_code == 401


def test_wrong_device_key_rejected(client, reader, tagged_doctor):
    resp = client.post(
        "/api/v1/presence/signals",
        json={"device_uid": "RDR-OPD12", "credential_type": "rfid", "raw_value": TAG},
        headers={"X-Device-Key": "not-the-key"},
    )
    assert resp.status_code == 401


def test_unknown_device_rejected(client, tagged_doctor):
    resp = _signal(client, device_uid="RDR-GHOST")
    assert resp.status_code == 404


# --- fusion ----------------------------------------------------------------


def test_rfid_signal_marks_doctor_present(client, reader, tagged_doctor):
    resp = _signal(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["matched"] is True
    assert body["status"] == "present"
    assert body["room"] == "OPD 12"

    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["status"] == "present"
    assert state["doctor_name"] == "Dr. Sharma"
    assert state["since"] is not None
    assert state["confidence"] == pytest.approx(0.95)


def test_unmatched_credential_is_recorded_but_changes_nothing(
    client, admin, reader, tagged_doctor
):
    _, admin_headers = admin
    resp = _signal(client, raw_value="TAG-INTRUDER-9999")
    assert resp.status_code == 200
    assert resp.json()["matched"] is False

    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["status"] == "unknown"

    # The unrecognised tag is still retained for security review.
    unmatched = client.get("/api/v1/presence/unmatched", headers=admin_headers).json()
    assert len(unmatched) == 1
    assert unmatched[0]["room"] == "OPD 12"


def test_low_confidence_face_match_does_not_change_state(
    client, admin, reader, doctor
):
    _, admin_headers = admin
    profile, _ = doctor
    client.post(
        f"/api/v1/doctors/{profile['id']}/credentials",
        json={"credential_type": "face", "raw_value": "FACE-TEMPLATE-DIGEST-1"},
        headers=admin_headers,
    )
    resp = _signal(
        client,
        credential_type="face",
        raw_value="FACE-TEMPLATE-DIGEST-1",
        confidence=0.31,
    )
    assert resp.json()["matched"] is True
    assert resp.json()["status"] is None
    assert "Confidence too low" in resp.json()["reason"]

    state = client.get(f"/api/v1/presence/doctors/{profile['id']}").json()
    assert state["status"] == "unknown"


def test_repeat_pings_do_not_reset_since(client, reader, tagged_doctor):
    _signal(client)
    first = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    _signal(client, direction="seen")
    second = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()

    assert second["since"] == first["since"]
    assert second["present_minutes"] == 0


def test_exit_signal_marks_absent(client, reader, tagged_doctor):
    _signal(client)
    _signal(client, direction="out")
    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["status"] == "absent"
    assert state["room"] is None
    assert state["since"] is None


def test_room_move_is_logged_and_keeps_since(client, admin, reader, tagged_doctor):
    _, admin_headers = admin
    client.post(
        "/api/v1/presence/devices",
        json={"device_uid": "RDR-OPD05", "device_type": "rfid_reader", "room": "OPD 5"},
        headers=admin_headers,
    )
    _signal(client)
    before = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()

    _signal(client, device_uid="RDR-OPD05")
    after = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()

    assert after["room"] == "OPD 5"
    assert after["since"] == before["since"]  # still one continuous presence

    events = client.get(
        f"/api/v1/presence/doctors/{tagged_doctor['id']}/events",
        headers=admin_headers,
    ).json()
    assert any("Moved from OPD 12 to OPD 5" in e["note"] for e in events)


def test_late_arriving_signal_does_not_overwrite_newer_state(
    client, reader, tagged_doctor
):
    now = datetime.now(timezone.utc)
    _signal(client, observed_at=now.isoformat(), direction="in")

    # A reader that was offline flushes an older exit reading afterwards.
    _signal(
        client,
        observed_at=(now - timedelta(minutes=10)).isoformat(),
        direction="out",
    )
    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["status"] == "present"


def test_batch_replays_oldest_first(client, reader, tagged_doctor):
    now = datetime.now(timezone.utc)
    resp = client.post(
        "/api/v1/presence/signals/batch",
        json={
            "signals": [
                {
                    "device_uid": "RDR-OPD12",
                    "credential_type": "rfid",
                    "raw_value": TAG,
                    "direction": "out",
                    "observed_at": (now - timedelta(minutes=10)).isoformat(),
                },
                {
                    "device_uid": "RDR-OPD12",
                    "credential_type": "rfid",
                    "raw_value": TAG,
                    "direction": "in",
                    # Comfortably inside the trust window, so the fused state
                    # is still 'present' and not 'stale' when we read it back.
                    "observed_at": (now - timedelta(minutes=1)).isoformat(),
                },
            ]
        },
        headers=DEVICE_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["matched"] == 2
    # Posted out-of-order, but the newest reading wins.
    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["status"] == "present"


def test_future_timestamp_rejected(client, reader, tagged_doctor):
    ahead = datetime.now(timezone.utc) + timedelta(hours=2)
    resp = _signal(client, observed_at=ahead.isoformat())
    assert resp.status_code == 422


def test_presence_goes_stale_without_fresh_signals(client, reader, tagged_doctor):
    old = datetime.now(timezone.utc) - timedelta(
        seconds=settings.presence_ttl_seconds + 60
    )
    _signal(client, observed_at=old.isoformat())

    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["status"] == "stale"
    assert state["present_minutes"] is None


def test_sweep_demotes_stale_presence(client, admin, reader, tagged_doctor, db_session):
    _, admin_headers = admin
    old = datetime.now(timezone.utc) - timedelta(
        seconds=settings.presence_ttl_seconds + 60
    )
    _signal(client, observed_at=old.isoformat())

    swept = client.post("/api/v1/presence/sweep", headers=admin_headers).json()
    assert swept["demoted"] == 1

    events = client.get(
        f"/api/v1/presence/doctors/{tagged_doctor['id']}/events", headers=admin_headers
    ).json()
    assert any(e["to_status"] == "stale" for e in events)


# --- roster comparison -----------------------------------------------------


def test_present_in_the_rostered_room(client, reader, rostered_doctor):
    _signal(client)
    state = client.get(f"/api/v1/presence/doctors/{rostered_doctor['id']}").json()
    assert state["deviation"] == "on_duty_as_rostered"
    assert state["expected_room"] == "OPD 12"
    assert state["minutes_late"] is not None


def test_present_in_the_wrong_room(client, admin, reader, rostered_doctor):
    _, admin_headers = admin
    client.post(
        "/api/v1/presence/devices",
        json={"device_uid": "RDR-OPD05", "device_type": "rfid_reader", "room": "OPD 5"},
        headers=admin_headers,
    )
    _signal(client, device_uid="RDR-OPD05")
    state = client.get(f"/api/v1/presence/doctors/{rostered_doctor['id']}").json()
    assert state["deviation"] == "wrong_room"
    assert state["expected_room"] == "OPD 12"


def test_absent_while_rostered_is_flagged(client, reader, rostered_doctor):
    state = client.get(f"/api/v1/presence/doctors/{rostered_doctor['id']}").json()
    assert state["status"] == "unknown"
    assert state["deviation"] == "absent_while_rostered"
    assert state["minutes_late"] >= 0


def test_present_outside_roster_is_flagged(client, reader, tagged_doctor):
    # tagged_doctor has no duty slot at all.
    _signal(client)
    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["deviation"] == "present_off_roster"


def test_not_rostered_and_not_present(client, reader, tagged_doctor):
    state = client.get(f"/api/v1/presence/doctors/{tagged_doctor['id']}").json()
    assert state["deviation"] == "not_rostered"


def test_approved_leave_supersedes_roster_comparison(
    client, admin, reader, rostered_doctor
):
    _, admin_headers = admin
    leave = client.post(
        f"/api/v1/doctors/{rostered_doctor['id']}/leaves",
        json={
            "start_date": local_today().isoformat(),
            "end_date": local_today().isoformat(),
            "reason": "Sick",
        },
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=admin_headers,
    )
    state = client.get(f"/api/v1/presence/doctors/{rostered_doctor['id']}").json()
    assert state["deviation"] == "on_approved_leave"


# --- manual override and live board ----------------------------------------


def test_reception_can_mark_presence_manually(client, admin, tagged_doctor):
    _, admin_headers = admin
    resp = client.post(
        "/api/v1/presence/manual",
        json={
            "doctor_id": tagged_doctor["id"],
            "status": "present",
            "room": "OPD 12",
            "note": "Card reader offline",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "present"
    assert resp.json()["last_credential_type"] == "manual"


def test_patients_cannot_set_manual_presence(client, register_user, tagged_doctor):
    _, patient_headers = register_user(phone="9000000077", role="patient")
    resp = client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": tagged_doctor["id"], "status": "present"},
        headers=patient_headers,
    )
    assert resp.status_code == 403


def test_live_board_filters_by_status(client, admin, reader, tagged_doctor):
    _, admin_headers = admin
    _signal(client)
    present = client.get(
        "/api/v1/presence/live?presence_status=present", headers=admin_headers
    ).json()
    assert [row["doctor_id"] for row in present] == [tagged_doctor["id"]]

    absent = client.get(
        "/api/v1/presence/live?presence_status=absent", headers=admin_headers
    ).json()
    assert absent == []


def test_is_doctor_present_helper(client, reader, tagged_doctor, db_session):
    assert service.is_doctor_present(db_session, tagged_doctor["id"]) is False
    _signal(client)
    assert service.is_doctor_present(db_session, tagged_doctor["id"]) is True
