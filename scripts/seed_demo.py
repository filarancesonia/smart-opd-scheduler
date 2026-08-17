"""Populate a demo hospital so every screen has something real to show.

    python -m scripts.seed_demo

Creates two departments, three doctors, a roster covering the current hour,
patients in a live queue, one senior citizen holding priority, and one doctor
who is rostered but has not arrived — because the absent doctor is the case the
whole system exists for, and a demo without one proves nothing.

Safe to re-run: it clears the database first.
"""

from __future__ import annotations

import sys
from datetime import time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import Base, SessionLocal, engine, init_db  # noqa: E402
from app.core.security import Role  # noqa: E402
from app.core.timeutil import local_now, local_today  # noqa: E402
from app.modules.booking import service as booking  # noqa: E402
from app.modules.booking.schemas import AppointmentCreate, PatientCreate  # noqa: E402
from app.modules.doctors import service as doctors  # noqa: E402
from app.modules.doctors.schemas import (  # noqa: E402
    CredentialCreate,
    DepartmentCreate,
    DoctorCreate,
    DutySlotCreate,
)
from app.modules.identity import service as identity  # noqa: E402
from app.modules.identity.schemas import RegisterRequest  # noqa: E402
from app.modules.presence import service as presence  # noqa: E402
from app.modules.presence.schemas import DeviceCreate, ManualPresence  # noqa: E402
from app.modules.queue import service as queue  # noqa: E402
from app.modules.scheduling import train  # noqa: E402

PASSWORD = "DemoPass123"


def window_around_now() -> tuple[time, time]:
    """A duty window guaranteed to contain the current local time."""
    hour = local_now().hour
    start = time(max(hour - 1, 0), 0)
    end_hour = min(hour + 3, 23)
    end = time(23, 59) if end_hour == 23 else time(end_hour, 0)
    return start, end


def seed_attendance_history(db, doctors_with_rooms, window_start: time) -> None:
    """Write presence events for the past 30 days.

    Dr. Verma is given a deliberate pattern — reliably absent on Saturdays —
    because a weekday absence habit is exactly what Room 8 is built to surface,
    and a report with nothing in it demonstrates nothing.
    """
    import random

    from app.core.timeutil import as_utc, combine_local
    from app.modules.presence.models import PresenceEvent, PresenceStatus

    rng = random.Random(20260817)
    today = local_today()

    for doctor, room in doctors_with_rooms:
        for offset in range(1, 31):
            day = today - timedelta(days=offset)

            if doctor.registration_no.startswith("MP-2017"):  # Dr. Verma
                if day.weekday() == 5 and rng.random() < 0.85:
                    continue  # the Saturday habit
                if rng.random() < 0.08:
                    continue
                late = rng.choice([0, 0, 5, 10])
            elif doctor.registration_no.startswith("MP-2014"):  # Dr. Sharma
                if rng.random() < 0.05:
                    continue
                late = rng.choice([0, 0, 0, 12, 25, 40])
            else:  # Dr. Khan
                if rng.random() < 0.03:
                    continue
                late = rng.choice([0, 0, 0, 0, 8])

            arrival = as_utc(combine_local(day, window_start)) + timedelta(minutes=late)
            db.add(
                PresenceEvent(
                    doctor_id=doctor.id,
                    from_status=str(PresenceStatus.ABSENT),
                    to_status=str(PresenceStatus.PRESENT),
                    room=room,
                    occurred_at=arrival,
                    source="device",
                    note="Seeded attendance history",
                )
            )
    db.commit()


