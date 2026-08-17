"""Room 9 — ABHA linking, ORS sync, FHIR export, and the mock/live boundary."""

from datetime import time, timedelta

import pytest

from app.core.timeutil import local_now, local_today
from app.modules.integration import service, verhoeff


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
def patient_and_appointment(client, admin, clinic, db_session):
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import AppointmentCreate, PatientCreate

    patient = booking_service.create_patient(
        db_session,
        PatientCreate(full_name="Asha Devi", phone="9555500001", age=44, gender="female"),
    )
    appointment = booking_service.book(
        db_session,
        patient_id=patient.id,
        payload=AppointmentCreate(
            doctor_id=clinic["id"],
            appointment_date=local_today(),
            reason="Persistent cough",
        ),
        channel="website",
    )
    return patient, appointment


#: A 14-digit ABHA number with a correct Verhoeff check digit.
VALID_ABHA = verhoeff.append_check_digit("9152104372862")


# --- Verhoeff --------------------------------------------------------------


def test_generated_check_digit_validates():
    assert len(VALID_ABHA) == 14
    assert verhoeff.is_valid(VALID_ABHA)


def test_single_digit_error_is_caught():
    """The whole reason for validating locally."""
    for position in range(len(VALID_ABHA)):
        for replacement in "0123456789":
            if replacement == VALID_ABHA[position]:
                continue
            corrupted = (
                VALID_ABHA[:position] + replacement + VALID_ABHA[position + 1 :]
            )
            assert not verhoeff.is_valid(corrupted)


def test_adjacent_transposition_is_caught():
    for position in range(len(VALID_ABHA) - 1):
        a, b = VALID_ABHA[position], VALID_ABHA[position + 1]
        if a == b:
            continue
        swapped = (
            VALID_ABHA[:position] + b + a + VALID_ABHA[position + 2 :]
        )
        assert not verhoeff.is_valid(swapped)


def test_formatting_characters_are_ignored():
    formatted = f"{VALID_ABHA[:2]}-{VALID_ABHA[2:6]}-{VALID_ABHA[6:10]}-{VALID_ABHA[10:]}"
    assert verhoeff.is_valid(formatted)


# --- status ----------------------------------------------------------------


def test_status_reports_offline_stubs_honestly(client, admin):
    _, headers = admin
    body = client.get("/api/v1/integration/status", headers=headers).json()
    for system in ("abha", "ors", "hmis"):
        assert body[system]["mode"] == "mock"
        assert body[system]["live"] is False
        assert "offline stub" in body[system]["note"]


# --- ABHA ------------------------------------------------------------------


def test_abha_link_succeeds_and_is_flagged_as_mock(
    client, admin, patient_and_appointment, db_session
):
    _, headers = admin
    patient, _ = patient_and_appointment

    resp = client.post(
        "/api/v1/integration/abha/link",
        json={
            "patient_id": patient.id,
            "abha_number": VALID_ABHA,
            "consent_reference": "CONSENT-2026-0001",
        },
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["external_id"] == VALID_ABHA
    assert body["status"] == "verified"
    assert body["consent_reference"] == "CONSENT-2026-0001"
    # No credentials configured, so this must not masquerade as a real link.
    assert body["is_mock"] is True

    db_session.refresh(patient)
    assert patient.abha_id == VALID_ABHA


def test_bad_check_digit_is_rejected_before_any_call_out(
    client, admin, patient_and_appointment
):
    _, headers = admin
    patient, _ = patient_and_appointment
    wrong = VALID_ABHA[:-1] + ("0" if VALID_ABHA[-1] != "0" else "1")

    resp = client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": wrong},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "check digit" in resp.json()["error"]["message"]


def test_short_abha_number_is_rejected(client, admin, patient_and_appointment):
    _, headers = admin
    patient, _ = patient_and_appointment
    resp = client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": "1234567890123456789"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_one_abha_number_cannot_cover_two_patients(
    client, admin, clinic, patient_and_appointment, db_session
):
    _, headers = admin
    patient, _ = patient_and_appointment
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import PatientCreate

    other = booking_service.create_patient(
        db_session, PatientCreate(full_name="Someone Else", phone="9555500002")
    )

    client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": VALID_ABHA},
        headers=headers,
    )
    clash = client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": other.id, "abha_number": VALID_ABHA},
        headers=headers,
    )
    assert clash.status_code == 409


def test_relinking_the_same_patient_is_idempotent(
    client, admin, patient_and_appointment
):
    _, headers = admin
    patient, _ = patient_and_appointment
    payload = {"patient_id": patient.id, "abha_number": VALID_ABHA}
    first = client.post("/api/v1/integration/abha/link", json=payload, headers=headers).json()
    second = client.post("/api/v1/integration/abha/link", json=payload, headers=headers).json()
    assert first["id"] == second["id"]


def test_revoking_a_link_clears_the_patient_record(
    client, admin, patient_and_appointment, db_session
):
    _, headers = admin
    patient, _ = patient_and_appointment
    link = client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": VALID_ABHA},
        headers=headers,
    ).json()

    revoked = client.delete(
        f"/api/v1/integration/links/{link['id']}", headers=headers
    ).json()
    assert revoked["status"] == "revoked"

    db_session.refresh(patient)
    assert patient.abha_id is None


# --- ORS -------------------------------------------------------------------


