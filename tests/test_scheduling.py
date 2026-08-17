"""Room 4 — predictors, the optimiser, and the training loop."""

from datetime import time, timedelta

import pytest

from app.core.config import settings
from app.core.timeutil import local_now, local_today
from app.modules.scheduling import optimizer, predictors, service
from app.modules.scheduling.optimizer import PlanItem
from app.modules.scheduling.predictors import PredictionContext


@pytest.fixture(autouse=True)
def isolated_model_dir(tmp_path):
    """Keep trained artifacts out of the repo and out of other tests.

    The predictors are module-level singletons, so their cached state has to be
    cleared on both sides of every test.
    """
    original = settings.model_dir
    settings.model_dir = str(tmp_path / "models")
    predictors.reset_all()
    yield
    predictors.reset_all()
    settings.model_dir = original


def _item(appointment_id, booked, duration, *, no_show=0.0, tier=0):
    return PlanItem(
        appointment_id=appointment_id,
        patient_id=appointment_id,
        patient_name=f"Patient {appointment_id}",
        booked_start=booked,
        expected_duration=duration,
        no_show_probability=no_show,
        priority_tier=tier,
    )


# --- optimiser -------------------------------------------------------------


def test_shortest_first_beats_first_come_first_served():
    """The classical result the optimiser rests on, checked numerically."""
    items = [
        _item(1, time(9, 0), 30),
        _item(2, time(9, 10), 5),
        _item(3, time(9, 20), 5),
    ]
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    assert result.total_expected_wait < result.baseline_wait
    assert result.improvement_pct > 0
    # The long case is seen last, so two people are not stuck behind it.
    assert [a.appointment_id for a in result.assignments] == [2, 3, 1]


def test_priority_tier_is_never_traded_for_average_wait():
    items = [
        _item(1, time(9, 0), 5),
        _item(2, time(9, 10), 45, tier=1),  # long, but vulnerable
        _item(3, time(9, 20), 5),
    ]
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    assert result.assignments[0].appointment_id == 2
    assert result.assignments[0].priority_tier == 1


def test_long_case_is_promoted_rather_than_starved():
    """Pure SPT would overtake the 45-minute patient with every short case.

    Booked first thing at 09:00, they would be seen at 10:58 — a 118 minute
    wait for arriving on time. The guard pulls them back to the front.
    """
    # One long case booked first, then eleven short ones every ten minutes.
    items = [_item(1, time(9, 0), 45)] + [
        _item(i, time(9 + (10 * (i - 1)) // 60, (10 * (i - 1)) % 60), 8)
        for i in range(2, 13)
    ]

    unguarded = optimizer._spt_order(items, set())
    assert unguarded[-1].appointment_id == 1  # SPT alone buries the long case

    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    long_case = next(a for a in result.assignments if a.appointment_id == 1)
    assert long_case.promoted_for_fairness is True
    assert long_case.position == 1
    assert any("promoted" in note for note in result.notes)
    # And the guard converges: nobody is left over the threshold.
    assert all(
        a.expected_wait_minutes <= optimizer.MAX_ACCEPTABLE_WAIT_MINUTES
        for a in result.assignments
    )


def test_fairness_guard_terminates_when_the_session_is_simply_overloaded():
    """More work than hours. No ordering can keep everyone under the cap.

    The guard must degrade gracefully rather than loop: once everyone is
    protected the order falls back to booked time, which is the fairest thing
    left to do.
    """
    items = [_item(i, time(9, 0), 20) for i in range(1, 16)]  # 300 minutes
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    assert len(result.assignments) == 15
    assert result.projected_overrun_minutes > 0


def test_nobody_is_scheduled_before_the_doctor_arrives():
    """A doctor who walks in at 11:14 cannot have seen anyone at 09:00."""
    items = [_item(1, time(9, 0), 10), _item(2, time(9, 10), 10)]
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(11, 14), available_until=time(13, 0)
    )
    assert all(a.predicted_start >= time(11, 14) for a in result.assignments)
    # The wait is real and is reported, not hidden.
    assert result.total_expected_wait > 0


def test_no_show_probability_discounts_expected_duration():
    certain = optimizer.optimise(
        [_item(1, time(9, 0), 20, no_show=0.0), _item(2, time(9, 0), 20, no_show=0.0)],
        doctor_id=1,
        available_from=time(9, 0),
        available_until=time(13, 0),
    )
    likely_absent = optimizer.optimise(
        [_item(1, time(9, 0), 20, no_show=0.9), _item(2, time(9, 0), 20, no_show=0.0)],
        doctor_id=1,
        available_from=time(9, 0),
        available_until=time(13, 0),
    )
    assert likely_absent.total_expected_wait < certain.total_expected_wait


def test_overbooking_is_recommended_from_expected_no_shows():
    items = [_item(i, time(9, 0), 10, no_show=0.5) for i in range(1, 7)]
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    assert result.expected_no_shows == pytest.approx(3.0)
    assert result.recommended_overbooking == 3
    assert any("extra patient" in note for note in result.notes)


def test_session_overrun_is_flagged():
    items = [_item(i, time(9, 0), 30) for i in range(1, 11)]  # 300 minutes of work
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(11, 0)
    )
    assert result.projected_overrun_minutes > 0
    assert any(a.overruns_session for a in result.assignments)
    assert any("overrun" in note for note in result.notes)


