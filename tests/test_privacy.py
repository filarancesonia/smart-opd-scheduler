"""Room 10 — encryption, audit, consent, face templates and DPDP data rights."""

from datetime import time, timedelta

import pytest
from sqlalchemy import text

from app.core.config import settings
from app.core.timeutil import local_now, local_today
from app.modules.privacy import crypto, service
from app.modules.privacy.models import ConsentPurpose, FaceTemplate

#: A deterministic 16-dimension stand-in for a real face embedding.
FACE_A = [0.10, 0.22, -0.31, 0.44, 0.05, -0.17, 0.28, 0.36,
          -0.09, 0.41, 0.13, -0.25, 0.30, 0.08, -0.19, 0.21]
FACE_A_NOISY = [v + (0.01 if i % 2 == 0 else -0.01) for i, v in enumerate(FACE_A)]
FACE_B = [-0.30, 0.11, 0.42, -0.08, 0.35, 0.19, -0.27, 0.04,
          0.38, -0.15, 0.23, 0.31, -0.06, 0.44, 0.17, -0.36]


@pytest.fixture
def clinic(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 3, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)
    for weekday in range(7):
        client.post(
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
    return profile


# --- encryption ------------------------------------------------------------


def test_encrypt_decrypt_roundtrip():
    secret = "House 14, Gandhi Nagar, Bhopal"
    ciphertext = crypto.encrypt(secret)
    assert ciphertext != secret
    assert crypto.decrypt(ciphertext) == secret


def test_ciphertext_is_non_deterministic():
    """Same plaintext twice must not produce the same ciphertext."""
    assert crypto.encrypt("same") != crypto.encrypt("same")


def test_tampered_ciphertext_is_rejected():
    ciphertext = crypto.encrypt("House 14")
    tampered = ciphertext[:-4] + "AAAA"
    assert crypto.try_decrypt(tampered) is None


def test_blind_index_is_deterministic_and_one_way():
    value = "9876543210"
    assert crypto.blind_index(value) == crypto.blind_index(value)
    assert crypto.blind_index(value) != value
    assert crypto.blind_index("9876543211") != crypto.blind_index(value)


def test_patient_address_is_ciphertext_in_the_database(db_session):
    """The application sees plaintext; the database only ever holds ciphertext."""
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import PatientCreate

    address = "House 14, Gandhi Nagar, Bhopal"
    patient = booking_service.create_patient(
        db_session,
        PatientCreate(full_name="Asha Devi", phone="9666600001", address=address),
    )
    assert patient.address == address

    raw = db_session.execute(
        text("SELECT address FROM patients WHERE id = :id"), {"id": patient.id}
    ).scalar_one()
    assert raw != address
    assert "Gandhi Nagar" not in raw
    assert crypto.decrypt(raw) == address


# --- audit -----------------------------------------------------------------


def test_state_changing_requests_are_audited(client, admin, register_user):
    _, headers = admin
    client.post(
        "/api/v1/departments",
        json={"name": "Cardiology", "code": "CARD"},
        headers=headers,
    )

    logs = client.get("/api/v1/privacy/audit", headers=headers).json()
    created = [entry for entry in logs if entry["path"].endswith("/departments")]
    assert created
    assert created[0]["action"] == "create"
    assert created[0]["actor_role"] == "admin"
    assert created[0]["status_code"] == 201


def test_reads_are_not_audited(client, admin):
    _, headers = admin
    before = len(client.get("/api/v1/privacy/audit", headers=headers).json())
    client.get("/api/v1/doctors", headers=headers)
    client.get("/api/v1/departments", headers=headers)
    after = len(client.get("/api/v1/privacy/audit", headers=headers).json())
    # Logging every GET in a busy OPD would bury what matters.
    assert after == before


def test_failed_logins_are_recorded_without_the_password(client, admin):
    _, headers = admin
    client.post(
        "/api/v1/auth/login",
        json={"phone": "9000000001", "password": "definitely-wrong-password"},
    )
    logs = client.get("/api/v1/privacy/audit", headers=headers).json()
    failures = [entry for entry in logs if entry["action"] == "login_failed"]
    assert failures
    assert failures[0]["status_code"] == 401
    for entry in logs:
        assert "definitely-wrong-password" not in entry["detail"]


def test_audit_log_is_admin_only(client, register_user):
    _, patient_headers = register_user(phone="9666600009", role="patient")
    assert client.get("/api/v1/privacy/audit", headers=patient_headers).status_code == 403


# --- consent ---------------------------------------------------------------


def test_face_notice_is_published_in_hindi_and_english(client, admin):
    _, headers = admin
    notice = client.get(
        "/api/v1/privacy/notices/face_recognition", headers=headers
    ).json()
    assert "तस्वीर कभी सहेजी नहीं जाएगी" in notice["body_hi"]
    assert "photograph is never stored" in notice["body_en"]
    assert "withdraw" in notice["retention_note_en"]


def test_consent_records_the_notice_version(client, admin, doctor):
    _, headers = admin
    doc_profile, _ = doctor
    from app.modules.doctors import service as doctors_service

    consent = client.post(
        "/api/v1/privacy/consents",
        json={
            "purpose": "face_recognition",
            "user_id": 2,  # the doctor's account
            "notice_version": "1.0",
            "notice_language": "hi",
        },
        headers=headers,
    )
    assert consent.status_code == 201, consent.text
    body = consent.json()
    assert body["status"] == "granted"
    assert body["notice_version"] == "1.0"


def test_consent_requires_exactly_one_subject(client, admin):
    _, headers = admin
    resp = client.post(
        "/api/v1/privacy/consents",
        json={"purpose": "sms_notifications"},
        headers=headers,
    )
    assert resp.status_code == 422


def test_granting_the_same_consent_twice_is_idempotent(client, admin):
    _, headers = admin
    payload = {"purpose": "sms_notifications", "user_id": 1}
    first = client.post("/api/v1/privacy/consents", json=payload, headers=headers).json()
    second = client.post("/api/v1/privacy/consents", json=payload, headers=headers).json()
    assert first["id"] == second["id"]


# --- face templates --------------------------------------------------------


def _grant_face_consent(client, headers, user_id):
    return client.post(
        "/api/v1/privacy/consents",
        json={"purpose": "face_recognition", "user_id": user_id},
        headers=headers,
    ).json()


def test_face_enrolment_is_refused_without_consent(client, admin, doctor):
    """The single most important rule in this module."""
    _, headers = admin
    profile, _ = doctor
    resp = client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["details"]["required_consent"] == "face_recognition"


def test_face_enrolment_stores_a_vector_and_no_image(
    client, admin, doctor, db_session
):
    _, headers = admin
    profile, _ = doctor
    _grant_face_consent(client, headers, profile["user_id"])

    resp = client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["dimensions"] == 16
    # The vector itself never leaves the server.
    assert "embedding" not in body

    # And there is structurally nowhere to put an image.
    columns = {c.name for c in FaceTemplate.__table__.columns}
    assert not {"image", "photo", "picture", "image_path"} & columns


def test_stored_embedding_is_encrypted_at_rest(client, admin, doctor, db_session):
    _, headers = admin
    profile, _ = doctor
    _grant_face_consent(client, headers, profile["user_id"])
    client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    )

    raw = db_session.execute(text("SELECT embedding FROM face_templates")).scalar_one()
    assert not raw.startswith("[")  # not plain JSON
    assert crypto.try_decrypt(raw) is not None


