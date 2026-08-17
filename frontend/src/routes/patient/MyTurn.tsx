import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import { useAsync, usePolling } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { humanise, timeOf, today, toneFor } from '../../lib/format'
import { Card, Empty, ErrorBox, Loading, Pill } from '../../components/ui'

export function MyTurn() {
  const { t } = useLang()
  const appointments = useAsync(() => api.myAppointments())

  if (appointments.loading) return <Loading />
  if (appointments.error)
    return <ErrorBox message={appointments.error} onRetry={appointments.reload} />

  // The queue only exists for today, and only once someone has checked in.
  const active = (appointments.data ?? []).find(
    (a) => a.appointment_date === today() && ['checked_in', 'in_progress'].includes(a.status),
  )

  if (!active) {
    return (
      <Card>
        <Empty title={t('notInQueue')} hint={t('notInQueueHint')} />
        <div style={{ textAlign: 'center' }}>
          <Link to="/" className="btn btn--ghost">
            {t('myAppointments')}
          </Link>
        </div>
      </Card>
    )
  }

  return <QueuePosition doctorId={active.doctor_id} doctorName={active.doctor_name} room={active.room} />
}

function QueuePosition({
  doctorId,
  doctorName,
  room,
}: {
  doctorId: number
  doctorName: string | null
  room: string
}) {
  const { t, pick, lang } = useLang()
  // Ten seconds: fast enough that "your turn now" is not stale, slow enough
  // that a full waiting room does not flood the server.
  const { data, error, loading, reload, lastUpdated } = usePolling(
    () => api.myPosition(doctorId),
    10_000,
    [doctorId],
  )

  if (loading && !data) return <Loading />
  if (error && !data) return <ErrorBox message={error} onRetry={reload} />
  if (!data) return null

  const called = data.status === 'called'

  return (
    <div className="stack" style={{ maxWidth: 560, margin: '0 auto' }}>
      <Card>
        <div className="stack" style={{ textAlign: 'center' }}>
          <div>
            <div className="stat__label">{t('yourToken')}</div>
            <div
              style={{
                fontSize: '4.6rem',
                fontWeight: 800,
                lineHeight: 1,
                fontVariantNumeric: 'tabular-nums',
                color: called ? 'var(--ok)' : 'var(--brand-700)',
              }}
            >
              {data.token_number}
            </div>
          </div>

          <div className="row" style={{ justifyContent: 'center' }}>
            <Pill tone={toneFor(data.status)}>{humanise(data.status)}</Pill>
            <Pill tone={data.doctor_present ? 'ok' : 'warn'}>
              {data.doctor_present ? t('present') : t('absent')}
            </Pill>
          </div>

          {/* The backend writes this sentence in both languages; it is the
              honest answer, including "we cannot estimate yet". */}
          <p className={`hi${called ? '' : ''}`} style={{ fontSize: '1.12rem', margin: 0 }}>
            {lang === 'hi' ? data.message_hi : data.message_en}
          </p>

          <div className="grid grid--3" style={{ gap: 12 }}>
            <Metric
              label={t('peopleAhead')}
              value={String(data.people_ahead)}
            />
            <Metric
              label={pick('अनुमानित प्रतीक्षा', 'Estimated wait')}
              value={
                data.estimated_wait_minutes === null
                  ? '—'
                  : `${data.estimated_wait_minutes} ${t('minutesShort')}`
              }
            />
            <Metric label={t('room')} value={room || '—'} />
          </div>
        </div>
      </Card>

      <div className="small muted" style={{ textAlign: 'center' }}>
        {doctorName}
        {lastUpdated && (
          <>
            {' · '}
            {pick('अपडेट', 'Updated')} {timeOf(lastUpdated.toISOString())}
          </>
        )}
      </div>
    </div>
  )
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <div className="stat__label">{label}</div>
      <div style={{ fontSize: '1.4rem', fontWeight: 700, fontVariantNumeric: 'tabular-nums' }}>
        {value}
      </div>
    </div>
  )
}