def test_push_to_ors_returns_a_reference(client, admin, patient_and_appointment):
    _, headers = admin
    _, appointment = patient_and_appointment

    body = client.post(
        f"/api/v1/integration/ors/appointments/{appointment.id}/push", headers=headers
    ).json()
    assert body["ok"] is True
    assert body["is_mock"] is True
    assert body["external_id"].startswith("ORS-")


def test_push_to_ors_is_idempotent(client, admin, patient_and_appointment):
    _, headers = admin
    _, appointment = patient_and_appointment
    url = f"/api/v1/integration/ors/appointments/{appointment.id}/push"

    first = client.post(url, headers=headers).json()
    second = client.post(url, headers=headers).json()
    assert first["external_id"] == second["external_id"]
    assert second["detail"] == "Already published"


def test_ors_pull_handles_an_empty_portal(client, admin):
    _, headers = admin
    body = client.post(
        "/api/v1/integration/ors/pull",
        json={"facility_id": "FAC-001"},
        headers=headers,
    ).json()
    assert body["fetched"] == 0
    assert body["is_mock"] is True


# --- FHIR ------------------------------------------------------------------


def test_fhir_bundle_is_well_formed(client, admin, patient_and_appointment):
    _, headers = admin
    patient, appointment = patient_and_appointment

    body = client.get(
        f"/api/v1/integration/hmis/appointments/{appointment.id}/bundle",
        headers=headers,
    ).json()
    bundle = body["bundle"]

    assert bundle["resourceType"] == "Bundle"
    assert bundle["type"] == "transaction"
    assert len(bundle["entry"]) == 2

    resources = {entry["resource"]["resourceType"]: entry["resource"] for entry in bundle["entry"]}
    assert resources["Patient"]["name"][0]["text"] == "Asha Devi"
    assert resources["Patient"]["gender"] == "female"

    encounter = resources["Encounter"]
    assert encounter["class"]["code"] == "AMB"
    assert encounter["subject"]["reference"] == f"Patient/patient-{patient.id}"
    assert encounter["participant"][0]["individual"]["display"] == "Dr. Sharma"
    assert encounter["serviceType"]["text"] == "General Medicine"
    assert encounter["reasonCode"][0]["text"] == "Persistent cough"
    assert (
        encounter["identifier"][0]["value"] == appointment.booking_reference
    )


def test_bundle_carries_the_abha_number_once_linked(
    client, admin, patient_and_appointment
):
    _, headers = admin
    patient, appointment = patient_and_appointment
    client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": VALID_ABHA},
        headers=headers,
    )

    bundle = client.get(
        f"/api/v1/integration/hmis/appointments/{appointment.id}/bundle",
        headers=headers,
    ).json()["bundle"]
    patient_resource = bundle["entry"][0]["resource"]
    systems = {i["system"]: i["value"] for i in patient_resource["identifier"]}
    assert systems["https://healthid.abdm.gov.in/"] == VALID_ABHA


def test_push_encounter_records_a_link(client, admin, patient_and_appointment):
    _, headers = admin
    _, appointment = patient_and_appointment

    body = client.post(
        f"/api/v1/integration/hmis/appointments/{appointment.id}/push", headers=headers
    ).json()
    assert body["ok"] is True
    assert body["is_mock"] is True

    links = client.get(
        "/api/v1/integration/links?system=hmis", headers=headers
    ).json()
    assert len(links) == 1


# --- audit and access ------------------------------------------------------


def test_every_exchange_is_logged(client, admin, patient_and_appointment):
    _, headers = admin
    patient, appointment = patient_and_appointment

    client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": VALID_ABHA},
        headers=headers,
    )
    client.post(
        f"/api/v1/integration/ors/appointments/{appointment.id}/push", headers=headers
    )

    logs = client.get("/api/v1/integration/sync-logs", headers=headers).json()
    operations = {entry["operation"] for entry in logs}
    assert {"verify_number", "push_appointment"} <= operations
    assert all(entry["is_mock"] for entry in logs)
    assert all(entry["duration_ms"] >= 0 for entry in logs)


def test_failures_are_logged_too(client, admin, patient_and_appointment, db_session):
    _, headers = admin
    patient, _ = patient_and_appointment
    wrong = VALID_ABHA[:-1] + ("0" if VALID_ABHA[-1] != "0" else "1")

    client.post(
        "/api/v1/integration/abha/link",
        json={"patient_id": patient.id, "abha_number": wrong},
        headers=headers,
    )
    logs = client.get(
        "/api/v1/integration/sync-logs?sync_status=failed", headers=headers
    ).json()
    assert len(logs) == 1
    assert "check digit" in logs[0]["error"]


def test_sync_logs_never_contain_the_full_payload(
    client, admin, patient_and_appointment
):
    """Health data must not leak into a log table Room 10 does not govern."""
    _, headers = admin
    patient, appointment = patient_and_appointment
    client.post(
        f"/api/v1/integration/ors/appointments/{appointment.id}/push", headers=headers
    )
    logs = client.get("/api/v1/integration/sync-logs", headers=headers).json()
    for entry in logs:
        assert "Asha Devi" not in entry["summary"]
        assert "9555500001" not in entry["summary"]


def test_integration_is_staff_only(client, register_user):
    _, patient_headers = register_user(phone="9555500099", role="patient")
    assert (
        client.get("/api/v1/integration/status", headers=patient_headers).status_code
        == 403
    )
    assert (
        client.get("/api/v1/integration/sync-logs", headers=patient_headers).status_code
        == 403
    )
