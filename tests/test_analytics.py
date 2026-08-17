"""Room 8 — live board, attendance, wait times, load and the Health Dept rollup."""

from datetime import time, timedelta

import pytest

from app.core.timeutil import local_now, local_today
from app.modules.analytics import service


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
                "valid_from": (local_today() - timedelta(days=30)).isoformat(),
            },
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
    return profile


@pytest.fixture
def health_dept(register_user):
    return register_user(
        phone="9444400001", role="health_dept", full_name="State Health Officer"
    )


def _add_patient(client, admin, db_session, name, phone, doctor_id, age=30):
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
    return patient, appointment


# --- access control --------------------------------------------------------


def test_analytics_is_closed_to_patients_and_doctors(
    client, clinic, doctor, register_user
):
    _, doc_headers = doctor
    _, patient_headers = register_user(phone="9444400009", role="patient")
    for headers in (doc_headers, patient_headers):
        assert client.get("/api/v1/analytics/live", headers=headers).status_code == 403


def test_health_department_accounts_can_read_reports(client, clinic, health_dept):
    _, headers = health_dept
    assert client.get("/api/v1/analytics/live", headers=headers).status_code == 200
    assert (
        client.get("/api/v1/analytics/health-department", headers=headers).status_code
        == 200
    )


# --- live overview ---------------------------------------------------------


def test_live_overview_counts_an_absent_rostered_doctor(client, admin, clinic):
    _, headers = admin
    body = client.get("/api/v1/analytics/live", headers=headers).json()

    assert body["doctors_total"] == 1
    assert body["doctors_present"] == 0
    assert body["doctors_absent_while_rostered"] == 1
    assert body["doctors"][0]["deviation"] == "absent_while_rostered"
    assert body["doctors"][0]["minutes_late"] >= 0


def test_live_overview_reflects_arrival_and_waiting_patients(
    client, admin, clinic, db_session
):
    _, headers = admin
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=headers,
    )
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=headers)

    for i in range(3):
        _, appointment = _add_patient(
            client, admin, db_session, f"Patient {i}", f"944440002{i}", clinic["id"]
        )
        client.post(
            "/api/v1/queue/join",
            json={"appointment_id": appointment.id},
            headers=headers,
        )

    body = client.get("/api/v1/analytics/live", headers=headers).json()
    assert body["doctors_present"] == 1
    assert body["doctors_absent_while_rostered"] == 0
    assert body["patients_waiting"] == 3
    assert body["doctors"][0]["waiting_count"] == 3
    assert body["doctors"][0]["room"] == "OPD 12"


def test_live_overview_counts_active_emergencies(
    client, admin, clinic, department, db_session
):
    _, headers = admin
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=headers,
    )
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=headers)
    client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "red",
            "complaint": "Chest pain",
            "department_id": department["id"],
            "patient": {"full_name": "Emergency Case", "phone": "9444400030", "age": 55},
        },
        headers=headers,
    )

    body = client.get("/api/v1/analytics/live", headers=headers).json()
    assert body["active_emergencies"] == 1


def test_leave_shows_separately_from_absence(client, admin, clinic):
    _, headers = admin
    leave = client.post(
        f"/api/v1/doctors/{clinic['id']}/leaves",
        json={
            "start_date": local_today().isoformat(),
            "end_date": local_today().isoformat(),
        },
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=headers,
    )

    body = client.get("/api/v1/analytics/live", headers=headers).json()
    assert body["doctors_on_leave"] == 1
    # Being on approved leave is not the same as failing to turn up.
    assert body["doctors_absent_while_rostered"] == 0


# --- attendance ------------------------------------------------------------


def test_attendance_report_records_a_missing_doctor(client, admin, clinic):
    _, headers = admin
    body = client.get(
        f"/api/v1/analytics/attendance?start_date={local_today().isoformat()}"
        f"&end_date={local_today().isoformat()}",
        headers=headers,
    ).json()

    row = body["rows"][0]
    assert row["days_rostered"] == 1
    assert row["days_present"] == 0
    assert row["days_absent"] == 1
    assert row["attendance_rate"] == 0.0


def test_attendance_report_records_arrival(client, admin, clinic):
    _, headers = admin
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=headers,
    )
    body = client.get(
        f"/api/v1/analytics/attendance?start_date={local_today().isoformat()}"
        f"&end_date={local_today().isoformat()}",
        headers=headers,
    ).json()

    row = body["rows"][0]
    assert row["days_present"] == 1
    assert row["attendance_rate"] == 1.0
    assert row["doctor_name"] == "Dr. Sharma"
    # The window opened an hour ago, so arriving now counts as late.
    assert row["days_late"] == 1
    assert row["average_minutes_late"] > 0


def test_leave_days_are_not_counted_as_absence(client, admin, clinic):
    _, headers = admin
    leave = client.post(
        f"/api/v1/doctors/{clinic['id']}/leaves",
        json={
            "start_date": local_today().isoformat(),
            "end_date": local_today().isoformat(),
        },
        headers=headers,
    ).json()
    client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=headers,
    )

    body = client.get(
        f"/api/v1/analytics/attendance?start_date={local_today().isoformat()}"
        f"&end_date={local_today().isoformat()}",
        headers=headers,
    ).json()
    row = body["rows"][0]
    assert row["days_on_leave"] == 1
    assert row["days_rostered"] == 0
    assert row["days_absent"] == 0


def test_absence_patterns_surface_a_weekday_habit(client, admin, clinic):
    """Absent 8% overall but 60% of Saturdays is a rostering problem."""
    _, headers = admin
    body = client.get(
        f"/api/v1/analytics/absence-patterns?start_date={(local_today() - timedelta(days=13)).isoformat()}"
        f"&end_date={local_today().isoformat()}",
        headers=headers,
    ).json()

    # Nobody has ever arrived, so every weekday shows a 100% absence rate.
    assert len(body) == 7
    assert all(row["absence_rate"] == 1.0 for row in body)
    assert {row["weekday_name"] for row in body} == {
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
        "Saturday",
        "Sunday",
    }


