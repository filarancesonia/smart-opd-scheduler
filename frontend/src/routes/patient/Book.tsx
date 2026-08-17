import { useState } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { api } from '../../lib/api'
import type { Doctor } from '../../lib/api'
import { useAction, useAsync } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { addDays, clock, longDate, today } from '../../lib/format'
import { Card, Empty, ErrorBox, Field, Loading, Pill } from '../../components/ui'

export function Book() {
  const { t, pick, lang } = useLang()
  const navigate = useNavigate()
  const { busy, error: bookError, run } = useAction()

  const [departmentId, setDepartmentId] = useState<number | null>(null)
  const [doctor, setDoctor] = useState<Doctor | null>(null)
  const [date, setDate] = useState(today())
  const [slot, setSlot] = useState<string | null>(null)
  const [reason, setReason] = useState('')
  const [reference, setReference] = useState<string | null>(null)

  const departments = useAsync(() => api.departments())
  const doctors = useAsync(
    () => (departmentId ? api.doctors(departmentId) : Promise.resolve([])),
    [departmentId],
  )
  const slots = useAsync(
    () => (doctor ? api.slots(doctor.id, date) : Promise.resolve(null)),
    [doctor?.id, date],
  )

  if (reference) {
    return (
      <div className="stack" style={{ maxWidth: 520, margin: '0 auto' }}>
        <Card>
          <div className="stack" style={{ textAlign: 'center' }}>
            <Pill tone="ok">{t('booked')}</Pill>
            <div>
              <div className="stat__label">{t('bookingReference')}</div>
              <div className="mono" style={{ fontSize: '2rem', fontWeight: 700 }}>
                {reference}
              </div>
            </div>
            <p className="muted hi">
              {pick(
                'यह नंबर सँभालकर रखें। रिसेप्शन पर दिखाकर टोकन लें।',
                'Keep this number. Show it at reception to collect your token.',
              )}
            </p>
            <Link to="/" className="btn btn--block">
              {t('myAppointments')}
            </Link>
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className="stack">
      <div className="page__head">
        <h1>{t('book')}</h1>
        <p>
          {pick(
            'विभाग और डॉक्टर चुनें। यदि डॉक्टर अभी नहीं पहुँचे हैं, तो आपको यहीं बता दिया जाएगा।',
            'Pick a department and doctor. If the doctor has not arrived, you will be told here.',
          )}
        </p>
      </div>

      {/* Step 1 — department */}
      <Card title={`1. ${t('chooseDepartment')}`}>
        {departments.loading && <Loading />}
        {departments.error && <ErrorBox message={departments.error} onRetry={departments.reload} />}
        <div className="row">
          {(departments.data ?? []).map((department) => (
            <button
              key={department.id}
              type="button"
              className={`btn ${departmentId === department.id ? '' : 'btn--ghost'}`}
              onClick={() => {
                setDepartmentId(department.id)
                setDoctor(null)
                setSlot(null)
              }}
            >
              {department.name}
            </button>
          ))}
          {!departments.loading && (departments.data ?? []).length === 0 && (
            <Empty title={pick('कोई विभाग नहीं मिला', 'No departments found')} />
          )}
        </div>
      </Card>

      {/* Step 2 — doctor */}
      {departmentId && (
        <Card title={`2. ${t('chooseDoctor')}`}>
          {doctors.loading && <Loading />}
          {doctors.error && <ErrorBox message={doctors.error} onRetry={doctors.reload} />}
          <div className="grid grid--3">
            {(doctors.data ?? []).map((candidate) => (
              <button
                key={candidate.id}
                type="button"
                className={`btn ${doctor?.id === candidate.id ? '' : 'btn--ghost'}`}
                style={{ flexDirection: 'column', alignItems: 'flex-start', textAlign: 'start' }}
                disabled={!candidate.is_accepting_patients}
                onClick={() => {
                  setDoctor(candidate)
                  setSlot(null)
                }}
              >
                <span style={{ fontWeight: 700 }}>{candidate.full_name}</span>
                <span className="tiny" style={{ opacity: 0.85 }}>
                  {candidate.specialisation || candidate.department_name}
                </span>
              </button>
            ))}
            {!doctors.loading && (doctors.data ?? []).length === 0 && (
              <Empty title={pick('इस विभाग में कोई डॉक्टर नहीं', 'No doctors in this department')} />
            )}
          </div>
        </Card>
      )}

      {/* Step 3 — date */}
      {doctor && (
        <Card title={`3. ${t('chooseDate')}`}>
          <div className="row">
            {[0, 1, 2, 3, 4, 5, 6].map((offset) => {
              const value = addDays(today(), offset)
              const label =
                offset === 0 ? t('today') : offset === 1 ? t('tomorrow') : longDate(value, lang)
              return (
                <button
                  key={value}
                  type="button"
                  className={`btn ${date === value ? '' : 'btn--ghost'}`}
                  onClick={() => {
                    setDate(value)
                    setSlot(null)
                  }}
                >
                  {label}
                </button>
              )
            })}
          </div>
        </Card>
      )}

      {/* Step 4 — slot */}
      {doctor && (
        <Card
          title={`4. ${t('chooseSlot')}`}
          action={
            slots.data && !slots.data.is_on_leave ? (
              <span className="small muted">
                {slots.data.remaining} {t('slotsLeft')}
              </span>
            ) : null
          }
        >
          {slots.loading && <Loading />}
          {slots.error && <ErrorBox message={slots.error} onRetry={slots.reload} />}

          {slots.data?.presence_warning && (
            <div className="note note--warn" style={{ marginBottom: 14 }}>
              {slots.data.presence_warning}
            </div>
          )}

          {slots.data?.is_on_leave && (
            <div className="note note--danger">{t('onLeave')}</div>
          )}

          {slots.data && !slots.data.is_on_leave && slots.data.slots.length === 0 && (
            <Empty title={t('noSlots')} />
          )}

          {slots.data && slots.data.slots.length > 0 && (
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(auto-fill, minmax(96px, 1fr))',
                gap: 8,
              }}
            >
              {slots.data.slots.map((option) => (
                <button
                  key={option.start}
                  type="button"
                  className={`btn ${slot === option.start ? '' : 'btn--ghost'}`}
                  disabled={!option.available}
                  onClick={() => setSlot(option.start)}
                  style={{ padding: '10px 6px' }}
                >
                  {clock(option.start)}
                </button>
              ))}
            </div>
          )}
        </Card>
      )}

      {/* Step 5 — confirm */}
      {doctor && slot && (
        <Card title={`5. ${t('confirm')}`}>
          <div className="stack">
            <Field label={pick('समस्या (वैकल्पिक)', 'Reason (optional)')}>
              <input
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                placeholder={pick('जैसे: तीन दिन से बुखार', 'e.g. Fever for three days')}
              />
            </Field>

            <div className="note note--info">
              <strong>{doctor.full_name}</strong> · {longDate(date, lang)} · {clock(slot)}
              {slots.data?.slots.find((s) => s.start === slot)?.room
                ? ` · ${t('room')} ${slots.data.slots.find((s) => s.start === slot)!.room}`
                : ''}
            </div>

            {bookError && <div className="note note--danger">{bookError}</div>}

            <button
              type="button"
              className="btn btn--lg btn--block"
              disabled={busy}
              onClick={async () => {
                const created = await run(() =>
                  api.book({
                    doctor_id: doctor.id,
                    appointment_date: date,
                    preferred_start: slot,
                    reason,
                  }),
                )
                if (created) setReference(created.booking_reference)
                else slots.reload()
              }}
            >
              {busy && <span className="spinner" aria-hidden />}
              {t('confirm')}
            </button>

            <button type="button" className="btn btn--ghost" onClick={() => navigate('/')}>
              {t('cancel')}
            </button>
          </div>
        </Card>
      )}
    </div>
  )
}
