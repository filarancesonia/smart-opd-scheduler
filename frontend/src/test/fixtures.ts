/* Fixture builders.
 *
 * Every builder takes an override object so a test states only the field it
 * actually cares about. That keeps the assertion and the reason for it in the
 * same three lines.
 */

import type {
  Appointment,
  DaySlots,
  DisplayBoard,
  Department,
  Doctor,
  HealthDeptSummary,
  LiveOverview,
  MyPosition,
  Optimisation,
  Patient,
  Presence,
  Queue,
  QueueEntry,
  User,
} from '../lib/api'

export const user = (over: Partial<User> = {}): User => ({
  id: 1,
  phone: '9876543210',
  full_name: 'Asha Devi',
  email: null,
  role: 'patient',
  is_active: true,
  abha_id: null,
  preferred_language: 'hi',
  ...over,
})

export const department = (over: Partial<Department> = {}): Department => ({
  id: 1,
  name: 'General Medicine',
  code: 'GM',
  floor: '2',
  is_active: true,
  ...over,
})

export const doctor = (over: Partial<Doctor> = {}): Doctor => ({
  id: 1,
  user_id: 2,
  department_id: 1,
  registration_no: 'MP-2014-88210',
  qualification: 'MBBS, MD',
  specialisation: 'Internal Medicine',
  designation: 'Senior Medical Officer',
  avg_consultation_minutes: 10,
  max_patients_per_day: 60,
  is_accepting_patients: true,
  full_name: 'Dr. Anil Sharma',
  department_name: 'General Medicine',
  ...over,
})

export const patient = (over: Partial<Patient> = {}): Patient => ({
  id: 1,
  user_id: 1,
  full_name: 'Asha Devi',
  phone: '9876543210',
  age: 34,
  gender: 'female',
  abha_id: null,
  preferred_language: 'hi',
  is_pregnant: false,
  has_disability: false,
  is_senior_citizen: false,
  ...over,
})

export const appointment = (over: Partial<Appointment> = {}): Appointment => ({
  id: 1,
  booking_reference: 'OPDABC2345',
  patient_id: 1,
  doctor_id: 1,
  department_id: 1,
  appointment_date: '2026-08-18',
  slot_start: '09:30:00',
  slot_end: '09:40:00',
  room: 'OPD 12',
  status: 'booked',
  channel: 'website',
  reason: '',
  is_follow_up: false,
  checked_in_at: null,
  patient_name: 'Asha Devi',
  doctor_name: 'Dr. Anil Sharma',
  department_name: 'General Medicine',
  ...over,
})

export const presence = (over: Partial<Presence> = {}): Presence => ({
  doctor_id: 1,
  doctor_name: 'Dr. Anil Sharma',
  department_name: 'General Medicine',
  status: 'present',
  room: 'OPD 12',
  since: '2026-08-18T03:44:00Z',
  last_signal_at: '2026-08-18T04:10:00Z',
  last_credential_type: 'rfid',
  confidence: 0.95,
  present_minutes: 26,
  deviation: 'on_duty_as_rostered',
  expected_room: 'OPD 12',
  expected_until: '2026-08-18T07:30:00Z',
  minutes_late: 0,
  ...over,
})

export const daySlots = (over: Partial<DaySlots> = {}): DaySlots => ({
  doctor_id: 1,
  date: '2026-08-18',
  is_on_leave: false,
  capacity: 4,
  booked: 1,
  remaining: 3,
  slots: [
    { start: '09:00:00', end: '09:10:00', room: 'OPD 12', available: true },
    { start: '09:10:00', end: '09:20:00', room: 'OPD 12', available: false },
    { start: '09:20:00', end: '09:30:00', room: 'OPD 12', available: true },
    { start: '09:30:00', end: '09:40:00', room: 'OPD 12', available: true },
  ],
  presence_warning: null,
  ...over,
})

export const queueEntry = (over: Partial<QueueEntry> = {}): QueueEntry => ({
  id: 1,
  appointment_id: 1,
  patient_id: 1,
  patient_name: 'Asha Devi',
  token_number: 1,
  position: 1,
  priority_tier: 0,
  status: 'waiting',
  joined_at: '2026-08-18T04:00:00Z',
  called_at: null,
  started_at: null,
  completed_at: null,
  predicted_duration: 10,
  estimated_wait_minutes: 0,
  skip_count: 0,
  note: '',
  ...over,
})

export const queue = (over: Partial<Queue> = {}): Queue => ({
  session_id: 1,
  doctor_id: 1,
  doctor_name: 'Dr. Anil Sharma',
  session_date: '2026-08-18',
  room: 'OPD 12',
  is_open: true,
  doctor_present: true,
  waiting_count: 1,
  completed_count: 0,
  observed_avg_minutes: null,
  now_serving: null,
  entries: [queueEntry()],
  ...over,
})

