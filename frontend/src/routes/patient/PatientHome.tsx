import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import type { Appointment } from '../../lib/api'
import { useAction, useAsync, usePolling } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { clock, humanise, longDate, today, toneFor } from '../../lib/format'
import { Card, Empty, ErrorBox, Loading, Pill } from '../../components/ui'

const ACTIVE = new Set(['booked', 'checked_in', 'in_progress'])

export function PatientHome() {
  const { t, pick, lang } = useLang()
  const { data, error, loading, reload } = useAsync(() => api.myAppointments())

  if (loading) return <Loading />
  if (error) return <ErrorBox message={error} onRetry={reload} />

  const appointments = data ?? []
  const upcoming = appointments.filter((a) => ACTIVE.has(a.status))
  const past = appointments.filter((a) => !ACTIVE.has(a.status))

  return (
    <div className="stack">
      <div className="page__head row row--between">
        <div>
          <h1>{t('myAppointments')}</h1>
          <p>
            {pick(
              'बुकिंग करने से पहले देखें कि डॉक्टर आज वाकई पहुँचे हैं या नहीं।',
              'See whether the doctor has actually arrived before you set out.',
            )}
          </p>
        </div>
        <Link to="/book" className="btn">
          + {t('book')}
        </Link>
      </div>

      {upcoming.length === 0 ? (
        <Card>
          <Empty
            title={t('noAppointments')}
            hint={pick('ऊपर “बुक करें” दबाकर शुरू करें।', 'Use “Book” above to get started.')}
          />
        </Card>
      ) : (
        <div className="grid grid--2">
          {upcoming.map((appointment) => (
            <AppointmentCard key={appointment.id} appointment={appointment} onChange={reload} />
          ))}
        </div>
      )}

      {past.length > 0 && (
        <Card title={pick('पिछले अपॉइंटमेंट', 'Past appointments')} flush>
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>{pick('तारीख', 'Date')}</th>
                  <th>{pick('डॉक्टर', 'Doctor')}</th>
                  <th>{t('bookingReference')}</th>
                  <th>{pick('स्थिति', 'Status')}</th>
                </tr>
              </thead>
              <tbody>
                {past.map((a) => (
                  <tr key={a.id}>
                    <td>{longDate(a.appointment_date, lang)}</td>
                    <td>{a.doctor_name ?? '—'}</td>
                    <td className="mono tiny">{a.booking_reference}</td>
                    <td>
                      <Pill tone={toneFor(a.status)}>{humanise(a.status)}</Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  )
}

function AppointmentCard({
  appointment,
  onChange,
}: {
  appointment: Appointment
  onChange: () => void
}) {
  const { t, pick, lang } = useLang()
  const { busy, run } = useAction()
  const isToday = appointment.appointment_date === today()

  // Only worth asking about presence for today's clinic; a doctor's location
  // next Tuesday is not knowable.
  const presence = usePolling(
    () => (isToday ? api.presence(appointment.doctor_id) : Promise.resolve(null)),
    30_000,
    [appointment.doctor_id, isToday],
  )

  const status = presence.data?.status
  const deviation = presence.data?.deviation

  return (
    <Card>
      <div className="stack">
        <div className="row row--between">
          <div>
            <h3 style={{ marginBottom: 2 }}>{appointment.doctor_name ?? '—'}</h3>
            <div className="small muted">{appointment.department_name}</div>
          </div>
          <Pill tone={toneFor(appointment.status)}>{humanise(appointment.status)}</Pill>
        </div>

        <div className="grid grid--3" style={{ gap: 12 }}>
          <Detail label={pick('तारीख', 'Date')} value={longDate(appointment.appointment_date, lang)} />
          <Detail label={pick('समय', 'Time')} value={clock(appointment.slot_start)} />
          <Detail label={t('room')} value={appointment.room || '—'} />
        </div>

        <div>
          <div className="stat__label">{t('bookingReference')}</div>
          <div className="mono" style={{ fontSize: '1.15rem', fontWeight: 700 }}>
            {appointment.booking_reference}
          </div>
        </div>

        {isToday && presence.data && (
          <div
            className={
              status === 'present'
                ? 'note note--ok'
                : deviation === 'on_approved_leave'
                  ? 'note note--danger'
                  : 'note note--warn'
            }
          >
            {status === 'present' ? (
              <>
                {t('present')}
                {presence.data.room ? ` · ${t('room')} ${presence.data.room}` : ''}
                {presence.data.present_minutes !== null
                  ? ` · ${presence.data.present_minutes} ${t('minutesShort')}`
                  : ''}
              </>
            ) : deviation === 'on_approved_leave' ? (
              t('onLeaveToday')
            ) : (
              <>
                {t('absent')}
                {presence.data.minutes_late ? ` · ${presence.data.minutes_late} ${t('minutesLate')}` : ''}
              </>
            )}
          </div>
        )}

        <div className="row">
          {appointment.status === 'checked_in' && (
            <Link to="/my-turn" className="btn">
              {t('navQueue')}
            </Link>
          )}
          <button
            type="button"
            className="btn btn--ghost"
            disabled={busy}
            onClick={async () => {
              const reason = pick('मरीज़ द्वारा रद्द', 'Cancelled by patient')
              await run(() => api.cancel(appointment.id, reason))
              onChange()
            }}
          >
            {t('cancel')}
          </button>
        </div>
      </div>
    </Card>
  )
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat__label">{label}</div>
      <div style={{ fontWeight: 600 }}>{value}</div>
    </div>
  )
}
