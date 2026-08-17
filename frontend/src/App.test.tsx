/* Route guards.
 *
 * These are the closest thing the frontend has to an access-control layer.
 * The server enforces roles properly — every one of these routes is also
 * gated in FastAPI — but a patient who can open the admin dashboard and see
 * it fail with 403s in the console is still a bug worth preventing.
 */

import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { App } from './App'
import { http } from './test/harness'
import { fixtures, renderApp } from './test/render'

function stubEverything() {
  http.on('GET', '/booking/appointments', [])
  http.on('GET', '/doctors', [fixtures.doctor({ user_id: 2 })])
  http.on('GET', '/presence/doctors/1', fixtures.presence())
  http.on('GET', '/queue/doctors/1', fixtures.queue())
  http.on('GET', '/scheduling/doctors/1/optimise', fixtures.optimisation())
  http.on('GET', '/analytics/live', fixtures.liveOverview())
  http.on('GET', '/analytics/health-department', fixtures.healthSummary())
  http.on('GET', '/analytics/attendance', { rows: [] })
  http.on('GET', '/analytics/wait-times', {
    start_date: '2026-07-19',
    end_date: '2026-08-18',
    overall: null,
    by_department: [],
    by_doctor: [],
  })
  http.on('GET', '/analytics/channels', [])
}

describe('signed out', () => {
  it('sends an anonymous visitor to the sign-in screen', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', lang: 'en' })

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('guards the admin dashboard', async () => {
    stubEverything()
    renderApp(<App />, { route: '/admin', lang: 'en' })

    expect(await screen.findByRole('button', { name: 'Sign in' })).toBeInTheDocument()
  })

  it('leaves the kiosk reachable, since it has no user session', async () => {
    stubEverything()
    renderApp(<App />, { route: '/kiosk', lang: 'en', deviceKey: 'dev-device-key' })

    expect(
      await screen.findByRole('button', { name: /Book an appointment/i }),
    ).toBeInTheDocument()
  })

  it('leaves the corridor board reachable', async () => {
    stubEverything()
    http.on('GET', '/queue/doctors/1/board', fixtures.board())
    renderApp(<App />, { route: '/board/1', lang: 'en', deviceKey: 'dev-device-key' })

    expect(await screen.findByText('Now serving')).toBeInTheDocument()
  })
})

describe('role routing', () => {
  it('shows a patient their appointments', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', as: { role: 'patient' }, lang: 'en' })

    expect(await screen.findByText('My appointments')).toBeInTheDocument()
  })

  it('sends a doctor to their own console', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', as: { id: 2, role: 'doctor' }, lang: 'en' })

    // A doctor landing on a patient booking screen would be a wasted tap
    // every single morning.
    expect(await screen.findByText('Queue')).toBeInTheDocument()
  })

  it('sends an administrator to the dashboard', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', as: { role: 'admin' }, lang: 'en' })

    expect(await screen.findByText('Right now')).toBeInTheDocument()
  })

  it('sends a Health Department account to the dashboard too', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', as: { role: 'health_dept' }, lang: 'en' })

    expect(await screen.findByText('Right now')).toBeInTheDocument()
  })
})

describe('role restrictions', () => {
  it('keeps a patient out of the admin dashboard', async () => {
    stubEverything()
    renderApp(<App />, { route: '/admin', as: { role: 'patient' }, lang: 'en' })

    expect(await screen.findByText('My appointments')).toBeInTheDocument()
    expect(screen.queryByText('Right now')).not.toBeInTheDocument()
  })

  it('keeps a patient out of the doctor console', async () => {
    stubEverything()
    renderApp(<App />, { route: '/doctor', as: { role: 'patient' }, lang: 'en' })

    expect(await screen.findByText('My appointments')).toBeInTheDocument()
  })

  it('lets a doctor open the admin dashboard only if they are also staff', async () => {
    stubEverything()
    renderApp(<App />, { route: '/admin', as: { id: 2, role: 'doctor' }, lang: 'en' })

    // Plain doctors are redirected to their own console.
    expect(await screen.findByText('Queue')).toBeInTheDocument()
  })
})

describe('navigation', () => {
  it('shows a patient only the links that apply to them', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', as: { role: 'patient' }, lang: 'en' })

    await screen.findByText('My appointments')
    expect(screen.getByRole('link', { name: 'My turn' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Administration' })).not.toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'Doctor console' })).not.toBeInTheDocument()
  })

  it('shows an administrator the administration link', async () => {
    stubEverything()
    renderApp(<App />, { route: '/', as: { role: 'admin' }, lang: 'en' })

    await screen.findByText('Right now')
    expect(screen.getByRole('link', { name: 'Administration' })).toBeInTheDocument()
    expect(screen.queryByRole('link', { name: 'My turn' })).not.toBeInTheDocument()
  })

  it('falls back to the home route for an unknown path', async () => {
    stubEverything()
    renderApp(<App />, { route: '/does-not-exist', as: { role: 'patient' }, lang: 'en' })

    expect(await screen.findByText('My appointments')).toBeInTheDocument()
  })
})
