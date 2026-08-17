import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Book } from './Book'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'

function renderBook() {
  http.on('GET', '/departments', [fixtures.department()])
  http.on('GET', '/doctors', [fixtures.doctor()])
  return renderApp(<Book />, { as: { role: 'patient' }, lang: 'en' })
}

async function pickDoctor(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole('button', { name: 'General Medicine' }))
  await user.click(await screen.findByRole('button', { name: /Dr. Anil Sharma/ }))
}

describe('choosing a slot', () => {
  it('walks department to doctor to time', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots())
    renderBook()

    await pickDoctor(user)

    expect(await screen.findByRole('button', { name: '09:00' })).toBeEnabled()
    expect(screen.getByText('3 times left')).toBeInTheDocument()
  })

  it('disables a slot someone else already took', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots())
    renderBook()

    await pickDoctor(user)

    expect(await screen.findByRole('button', { name: '09:10' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '09:20' })).toBeEnabled()
  })

  it('says so plainly when the doctor is on leave', async () => {
    const user = userEvent.setup()
    http.on(
      'GET',
      '/booking/doctors/1/slots',
      fixtures.daySlots({ is_on_leave: true, slots: [], remaining: 0 }),
    )
    renderBook()

    await pickDoctor(user)

    expect(await screen.findByText('On leave')).toBeInTheDocument()
  })

  it('explains an empty day rather than showing nothing', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots({ slots: [], remaining: 0 }))
    renderBook()

    await pickDoctor(user)

    expect(await screen.findByText('No times available on this day')).toBeInTheDocument()
  })
})

describe('live presence', () => {
  /* Room 1 feeding Room 3. Telling someone the doctor has not turned up
   * *before* they choose a time is the entire point of the project. */
  it('warns on the booking screen when the doctor has not arrived', async () => {
    const user = userEvent.setup()
    http.on(
      'GET',
      '/booking/doctors/1/slots',
      fixtures.daySlots({
        presence_warning: 'Doctor has not arrived yet (expected 76 minutes ago)',
      }),
    )
    renderBook()

    await pickDoctor(user)

    expect(
      await screen.findByText('Doctor has not arrived yet (expected 76 minutes ago)'),
    ).toBeInTheDocument()
  })

  it('shows no warning when the doctor is where they should be', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots())
    renderBook()

    await pickDoctor(user)
    await screen.findByRole('button', { name: '09:00' })

    // Matched narrowly: the page's own intro sentence also mentions arrival.
    expect(screen.queryByText(/^Doctor has not arrived yet/)).not.toBeInTheDocument()
  })
})

describe('confirming', () => {
  it('shows the reference and keeps it readable', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots())
    http.on('POST', '/booking/appointments', fixtures.appointment({ booking_reference: 'OPDK7M2QXR' }))
    renderBook()

    await pickDoctor(user)
    await user.click(await screen.findByRole('button', { name: '09:00' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByText('OPDK7M2QXR')).toBeInTheDocument()
    expect(screen.getByText(/Keep this number/i)).toBeInTheDocument()
  })

  it('sends the chosen slot, not just the date', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots())
    http.on('POST', '/booking/appointments', fixtures.appointment())
    renderBook()

    await pickDoctor(user)
    await user.click(await screen.findByRole('button', { name: '09:20' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    await screen.findByText(/Keep this number/i)
    expect(http.lastCallTo('POST', '/booking/appointments')?.body).toMatchObject({
      doctor_id: 1,
      preferred_start: '09:20:00',
    })
  })

  it('reports a slot taken between loading and confirming', async () => {
    const user = userEvent.setup()
    http.on('GET', '/booking/doctors/1/slots', fixtures.daySlots())
    http.onError(
      'POST',
      '/booking/appointments',
      'conflict',
      'That slot has already been taken',
      409,
    )
    renderBook()

    await pickDoctor(user)
    await user.click(await screen.findByRole('button', { name: '09:00' }))
    await user.click(screen.getByRole('button', { name: 'Confirm' }))

    expect(await screen.findByText('That slot has already been taken')).toBeInTheDocument()
  })
})
