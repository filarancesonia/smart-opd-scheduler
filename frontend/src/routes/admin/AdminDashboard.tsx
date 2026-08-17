import { Link } from 'react-router-dom'
import { api } from '../../lib/api'
import { useAsync, usePolling } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { humanise, minutes, percent, timeOf, toneFor } from '../../lib/format'
import { Card, Empty, ErrorBox, Loading, Pill, Stat } from '../../components/ui'

const REFRESH_MS = 15_000

export function AdminDashboard() {
  const { t, pick } = useLang()

  const live = usePolling(() => api.liveOverview(), REFRESH_MS)
  const attendance = useAsync(() => api.attendance())
  const waits = useAsync(() => api.waitTimes())
  const channels = useAsync(() => api.channels())
  const summary = useAsync(() => api.healthSummary())

  if (live.loading && !live.data) return <Loading />
  if (live.error && !live.data) return <ErrorBox message={live.error} onRetry={live.reload} />

  const overview = live.data

  return (
    <div className="stack">
      <div className="page__head row row--between">
        <div>
          <h1>{t('navAdmin')}</h1>
          <p>
            {pick(
              'सभी आँकड़े वही हैं जो सिस्टम ने खुद दर्ज किए — कोई अलग रिकॉर्ड नहीं रखा जाता।',
              'Every figure here is derived from what the system already recorded — no separate books.',
            )}
          </p>
        </div>
        {live.lastUpdated && (
          <span className="small muted">
            {pick('अपडेट', 'Updated')} {timeOf(live.lastUpdated.toISOString())}
          </span>
        )}
      </div>

      {/* Alerts first — the whole point of a control tower. */}
      {summary.data && (
        <Card title={t('alerts')}>
          {summary.data.alerts.length === 0 ? (
            <div className="note note--ok">{t('noAlerts')}</div>
          ) : (
            <div className="stack stack--sm">
              {summary.data.alerts.map((alert) => (
                <div key={alert} className="note note--warn">
                  {alert}
                </div>
              ))}
            </div>
          )}
        </Card>
      )}

      {overview && (
        <div className="grid grid--4">
          <Stat
            label={t('doctorsPresent')}
            value={`${overview.doctors_present} / ${overview.doctors_total}`}
            foot={`${overview.doctors_on_leave} ${t('onLeave').toLowerCase()}`}
            tone={overview.doctors_present === 0 && overview.doctors_total > 0 ? 'alert' : 'good'}
          />
          <Stat
            label={t('absentRostered')}
            value={overview.doctors_absent_while_rostered}
            tone={overview.doctors_absent_while_rostered > 0 ? 'alert' : undefined}
            foot={pick('ड्यूटी पर होने चाहिए', 'should be on duty now')}
          />
          <Stat label={t('patientsWaiting')} value={overview.patients_waiting} />
          <Stat
            label={t('longestWait')}
            value={
              overview.longest_wait_minutes === null
                ? '—'
                : `${overview.longest_wait_minutes} ${t('minutesShort')}`
            }
            tone={(overview.longest_wait_minutes ?? 0) > 45 ? 'alert' : undefined}
            foot={
              overview.active_emergencies > 0
                ? `${overview.active_emergencies} ${t('emergencies').toLowerCase()}`
                : undefined
            }
          />
        </div>
      )}

      {/* Live doctor board */}
      <Card title={pick('अभी की स्थिति', 'Right now')} flush>
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>{pick('डॉक्टर', 'Doctor')}</th>
                <th>{pick('विभाग', 'Department')}</th>
                <th>{pick('उपस्थिति', 'Presence')}</th>
                <th>{pick('रोस्टर से तुलना', 'Vs roster')}</th>
                <th className="num">{t('waiting')}</th>
                <th className="num">{t('longestWait')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(overview?.doctors ?? []).map((row) => (
                <tr key={row.doctor_id}>
                  <td style={{ fontWeight: 600 }}>{row.doctor_name}</td>
                  <td className="small muted">{row.department_name}</td>
                  <td>
                    <Pill tone={toneFor(row.presence_status)}>
                      {humanise(row.presence_status)}
                    </Pill>
                    {row.room && <span className="tiny muted"> · {row.room}</span>}
                  </td>
                  <td>
                    {row.deviation ? (
                      <Pill tone={toneFor(row.deviation)}>{humanise(row.deviation)}</Pill>
                    ) : (
                      '—'
                    )}
                    {row.minutes_late ? (
                      <span className="tiny muted"> · {row.minutes_late}m</span>
                    ) : null}
                  </td>
                  <td className="num">{row.waiting_count}</td>
                  <td className="num">
                    {row.longest_wait_minutes === null ? '—' : `${row.longest_wait_minutes}m`}
                  </td>
                  <td>
                    <Link
                      to={`/board/${row.doctor_id}`}
                      className="btn btn--ghost tiny"
                      style={{ minHeight: 34, padding: '4px 10px' }}
                    >
                      {pick('बोर्ड', 'Board')}
                    </Link>
                  </td>
                </tr>
              ))}
              {(overview?.doctors ?? []).length === 0 && (
                <tr>
                  <td colSpan={7}>
                    <Empty title={pick('कोई डॉक्टर पंजीकृत नहीं', 'No doctors registered')} />
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      <div className="grid grid--2">
        {/* Attendance */}
        <Card title={t('attendance')} flush>
          {attendance.loading && <Loading />}
          {attendance.data && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{pick('डॉक्टर', 'Doctor')}</th>
                    <th className="num">{pick('उपस्थिति', 'Attendance')}</th>
                    <th className="num">{pick('समय पर', 'Punctual')}</th>
                    <th className="num">{pick('औसत देरी', 'Avg late')}</th>
                  </tr>
                </thead>
                <tbody>
                  {attendance.data.rows.map((row) => (
                    <tr key={row.doctor_id}>
                      <td>{row.doctor_name}</td>
                      <td className="num">
                        <Pill tone={row.attendance_rate >= 0.85 ? 'ok' : 'danger'}>
                          {percent(row.attendance_rate)}
                        </Pill>
                      </td>
                      <td className="num">{percent(row.punctuality_rate)}</td>
                      <td className="num">{minutes(row.average_minutes_late, 'm')}</td>
                    </tr>
                  ))}
                  {attendance.data.rows.length === 0 && (
                    <tr>
                      <td colSpan={4}>
                        <Empty title={pick('कोई डेटा नहीं', 'No data yet')} />
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {/* Wait times */}
        <Card title={t('waitTimes')} flush>
          {waits.loading && <Loading />}
          {waits.data && !waits.data.overall && (
            <Empty
              title={pick('अभी कोई प्रतीक्षा दर्ज नहीं', 'No waits recorded yet')}
              hint={pick(
                'कतार चलने के बाद यहाँ आँकड़े दिखेंगे।',
                'Figures appear once queues have run.',
              )}
            />
          )}
          {waits.data?.overall && (
            <>
              <div className="grid grid--3" style={{ padding: '0 20px 16px' }}>
                <Stat
                  label={pick('औसत', 'Mean')}
                  value={minutes(waits.data.overall.mean_minutes, 'm')}
                />
                <Stat
                  label={pick('मध्यक', 'Median')}
                  value={minutes(waits.data.overall.median_minutes, 'm')}
                />
                <Stat
                  label="P90"
                  value={minutes(waits.data.overall.p90_minutes, 'm')}
                  tone={waits.data.overall.p90_minutes > 45 ? 'alert' : undefined}
                />
              </div>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{pick('विभाग', 'Department')}</th>
                      <th className="num">{pick('औसत', 'Mean')}</th>
                      <th className="num">P90</th>
                      <th className="num">n</th>
                    </tr>
                  </thead>
                  <tbody>
                    {waits.data.by_department.map((row) => (
                      <tr key={row.label}>
                        <td>{row.label}</td>
                        <td className="num">{row.mean_minutes}m</td>
                        <td className="num">{row.p90_minutes}m</td>
                        <td className="num">{row.sample_size}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </Card>
      </div>

      <div className="grid grid--2">
        {/* Channel mix */}
        <Card title={t('channels')}>
          {channels.loading && <Loading />}
          {channels.data && channels.data.length === 0 && (
            <Empty title={pick('कोई बुकिंग नहीं', 'No bookings yet')} />
          )}
          <div className="stack stack--sm">
            {(channels.data ?? []).map((row) => (
              <div key={row.channel}>
                <div className="row row--between small">
                  <span style={{ fontWeight: 600 }}>{humanise(row.channel)}</span>
                  <span className="muted">
                    {row.bookings} · {row.share_pct}%
                  </span>
                </div>
                <div
                  style={{
                    height: 10,
                    borderRadius: 999,
                    background: 'var(--surface-3)',
                    overflow: 'hidden',
                    marginTop: 4,
                  }}
                >
                  <div
                    style={{
                      width: `${row.share_pct}%`,
                      height: '100%',
                      background: 'var(--brand-600)',
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
          <p className="tiny muted" style={{ marginTop: 14, marginBottom: 0 }}>
            {pick(
              'जिस माध्यम का कोई उपयोग नहीं करता, वह उन लोगों के लिए विफल रहा जिनके लिए बनाया गया था।',
              'A channel nobody uses is a channel that failed the people it was built for.',
            )}
          </p>
        </Card>

        {/* Health department rollup */}
        <Card title={pick('स्वास्थ्य विभाग सारांश', 'Health Department summary')}>
          {summary.loading && <Loading />}
          {summary.data && (
            <div className="grid grid--2" style={{ gap: 12 }}>
              <Stat
                label={pick('कुल अपॉइंटमेंट', 'Appointments')}
                value={summary.data.total_appointments}
                foot={`${summary.data.completed} ${pick('पूरे', 'completed')}`}
              />
              <Stat
                label={pick('अनुपस्थिति दर', 'No-show rate')}
                value={percent(summary.data.no_show_rate)}
                tone={summary.data.no_show_rate > 0.25 ? 'alert' : undefined}
              />
              <Stat
                label={pick('डॉक्टर उपस्थिति', 'Doctor attendance')}
                value={percent(summary.data.doctor_attendance_rate)}
                tone={summary.data.doctor_attendance_rate < 0.85 ? 'alert' : 'good'}
              />
              <Stat
                label={pick('आपात मामले', 'Emergencies')}
                value={summary.data.emergencies_handled}
              />
            </div>
          )}
        </Card>
      </div>
    </div>
  )
}
