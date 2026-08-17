"""Room 2 — roster, leaves, credentials and expected availability."""

from datetime import date, datetime, time, timedelta

from app.core.security import credential_fingerprint
from app.modules.doctors import service
from app.modules.doctors.models import DoctorCredential

MONDAY = date(2026, 8, 17)  # a real Monday
TUESDAY = MONDAY + timedelta(days=1)


def test_department_code_is_uppercased_and_unique(client, admin, department):
    _, headers = admin
    assert department["code"] == "GM"
    dup = client.post(
        "/api/v1/departments", json={"name": "Other", "code": "GM"}, headers=headers
    )
    assert dup.status_code == 409


def test_only_admin_creates_departments(client, register_user):
    _, patient_headers = register_user(phone="9000000009", role="patient")
    resp = client.post(
        "/api/v1/departments",
        json={"name": "Cardiology", "code": "CARD"},
        headers=patient_headers,
    )
    assert resp.status_code == 403


def test_doctor_profile_requires_doctor_role(client, admin, department, register_user):
    _, admin_headers = admin
    patient, _ = register_user(phone="9000000008", role="patient")
    resp = client.post(
        "/api/v1/doctors",
        json={
            "user_id": patient["id"],
            "department_id": department["id"],
            "registration_no": "MH-0000-1",
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["details"]["current_role"] == "patient"


def test_doctor_response_joins_name_and_department(client, doctor):
    profile, _ = doctor
    listed = client.get("/api/v1/doctors").json()
    assert listed[0]["full_name"] == "Dr. Sharma"
    assert listed[0]["department_name"] == "General Medicine"


def test_duty_slot_clash_is_rejected(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    base = {
        "day_of_week": 0,
        "start_time": "09:00:00",
        "end_time": "13:00:00",
        "room": "OPD 12",
        "valid_from": MONDAY.isoformat(),
    }
    first = client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots", json=base, headers=admin_headers
    )
    assert first.status_code == 201

    overlapping = {**base, "start_time": "12:00:00", "end_time": "16:00:00", "room": "OPD 5"}
    clash = client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json=overlapping,
        headers=admin_headers,
    )
    assert clash.status_code == 409
    assert clash.json()["error"]["details"]["room"] == "OPD 12"


def test_adjacent_duty_slots_are_allowed(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    for start, end in [("09:00:00", "13:00:00"), ("13:00:00", "17:00:00")]:
        resp = client.post(
            f"/api/v1/doctors/{profile['id']}/duty-slots",
            json={
                "day_of_week": 0,
                "start_time": start,
                "end_time": end,
                "room": "OPD 12",
                "valid_from": MONDAY.isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text


def test_end_before_start_is_rejected(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    resp = client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": 0,
            "start_time": "13:00:00",
            "end_time": "09:00:00",
            "room": "OPD 12",
            "valid_from": MONDAY.isoformat(),
        },
        headers=admin_headers,
    )
    assert resp.status_code == 422


def test_availability_reflects_roster_and_capacity(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "13:00:00",
            "room": "OPD 12",
            "valid_from": MONDAY.isoformat(),
        },
        headers=admin_headers,
    )

    monday = client.get(
        f"/api/v1/doctors/{profile['id']}/availability?date={MONDAY.isoformat()}"
    ).json()
    assert monday["total_minutes"] == 240
    assert monday["capacity_estimate"] == 24  # 240 minutes / 10 per patient
    assert monday["windows"][0]["room"] == "OPD 12"

    # Nothing is rostered on Tuesday.
    tuesday = client.get(
        f"/api/v1/doctors/{profile['id']}/availability?date={TUESDAY.isoformat()}"
    ).json()
    assert tuesday["windows"] == []
    assert tuesday["capacity_estimate"] == 0


def test_capacity_is_capped_by_max_patients_per_day(client, admin, doctor, db_session):
    _, admin_headers = admin
    profile, _ = doctor
    client.patch(
        f"/api/v1/doctors/{profile['id']}",
        json={"max_patients_per_day": 10},
        headers=admin_headers,
    )
    client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "17:00:00",
            "room": "OPD 12",
            "valid_from": MONDAY.isoformat(),
        },
        headers=admin_headers,
    )
    result = service.get_day_availability(db_session, profile["id"], MONDAY)
    assert result.total_minutes == 480  # would fit 48 patients
    assert result.capacity_estimate == 10  # but the cap wins


def test_approved_leave_clears_the_day(client, admin, doctor):
    _, admin_headers = admin
    profile, doc_headers = doctor
    client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "13:00:00",
            "room": "OPD 12",
            "valid_from": MONDAY.isoformat(),
        },
        headers=admin_headers,
    )
    leave = client.post(
        f"/api/v1/doctors/{profile['id']}/leaves",
        json={
            "leave_type": "conference",
            "start_date": MONDAY.isoformat(),
            "end_date": MONDAY.isoformat(),
            "reason": "State medical conference",
        },
        headers=doc_headers,
    ).json()

    # Pending leave must not yet affect the roster.
    still_on = client.get(
        f"/api/v1/doctors/{profile['id']}/availability?date={MONDAY.isoformat()}"
    ).json()
    assert still_on["is_on_leave"] is False
    assert still_on["total_minutes"] == 240

    approved = client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=admin_headers,
    )
    assert approved.status_code == 200

    now_off = client.get(
        f"/api/v1/doctors/{profile['id']}/availability?date={MONDAY.isoformat()}"
    ).json()
    assert now_off["is_on_leave"] is True
    assert now_off["leave_type"] == "conference"
    assert now_off["windows"] == []


