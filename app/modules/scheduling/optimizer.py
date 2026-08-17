"""Room 4 — the slot optimiser.

The problem: a doctor is actually present from T1 to T2 (Room 1 knows, the
printed timetable does not). N patients are booked. Each has a predicted
consultation length and a probability of not turning up. In what order should
they be seen so that total waiting time is smallest?

This is single-machine scheduling. Minimising *total* flow time on one server
is solved exactly by Shortest Processing Time first — a classical result, and
the reason the optimiser is not a black box. Two things are layered on top:

  1. Priority tiers (Room 7) are absolute. An emergency is never traded off
     against average waiting time.
  2. Pure SPT starves long cases: a complicated patient is overtaken every
     time a shorter one is booked. A guard promotes anyone projected to wait
     beyond a threshold, so no one is sacrificed to the average.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import time

#: Nobody should be pushed past this, however long their consultation.
MAX_ACCEPTABLE_WAIT_MINUTES = 90

#: Bound on fairness promotions, so a pathological input cannot loop forever.
MAX_FAIRNESS_PASSES = 25


@dataclass
class PlanItem:
    """One booked patient, with predictions already attached."""

    appointment_id: int
    patient_id: int
    patient_name: str
    booked_start: time
    expected_duration: float
    no_show_probability: float
    priority_tier: int = 0  # higher wins; Room 7 sets this
    duration_source: str = "heuristic"
    no_show_source: str = "heuristic"


@dataclass
class Assignment:
    appointment_id: int
    patient_id: int
    patient_name: str
    position: int
    booked_start: time
    predicted_start: time
    predicted_end: time
    expected_wait_minutes: float
    expected_duration: float
    no_show_probability: float
    priority_tier: int
    overruns_session: bool = False
    promoted_for_fairness: bool = False


@dataclass
class OptimisationResult:
    doctor_id: int
    available_from: time
    available_until: time
    assignments: list[Assignment] = field(default_factory=list)
    total_expected_wait: float = 0.0
    average_wait: float = 0.0
    baseline_wait: float = 0.0
    baseline_average_wait: float = 0.0
    improvement_pct: float = 0.0
    expected_no_shows: float = 0.0
    recommended_overbooking: int = 0
    session_minutes: int = 0
    projected_overrun_minutes: float = 0.0
    notes: list[str] = field(default_factory=list)


# --- time helpers ----------------------------------------------------------


def _to_minutes(clock: time) -> int:
    return clock.hour * 60 + clock.minute


def _to_time(minutes: float) -> time:
    total = int(round(minutes))
    total = max(0, min(total, 23 * 60 + 59))
    return time(total // 60, total % 60)


# --- core simulation -------------------------------------------------------


def _simulate(
    order: list[PlanItem], start_minutes: int, until_minutes: int
) -> tuple[list[Assignment], float]:
    """Walk an ordering and compute when each patient is actually seen.

    Expected duration is discounted by the probability of a no-show: a patient
    who is 30% likely not to appear consumes 70% of their slot in expectation,
    which is what the people behind them will actually experience.
    """
    assignments: list[Assignment] = []
    cursor = float(start_minutes)
    total_wait = 0.0

    for position, item in enumerate(order):
        booked = _to_minutes(item.booked_start)
        # A patient is not waiting before the doctor's session opens, nor
        # before their own appointment time.
        ready = max(booked, start_minutes)
        seen_at = max(cursor, float(ready))

        wait = max(0.0, seen_at - ready)
        effective = item.expected_duration * (1.0 - item.no_show_probability)
        end = seen_at + effective

        assignments.append(
            Assignment(
                appointment_id=item.appointment_id,
                patient_id=item.patient_id,
                patient_name=item.patient_name,
                position=position + 1,
                booked_start=item.booked_start,
                predicted_start=_to_time(seen_at),
                predicted_end=_to_time(end),
                expected_wait_minutes=round(wait, 1),
                expected_duration=round(item.expected_duration, 1),
                no_show_probability=round(item.no_show_probability, 3),
                priority_tier=item.priority_tier,
                overruns_session=end > until_minutes,
            )
        )
        total_wait += wait
        cursor = end

    return assignments, total_wait


def _spt_order(items: list[PlanItem], protected: set[int]) -> list[PlanItem]:
    """Priority tier first, then promoted patients, then shortest job first."""
    return sorted(
        items,
        key=lambda i: (
            -i.priority_tier,
            0 if i.appointment_id in protected else 1,
            _to_minutes(i.booked_start) if i.appointment_id in protected else 0,
            i.expected_duration,
            _to_minutes(i.booked_start),
        ),
    )


def optimise(
    items: list[PlanItem],
    *,
    doctor_id: int,
    available_from: time,
    available_until: time,
) -> OptimisationResult:
    """Order the day's patients to minimise total waiting time."""
    start_minutes = _to_minutes(available_from)
    until_minutes = _to_minutes(available_until)
    session_minutes = max(until_minutes - start_minutes, 0)

    result = OptimisationResult(
        doctor_id=doctor_id,
        available_from=available_from,
        available_until=available_until,
        session_minutes=session_minutes,
    )
    if not items:
        result.notes.append("No patients booked for this session")
        return result

    # Baseline: first-come-first-served by booked time — what happens today.
    fifo = sorted(items, key=lambda i: (_to_minutes(i.booked_start), i.appointment_id))
    _, baseline_wait = _simulate(fifo, start_minutes, until_minutes)

    # Optimised, with fairness promotions applied until nobody is starving.
    protected: set[int] = set()
    order = _spt_order(items, protected)
    assignments, total_wait = _simulate(order, start_minutes, until_minutes)

    for _ in range(MAX_FAIRNESS_PASSES):
        starving = [
            a
            for a in assignments
            if a.expected_wait_minutes > MAX_ACCEPTABLE_WAIT_MINUTES
            and a.appointment_id not in protected
        ]
        if not starving:
            break
        worst = max(starving, key=lambda a: a.expected_wait_minutes)
        protected.add(worst.appointment_id)
        order = _spt_order(items, protected)
        assignments, total_wait = _simulate(order, start_minutes, until_minutes)

    for assignment in assignments:
        assignment.promoted_for_fairness = assignment.appointment_id in protected

    count = len(items)
    result.assignments = assignments
    result.total_expected_wait = round(total_wait, 1)
    result.average_wait = round(total_wait / count, 1)
    result.baseline_wait = round(baseline_wait, 1)
    result.baseline_average_wait = round(baseline_wait / count, 1)
    result.improvement_pct = (
        round((baseline_wait - total_wait) / baseline_wait * 100, 1)
        if baseline_wait > 0
        else 0.0
    )

    # Overbooking: if three people are expected not to come, three more can be
    # fitted without lengthening anyone's wait in expectation.
    result.expected_no_shows = round(
        sum(i.no_show_probability for i in items), 2
    )
    result.recommended_overbooking = int(result.expected_no_shows)

    overrun = max(
        0.0,
        max((_to_minutes(a.predicted_end) for a in assignments), default=0)
        - until_minutes,
    )
    result.projected_overrun_minutes = round(overrun, 1)

    if protected:
        result.notes.append(
            f"{len(protected)} patient(s) promoted so nobody waits over "
            f"{MAX_ACCEPTABLE_WAIT_MINUTES} minutes"
        )
    if overrun > 0:
        result.notes.append(
            f"Session projected to overrun by {overrun:.0f} minutes — "
            "consider an extra clinic or moving patients to another doctor"
        )
    if result.recommended_overbooking > 0:
        result.notes.append(
            f"{result.expected_no_shows:.1f} no-shows expected; "
            f"{result.recommended_overbooking} extra patient(s) can be booked"
        )

    return result
