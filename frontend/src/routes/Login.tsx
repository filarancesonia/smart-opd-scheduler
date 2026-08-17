import { useState } from 'react'
import { Link, Navigate, useLocation, useNavigate } from 'react-router-dom'
import { Field } from '../components/ui'
import { useAuth } from '../lib/auth'
import { useAction } from '../lib/hooks'
import { useLang } from '../lib/i18n'

export function Login() {
  const { user, login, register, loading } = useAuth()
  const { t, toggle, pick } = useLang()
  const { busy, error, run } = useAction()
  const navigate = useNavigate()
  const location = useLocation() as { state?: { from?: string } }

  const [mode, setMode] = useState<'login' | 'register'>('login')
  const [phone, setPhone] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')

  if (loading) return null
  if (user) return <Navigate to={location.state?.from ?? '/'} replace />

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    const result = await run(async () =>
      mode === 'login'
        ? login(phone, password)
        : register({ phone, full_name: fullName, password }),
    )
    if (result) navigate(location.state?.from ?? '/', { replace: true })
  }

  return (
    <div className="shell">
      <main
        className="page"
        style={{ maxWidth: 460, display: 'flex', alignItems: 'center', minHeight: '100%' }}
      >
        <div className="stack" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <div
              className="topbar__mark"
              aria-hidden
              style={{
                width: 52,
                height: 52,
                margin: '0 auto 14px',
                background: 'var(--brand-700)',
                color: '#fff',
                fontSize: '1rem',
              }}
            >
              OPD
            </div>
            <h1 style={{ marginBottom: 4 }}>{t('appName')}</h1>
            <p className="muted hi">{t('tagline')}</p>
          </div>

          <section className="card">
            <form className="stack" onSubmit={submit}>
              {mode === 'register' && (
                <Field label={t('fullName')}>
                  <input
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    autoComplete="name"
                    required
                    minLength={2}
                  />
                </Field>
              )}

              <Field label={t('phone')} hint={t('phoneHint')}>
                <input
                  value={phone}
                  onChange={(e) => setPhone(e.target.value)}
                  inputMode="numeric"
                  autoComplete="tel"
                  placeholder="9876543210"
                  required
                />
              </Field>

              <Field label={t('password')} hint={mode === 'register' ? t('passwordHint') : undefined}>
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  required
                  minLength={mode === 'register' ? 8 : undefined}
                />
              </Field>

              {error && <div className="note note--danger">{error}</div>}

              <button type="submit" className="btn btn--block" disabled={busy}>
                {busy && <span className="spinner" aria-hidden />}
                {mode === 'login' ? t('signIn') : t('register')}
              </button>
            </form>

            <div className="row row--between" style={{ marginTop: 16 }}>
              <button
                type="button"
                className="btn btn--ghost"
                onClick={() => setMode(mode === 'login' ? 'register' : 'login')}
              >
                {mode === 'login' ? t('noAccount') : t('haveAccount')}
              </button>
              <button type="button" className="btn btn--ghost" onClick={toggle}>
                {t('language')}
              </button>
            </div>
          </section>

          <div className="small muted" style={{ textAlign: 'center' }}>
            {pick(
              'बिना स्मार्टफोन वाले मरीज़ अस्पताल में लगी टचस्क्रीन या फ़ोन लाइन का उपयोग करें।',
              'Patients without a smartphone can use the touchscreen inside the hospital or the phone line.',
            )}
            <div style={{ marginTop: 6 }}>
              <Link to="/kiosk">{pick('कियोस्क खोलें', 'Open kiosk')}</Link>
            </div>
          </div>
        </div>
      </main>
    </div>
  )
}