def test_face_matching_recognises_the_same_face(client, admin, doctor):
    _, headers = admin
    profile, _ = doctor
    _grant_face_consent(client, headers, profile["user_id"])
    client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    )

    same = client.post(
        "/api/v1/privacy/face/match",
        json={"embedding": FACE_A_NOISY, "threshold": 0.9},
        headers=headers,
    ).json()
    assert same["matched"] is True
    assert same["doctor_id"] == profile["id"]
    assert same["similarity"] > 0.9


def test_face_matching_rejects_a_different_face(client, admin, doctor):
    _, headers = admin
    profile, _ = doctor
    _grant_face_consent(client, headers, profile["user_id"])
    client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    )

    other = client.post(
        "/api/v1/privacy/face/match",
        json={"embedding": FACE_B, "threshold": 0.75},
        headers=headers,
    ).json()
    assert other["matched"] is False
    assert other["reason"] == "Below the match threshold"


def test_enrolment_registers_a_digest_with_room_2(client, admin, doctor, db_session):
    """Room 1 can match at the door without this table being readable."""
    _, headers = admin
    profile, _ = doctor
    _grant_face_consent(client, headers, profile["user_id"])
    template = client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    ).json()

    from app.modules.doctors import service as doctors_service

    found = doctors_service.resolve_credential(
        db_session, "face", template["template_digest"]
    )
    assert found is not None
    assert found.id == profile["id"]


def test_withdrawing_consent_destroys_the_face_vector(
    client, admin, doctor, db_session
):
    """Withdrawal must destroy the data, not just flip a flag."""
    _, headers = admin
    profile, _ = doctor
    consent = _grant_face_consent(client, headers, profile["user_id"])
    client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": profile["id"], "embedding": FACE_A},
        headers=headers,
    )
    assert db_session.query(FaceTemplate).count() == 1

    withdrawn = client.delete(
        f"/api/v1/privacy/consents/{consent['id']}", headers=headers
    ).json()
    assert withdrawn["status"] == "withdrawn"

    db_session.expire_all()
    assert db_session.query(FaceTemplate).count() == 0


