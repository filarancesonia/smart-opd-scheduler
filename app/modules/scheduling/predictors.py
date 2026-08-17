"""Room 4 — the two learned models.

A hospital deploying this on day one has no history, so both predictors ship
with a transparent heuristic and switch to the trained model only once enough
real consultations exist. Every prediction reports which path produced it;
a number whose provenance you cannot state is a number you cannot defend.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import joblib
import numpy as np

from app.core.config import settings

#: Below this many rows a fitted model would just memorise noise.
MIN_TRAINING_ROWS = 60

#: Channel encoded as an ordinal. Booking route correlates with no-show rate:
#: someone who walked to a kiosk is already in the building.
CHANNEL_CODES = {
    "kiosk": 0,
    "staff": 1,
    "mobile_app": 2,
    "website": 3,
    "ivr": 4,
}


@dataclass
class PredictionContext:
    """Everything both models are allowed to look at.

    Deliberately excludes name, address, caste, religion and gender — a
    scheduling model must not learn to make anyone wait longer for those.
    """

    doctor_avg_minutes: int = 10
    patient_age: int | None = None
    is_follow_up: bool = False
    lead_time_days: int = 0
    prior_visits: int = 0
    prior_no_shows: int = 0
    day_of_week: int = 0
    hour_of_day: int = 9
    slot_index: int = 0
    is_senior: bool = False
    has_priority_flag: bool = False
    channel: str = "website"

    def as_dict(self) -> dict:
        return asdict(self)


DURATION_FEATURES = [
    "doctor_avg_minutes",
    "patient_age",
    "is_follow_up",
    "prior_visits",
    "day_of_week",
    "hour_of_day",
    "slot_index",
    "is_senior",
]

NO_SHOW_FEATURES = [
    "lead_time_days",
    "prior_visits",
    "prior_no_shows",
    "patient_age",
    "is_follow_up",
    "day_of_week",
    "hour_of_day",
    "channel_code",
    "is_senior",
    "has_priority_flag",
]


def _row(ctx: PredictionContext, columns: list[str]) -> list[float]:
    values = ctx.as_dict()
    # Median-ish stand-in; age is genuinely unknown for many walk-ins.
    values["patient_age"] = ctx.patient_age if ctx.patient_age is not None else 35
    values["channel_code"] = CHANNEL_CODES.get(ctx.channel, 3)
    return [float(values[c]) for c in columns]


@dataclass
class Prediction:
    value: float
    source: str  # "model" or "heuristic"
    detail: str = ""


class _BasePredictor:
    kind: str = ""
    features: list[str] = []

    def __init__(self) -> None:
        self._model = None
        self._meta: dict = {}
        self._loaded = False

    @property
    def path(self) -> Path:
        return Path(settings.model_dir) / f"{self.kind}.joblib"

    def load(self) -> None:
        """Load the artifact from disk if present. Safe to call repeatedly."""
        self._loaded = True
        if not self.path.exists():
            self._model = None
            return
        try:
            bundle = joblib.load(self.path)
            self._model = bundle["model"]
            self._meta = bundle.get("meta", {})
        except Exception:
            # A corrupt or version-mismatched artifact must degrade to the
            # heuristic, never take the scheduling engine down.
            self._model = None
            self._meta = {}

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def reset(self) -> None:
        """Force a reload on next use — called after training."""
        self._loaded = False
        self._model = None
        self._meta = {}

    @property
    def is_trained(self) -> bool:
        self._ensure_loaded()
        return self._model is not None

    @property
    def meta(self) -> dict:
        self._ensure_loaded()
        return self._meta


class DurationPredictor(_BasePredictor):
    """How many minutes will this consultation actually take?"""

    kind = "duration"
    features = DURATION_FEATURES

    MIN_MINUTES = 3
    MAX_MINUTES = 60

    def predict(self, ctx: PredictionContext) -> Prediction:
        self._ensure_loaded()
        if self._model is not None:
            raw = float(self._model.predict(np.array([_row(ctx, self.features)]))[0])
            minutes = float(np.clip(raw, self.MIN_MINUTES, self.MAX_MINUTES))
            return Prediction(minutes, "model", f"{self.kind} v{self._meta.get('version', '?')}")
        return self._heuristic(ctx)

    def _heuristic(self, ctx: PredictionContext) -> Prediction:
        minutes = float(ctx.doctor_avg_minutes)
        notes = []

        if ctx.is_follow_up:
            # A review of known findings is consistently shorter than a workup.
            minutes *= 0.7
            notes.append("follow-up -30%")
        if ctx.prior_visits == 0:
            minutes += 2
            notes.append("first visit +2m")
        if ctx.is_senior:
            minutes += 3
            notes.append("senior +3m")
        if ctx.slot_index > 20:
            # Late in a long list the doctor speeds up; observed in OPD studies.
            minutes *= 0.9
            notes.append("late slot -10%")

        minutes = float(np.clip(minutes, self.MIN_MINUTES, self.MAX_MINUTES))
        return Prediction(minutes, "heuristic", "; ".join(notes) or "doctor average")


class NoShowPredictor(_BasePredictor):
    """How likely is this patient not to turn up?"""

    kind = "no_show"
    features = NO_SHOW_FEATURES

    MIN_P = 0.02
    MAX_P = 0.85
    BASE_RATE = 0.15

    def predict(self, ctx: PredictionContext) -> Prediction:
        self._ensure_loaded()
        if self._model is not None:
            proba = float(
                self._model.predict_proba(np.array([_row(ctx, self.features)]))[0][1]
            )
            p = float(np.clip(proba, self.MIN_P, self.MAX_P))
            return Prediction(p, "model", f"{self.kind} v{self._meta.get('version', '?')}")
        return self._heuristic(ctx)

    def _heuristic(self, ctx: PredictionContext) -> Prediction:
        p = self.BASE_RATE
        notes = []

        # The further ahead a booking was made, the more life intervenes.
        lead_effect = min(ctx.lead_time_days * 0.006, 0.15)
        if lead_effect:
            p += lead_effect
            notes.append(f"lead time +{lead_effect:.2f}")

        if ctx.prior_visits > 0:
            rate = ctx.prior_no_shows / ctx.prior_visits
            p += rate * 0.40
            notes.append(f"prior no-show rate {rate:.0%}")

        if ctx.is_follow_up:
            p -= 0.05
            notes.append("follow-up -0.05")
        if ctx.is_senior:
            p -= 0.03
            notes.append("senior -0.03")
        if ctx.has_priority_flag:
            p -= 0.04
            notes.append("priority -0.04")
        if ctx.channel == "kiosk":
            # Booked while standing in the hospital.
            p -= 0.06
            notes.append("kiosk -0.06")

        p = float(np.clip(p, self.MIN_P, self.MAX_P))
        return Prediction(p, "heuristic", "; ".join(notes) or "base rate")


#: Module-level singletons — loading a joblib file per request would be waste.
duration_predictor = DurationPredictor()
no_show_predictor = NoShowPredictor()


def reset_all() -> None:
    duration_predictor.reset()
    no_show_predictor.reset()


def status() -> dict:
    """What the engine is currently running on, for the admin dashboard."""
    out = {}
    for predictor in (duration_predictor, no_show_predictor):
        meta = predictor.meta
        out[predictor.kind] = {
            "trained": predictor.is_trained,
            "source": "model" if predictor.is_trained else "heuristic",
            "version": meta.get("version"),
            "n_samples": meta.get("n_samples"),
            "trained_on_synthetic": meta.get("trained_on_synthetic"),
            "metrics": meta.get("metrics", {}),
        }
    return out


def save_bundle(kind: str, model, meta: dict) -> Path:
    directory = Path(settings.model_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{kind}.joblib"
    joblib.dump({"model": model, "meta": meta}, path)
    # A sidecar so the metadata is readable without unpickling anything.
    (directory / f"{kind}.meta.json").write_text(json.dumps(meta, indent=2))
    return path
