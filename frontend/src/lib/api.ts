/* Typed client for the FastAPI backend.
 *
 * The backend returns a consistent envelope on failure:
 *   { "error": { "code": "conflict", "message": "...", "details": {...} } }
 * ApiError carries all three so a screen can show the message and still branch
 * on the code — which matters, because "fully booked" and "doctor on leave"
 * are both 409 and need different words.
 */

const BASE = '/api/v1'

export class ApiError extends Error {
  code: string
  status: number
  details: Record<string, unknown>

  constructor(status: number, code: string, message: string, details: Record<string, unknown> = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

const TOKEN_KEY = 'opd.access_token'
const REFRESH_KEY = 'opd.refresh_token'
const DEVICE_KEY = 'opd.device_key'

export const tokens = {
  get access() {
    return localStorage.getItem(TOKEN_KEY)
  },
  get refresh() {
    return localStorage.getItem(REFRESH_KEY)
  },
  set(access: string, refresh: string) {
    localStorage.setItem(TOKEN_KEY, access)
    localStorage.setItem(REFRESH_KEY, refresh)
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY)
    localStorage.removeItem(REFRESH_KEY)
  },
}

/* Kiosks and corridor screens authenticate as provisioned devices, not users.
 * Storing the key in localStorage is a real limitation: anything in the
 * browser is readable by whoever controls the browser. It is acceptable only
 * because these run on hospital-owned hardware in kiosk mode. A production
 * deployment should terminate the device key in a local agent instead. */
export const deviceKey = {
  get() {
    return localStorage.getItem(DEVICE_KEY) ?? ''
  },
  set(value: string) {
    localStorage.setItem(DEVICE_KEY, value.trim())
  },
  clear() {
    localStorage.removeItem(DEVICE_KEY)
  },
}

type Options = {
  method?: string
  body?: unknown
  auth?: boolean
  device?: boolean
  signal?: AbortSignal
}

async function request<T>(path: string, options: Options = {}): Promise<T> {
  const { method = 'GET', body, auth = true, device = false, signal } = options
  const headers: Record<string, string> = {}

  if (body !== undefined) headers['Content-Type'] = 'application/json'
  if (auth && tokens.access) headers['Authorization'] = `Bearer ${tokens.access}`
  if (device) headers['X-Device-Key'] = deviceKey.get()

  const response = await fetch(`${BASE}${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal,
  })

  if (response.status === 204) return undefined as T

  const text = await response.text()
  const payload = text ? JSON.parse(text) : null

  if (!response.ok) {
    const envelope = payload?.error
    if (envelope) {
      throw new ApiError(response.status, envelope.code, envelope.message, envelope.details ?? {})
    }
    // FastAPI's own validation errors have a different shape.
    const detail = payload?.detail
    const message = Array.isArray(detail)
      ? detail.map((d: { msg?: string }) => d.msg ?? 'Invalid value').join('; ')
      : typeof detail === 'string'
        ? detail
        : `Request failed (${response.status})`
    throw new ApiError(response.status, 'http_error', message)
  }

  return payload as T
}

/* --- types (mirroring the backend schemas) ------------------------------ */

export type Role = 'patient' | 'doctor' | 'staff' | 'admin' | 'health_dept' | 'device'

export type User = {
  id: number
  phone: string
  full_name: string
  email: string | null
  role: Role
  is_active: boolean
  abha_id: string | null
  preferred_language: string
}

export type TokenPair = {
  access_token: string
  refresh_token: string
  token_type: string
  expires_in: number
}

export type Department = {
  id: number
  name: string
  code: string
  floor: string | null
  is_active: boolean
}

export type Doctor = {
  id: number
  user_id: number
  department_id: number
  registration_no: string
  qualification: string
  specialisation: string
  designation: string
  avg_consultation_minutes: number
  max_patients_per_day: number
  is_accepting_patients: boolean
  full_name: string | null
  department_name: string | null
}

export type Slot = {
  start: string
  end: string
  room: string
  available: boolean
}

export type DaySlots = {
  doctor_id: number
  date: string
  is_on_leave: boolean
  capacity: number
  booked: number
  remaining: number
  slots: Slot[]
  presence_warning: string | null
}

export type Appointment = {
  id: number
  booking_reference: string
  patient_id: number
  doctor_id: number
  department_id: number
  appointment_date: string
  slot_start: string
  slot_end: string
  room: string
  status: string
  channel: string
  reason: string
  is_follow_up: boolean
  checked_in_at: string | null
  patient_name: string | null
  doctor_name: string | null
  department_name: string | null
}

export type Patient = {
  id: number
  user_id: number | null
  full_name: string
  phone: string
  age: number | null
  gender: string | null
  abha_id: string | null
  preferred_language: string
  is_pregnant: boolean
  has_disability: boolean
  is_senior_citizen: boolean
}

export type Presence = {
  doctor_id: number
  doctor_name: string | null
  department_name: string | null
  status: string
  room: string | null
  since: string | null
  last_signal_at: string | null
  last_credential_type: string | null
  confidence: number
  present_minutes: number | null
  deviation: string | null
  expected_room: string | null
  expected_until: string | null
  minutes_late: number | null
}

export type QueueEntry = {
  id: number
  appointment_id: number
  patient_id: number
  patient_name: string | null
  token_number: number
  position: number
  priority_tier: number
  status: string
  joined_at: string
  called_at: string | null
  started_at: string | null
  completed_at: string | null
  predicted_duration: number
  estimated_wait_minutes: number | null
  skip_count: number
  note: string
}

export type Queue = {
  session_id: number
  doctor_id: number
  doctor_name: string | null
  session_date: string
  room: string
  is_open: boolean
  doctor_present: boolean
  waiting_count: number
  completed_count: number
  observed_avg_minutes: number | null
  now_serving: number | null
  entries: QueueEntry[]
}

export type BoardRow = {
  token_number: number
  display_name: string
  status: string
  estimated_wait_minutes: number | null
  is_priority: boolean
}

export type DisplayBoard = {
  doctor_id: number
  doctor_name: string | null
  room: string
  doctor_present: boolean
  status_line_hi: string
  status_line_en: string
  now_serving: number | null
  next_tokens: BoardRow[]
  updated_at: string
}

export type MyPosition = {
  token_number: number
  position: number
  people_ahead: number
  status: string
  estimated_wait_minutes: number | null
  estimated_call_time: string | null
  doctor_present: boolean
  message_hi: string
  message_en: string
}

export type KioskTicket = {
  booking_reference: string
  patient_name: string
  doctor_name: string
  department_name: string
  room: string
  appointment_date: string
  slot_start: string
  message_hi: string
  message_en: string
}

export type LiveDoctorRow = {
  doctor_id: number
  doctor_name: string | null
  department_name: string | null
  presence_status: string
  room: string | null
  deviation: string | null
  minutes_late: number | null
  waiting_count: number
  longest_wait_minutes: number | null
  now_serving: number | null
}

export type LiveOverview = {
  generated_at: string
  doctors_total: number
  doctors_present: number
  doctors_absent_while_rostered: number
  doctors_on_leave: number
  patients_waiting: number
  longest_wait_minutes: number | null
  active_emergencies: number
  doctors: LiveDoctorRow[]
}

export type AttendanceRow = {
  doctor_id: number
  doctor_name: string | null
  department_name: string | null
  days_rostered: number
  days_present: number
  days_absent: number
  days_on_leave: number
  attendance_rate: number
  average_minutes_late: number
  days_late: number
  punctuality_rate: number
}

export type WaitTimeRow = {
  label: string
  sample_size: number
  mean_minutes: number
  median_minutes: number
  p90_minutes: number
  max_minutes: number
}

export type WaitTimeReport = {
  start_date: string
  end_date: string
  overall: WaitTimeRow | null
  by_department: WaitTimeRow[]
  by_doctor: WaitTimeRow[]
}

export type ChannelMixRow = {
  channel: string
  bookings: number
  share_pct: number
  no_show_rate: number
}

export type HealthDeptSummary = {
  start_date: string
  end_date: string
  departments: number
  doctors: number
  total_appointments: number
  completed: number
  no_show_rate: number
  average_wait_minutes: number
  doctor_attendance_rate: number
  doctor_punctuality_rate: number
  emergencies_handled: number
  bookings_by_channel: ChannelMixRow[]
  alerts: string[]
}

export type OptimisationAssignment = {
  appointment_id: number
  patient_id: number
  patient_name: string
  position: number
  booked_start: string
  predicted_start: string
  predicted_end: string
  expected_wait_minutes: number
  expected_duration: number
  no_show_probability: number
  priority_tier: number
  overruns_session: boolean
  promoted_for_fairness: boolean
}

export type Optimisation = {
  doctor_id: number
  doctor_name: string | null
  plan_date: string
  available_from: string
  available_until: string
  session_minutes: number
  assignments: OptimisationAssignment[]
  total_expected_wait: number
  average_wait: number
  baseline_wait: number
  baseline_average_wait: number
  improvement_pct: number
  expected_no_shows: number
  recommended_overbooking: number
  projected_overrun_minutes: number
  notes: string[]
  used_live_presence: boolean
  engine: Record<string, { trained: boolean; source: string }>
}

/* --- endpoints ---------------------------------------------------------- */

export const api = {
  // Identity
  login: (phone: string, password: string) =>
    request<TokenPair>('/auth/login', { method: 'POST', body: { phone, password }, auth: false }),
  register: (body: {
    phone: string
    full_name: string
    password: string
    role?: Role
    preferred_language?: string
  }) => request<User>('/auth/register', { method: 'POST', body, auth: false }),
  me: () => request<User>('/auth/me'),

  // Room 2
  departments: () => request<Department[]>('/departments'),
  doctors: (departmentId?: number) =>
    request<Doctor[]>(`/doctors${departmentId ? `?department_id=${departmentId}` : ''}`),
  doctor: (id: number) => request<Doctor>(`/doctors/${id}`),

  // Room 1
  presence: (doctorId: number) => request<Presence>(`/presence/doctors/${doctorId}`),
  livePresence: () => request<Presence[]>('/presence/live'),
  setManualPresence: (body: { doctor_id: number; status: string; room?: string; note?: string }) =>
    request<Presence>('/presence/manual', { method: 'POST', body }),

  // Room 3
  slots: (doctorId: number, date: string) =>
    request<DaySlots>(`/booking/doctors/${doctorId}/slots?date=${date}`),
  book: (body: { doctor_id: number; appointment_date: string; reason?: string; preferred_start?: string }, channel = 'website') =>
    request<Appointment>(`/booking/appointments?channel=${channel}`, { method: 'POST', body }),
  myAppointments: () => request<Appointment[]>('/booking/appointments'),
  appointmentsFor: (doctorId: number, date: string) =>
    request<Appointment[]>(`/booking/appointments?doctor_id=${doctorId}&on_date=${date}`),
  myPatient: () => request<Patient>('/booking/me/patient'),
  cancel: (id: number, reason: string) =>
    request<Appointment>(`/booking/appointments/${id}/cancel`, { method: 'POST', body: { reason } }),

  // Kiosk (device key)
  kioskLookup: (phone: string) =>
    request<Patient[]>('/booking/kiosk/lookup', {
      method: 'POST',
      body: { phone },
      auth: false,
      device: true,
    }),
  kioskBook: (body: {
    doctor_id: number
    appointment_date: string
    reason?: string
    patient: { full_name: string; phone: string; age?: number | null }
  }) =>
    request<KioskTicket>('/booking/kiosk/book', {
      method: 'POST',
      body,
      auth: false,
      device: true,
    }),

  // Room 4
  optimise: (doctorId: number, date?: string) =>
    request<Optimisation>(`/scheduling/doctors/${doctorId}/optimise${date ? `?date=${date}` : ''}`),

  // Room 5
  queue: (doctorId: number) => request<Queue>(`/queue/doctors/${doctorId}`),
  openQueue: (doctorId: number, room = '') =>
    request<Queue>(`/queue/doctors/${doctorId}/open`, { method: 'POST', body: { room } }),
  joinQueue: (appointmentId: number) =>
    request<QueueEntry>('/queue/join', { method: 'POST', body: { appointment_id: appointmentId } }),
  callNext: (doctorId: number) =>
    request<{ called: QueueEntry | null; reason: string | null; remaining_waiting: number }>(
      `/queue/doctors/${doctorId}/call-next`,
      { method: 'POST' },
    ),
  startConsultation: (entryId: number) =>
    request<QueueEntry>(`/queue/entries/${entryId}/start`, { method: 'POST' }),
  completeConsultation: (entryId: number, note = '') =>
    request<QueueEntry>(`/queue/entries/${entryId}/complete`, { method: 'POST', body: { note } }),
  skipEntry: (entryId: number) =>
    request<QueueEntry>(`/queue/entries/${entryId}/skip`, { method: 'POST' }),
  markNoShow: (entryId: number) =>
    request<QueueEntry>(`/queue/entries/${entryId}/no-show`, { method: 'POST' }),
  myPosition: (doctorId: number) => request<MyPosition>(`/queue/doctors/${doctorId}/my-position`),
  board: (doctorId: number, signal?: AbortSignal) =>
    request<DisplayBoard>(`/queue/doctors/${doctorId}/board`, { auth: false, device: true, signal }),

  // Room 8
  liveOverview: () => request<LiveOverview>('/analytics/live'),
  attendance: () => request<{ rows: AttendanceRow[] }>('/analytics/attendance'),
  waitTimes: () => request<WaitTimeReport>('/analytics/wait-times'),
  channels: () => request<ChannelMixRow[]>('/analytics/channels'),
  healthSummary: () => request<HealthDeptSummary>('/analytics/health-department'),
}