def test_face_endpoints_are_admin_only(client, register_user):
    _, patient_headers = register_user(phone="9666600019", role="patient")
    resp = client.post(
        "/api/v1/privacy/face/enrol",
        json={"doctor_id": 1, "embedding": FACE_A},
        headers=patient_headers,
    )
    assert resp.status_code == 403


# --- DPDP data rights ------------------------------------------------------


def test_data_export_returns_everything_held(client, admin, clinic, register_user):
    _, patient_headers = register_user(
        phone="9666600021", role="patient", full_name="Asha Devi"
    )
    client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
        },
        headers=patient_headers,
    )

    export = client.get("/api/v1/privacy/me/export", headers=patient_headers).json()
    assert export["account"]["full_name"] == "Asha Devi"
    assert export["patient_record"]["phone"] == "9666600021"
    assert len(export["appointments"]) == 1
    assert len(export["notifications"]) == 1
    assert "retained under medical record rules" in export["note"]


def test_export_is_itself_audited(client, admin, register_user):
    _, admin_headers = admin
    _, patient_headers = register_user(phone="9666600022", role="patient")
    client.get("/api/v1/privacy/me/export", headers=patient_headers)

    logs = client.get("/api/v1/privacy/audit", headers=admin_headers).json()
    exports = [entry for entry in logs if entry["action"] == "export"]
    assert exports
    assert "right of access" in exports[0]["detail"]


def test_erasure_anonymises_but_keeps_the_clinical_record(
    client, admin, clinic, register_user, db_session
):
    _, admin_headers = admin
    _, patient_headers = register_user(
        phone="9666600031", role="patient", full_name="Asha Devi"
    )
    booked = client.post(
        "/api/v1/booking/appointments",
        json={
            "doctor_id": clinic["id"],
            "appointment_date": local_today().isoformat(),
        },
        headers=patient_headers,
    ).json()

    request = client.post(
        "/api/v1/privacy/me/requests",
        json={"request_type": "erasure", "detail": "Please remove my details"},
        headers=patient_headers,
    ).json()

    result = client.post(
        f"/api/v1/privacy/requests/{request['id']}/erase", headers=admin_headers
    ).json()
    assert result["anonymised_patient_records"] == 1
    assert any("retained" in note for note in result["retained"])

    from app.modules.booking import service as booking_service

    appointment = booking_service.get_appointment(db_session, booked["id"])
    # The appointment survives — it is a medical record.
    assert appointment is not None
    db_session.expire_all()
    from app.modules.booking.models import Patient

    patient = db_session.get(Patient, appointment.patient_id)
    assert "Asha Devi" not in patient.full_name
    assert patient.phone == "0000000000"


def test_erasure_cannot_be_run_twice(client, admin, register_user):
    _, admin_headers = admin
    _, patient_headers = register_user(phone="9666600041", role="patient")
    request = client.post(
        "/api/v1/privacy/me/requests",
        json={"request_type": "erasure"},
        headers=patient_headers,
    ).json()

    client.post(
        f"/api/v1/privacy/requests/{request['id']}/erase", headers=admin_headers
    )
    again = client.post(
        f"/api/v1/privacy/requests/{request['id']}/erase", headers=admin_headers
    )
    assert again.status_code == 409


def test_erasure_is_admin_only(client, register_user):
    _, patient_headers = register_user(phone="9666600051", role="patient")
    request = client.post(
        "/api/v1/privacy/me/requests",
        json={"request_type": "erasure"},
        headers=patient_headers,
    ).json()
    resp = client.post(
        f"/api/v1/privacy/requests/{request['id']}/erase", headers=patient_headers
    )
    assert resp.status_code == 403


# --- compliance summary ----------------------------------------------------


def test_privacy_status_reports_zero_stored_images(client, admin):
    _, headers = admin
    body = client.get("/api/v1/privacy/status", headers=headers).json()
    assert body["encryption_enabled"] is True
    assert body["face_images_stored"] == 0
    assert any("no image is retained" in note for note in body["dpdp_notes"])


def test_privacy_status_warns_about_default_keys(client, admin):
    _, headers = admin
    original = settings.field_encryption_key
    settings.field_encryption_key = "change-me-too"
    try:
        body = client.get("/api/v1/privacy/status", headers=headers).json()
        assert body["encryption_key_configured"] is False
        assert any("FIELD_ENCRYPTION_KEY" in note for note in body["dpdp_notes"])
    finally:
        settings.field_encryption_key = original


def test_privacy_status_is_admin_only(client, register_user):
    _, patient_headers = register_user(phone="9666600061", role="patient")
    assert (
        client.get("/api/v1/privacy/status", headers=patient_headers).status_code == 403
    )