def main() -> None:
    print("Resetting database…")
    Base.metadata.drop_all(bind=engine)
    init_db()

    db = SessionLocal()
    try:
        start, end = window_around_now()
        today = local_today()

        # --- accounts ---
        admin = identity.register(
            db,
            RegisterRequest(
                phone="9000000001", full_name="Priya Nair", password=PASSWORD, role=Role.ADMIN
            ),
        )
        staff = identity.register(
            db,
            RegisterRequest(
                phone="9000000009", full_name="Reception Desk", password=PASSWORD, role=Role.STAFF
            ),
        )
        sharma_user = identity.register(
            db,
            RegisterRequest(
                phone="9000000002", full_name="Dr. Anil Sharma", password=PASSWORD, role=Role.DOCTOR
            ),
        )
        verma_user = identity.register(
            db,
            RegisterRequest(
                phone="9000000003", full_name="Dr. Meena Verma", password=PASSWORD, role=Role.DOCTOR
            ),
        )
        khan_user = identity.register(
            db,
            RegisterRequest(
                phone="9000000004", full_name="Dr. Imran Khan", password=PASSWORD, role=Role.DOCTOR
            ),
        )

        # --- departments ---
        medicine = doctors.create_department(
            db, DepartmentCreate(name="General Medicine", code="GM", floor="2")
        )
        ortho = doctors.create_department(
            db, DepartmentCreate(name="Orthopaedics", code="ORTHO", floor="3")
        )

        # --- doctors ---
        sharma = doctors.create_doctor(
            db,
            DoctorCreate(
                user_id=sharma_user.id,
                department_id=medicine.id,
                registration_no="MP-2014-88210",
                qualification="MBBS, MD (Medicine)",
                specialisation="Internal Medicine",
                designation="Senior Medical Officer",
                avg_consultation_minutes=8,
            ),
        )
        verma = doctors.create_doctor(
            db,
            DoctorCreate(
                user_id=verma_user.id,
                department_id=medicine.id,
                registration_no="MP-2017-44112",
                qualification="MBBS, DNB",
                specialisation="Diabetes & Thyroid",
                designation="Medical Officer",
                avg_consultation_minutes=12,
            ),
        )
        khan = doctors.create_doctor(
            db,
            DoctorCreate(
                user_id=khan_user.id,
                department_id=ortho.id,
                registration_no="MP-2012-10577",
                qualification="MBBS, MS (Ortho)",
                specialisation="Joint replacement",
                designation="Consultant",
                avg_consultation_minutes=15,
            ),
        )

        # --- roster: every weekday, covering the current hour ---
        for doctor, room in ((sharma, "OPD 12"), (verma, "OPD 14"), (khan, "OPD 31")):
            for weekday in range(7):
                doctors.add_duty_slot(
                    db,
                    doctor.id,
                    DutySlotCreate(
                        day_of_week=weekday,
                        start_time=start,
                        end_time=end,
                        room=room,
                        valid_from=today - timedelta(days=30),
                    ),
                )
            doctors.add_credential(
                db,
                doctor.id,
                CredentialCreate(
                    credential_type="rfid",
                    raw_value=f"RFID-{doctor.registration_no}",
                    label="ID card",
                ),
            )

        # --- door hardware ---
        for uid, room, dept in (
            ("RDR-OPD12", "OPD 12", medicine.id),
            ("RDR-OPD14", "OPD 14", medicine.id),
            ("RDR-OPD31", "OPD 31", ortho.id),
        ):
            presence.register_device(
                db,
                DeviceCreate(
                    device_uid=uid, device_type="rfid_reader", room=room, department_id=dept
                ),
            )

        # --- who has actually turned up ---
        # Sharma is in. Verma is rostered and missing — that contrast is the
        # entire pitch, so the demo must contain it.
        presence.set_manual_presence(
            db,
            ManualPresence(doctor_id=sharma.id, status="present", room="OPD 12"),
            admin.id,
        )
        presence.set_manual_presence(
            db,
            ManualPresence(doctor_id=khan.id, status="present", room="OPD 31"),
            admin.id,
        )

        # --- thirty days of attendance history ---
        # Without this the analytics are technically correct and useless: one
        # day of presence against a month of roster reads as 3% attendance.
        seed_attendance_history(db, [(sharma, "OPD 12"), (verma, "OPD 14"), (khan, "OPD 31")], start)

        # --- a patient account someone can log in as ---
        asha_user = identity.register(
            db,
            RegisterRequest(
                phone="9123456780", full_name="Asha Devi", password=PASSWORD, role=Role.PATIENT
            ),
        )
        asha = booking.patient_for_user(db, asha_user)

        # --- walk-ins, including one senior citizen for the priority demo ---
        walkins = [
            ("Ramesh Yadav", "9812345671", 67),
            ("Sunita Bai", "9812345672", 34),
            ("Vijay Kumar", "9812345673", 41),
            ("Farida Begum", "9812345674", 29),
            ("Mohan Lal", "9812345675", 58),
        ]
        patients = [asha]
        for name, phone, age in walkins:
            patients.append(
                booking.create_patient(
                    db, PatientCreate(full_name=name, phone=phone, age=age)
                )
            )

        # --- Sharma's clinic: booked and queued ---
        queue.open_queue(db, sharma.id, today, "OPD 12")
        for patient in patients:
            appointment = booking.book(
                db,
                patient_id=patient.id,
                payload=AppointmentCreate(doctor_id=sharma.id, appointment_date=today),
                channel="kiosk" if patient is not asha else "mobile_app",
            )
            queue.join(db, appointment.id)

        # --- a couple of bookings for the doctor who has not arrived ---
        for patient in patients[1:3]:
            booking.book(
                db,
                patient_id=patient.id,
                payload=AppointmentCreate(doctor_id=verma.id, appointment_date=today),
                channel="website",
            )

        # --- train the Room 4 models so the plan shows real predictions ---
        print("Generating synthetic history and training models…")
        train.generate_synthetic(db, 900)
        result = train.train_all(db)

        print("\nDemo hospital ready.\n")
        print(f"  Admin      9000000001 / {PASSWORD}")
        print(f"  Doctor     9000000002 / {PASSWORD}   (Dr. Sharma — present, queue running)")
        print(f"  Doctor     9000000003 / {PASSWORD}   (Dr. Verma  — rostered but ABSENT)")
        print(f"  Reception  9000000009 / {PASSWORD}")
        print(f"  Patient    9123456780 / {PASSWORD}   (Asha Devi, token 1)")
        print(f"\n  Corridor board:  /board/{sharma.id}")
        print("  Kiosk:           /kiosk   (device key: dev-device-key)")
        print(
            f"\n  Duration model:  {result['duration'].get('metrics', {})}"
            f"\n  No-show model:   {result['no_show'].get('metrics', {})}"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
