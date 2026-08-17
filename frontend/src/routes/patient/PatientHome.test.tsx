import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { PatientHome } from './PatientHome'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'
import { addDays, today } from '../../lib/format'

const renderHome = () => renderApp(<PatientHome />, { as: { role: 'patient' }, lang: 'en' })

describe('empty state', () => {
  it('invites a first booking instead of showing a blank page', async () => {
    http.on('GET', '/booking/appointments', [])
    renderHome()

    expect(await screen.findByText('No appointments yet')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: /Book/ })).toHaveAttribute('href', '/book')
  })
})

describe('upcoming appointments', () => {
  it('shows the details a patient actually needs', async () => {
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: addDays(today(), 2) }),
    ])
    renderHome()

    expect(await screen.findByText('Dr. Anil Sharma')).toBeInTheDocument()
    expect(screen.getByText('OPDABC2345')).toBeInTheDocument()
    expect(screen.getByText('OPD 12')).toBeInTheDocument()
    expect(screen.getByText('09:30')).toBeInTheDocument()
  })

  it('does not ask about presence for a future date', async () => {
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: addDays(today(), 2) }),
    ])
    renderHome()

    await screen.findByText('Dr. Anil Sharma')
    // Where a doctor will be next Tuesday is not a knowable thing.
    expect(http.callsTo('GET', '/presence/doctors/1')).toHaveLength(0)
  })

  it("shows live presence for today's appointment", async () => {
    http.on('GET', '/booking/appointments', [fixtures.appointment({ appointment_date: today() })])
    http.on('GET', '/presence/doctors/1', fixtures.presence())
    renderHome()

    expect(await screen.findByText(/Available/)).toBeInTheDocument()
  })

  it('warns when the doctor has not turned up today', async () => {
    http.on('GET', '/booking/appointments', [fixtures.appointment({ appointment_date: today() })])
    http.on(
      'GET',
      '/presence/doctors/1',
      fixtures.presence({
        status: 'unknown',
        deviation: 'absent_while_rostered',
        minutes_late: 76,
        present_minutes: null,
      }),
    )
    renderHome()

    expect(await screen.findByText(/Not arrived/)).toBeInTheDocument()
    expect(screen.getByText(/76 minutes late/)).toBeInTheDocument()
  })

  it('says when the doctor is on leave', async () => {
    http.on('GET', '/booking/appointments', [fixtures.appointment({ appointment_date: today() })])
    http.on(
      'GET',
      '/presence/doctors/1',
      fixtures.presence({ status: 'unknown', deviation: 'on_approved_leave' }),
    )
    renderHome()

    expect(await screen.findByText('On leave today')).toBeInTheDocument()
  })

  it('offers the queue link only once checked in', async () => {
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: today(), status: 'checked_in' }),
    ])
    http.on('GET', '/presence/doctors/1', fixtures.presence())
    renderHome()

    expect(await screen.findByRole('link', { name: 'My turn' })).toHaveAttribute(
      'href',
      '/my-turn',
    )
  })
})

describe('cancelling', () => {
  it('cancels through the API and refreshes the list', async () => {
    const user = userEvent.setup()
    // The later `on` is the fallback; the `once` is registered after it so it
    // sits in front and answers the first load only.
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: addDays(today(), 2), status: 'cancelled' }),
    ])
    http.once('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: addDays(today(), 2) }),
    ])
    http.on('POST', '/booking/appointments/1/cancel', fixtures.appointment({ status: 'cancelled' }))
    renderHome()

    await user.click(await screen.findByRole('button', { name: 'Cancel' }))

    await waitFor(() =>
      expect(http.lastCallTo('POST', '/booking/appointments/1/cancel')).toBeDefined(),
    )
    // A cancelled booking drops out of the active list and into history.
    expect(await screen.findByText('Past appointments')).toBeInTheDocument()
  })
})

describe('history', () => {
  it('lists finished appointments separately from upcoming ones', async () => {
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ id: 1, appointment_date: today(), status: 'booked' }),
      fixtures.appointment({
        id: 2,
        appointment_date: '2026-01-05',
        status: 'completed',
        booking_reference: 'OPDZZZ9999',
      }),
    ])
    http.on('GET', '/presence/doctors/1', fixtures.presence())
    renderHome()

    expect(await screen.findByText('Past appointments')).toBeInTheDocument()
    expect(screen.getByText('OPDZZZ9999')).toBeInTheDocument()
    expect(screen.getByText('Completed')).toBeInTheDocument()
  })
})
