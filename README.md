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

All ten are built, wired together and tested — **263 backend tests passing**.

## How the rooms feed each other

The modules are not ten separate features; the value is in the wiring.

- Room 1 tells Room 3 *"the doctor has not arrived yet"* on the booking screen.
- Room 1 tells Room 4 the session really starts at 11:14, not 09:00, so the
  plan is built against reality rather than the timetable.
- Room 4 orders Room 5's live queue, and Room 5 feeds the real consultation
  durations back into Room 4's training data.
- Room 7's triage inserts into Room 5 and is bounded by Room 5's fairness rules.
- Room 8 derives every figure from what Rooms 1–7 already recorded, and owns
  no tables of its own.
- Room 10 gates Room 1's face recognition: no consent, no enrolment, no override.

## Design decisions worth stating

**Nothing invents a number it does not have.** When the doctor is absent and no
consultation is running, the queue shows no ETA at all rather than a guess
dressed up as information. Every AI prediction reports whether it came from a
trained model or the heuristic fallback.

**The scheduling models never see gender, caste, religion, address or name.** A
model that decides who waits longer must not be able to learn those. A test
asserts the feature lists stay clean.

**Face recognition stores a vector, never a photograph.** There is no column
anywhere that could hold an image. The vector is encrypted at rest, tied to a
specific consent record, and destroyed the moment that consent is withdrawn.

**Offline stubs are always labelled.** Room 6's console driver and Room 9's
mock gateways are flagged `is_mock` all the way through to the API. A demo that
cannot be told apart from a real integration will eventually be mistaken for one.

## The five screens

The backend is API-first; the frontend is five surfaces over it, because the
people involved are not one audience.

| Surface | Route | Who it is for |
|---|---|---|
| **Patient app** | `/` | Book, see whether the doctor has actually arrived, watch your token |
| **Kiosk** | `/kiosk` | Walk-ins with no smartphone — on-screen keypad, Hindi first, prints a slip |
| **Corridor board** | `/board/:doctorId` | The waiting hall — huge tokens, read from ten metres |
| **Doctor console** | `/doctor` | Run the queue: call next, start, complete, plus the AI plan |
| **Admin dashboard** | `/admin` | Live presence, attendance, wait times, channel mix, alerts |

Kiosk and board authenticate as provisioned devices with a device key, not a
user login. The patient, doctor and admin surfaces use a normal JWT session.

Everything is bilingual with a one-tap toggle, and Hindi is the default —
the people this is for read Hindi first. No web fonts are loaded: a hospital
network is often slow, and a screen that renders late is a screen nobody
trusts.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2.0 · SQLite in dev (Postgres-ready) ·
scikit-learn for the Room 4 models · cryptography for field encryption.

React 19 · TypeScript · Vite · React Router. No UI framework and no CSS
library — the design system is ~600 lines of plain CSS with tokens, so there
is no build-tool churn and nothing to upgrade before a demo.

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

Seed a whole demo hospital — three doctors, a live queue, thirty days of
attendance history, and trained models:

```bash
python -m scripts.seed_demo
```

It prints the logins. One doctor is deliberately rostered-but-absent, because
that is the case the system exists for and a demo without one proves nothing.

Then the frontend, in a second terminal:

```bash
npm install --prefix frontend && npm run dev --prefix frontend
```

<http://localhost:5173> — the Vite dev server proxies `/api` to the backend.
The kiosk and corridor board ask for a device key on first use; in development
it is `dev-device-key`.

Run the tests — 263 backend, 152 frontend:

```bash
pytest -q
```

```bash
npm test --prefix frontend
```

Frontend coverage sits at 96% of statements (`npm run test:coverage --prefix frontend`).
Two of those tests exist because of specific bugs found by driving the real
thing, and both were checked by reintroducing the bug and watching them fail:

- `usePolling` skipping its first fetch in a background tab
- the kiosk keypad dropping digits when taps outran a re-render

## Layout

```
app/
  core/       config, database, security primitives, time handling, shared deps
  models/     registry that imports every module's tables
  modules/
    identity/       accounts, login, JWT refresh
    presence/       Room 1     doctors/     Room 2
    booking/        Room 3     scheduling/  Room 4
    queue/          Room 5     notifications/ Room 6
    emergency/      Room 7     analytics/   Room 8
    integration/    Room 9     privacy/     Room 10
```

Every module is a self-contained package exposing a router; `app/api.py` mounts
them. Cross-module calls go through lazy imports so a module still works if a
neighbour is not deployed.

## Known limitations

Honest about what is not done:

- `Patient.phone` is not encrypted. Reception looks patients up by phone dozens
  of times a day, and encrypting it breaks exact-match lookup. Room 10 ships the
  blind index that solves this; the migration is not written yet.
- Tables are created with `create_all` rather than Alembic migrations.
- The Room 9 gateways have only been exercised against their offline stubs — no
  real ABDM sandbox credentials have been used.
- Face embeddings are matched by cosine similarity against every active
  template, which is linear in the number of doctors. Fine for one hospital,
  not for a state.
- The device key for the kiosk and corridor board is held in browser
  localStorage. Anything in a browser is readable by whoever controls it; this
  is acceptable only because those screens run on hospital-owned hardware in
  kiosk mode. A real deployment should terminate the key in a local agent.
- Frontend tests stub `fetch` rather than running against the real API, so
  they verify the client's half of the contract and not the contract itself.
  A schema change on the server would keep them green.
- There are no end-to-end tests driving a real browser against a real backend,
  and no visual regression tests.
- `scripts/seed_demo.py` builds the duty window around the current hour so the
  demo works whenever it is run. Seed it at 2am and the clinic hours will look
  strange — correct, but strange.
