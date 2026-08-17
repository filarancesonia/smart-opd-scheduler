import { render } from '@testing-library/react'
import type { RenderResult } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import type { ReactElement } from 'react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { AuthProvider } from '../lib/auth'
import { LanguageProvider } from '../lib/i18n'
import type { Lang } from '../lib/i18n'
import type { User } from '../lib/api'
import { http } from './harness'
import * as fixtures from './fixtures'

type Options = {
  /** Entry path for MemoryRouter, e.g. "/board/1". */
  route?: string
  /** Route pattern when the component reads params, e.g. "/board/:doctorId". */
  path?: string
  /** Tests assert in English by default so failures read plainly. */
  lang?: Lang
  /** Seed a signed-in session and stub /auth/me. */
  as?: Partial<User> | null
  deviceKey?: string
}

export function renderApp(ui: ReactElement, options: Options = {}): RenderResult {
  const { route = '/', path, lang = 'en', as = null, deviceKey } = options

  localStorage.setItem('opd.lang', lang)
  if (deviceKey) localStorage.setItem('opd.device_key', deviceKey)

  if (as) {
    const account = fixtures.user(as)
    localStorage.setItem('opd.access_token', 'test-access-token')
    localStorage.setItem('opd.refresh_token', 'test-refresh-token')
    http.on('GET', '/auth/me', account)
  }

  const tree = (
    <MemoryRouter initialEntries={[route]}>
      <LanguageProvider>
        <AuthProvider>
          {path ? (
            <Routes>
              <Route path={path} element={ui} />
            </Routes>
          ) : (
            ui
          )}
        </AuthProvider>
      </LanguageProvider>
    </MemoryRouter>
  )

  return render(tree)
}

export const ui = userEvent.setup.bind(userEvent)
export { userEvent, fixtures }