export const board = (over: Partial<DisplayBoard> = {}): DisplayBoard => ({
  doctor_id: 1,
  doctor_name: 'Dr. Anil Sharma',
  room: 'OPD 12',
  doctor_present: true,
  status_line_hi: 'डॉक्टर उपलब्ध हैं।',
  status_line_en: 'Doctor is available.',
  now_serving: 3,
  next_tokens: [
    {
      token_number: 4,
      display_name: 'Ramesh Y.',
      status: 'waiting',
      estimated_wait_minutes: 12,
      is_priority: true,
    },
    {
      token_number: 5,
      display_name: 'Asha D.',
      status: 'waiting',
      estimated_wait_minutes: 25,
      is_priority: false,
    },
  ],
  updated_at: '2026-08-18T04:10:00Z',
  ...over,
})

export const myPosition = (over: Partial<MyPosition> = {}): MyPosition => ({
  token_number: 7,
  position: 3,
  people_ahead: 2,
  status: 'waiting',
  estimated_wait_minutes: 22,
  estimated_call_time: '2026-08-18T04:32:00Z',
  doctor_present: true,
  message_hi: 'आपकी बारी लगभग 22 मिनट में है। आपसे 2 लोग आगे हैं।',
  message_en: 'Your turn is in about 22 minutes. 2 people are ahead of you.',
  ...over,
})

export const optimisation = (over: Partial<Optimisation> = {}): Optimisation => ({
  doctor_id: 1,
  doctor_name: 'Dr. Anil Sharma',
  plan_date: '2026-08-18',
  available_from: '09:00:00',
  available_until: '13:00:00',
  session_minutes: 240,
  assignments: [
    {
      appointment_id: 1,
      patient_id: 1,
      patient_name: 'Asha Devi',
      position: 1,
      booked_start: '09:00:00',
      predicted_start: '09:00:00',
      predicted_end: '09:08:00',
      expected_wait_minutes: 0,
      expected_duration: 8,
      no_show_probability: 0.12,
      priority_tier: 0,
      overruns_session: false,
      promoted_for_fairness: false,
    },
  ],
  total_expected_wait: 120,
  average_wait: 20,
  baseline_wait: 180,
  baseline_average_wait: 30,
  improvement_pct: 33.3,
  expected_no_shows: 1.4,
  recommended_overbooking: 1,
  projected_overrun_minutes: 0,
  notes: [],
  used_live_presence: true,
  engine: {
    duration: { trained: true, source: 'model' },
    no_show: { trained: true, source: 'model' },
  },
  ...over,
})

export const liveOverview = (over: Partial<LiveOverview> = {}): LiveOverview => ({
  generated_at: '2026-08-18T04:10:00Z',
  doctors_total: 3,
  doctors_present: 2,
  doctors_absent_while_rostered: 1,
  doctors_on_leave: 0,
  patients_waiting: 6,
  longest_wait_minutes: 41,
  active_emergencies: 0,
  doctors: [
    {
      doctor_id: 1,
      doctor_name: 'Dr. Anil Sharma',
      department_name: 'General Medicine',
      presence_status: 'present',
      room: 'OPD 12',
      deviation: 'on_duty_as_rostered',
      minutes_late: 0,
      waiting_count: 6,
      longest_wait_minutes: 41,
      now_serving: 3,
    },
    {
      doctor_id: 2,
      doctor_name: 'Dr. Meena Verma',
      department_name: 'General Medicine',
      presence_status: 'unknown',
      room: null,
      deviation: 'absent_while_rostered',
      minutes_late: 76,
      waiting_count: 0,
      longest_wait_minutes: null,
      now_serving: null,
    },
  ],
  ...over,
})

export const healthSummary = (over: Partial<HealthDeptSummary> = {}): HealthDeptSummary => ({
  start_date: '2026-07-19',
  end_date: '2026-08-18',
  departments: 2,
  doctors: 3,
  total_appointments: 120,
  completed: 96,
  no_show_rate: 0.12,
  average_wait_minutes: 18.4,
  doctor_attendance_rate: 0.9,
  doctor_punctuality_rate: 0.61,
  emergencies_handled: 4,
  bookings_by_channel: [
    { channel: 'kiosk', bookings: 60, share_pct: 50, no_show_rate: 0.05 },
    { channel: 'website', bookings: 40, share_pct: 33.3, no_show_rate: 0.15 },
    { channel: 'ivr', bookings: 20, share_pct: 16.7, no_show_rate: 0.2 },
  ],
  alerts: ['Dr. Meena Verma absent 100% of Saturdays'],
  ...over,
})
