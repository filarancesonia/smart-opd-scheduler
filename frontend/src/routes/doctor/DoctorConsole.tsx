import { useState } from 'react'
import { api } from '../../lib/api'
import type { QueueEntry } from '../../lib/api'
import { useAuth } from '../../lib/auth'
import { useAction, useAsync, usePolling } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { humanise, timeOf, toneFor } from '../../lib/format'
import { Card, Empty, ErrorBox, Loading, Pill, Stat } from '../../components/ui'

const REFRESH_MS = 8_000

export function DoctorConsole() {
  const { user } = useAuth()
  const { pick } = useLang()
  const doctors = useAsync(() => api.doctors())
  const [override, setOverride] = useState<number | null>(null)

  if (doctors.loading) return <Loading />
  if (doctors.error) return <ErrorBox message={doctors.error} onRetry={doctors.reload} />

  const list = doctors.data ?? []
  const own = list.find((d) => d.user_id === user?.id)
  const doctorId = override ?? own?.id ?? list[0]?.id

  if (!doctorId) {
    return (
      <Card>
        <Empty title={pick('कोई डॉक्टर प्रोफ़ाइल नहीं मिली', 'No doctor profile found')} />
      </Card>
    )
  }

  return (
    <div className="stack">
      {!own && (
        <Card title={pick('डॉक्टर चुनें', 'Choose a doctor')}>
          <div className="row">
            {list.map((d) => (
              <button
                key={d.id}
                type="button"
                className={`btn ${doctorId === d.id ? '' : 'btn--ghost'}`}
                onClick={() => setOverride(d.id)}
              >
                {d.full_name}
              </button>
            ))}
          </div>
        </Card>
      )}
      <Console doctorId={doctorId} />
    </div>
  )
}