# --- wait times ------------------------------------------------------------


def test_wait_time_report_is_empty_without_data(client, admin, clinic):
    _, headers = admin
    body = client.get("/api/v1/analytics/wait-times", headers=headers).json()
    assert body["overall"] is None
    assert body["by_doctor"] == []


def test_wait_time_report_measures_token_to_call(client, admin, clinic, db_session):
    _, headers = admin
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=headers,
    )
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=headers)

    _, appointment = _add_patient(
        client, admin, db_session, "Asha Devi", "9444400040", clinic["id"]
    )
    client.post(
        "/api/v1/queue/join", json={"appointment_id": appointment.id}, headers=headers
    )
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/call-next", headers=headers)

    body = client.get("/api/v1/analytics/wait-times", headers=headers).json()
    assert body["overall"]["sample_size"] == 1
    assert body["overall"]["mean_minutes"] >= 0
    assert body["by_doctor"][0]["label"] == "Dr. Sharma"
    assert body["by_department"][0]["label"] == "General Medicine"


def test_percentile_maths():
    row = service._summarise("test", [1, 2, 3, 4, 5, 6, 7, 8, 9, 10])
    assert row.sample_size == 10
    assert row.mean_minutes == 5.5
    assert row.median_minutes == 5.5
    # Nearest-rank p90: index round(0.9 * 9) = 8, so the ninth value.
    assert row.p90_minutes == 9.0
    assert row.max_minutes == 10


def test_percentile_handles_a_single_sample():
    row = service._summarise("test", [7])
    assert row.median_minutes == row.p90_minutes == 7.0
    assert row.max_minutes == 7


# --- load and channels -----------------------------------------------------


def test_department_load(client, admin, clinic, db_session):
    _, headers = admin
    for i in range(2):
        _add_patient(
            client, admin, db_session, f"Patient {i}", f"944440005{i}", clinic["id"]
        )

    body = client.get("/api/v1/analytics/department-load", headers=headers).json()
    row = body[0]
    assert row["department_name"] == "General Medicine"
    assert row["appointments"] == 2
    assert row["doctors"] == 1
    assert row["utilisation_pct"] > 0


def test_channel_mix_shows_every_route_that_was_used(
    client, admin, clinic, register_user, db_session
):
    _, admin_headers = admin

    # Website booking by an account holder.
    _, patient_headers = register_user(phone="9444400060", role="patient")
    client.post(
        "/api/v1/booking/appointments?channel=website",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
        },
        headers=patient_headers,
    )
    # Kiosk walk-in.
    from app.core.config import settings

    client.post(
        "/api/v1/booking/kiosk/book",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
            "patient": {"full_name": "Kiosk Walk In", "phone": "9444400061", "age": 65},
        },
        headers={"X-Device-Key": settings.device_api_key},
    )

    body = client.get("/api/v1/analytics/channels", headers=admin_headers).json()
    channels = {row["channel"]: row for row in body}
    assert "website" in channels
    assert "kiosk" in channels
    assert sum(row["bookings"] for row in body) == 2
    assert abs(sum(row["share_pct"] for row in body) - 100.0) < 0.2


def test_notification_stats(client, admin, clinic, register_user):
    _, admin_headers = admin
    _, patient_headers = register_user(phone="9444400070", role="patient")
    client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
        },
        headers=patient_headers,
    )
    client.post("/api/v1/notifications/dispatch", headers=admin_headers)

    body = client.get("/api/v1/analytics/notifications", headers=admin_headers).json()
    sms = next(row for row in body if row["channel"] == "sms")
    assert sms["sent"] == 1
    assert sms["delivery_rate"] == 1.0


# --- health department rollup ----------------------------------------------


def test_health_department_summary_raises_alerts(client, admin, clinic, health_dept):
    _, headers = health_dept
    body = client.get(
        f"/api/v1/analytics/health-department"
        f"?start_date={local_today().isoformat()}&end_date={local_today().isoformat()}",
        headers=headers,
    ).json()

    assert body["departments"] == 1
    assert body["doctors"] == 1
    # The doctor never arrived, so attendance is 0% and that must be flagged.
    assert body["doctor_attendance_rate"] == 0.0
    assert any("attendance" in alert for alert in body["alerts"])


def test_health_department_summary_stays_quiet_when_things_are_fine(
    client, admin, clinic, health_dept
):
    _, admin_headers = admin
    _, hd_headers = health_dept
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )

    body = client.get(
        f"/api/v1/analytics/health-department"
        f"?start_date={local_today().isoformat()}&end_date={local_today().isoformat()}",
        headers=hd_headers,
    ).json()
    assert body["doctor_attendance_rate"] == 1.0
    assert not any("attendance" in alert for alert in body["alerts"])


def test_summary_counts_emergencies(client, admin, clinic, department, health_dept):
    _, admin_headers = admin
    _, hd_headers = health_dept
    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": clinic["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )
    client.post(f"/api/v1/queue/doctors/{clinic['id']}/open", json={}, headers=admin_headers)
    client.post(
        "/api/v1/emergency/triage",
        json={
            "triage_level": "orange",
            "complaint": "Severe asthma attack",
            "department_id": department["id"],
            "patient": {"full_name": "Asthma Case", "phone": "9444400080", "age": 44},
        },
        headers=admin_headers,
    )

    body = client.get("/api/v1/analytics/health-department", headers=hd_headers).json()
    assert body["emergencies_handled"] == 1
