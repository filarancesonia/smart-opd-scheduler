# Smart OPD Scheduler

Presence-aware outpatient scheduling for government hospitals.

Government OPDs run on a fiction: a printed timetable says Dr. Sharma sees patients
from 9:00, so tokens are handed out from 9:00. If the doctor is actually in surgery
until 11:00, two hundred people sit on a bench and nobody tells them. This system
replaces the fiction with a live signal — **is the doctor physically here, right now?**
— and re-plans the day around the answer.

## The ten modules

| # | Module | Job |
|---|--------|-----|
| 1 | **Presence Detection** | RFID / face / BLE readers at doors, fused into "Dr. Sharma = PRESENT, OPD 12, since 9:14 AM" |
| 2 | **Doctor Profile & Duty Roster** | Doctors, departments, working hours, weekly timetable, leaves — the *expected* schedule Room 1 checks against |
| 3 | **Patient Booking** | Four channels so nobody is excluded: mobile app, website, in-hospital kiosk, Hindi IVR |
| 4 | **AI Scheduling Engine** | Duration predictor + no-show predictor + slot optimiser that minimises total waiting time |
| 5 | **Live Queue Management** | Real-time tokens, "your turn in 22 minutes", corridor display boards, live re-ordering |
| 6 | **Notifications** | SMS, app push, WhatsApp, voice call — reminders, cancellations, reschedules |
| 7 | **Emergency & Priority** | Emergencies, senior citizens, pregnant and disabled patients override the queue safely |
| 8 | **Admin & Analytics** | Attendance, wait times, department load, absent-doctor patterns for hospital and Health Dept |
| 9 | **Integration** | ABHA health ID, ORS, hospital HMIS/EHR — so this is not one more island |
| 10 | **Security & Privacy** | Encryption, RBAC, audit logs, consent, face *signatures* never photos — DPDP Act 2023 |

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · SQLite in dev (Postgres-ready) ·
scikit-learn for the Room 4 models.

Passwords use stdlib `scrypt` rather than bcrypt so the project installs with no
native build toolchain — relevant when judges clone it onto a fresh machine.

## Running it

```bash
python -m venv .venv && .venv/Scripts/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

Interactive API docs: <http://127.0.0.1:8000/docs>

## Layout

```
app/
  core/       config, database, security primitives, shared dependencies
  models/     registry that imports every module's tables
  modules/
    identity/     accounts, login, JWT refresh
    ...           one package per room, each with models / schemas / service / router
```

Every module is a self-contained package exposing a router; `app/api.py` mounts them.