def test_leave_cannot_be_decided_twice(client, admin, doctor):
    _, admin_headers = admin
    profile, doc_headers = doctor
    leave = client.post(
        f"/api/v1/doctors/{profile['id']}/leaves",
        json={"start_date": MONDAY.isoformat(), "end_date": MONDAY.isoformat()},
        headers=doc_headers,
    ).json()
    client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=admin_headers,
    )
    again = client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "rejected"},
        headers=admin_headers,
    )
    assert again.status_code == 409


def test_doctor_cannot_file_leave_for_another_doctor(
    client, admin, doctor, department, register_user
):
    _, admin_headers = admin
    profile, _ = doctor
    other_user, other_headers = register_user(phone="9000000003", role="doctor")
    client.post(
        "/api/v1/doctors",
        json={
            "user_id": other_user["id"],
            "department_id": department["id"],
            "registration_no": "MH-2020-99999",
        },
        headers=admin_headers,
    )
    resp = client.post(
        f"/api/v1/doctors/{profile['id']}/leaves",
        json={"start_date": MONDAY.isoformat(), "end_date": MONDAY.isoformat()},
        headers=other_headers,
    )
    assert resp.status_code == 403


# --- credentials -----------------------------------------------------------


def test_raw_credential_value_is_never_stored(client, admin, doctor, db_session):
    _, admin_headers = admin
    profile, _ = doctor
    resp = client.post(
        f"/api/v1/doctors/{profile['id']}/credentials",
        json={"credential_type": "rfid", "raw_value": "TAG-0042-ABCD", "label": "ID card"},
        headers=admin_headers,
    )
    assert resp.status_code == 201
    # The response must not echo the fingerprint or the raw tag.
    assert "fingerprint" not in resp.json()
    assert "raw_value" not in resp.json()

    stored = db_session.query(DoctorCredential).one()
    assert stored.fingerprint != "TAG-0042-ABCD"
    assert stored.fingerprint == credential_fingerprint("TAG-0042-ABCD")


def test_credential_cannot_be_shared_between_doctors(
    client, admin, doctor, department, register_user
):
    _, admin_headers = admin
    profile, _ = doctor
    payload = {"credential_type": "rfid", "raw_value": "TAG-0042-ABCD"}
    client.post(
        f"/api/v1/doctors/{profile['id']}/credentials",
        json=payload,
        headers=admin_headers,
    )

    other_user, _ = register_user(phone="9000000004", role="doctor")
    other = client.post(
        "/api/v1/doctors",
        json={
            "user_id": other_user["id"],
            "department_id": department["id"],
            "registration_no": "MH-2021-11111",
        },
        headers=admin_headers,
    ).json()

    clash = client.post(
        f"/api/v1/doctors/{other['id']}/credentials",
        json=payload,
        headers=admin_headers,
    )
    assert clash.status_code == 409


def test_resolve_credential_finds_and_respects_revocation(
    client, admin, doctor, db_session
):
    _, admin_headers = admin
    profile, _ = doctor
    created = client.post(
        f"/api/v1/doctors/{profile['id']}/credentials",
        json={"credential_type": "rfid", "raw_value": "TAG-0042-ABCD"},
        headers=admin_headers,
    ).json()

    found = service.resolve_credential(db_session, "rfid", "TAG-0042-ABCD")
    assert found is not None and found.id == profile["id"]
    assert service.resolve_credential(db_session, "rfid", "TAG-WRONG") is None

    client.delete(
        f"/api/v1/doctors/{profile['id']}/credentials/{created['id']}",
        headers=admin_headers,
    )
    db_session.expire_all()
    assert service.resolve_credential(db_session, "rfid", "TAG-0042-ABCD") is None


def test_credentials_are_admin_only(client, doctor):
    profile, doc_headers = doctor
    resp = client.get(
        f"/api/v1/doctors/{profile['id']}/credentials", headers=doc_headers
    )
    assert resp.status_code == 403


def test_rostered_window_at(client, admin, doctor, db_session):
    _, admin_headers = admin
    profile, _ = doctor
    client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "13:00:00",
            "room": "OPD 12",
            "valid_from": MONDAY.isoformat(),
        },
        headers=admin_headers,
    )
    during = datetime.combine(MONDAY, time(10, 30))
    after = datetime.combine(MONDAY, time(14, 0))
    assert service.rostered_window_at(db_session, profile["id"], during).room == "OPD 12"
    assert service.rostered_window_at(db_session, profile["id"], after) is None


def test_expired_duty_slot_stops_applying(client, admin, doctor, db_session):
    _, admin_headers = admin
    profile, _ = doctor
    client.post(
        f"/api/v1/doctors/{profile['id']}/duty-slots",
        json={
            "day_of_week": 0,
            "start_time": "09:00:00",
            "end_time": "13:00:00",
            "room": "OPD 12",
            "valid_from": (MONDAY - timedelta(days=28)).isoformat(),
            "valid_to": (MONDAY - timedelta(days=7)).isoformat(),
        },
        headers=admin_headers,
    )
    result = service.get_day_availability(db_session, profile["id"], MONDAY)
    assert result.windows == []
