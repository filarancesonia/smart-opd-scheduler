import { NavLink, Link, useNavigate } from 'react-router-dom'
import type { ReactNode } from 'react'
import { useAuth } from '../lib/auth'
import { useLang } from '../lib/i18n'

/** Chrome for the signed-in surfaces. Kiosk and board deliberately skip it. */
export function Shell({ children, wide = false }: { children: ReactNode; wide?: boolean }) {
  const { user, logout } = useAuth()
  const { t, toggle } = useLang()
  const navigate = useNavigate()

  const role = user?.role

  return (
    <div className="shell">
      <header className="topbar">
        <Link to="/" className="topbar__brand">
          <span className="topbar__mark" aria-hidden>
            OPD
          </span>
          <span>{t('appName')}</span>
        </Link>

        <nav className="topbar__nav">
          {user && (
            <>
              <NavLink to="/" className="topbar__link" end>
                {t('navBook')}
              </NavLink>
              {role === 'patient' && (
                <NavLink to="/my-turn" className="topbar__link">
                  {t('navQueue')}
                </NavLink>
              )}
              {role === 'doctor' && (
                <NavLink to="/doctor" className="topbar__link">
                  {t('navDoctor')}
                </NavLink>
              )}
              {(role === 'admin' || role === 'health_dept' || role === 'staff') && (
                <NavLink to="/admin" className="topbar__link">
                  {t('navAdmin')}
                </NavLink>
              )}
            </>
          )}

          <button
            type="button"
            className="topbar__link"
            onClick={toggle}
            style={{ background: 'transparent', border: 0, cursor: 'pointer' }}
          >
            {t('language')}
          </button>

          {user ? (
            <button
              type="button"
              className="topbar__link"
              onClick={() => {
                logout()
                navigate('/login')
              }}
              style={{ background: 'transparent', border: 0, cursor: 'pointer' }}
            >
              {t('signOut')}
            </button>
          ) : (
            <NavLink to="/login" className="topbar__link">
              {t('signIn')}
            </NavLink>
          )}
        </nav>
      </header>

      <main className={`page${wide ? ' page--wide' : ''}`}>{children}</main>
    </div>
  )
}
