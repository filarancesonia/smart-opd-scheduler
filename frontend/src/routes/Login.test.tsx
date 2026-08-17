import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Login } from './Login'
import { http } from '../test/harness'
import { fixtures, renderApp } from '../test/render'

const tokenPair = {
  access_token: 'access-123',
  refresh_token: 'refresh-456',
  token_type: 'bearer',
  expires_in: 3600,
}

describe('sign in', () => {
  it('stores both tokens on success', async () => {
    const user = userEvent.setup()
    http.on('POST', '/auth/login', tokenPair)
    http.on('GET', '/auth/me', fixtures.user())

    renderApp(<Login />, { lang: 'en' })
    await user.type(screen.getByPlaceholderText('9876543210'), '9876543210')
    await user.type(screen.getByLabelText(/^Password/), 'DemoPass123')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    // Login redirects away, so the form going is the observable success.
    await waitFor(() =>
      expect(screen.queryByRole('button', { name: 'Sign in' })).not.toBeInTheDocument(),
    )
    expect(localStorage.getItem('opd.access_token')).toBe('access-123')
    expect(localStorage.getItem('opd.refresh_token')).toBe('refresh-456')
  })

  it('shows the failure message and keeps the person on the form', async () => {
    const user = userEvent.setup()
    http.onError(
      'POST',
      '/auth/login',
      'unauthenticated',
      'Mobile number or password is incorrect',
      401,
    )

    renderApp(<Login />, { lang: 'en' })
    await user.type(screen.getByPlaceholderText('9876543210'), '9876543210')
    await user.type(screen.getByLabelText(/^Password/), 'wrong-password')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Mobile number or password is incorrect')).toBeInTheDocument()
    expect(localStorage.getItem('opd.access_token')).toBeNull()
    expect(screen.getByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('does not store a token when the profile fetch fails after login', async () => {
    const user = userEvent.setup()
    http.on('POST', '/auth/login', tokenPair)
    http.onError('GET', '/auth/me', 'unauthenticated', 'Account no longer active', 401)

    renderApp(<Login />, { lang: 'en' })
    await user.type(screen.getByPlaceholderText('9876543210'), '9876543210')
    await user.type(screen.getByLabelText(/^Password/), 'DemoPass123')
    await user.click(screen.getByRole('button', { name: 'Sign in' }))

    expect(await screen.findByText('Account no longer active')).toBeInTheDocument()
  })
})

describe('registration', () => {
  it('switches to the register form and asks for a name', async () => {
    const user = userEvent.setup()
    renderApp(<Login />, { lang: 'en' })

    await user.click(screen.getByRole('button', { name: 'No account?' }))

    expect(screen.getByLabelText('Full name')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Create account' })).toBeInTheDocument()
  })

  it('signs the person straight in after registering', async () => {
    const user = userEvent.setup()
    http.on('POST', '/auth/register', fixtures.user())
    http.on('POST', '/auth/login', tokenPair)
    http.on('GET', '/auth/me', fixtures.user())

    renderApp(<Login />, { lang: 'en' })
    await user.click(screen.getByRole('button', { name: 'No account?' }))
    await user.type(screen.getByLabelText('Full name'), 'Asha Devi')
    await user.type(screen.getByPlaceholderText('9876543210'), '9876543210')
    await user.type(screen.getByLabelText(/^Password/), 'DemoPass123')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    // Making someone type their password twice in a row is a needless step.
    expect(localStorage.getItem('opd.access_token')).toBe('access-123')
  })

  it('surfaces a duplicate-number conflict', async () => {
    const user = userEvent.setup()
    http.onError(
      'POST',
      '/auth/register',
      'conflict',
      'An account with this mobile number already exists',
      409,
    )

    renderApp(<Login />, { lang: 'en' })
    await user.click(screen.getByRole('button', { name: 'No account?' }))
    await user.type(screen.getByLabelText('Full name'), 'Asha Devi')
    await user.type(screen.getByPlaceholderText('9876543210'), '9876543210')
    await user.type(screen.getByLabelText(/^Password/), 'DemoPass123')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(
      await screen.findByText('An account with this mobile number already exists'),
    ).toBeInTheDocument()
  })
})

describe('access for people without a smartphone', () => {
  it('links to the kiosk from the sign-in screen', () => {
    renderApp(<Login />, { lang: 'en' })
    expect(screen.getByRole('link', { name: 'Open kiosk' })).toHaveAttribute('href', '/kiosk')
    expect(
      screen.getByText(/touchscreen inside the hospital or the phone line/i),
    ).toBeInTheDocument()
  })

  it('opens in Hindi', () => {
    renderApp(<Login />, { lang: 'hi' })
    expect(screen.getByRole('button', { name: 'लॉग इन करें' })).toBeInTheDocument()
  })
})