def test_empty_session_is_handled():
    result = optimizer.optimise(
        [], doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    assert result.assignments == []
    assert result.total_expected_wait == 0
    assert result.improvement_pct == 0


def test_positions_are_sequential_and_complete():
    items = [_item(i, time(9, 0), 5 + i) for i in range(1, 8)]
    result = optimizer.optimise(
        items, doctor_id=1, available_from=time(9, 0), available_until=time(13, 0)
    )
    assert [a.position for a in result.assignments] == list(range(1, 8))
    assert {a.appointment_id for a in result.assignments} == {i for i in range(1, 8)}


# --- heuristic predictors (cold start) -------------------------------------


def test_duration_heuristic_reflects_clinical_reality():
    base = PredictionContext(doctor_avg_minutes=10, prior_visits=3)
    follow_up = PredictionContext(doctor_avg_minutes=10, prior_visits=3, is_follow_up=True)
    senior = PredictionContext(doctor_avg_minutes=10, prior_visits=3, is_senior=True)

    p = predictors.duration_predictor
    assert p.predict(follow_up).value < p.predict(base).value
    assert p.predict(senior).value > p.predict(base).value
    assert p.predict(base).source == "heuristic"


def test_first_visit_takes_longer_than_a_return():
    p = predictors.duration_predictor
    first = p.predict(PredictionContext(doctor_avg_minutes=10, prior_visits=0))
    repeat = p.predict(PredictionContext(doctor_avg_minutes=10, prior_visits=4))
    assert first.value > repeat.value


def test_duration_is_clamped_to_a_sane_range():
    p = predictors.duration_predictor
    assert p.predict(PredictionContext(doctor_avg_minutes=1)).value >= p.MIN_MINUTES
    assert p.predict(PredictionContext(doctor_avg_minutes=500)).value <= p.MAX_MINUTES


def test_no_show_heuristic_responds_to_lead_time_and_history():
    p = predictors.no_show_predictor
    tomorrow = PredictionContext(lead_time_days=1)
    far_off = PredictionContext(lead_time_days=25)
    assert p.predict(far_off).value > p.predict(tomorrow).value

    reliable = PredictionContext(prior_visits=10, prior_no_shows=0)
    unreliable = PredictionContext(prior_visits=10, prior_no_shows=8)
    assert p.predict(unreliable).value > p.predict(reliable).value


def test_kiosk_booking_is_least_likely_to_be_missed():
    p = predictors.no_show_predictor
    kiosk = p.predict(PredictionContext(channel="kiosk"))
    website = p.predict(PredictionContext(channel="website"))
    assert kiosk.value < website.value


def test_no_show_probability_stays_a_probability():
    p = predictors.no_show_predictor
    extreme = PredictionContext(lead_time_days=365, prior_visits=10, prior_no_shows=10)
    assert 0.0 < p.predict(extreme).value <= p.MAX_P


def test_predictors_do_not_look_at_protected_attributes():
    """A scheduling model must not learn to make anyone wait longer."""
    for column in predictors.DURATION_FEATURES + predictors.NO_SHOW_FEATURES:
        assert column not in {"gender", "religion", "caste", "address", "full_name"}


# --- API and end-to-end ----------------------------------------------------


@pytest.fixture
def clinic(client, admin, doctor):
    _, admin_headers = admin
    profile, _ = doctor
    for weekday in range(7):
        client.post(
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
    return profile


def _book(client, headers, doctor_id, **extra):
    payload = {
        "doctor_id": doctor_id,
        "appointment_date": (local_today() + timedelta(days=1)).isoformat(),
    }
    payload.update(extra)
    resp = client.post("/api/v1/booking/appointments", json=payload, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_status_reports_heuristic_before_any_training(client, admin):
    _, headers = admin
    body = client.get("/api/v1/scheduling/status", headers=headers).json()
    assert body["duration"]["source"] == "heuristic"
    assert body["no_show"]["source"] == "heuristic"
    assert body["training_rows"] == 0


def test_prediction_endpoint_reports_provenance(
    client, admin, clinic, register_user
):
    _, admin_headers = admin
    _, patient_headers = register_user(phone="9123400001", role="patient")
    appointment = _book(client, patient_headers, clinic["id"])

    body = client.get(
        f"/api/v1/scheduling/appointments/{appointment['id']}/prediction",
        headers=admin_headers,
    ).json()
    assert body["predicted_duration_minutes"]["source"] == "heuristic"
    assert body["predicted_duration_minutes"]["value"] > 0
    assert 0 < body["no_show_probability"]["value"] < 1
    assert body["predicted_duration_minutes"]["detail"]


def test_optimise_orders_a_real_clinic(client, admin, clinic, register_user):
    _, admin_headers = admin
    for i in range(5):
        _, headers = register_user(phone=f"912340001{i}", role="patient")
        _book(client, headers, clinic["id"])

    body = client.get(
        f"/api/v1/scheduling/doctors/{clinic['id']}/optimise"
        f"?date={(local_today() + timedelta(days=1)).isoformat()}",
        headers=admin_headers,
    ).json()

    assert len(body["assignments"]) == 5
    assert body["available_from"] == "09:00:00"
    assert body["available_until"] == "13:00:00"
    assert body["total_expected_wait"] <= body["baseline_wait"]
    assert body["engine"]["duration"]["source"] == "heuristic"


def test_optimise_respects_senior_citizen_priority(
    client, admin, clinic, register_user, db_session
):
    _, admin_headers = admin
    from app.modules.booking import service as booking_service
    from app.modules.booking.schemas import AppointmentCreate, PatientCreate

    tomorrow = local_today() + timedelta(days=1)
    young = booking_service.create_patient(
        db_session, PatientCreate(full_name="Young Patient", phone="9123400021", age=25)
    )
    elder = booking_service.create_patient(
        db_session, PatientCreate(full_name="Elder Patient", phone="9123400022", age=72)
    )
    for patient in (young, elder):
        booking_service.book(
            db_session,
            patient_id=patient.id,
            payload=AppointmentCreate(doctor_id=clinic["id"], appointment_date=tomorrow),
            channel="website",
        )

    body = client.get(
        f"/api/v1/scheduling/doctors/{clinic['id']}/optimise?date={tomorrow.isoformat()}",
        headers=admin_headers,
    ).json()
    assert body["assignments"][0]["patient_name"] == "Elder Patient"
    assert body["assignments"][0]["priority_tier"] == 1


def test_optimise_uses_live_presence_for_today(
    client, admin, doctor, department, register_user
):
    """The whole point: plan against where the doctor is, not the timetable."""
    _, admin_headers = admin
    profile, _ = doctor

    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 2, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)
    client.post(
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

    absent = client.get(
        f"/api/v1/scheduling/doctors/{profile['id']}/optimise", headers=admin_headers
    ).json()
    assert absent["used_live_presence"] is False

    client.post(
        "/api/v1/presence/manual",
        json={"doctor_id": profile["id"], "status": "present", "room": "OPD 12"},
        headers=admin_headers,
    )
    present = client.get(
        f"/api/v1/scheduling/doctors/{profile['id']}/optimise", headers=admin_headers
    ).json()
    assert present["used_live_presence"] is True


def test_optimise_refuses_a_leave_day(client, admin, clinic):
    _, admin_headers = admin
    tomorrow = local_today() + timedelta(days=1)
    leave = client.post(
        f"/api/v1/doctors/{clinic['id']}/leaves",
        json={"start_date": tomorrow.isoformat(), "end_date": tomorrow.isoformat()},
        headers=admin_headers,
    ).json()
    client.post(
        f"/api/v1/leaves/{leave['id']}/decision",
        json={"status": "approved"},
        headers=admin_headers,
    )
    resp = client.get(
        f"/api/v1/scheduling/doctors/{clinic['id']}/optimise?date={tomorrow.isoformat()}",
        headers=admin_headers,
    )
    assert resp.status_code == 409


def test_saved_plan_is_retrievable(client, admin, clinic, register_user):
    _, admin_headers = admin
    _, headers = register_user(phone="9123400031", role="patient")
    _book(client, headers, clinic["id"])

    saved = client.post(
        f"/api/v1/scheduling/doctors/{clinic['id']}/optimise",
        json={
            "plan_date": (local_today() + timedelta(days=1)).isoformat(),
            "save": True,
        },
        headers=admin_headers,
    )
    assert saved.status_code == 200

    plans = client.get(
        f"/api/v1/scheduling/doctors/{clinic['id']}/plans", headers=admin_headers
    ).json()
    assert len(plans) == 1
    assert plans[0]["doctor_id"] == clinic["id"]


def test_patients_cannot_run_the_optimiser(client, clinic, register_user):
    _, headers = register_user(phone="9123400041", role="patient")
    resp = client.get(
        f"/api/v1/scheduling/doctors/{clinic['id']}/optimise", headers=headers
    )
    assert resp.status_code == 403


# --- training loop ---------------------------------------------------------


def test_training_needs_enough_data(client, admin, doctor):
    _, headers = admin
    result = client.post(
        "/api/v1/scheduling/train", json={"synthetic_rows": 10}, headers=headers
    ).json()
    assert result["duration"]["trained"] is False
    assert "Need" in result["duration"]["reason"]


def test_synthetic_training_produces_working_models(client, admin, doctor):
    _, headers = admin
    result = client.post(
        "/api/v1/scheduling/train", json={"synthetic_rows": 500}, headers=headers
    ).json()

    assert result["synthetic_rows_generated"] == 500
    assert result["duration"]["trained"] is True
    assert result["no_show"]["trained"] is True
    # Trained on fabricated data, and says so — everywhere.
    assert result["duration"]["trained_on_synthetic"] is True
    assert result["duration"]["metrics"]["mae_minutes"] > 0
    assert result["no_show"]["metrics"]["roc_auc"] > 0.6

    status = client.get("/api/v1/scheduling/status", headers=headers).json()
    assert status["duration"]["source"] == "model"
    assert status["no_show"]["source"] == "model"
    assert status["synthetic_rows"] == 500
    assert status["duration"]["trained_on_synthetic"] is True


def test_predictions_switch_to_the_model_after_training(
    client, admin, clinic, register_user
):
    _, admin_headers = admin
    _, patient_headers = register_user(phone="9123400051", role="patient")
    appointment = _book(client, patient_headers, clinic["id"])

    before = client.get(
        f"/api/v1/scheduling/appointments/{appointment['id']}/prediction",
        headers=admin_headers,
    ).json()
    assert before["predicted_duration_minutes"]["source"] == "heuristic"

    client.post(
        "/api/v1/scheduling/train", json={"synthetic_rows": 500}, headers=admin_headers
    )

    after = client.get(
        f"/api/v1/scheduling/appointments/{appointment['id']}/prediction",
        headers=admin_headers,
    ).json()
    assert after["predicted_duration_minutes"]["source"] == "model"
    assert after["no_show_probability"]["source"] == "model"
    assert 3 <= after["predicted_duration_minutes"]["value"] <= 60


def test_a_corrupt_artifact_falls_back_instead_of_crashing(client, admin, doctor):
    _, headers = admin
    client.post(
        "/api/v1/scheduling/train", json={"synthetic_rows": 500}, headers=headers
    )
    assert predictors.duration_predictor.is_trained

    predictors.duration_predictor.path.write_bytes(b"not a joblib file")
    predictors.reset_all()

    # Degrades to the heuristic; the scheduling engine keeps running.
    prediction = predictors.duration_predictor.predict(PredictionContext())
    assert prediction.source == "heuristic"
    assert prediction.value > 0


def test_training_is_admin_only(client, register_user):
    _, headers = register_user(phone="9123400061", role="patient")
    resp = client.post(
        "/api/v1/scheduling/train", json={"synthetic_rows": 0}, headers=headers
    )
    assert resp.status_code == 403


def test_completed_consultation_becomes_a_training_row(
    client, admin, clinic, register_user, db_session
):
    """The feedback loop: real outcomes flow back into the models."""
    from datetime import datetime, timezone

    _, admin_headers = admin
    _, patient_headers = register_user(phone="9123400071", role="patient")
    booked = _book(client, patient_headers, clinic["id"])

    from app.modules.booking import service as booking_service

    appointment = booking_service.get_appointment(db_session, booked["id"])
    started = datetime.now(timezone.utc)
    record = service.record_consultation(
        db_session,
        appointment,
        actual_start=started,
        actual_end=started + timedelta(minutes=14),
    )
    assert record.duration_minutes == 14
    assert record.was_no_show is False
    assert record.is_synthetic is False

    status = client.get("/api/v1/scheduling/status", headers=admin_headers).json()
    assert status["training_rows"] == 1
    assert status["synthetic_rows"] == 0


def test_recording_the_same_consultation_twice_is_idempotent(
    client, clinic, register_user, db_session
):
    _, patient_headers = register_user(phone="9123400081", role="patient")
    booked = _book(client, patient_headers, clinic["id"])

    from app.modules.booking import service as booking_service

    appointment = booking_service.get_appointment(db_session, booked["id"])
    first = service.record_consultation(db_session, appointment, was_no_show=True)
    second = service.record_consultation(db_session, appointment, was_no_show=True)
    assert first.id == second.id
