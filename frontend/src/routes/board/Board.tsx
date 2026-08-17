import { useState } from 'react'
import { useParams } from 'react-router-dom'
import { api, deviceKey } from '../../lib/api'
import { usePolling } from '../../lib/hooks'
import { useLang } from '../../lib/i18n'
import { timeOf } from '../../lib/format'
import { Field } from '../../components/ui'
import './board.css'

/** Fast enough that a called token appears almost immediately. */
const REFRESH_MS = 5_000

export function Board() {
  const { doctorId } = useParams()
  const { pick } = useLang()
  const [hasKey, setHasKey] = useState(() => Boolean(deviceKey.get()))
  const id = Number(doctorId)

  // No key means no call. Polling a request that is guaranteed to 401 every
  // five seconds would be pure noise on a screen that is not set up yet.
  const { data, error, lastUpdated } = usePolling(
    () => (hasKey ? api.board(id) : Promise.resolve(null)),
    REFRESH_MS,
    [id, hasKey],
  )

  if (!hasKey) {
    return (
      <div className="board">
        <BoardSetup onSaved={() => setHasKey(true)} />
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="board">
        <div className="board__setup">
          <h2>{pick('बोर्ड लोड नहीं हो सका', 'Could not load the board')}</h2>
          <p className="muted">{error}</p>
          <button
            type="button"
            className="btn btn--ghost"
            onClick={() => {
              deviceKey.clear()
              setHasKey(false)
            }}
          >
            {pick('डिवाइस कुंजी बदलें', 'Change device key')}
          </button>
        </div>
      </div>
    )
  }

  if (!data) {
    return (
      <div className="board">
        <div className="board__serving" style={{ margin: 'auto', minWidth: 300 }}>
          <span className="spinner" aria-hidden />
        </div>
      </div>
    )
  }

  return (
    <div className="board">
      <header className="board__head">
        <div>
          <div className="board__doctor">{data.doctor_name ?? '—'}</div>
          <div className="board__room">
            {pick('कमरा', 'Room')} {data.room || '—'}
          </div>
        </div>
        <div className={`board__status board__status--${data.doctor_present ? 'in' : 'out'}`}>
          <span className="board__dot" aria-hidden />
          {data.doctor_present
            ? pick('डॉक्टर उपलब्ध', 'Doctor available')
            : pick('डॉक्टर नहीं पहुँचे', 'Doctor not arrived')}
        </div>
      </header>

      <div className="board__main">
        <section className="board__serving" aria-live="polite">
          <div className="board__serving-label">{pick('अभी बुलाया जा रहा है', 'Now serving')}</div>
          {data.now_serving !== null ? (
            <div className="board__token">{data.now_serving}</div>
          ) : (
            <div className="board__token board__token--idle">
              {pick('प्रतीक्षा करें', 'Please wait')}
            </div>
          )}
        </section>

        <section className="board__next">
          <h2>{pick('अगले नंबर', 'Next tokens')}</h2>
          <ul className="board__list">
            {data.next_tokens.map((row) => (
              <li
                key={row.token_number}
                className={`board__row${row.is_priority ? ' board__row--priority' : ''}`}
              >
                <span className="board__row-token">{row.token_number}</span>
                {/* Names arrive already masked to initials by the backend. */}
                <span className="board__row-name">{row.display_name}</span>
                {row.estimated_wait_minutes !== null && (
                  <span className="board__row-wait">
                    ~{row.estimated_wait_minutes} {pick('मि', 'min')}
                  </span>
                )}
              </li>
            ))}
            {data.next_tokens.length === 0 && (
              <li className="board__row">
                <span className="board__row-name">
                  {pick('कतार में कोई नहीं', 'Nobody waiting')}
                </span>
              </li>
            )}
          </ul>
        </section>
      </div>

      <footer className="board__foot">
        <div className="board__line hi">{data.status_line_hi}</div>
        <div className="board__line board__line--en">{data.status_line_en}</div>
      </footer>

      {lastUpdated && (
        <div className="board__updated">{timeOf(lastUpdated.toISOString())}</div>
      )}
    </div>
  )
}

function BoardSetup({ onSaved }: { onSaved: () => void }) {
  const { pick } = useLang()
  const [value, setValue] = useState('')

  return (
    <div className="board__setup">
      <h2>{pick('डिस्प्ले सेटअप', 'Display setup')}</h2>
      <p className="muted">
        {pick(
          'यह स्क्रीन अस्पताल के उपकरण के रूप में जुड़ती है। प्रशासक से डिवाइस कुंजी लें।',
          'This screen connects as hospital hardware. Get the device key from the administrator.',
        )}
      </p>
      <Field label={pick('डिवाइस कुंजी', 'Device key')}>
        <input value={value} onChange={(e) => setValue(e.target.value)} placeholder="dev-device-key" />
      </Field>
      <button
        type="button"
        className="btn btn--block"
        style={{ marginTop: 16 }}
        disabled={value.trim().length < 3}
        onClick={() => {
          deviceKey.set(value)
          onSaved()
        }}
      >
        {pick('सहेजें', 'Save')}
      </button>
    </div>
  )
}
