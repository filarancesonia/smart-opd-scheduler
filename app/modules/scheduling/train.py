"""Room 4 — training pipeline.

Run directly to bootstrap a demo:

    python -m app.modules.scheduling.train --synthetic 2000

Synthetic rows are written with ``is_synthetic = True`` and every model trained
on them is stamped ``trained_on_synthetic``. That flag is carried all the way
into the API response, so nobody can mistake a seeded demo for evidence
gathered from real patients.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, time, timedelta

import numpy as np
from sklearn.ensemble import GradientBoostingClassifier, GradientBoostingRegressor
from sklearn.metrics import (
    brier_score_loss,
    mean_absolute_error,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import SessionLocal, utcnow
from app.core.errors import ValidationError
from app.modules.scheduling import predictors
from app.modules.scheduling.models import ConsultationRecord, ModelArtifact, ModelKind
from app.modules.scheduling.predictors import (
    CHANNEL_CODES,
    DURATION_FEATURES,
    MIN_TRAINING_ROWS,
    NO_SHOW_FEATURES,
    PredictionContext,
    _row,
)

SEED = 20260817


# --- feature frames --------------------------------------------------------


def _context_from_record(record: ConsultationRecord, doctor_avg: int) -> PredictionContext:
    return PredictionContext(
        doctor_avg_minutes=doctor_avg,
        patient_age=record.patient_age,
        is_follow_up=record.is_follow_up,
        lead_time_days=record.lead_time_days,
        prior_visits=record.prior_visits,
        prior_no_shows=record.prior_no_shows,
        day_of_week=record.day_of_week,
        hour_of_day=record.hour_of_day,
        slot_index=record.slot_index,
        is_senior=record.is_senior,
        has_priority_flag=record.has_priority_flag,
        channel=record.channel,
    )


def _load_records(db: Session) -> list[ConsultationRecord]:
    return list(db.execute(select(ConsultationRecord)).scalars())


def _doctor_averages(db: Session) -> dict[int, int]:
    from app.modules.doctors.models import Doctor

    return {
        d.id: d.avg_consultation_minutes
        for d in db.execute(select(Doctor)).scalars()
    }


# --- training --------------------------------------------------------------


def train_duration(db: Session) -> dict:
    """Fit the consultation-length regressor on completed consultations."""
    averages = _doctor_averages(db)
    rows = [
        r
        for r in _load_records(db)
        if not r.was_no_show and r.duration_minutes is not None
    ]
    if len(rows) < MIN_TRAINING_ROWS:
        return {
            "trained": False,
            "reason": f"Need {MIN_TRAINING_ROWS} completed consultations, have {len(rows)}",
            "n_samples": len(rows),
        }

    X = np.array(
        [
            _row(_context_from_record(r, averages.get(r.doctor_id, 10)), DURATION_FEATURES)
            for r in rows
        ]
    )
    y = np.array([float(r.duration_minutes) for r in rows])

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=SEED
    )
    model = GradientBoostingRegressor(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=SEED
    )
    model.fit(X_train, y_train)

    predicted = model.predict(X_test)
    metrics = {
        "mae_minutes": round(float(mean_absolute_error(y_test, predicted)), 2),
        "r2": round(float(r2_score(y_test, predicted)), 3),
        "test_size": int(len(y_test)),
    }
    return _persist(db, ModelKind.DURATION, model, rows, metrics)


def train_no_show(db: Session) -> dict:
    """Fit the attendance classifier on every appointment, kept or missed."""
    averages = _doctor_averages(db)
    rows = _load_records(db)
    if len(rows) < MIN_TRAINING_ROWS:
        return {
            "trained": False,
            "reason": f"Need {MIN_TRAINING_ROWS} appointments, have {len(rows)}",
            "n_samples": len(rows),
        }

    y = np.array([1 if r.was_no_show else 0 for r in rows])
    if len(np.unique(y)) < 2:
        return {
            "trained": False,
            "reason": "Training data contains only one outcome class",
            "n_samples": len(rows),
        }

    X = np.array(
        [
            _row(_context_from_record(r, averages.get(r.doctor_id, 10)), NO_SHOW_FEATURES)
            for r in rows
        ]
    )
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, random_state=SEED, stratify=y
    )
    model = GradientBoostingClassifier(
        n_estimators=200, max_depth=3, learning_rate=0.05, random_state=SEED
    )
    model.fit(X_train, y_train)

    proba = model.predict_proba(X_test)[:, 1]
    metrics = {
        # AUC over accuracy: no-shows are the minority class, and a model that
        # predicts "everyone attends" would score 85% accurate and be useless.
        "roc_auc": round(float(roc_auc_score(y_test, proba)), 3),
        "brier": round(float(brier_score_loss(y_test, proba)), 4),
        "base_rate": round(float(y.mean()), 3),
        "test_size": int(len(y_test)),
    }
    return _persist(db, ModelKind.NO_SHOW, model, rows, metrics)


def _persist(
    db: Session, kind: ModelKind, model, rows: list[ConsultationRecord], metrics: dict
) -> dict:
    synthetic = any(r.is_synthetic for r in rows)
    version = utcnow().strftime("%Y%m%d%H%M%S")
    meta = {
        "version": version,
        "n_samples": len(rows),
        "trained_on_synthetic": synthetic,
        "metrics": metrics,
    }
    path = predictors.save_bundle(str(kind), model, meta)

    db.execute(
        ModelArtifact.__table__.update()
        .where(ModelArtifact.kind == str(kind))
        .values(is_active=False)
    )
    db.add(
        ModelArtifact(
            kind=str(kind),
            version=version,
            path=str(path),
            n_samples=len(rows),
            trained_on_synthetic=synthetic,
            metrics=json.dumps(metrics),
            is_active=True,
        )
    )
    db.commit()
    predictors.reset_all()

    return {
        "trained": True,
        "kind": str(kind),
        "version": version,
        "n_samples": len(rows),
        "trained_on_synthetic": synthetic,
        "metrics": metrics,
    }


def train_all(db: Session) -> dict:
    return {"duration": train_duration(db), "no_show": train_no_show(db)}


# --- synthetic bootstrap ---------------------------------------------------


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-x))


def generate_synthetic(db: Session, n: int = 2000, *, seed: int = SEED) -> int:
    """Write ``n`` plausible historical consultations.

    The generative process below is intentionally *not* the same shape as the
    heuristic fallback in predictors.py — otherwise "the model beats the
    heuristic" would only prove the heuristic was copied into the data.
    """
    from app.modules.doctors.models import Doctor

    doctors = list(db.execute(select(Doctor)).scalars())
    if not doctors:
        raise ValidationError(
            "Create at least one doctor before generating synthetic history"
        )

    rng = np.random.default_rng(seed)
    channels = list(CHANNEL_CODES.keys())
    today = date.today()
    created = 0

    for _ in range(n):
        doctor = doctors[int(rng.integers(len(doctors)))]
        age = int(np.clip(rng.normal(42, 19), 1, 95))
        is_senior = age >= 60
        is_follow_up = bool(rng.random() < 0.38)
        channel = channels[int(rng.integers(len(channels)))]
        lead_time = int(np.clip(rng.exponential(4.5), 0, 30))
        prior_visits = int(np.clip(rng.poisson(2.2), 0, 25))
        prior_no_shows = (
            int(rng.binomial(prior_visits, 0.18)) if prior_visits else 0
        )
        offset = int(rng.integers(1, 365))
        record_date = today - timedelta(days=offset)
        hour = int(rng.integers(9, 17))
        slot_index = int(rng.integers(0, 30))

        # --- attendance ---
        no_show_rate = prior_no_shows / prior_visits if prior_visits else 0.0
        logit = (
            -2.05
            + 0.045 * lead_time
            + 2.10 * no_show_rate
            - 0.45 * is_follow_up
            - 0.35 * is_senior
            + (0.55 if channel == "ivr" else 0.0)
            - (0.60 if channel == "kiosk" else 0.0)
            + 0.04 * (hour - 12)
        )
        was_no_show = bool(rng.random() < _sigmoid(logit))

        # --- duration (only meaningful if they attended) ---
        duration = None
        if not was_no_show:
            base = (
                5.2
                + 0.065 * age
                + (4.6 if not is_follow_up else 0.0)
                + (2.1 if is_senior else 0.0)
                - 0.055 * slot_index
                + 0.35 * (doctor.avg_consultation_minutes - 10)
            )
            duration = int(np.clip(rng.normal(base, 2.6), 3, 60))

        db.add(
            ConsultationRecord(
                doctor_id=doctor.id,
                department_id=doctor.department_id,
                record_date=record_date,
                scheduled_start=time(hour, 0),
                duration_minutes=duration,
                was_no_show=was_no_show,
                patient_age=age,
                is_follow_up=is_follow_up,
                channel=channel,
                lead_time_days=lead_time,
                prior_visits=prior_visits,
                prior_no_shows=prior_no_shows,
                day_of_week=record_date.weekday(),
                hour_of_day=hour,
                slot_index=slot_index,
                is_senior=is_senior,
                has_priority_flag=bool(rng.random() < 0.12),
                is_synthetic=True,
            )
        )
        created += 1

    db.commit()
    return created


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the Room 4 models")
    parser.add_argument(
        "--synthetic",
        type=int,
        default=0,
        metavar="N",
        help="generate N synthetic historical consultations before training",
    )
    args = parser.parse_args()

    from app.core.database import init_db

    init_db()
    db = SessionLocal()
    try:
        if args.synthetic:
            count = generate_synthetic(db, args.synthetic)
            print(f"Generated {count} synthetic consultation records")
        print(json.dumps(train_all(db), indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