function Console({ doctorId }: { doctorId: number }) {
  const { t, pick } = useLang()
  const { busy, error: actionError, run } = useAction()

  const presence = usePolling(() => api.presence(doctorId), REFRESH_MS, [doctorId])
  const queue = usePolling(
    () => api.queue(doctorId).catch(() => null),
    REFRESH_MS,
    [doctorId],
  )
  const plan = useAsync(() => api.optimise(doctorId).catch(() => null), [doctorId])

  const refreshAll = () => {
    queue.reload()
    presence.reload()
    plan.reload()
  }

  const entries = queue.data?.entries ?? []
  const activeEntry = entries.find((e) => ['called', 'in_progress'].includes(e.status))
  const waiting = entries.filter((e) => ['waiting', 'skipped'].includes(e.status))

  return (
    <div className="stack">
      <div className="page__head row row--between">
        <div>
          <h1>{presence.data?.doctor_name ?? pick('डॉक्टर पैनल', 'Doctor console')}</h1>
          <p>{presence.data?.department_name}</p>
        </div>
        <div className="row">
          {presence.data?.status !== 'present' && (
            <button
              type="button"
              className="btn"
              disabled={busy}
              onClick={async () => {
                await run(() =>
                  api.setManualPresence({
                    doctor_id: doctorId,
                    status: 'present',
                    room: presence.data?.expected_room ?? '',
                  }),
                )
                refreshAll()
              }}
            >
              {t('markArrived')}
            </button>
          )}
          {!queue.data && (
            <button
              type="button"
              className="btn btn--ghost"
              disabled={busy}
              onClick={async () => {
                await run(() => api.openQueue(doctorId))
                refreshAll()
              }}
            >
              {t('openQueue')}
            </button>
          )}
          <button type="button" className="btn btn--ghost" onClick={refreshAll}>
            {t('refresh')}
          </button>
        </div>
      </div>

      {actionError && <div className="note note--danger">{actionError}</div>}

      <div className="grid grid--4">
        <Stat
          label={pick('उपस्थिति', 'Presence')}
          value={presence.data?.status === 'present' ? t('present') : t('absent')}
          foot={
            presence.data?.status === 'present'
              ? `${t('room')} ${presence.data.room ?? '—'}`
              : presence.data?.minutes_late
                ? `${presence.data.minutes_late} ${t('minutesLate')}`
                : undefined
          }
          tone={presence.data?.status === 'present' ? 'good' : 'alert'}
        />
        <Stat label={t('waiting')} value={queue.data?.waiting_count ?? 0} />
        <Stat
          label={pick('पूरे हुए', 'Completed')}
          value={queue.data?.completed_count ?? 0}
          foot={
            queue.data?.observed_avg_minutes
              ? `${pick('औसत', 'avg')} ${queue.data.observed_avg_minutes} ${t('minutesShort')}`
              : undefined
          }
        />
        <Stat
          label={t('nowServing')}
          value={queue.data?.now_serving ?? '—'}
          foot={activeEntry?.patient_name ?? undefined}
        />
      </div>

      {!queue.data ? (
        <Card>
          <Empty
            title={t('queueClosed')}
            hint={pick(
              'मरीज़ों को बुलाने से पहले कतार शुरू करें।',
              'Open the queue before calling patients.',
            )}
          />
        </Card>
      ) : (
        <>
          {presence.data?.status !== 'present' && (
            <div className="note note--warn">
              {pick(
                'जब तक उपस्थिति दर्ज नहीं होती, मरीज़ों को नहीं बुलाया जा सकता।',
                'Patients cannot be called until your presence is recorded.',
              )}
            </div>
          )}

          <Card
            title={pick('कतार', 'Queue')}
            action={
              <div className="row">
                {activeEntry ? (
                  activeEntry.status === 'called' ? (
                    <>
                      <button
                        type="button"
                        className="btn"
                        disabled={busy}
                        onClick={async () => {
                          await run(() => api.startConsultation(activeEntry.id))
                          refreshAll()
                        }}
                      >
                        {t('startConsult')}
                      </button>
                      <button
                        type="button"
                        className="btn btn--ghost"
                        disabled={busy}
                        onClick={async () => {
                          await run(() => api.skipEntry(activeEntry.id))
                          refreshAll()
                        }}
                      >
                        {t('skip')}
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      className="btn"
                      disabled={busy}
                      onClick={async () => {
                        await run(() => api.completeConsultation(activeEntry.id))
                        refreshAll()
                      }}
                    >
                      {t('completeConsult')}
                    </button>
                  )
                ) : (
                  <button
                    type="button"
                    className="btn"
                    disabled={busy || waiting.length === 0}
                    onClick={async () => {
                      await run(() => api.callNext(doctorId))
                      refreshAll()
                    }}
                  >
                    {t('callNext')}
                  </button>
                )}
              </div>
            }
            flush
          >
            {entries.length === 0 ? (
              <Empty title={t('emptyQueue')} />
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="num">{pick('टोकन', 'Token')}</th>
                      <th>{pick('मरीज़', 'Patient')}</th>
                      <th>{pick('स्थिति', 'Status')}</th>
                      <th className="num">{pick('प्रतीक्षा', 'Wait')}</th>
                      <th>{pick('जुड़े', 'Joined')}</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {entries.map((entry) => (
                      <QueueRow
                        key={entry.id}
                        entry={entry}
                        busy={busy}
                        onAction={refreshAll}
                        run={run}
                      />
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </>
      )}

      {plan.data && plan.data.assignments.length > 0 && (
        <Card title={pick('आज की योजना (AI)', "Today's plan (AI)")}>
          <div className="grid grid--4" style={{ marginBottom: 16 }}>
            <Stat
              label={pick('औसत प्रतीक्षा', 'Average wait')}
              value={`${plan.data.average_wait} ${t('minutesShort')}`}
              foot={`${pick('पहले', 'was')} ${plan.data.baseline_average_wait}`}
            />
            {/* Can legitimately be negative: priority tiers are absolute, so
                seeing a senior citizen first costs a little average wait. That
                is a trade-off worth showing, not hiding. */}
            <Stat
              label={pick('पहले आओ पहले पाओ की तुलना में', 'vs first-come-first-served')}
              value={`${plan.data.improvement_pct > 0 ? '−' : '+'}${Math.abs(plan.data.improvement_pct)}%`}
              tone={plan.data.improvement_pct > 0 ? 'good' : undefined}
              foot={
                plan.data.improvement_pct >= 0
                  ? pick('कुल प्रतीक्षा', 'total waiting')
                  : pick('प्राथमिकता की कीमत', 'cost of priority')
              }
            />
            <Stat
              label={pick('अनुमानित अनुपस्थित', 'Expected no-shows')}
              value={plan.data.expected_no_shows}
              foot={`+${plan.data.recommended_overbooking} ${pick('अतिरिक्त', 'extra')}`}
            />
            <Stat
              label={pick('सत्र', 'Session')}
              value={`${plan.data.available_from.slice(0, 5)}–${plan.data.available_until.slice(0, 5)}`}
              foot={
                plan.data.used_live_presence
                  ? pick('वास्तविक उपस्थिति से', 'from live presence')
                  : pick('रोस्टर से', 'from roster')
              }
            />
          </div>
          {plan.data.notes.map((note) => (
            <div key={note} className="note note--info" style={{ marginBottom: 8 }}>
              {note}
            </div>
          ))}
          <p className="tiny muted" style={{ marginTop: 12, marginBottom: 0 }}>
            {pick('अवधि अनुमान', 'Duration estimates')}:{' '}
            {plan.data.engine?.duration?.source === 'model'
              ? pick('प्रशिक्षित मॉडल', 'trained model')
              : pick('नियम-आधारित', 'heuristic')}
          </p>
        </Card>
      )}
    </div>
  )
}

function QueueRow({
  entry,
  busy,
  onAction,
  run,
}: {
  entry: QueueEntry
  busy: boolean
  onAction: () => void
  run: <T>(fn: () => Promise<T>) => Promise<T | null>
}) {
  const { t, pick } = useLang()
  const canAct = ['waiting', 'skipped', 'called'].includes(entry.status)

  return (
    <tr>
      <td className="num" style={{ fontWeight: 700 }}>
        {entry.token_number}
      </td>
      <td>
        {entry.patient_name}
        {entry.priority_tier > 0 && (
          <>
            {' '}
            <Pill tone="warn">
              {entry.priority_tier >= 3
                ? pick('आपात', 'Emergency')
                : entry.priority_tier === 2
                  ? pick('तत्काल', 'Urgent')
                  : pick('प्राथमिकता', 'Priority')}
            </Pill>
          </>
        )}
      </td>
      <td>
        <Pill tone={toneFor(entry.status)}>{humanise(entry.status)}</Pill>
      </td>
      <td className="num">
        {entry.estimated_wait_minutes === null ? '—' : `${entry.estimated_wait_minutes}m`}
      </td>
      <td className="tiny muted">{timeOf(entry.joined_at)}</td>
      <td>
        {canAct && (
          <button
            type="button"
            className="btn btn--ghost tiny"
            style={{ minHeight: 34, padding: '4px 10px' }}
            disabled={busy}
            onClick={async () => {
              await run(() => api.markNoShow(entry.id))
              onAction()
            }}
          >
            {t('noShow')}
          </button>
        )}
      </td>
    </tr>
  )
}
