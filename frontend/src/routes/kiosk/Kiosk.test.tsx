import { act, fireEvent, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Kiosk } from './Kiosk'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'

const KEY = 'dev-device-key'

function renderKiosk(options: { deviceKey?: string } = {}) {
  return renderApp(<Kiosk />, { deviceKey: options.deviceKey ?? KEY, lang: 'en' })
}

async function startBooking(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('button', { name: /Book an appointment/i }))
}

function display() {
  return document.querySelector('.kiosk__display')
}

async function tapDigits(digits: string) {
  // Every tap dispatched inside a single act(), so React commits once at the
  // end rather than between taps. This is what a fast double-tap on a
  // sluggish panel looks like, and it is what the old stale-closure keypad
  // could not survive.
  await act(async () => {
    for (const digit of digits) {
      fireEvent.click(screen.getByRole('button', { name: digit }))
    }
  })
}

describe('device provisioning', () => {
  it('asks for a device key before anything else', () => {
    renderApp(<Kiosk />, { lang: 'en' })
    expect(screen.getByText('Device key')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Book an appointment/i })).not.toBeInTheDocument()
  })

  it('warns that the key is stored in the browser', () => {
    renderApp(<Kiosk />, { lang: 'en' })
    expect(screen.getByText(/only run the kiosk on hospital-owned hardware/i)).toBeInTheDocument()
  })

  it('starts once a key is saved', async () => {
    const user = userEvent.setup()
    renderApp(<Kiosk />, { lang: 'en' })

    await user.type(screen.getByPlaceholderText('dev-device-key'), KEY)
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(screen.getByRole('button', { name: /Book an appointment/i })).toBeInTheDocument()
    expect(localStorage.getItem('opd.device_key')).toBe(KEY)
  })
})

describe('keypad', () => {
  /* Regression. The keypad used to build each next value from the `value`
   * prop, so taps arriving faster than a re-render all read the same stale
   * string and every digit but one was lost. Ten digits used to yield "11". */
  it('keeps every digit when ten taps land in a single tick', async () => {
    const user = userEvent.setup()
    renderKiosk()
    await startBooking(user)

    await tapDigits('9812345671')

    expect(display()).toHaveTextContent('9812345671')
  })

  it('will not exceed ten digits however hard the panel is tapped', async () => {
    const user = userEvent.setup()
    renderKiosk()
    await startBooking(user)

    await tapDigits('98123456719999')

    expect(display()).toHaveTextContent('9812345671')
  })

  it('backspaces and clears', async () => {
    const user = userEvent.setup()
    renderKiosk()
    await startBooking(user)

    await tapDigits('98123')
    await user.click(screen.getByRole('button', { name: '←' }))
    expect(display()).toHaveTextContent('9812')

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    expect(display()).toHaveTextContent('__________')
  })

  it('keeps Next disabled until the number is complete', async () => {
    const user = userEvent.setup()
    renderKiosk()
    await startBooking(user)

    const next = screen.getByRole('button', { name: 'Next' })
    expect(next).toBeDisabled()

    await tapDigits('981234567')
    expect(next).toBeDisabled()

    await tapDigits('1')
    expect(next).toBeEnabled()
  })
})

describe('booking flow', () => {
  it('sends the device key on lookup and offers the matching people', async () => {
    const user = userEvent.setup()
    http.on('POST', '/booking/kiosk/lookup', [
      fixtures.patient({ id: 5, full_name: 'Ramesh Yadav', age: 67, is_senior_citizen: true }),
      fixtures.patient({ id: 6, full_name: 'Sunita Yadav', age: 61, is_senior_citizen: true }),
    ])
    http.on('GET', '/departments', [fixtures.department()])

    renderKiosk()
    await startBooking(user)
    await tapDigits('9812345671')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    // One phone number often covers a whole family.
    expect(await screen.findByText('Ramesh Yadav')).toBeInTheDocument()
    expect(screen.getByText('Sunita Yadav')).toBeInTheDocument()
    expect(screen.getAllByText(/Senior citizen/).length).toBe(2)

    expect(http.lastCallTo('POST', '/booking/kiosk/lookup')?.headers['x-device-key']).toBe(KEY)
  })

  it('goes straight to name entry when the number is unknown', async () => {
    const user = userEvent.setup()
    http.on('POST', '/booking/kiosk/lookup', [])

    renderKiosk()
    await startBooking(user)
    await tapDigits('9812345671')
    await user.click(screen.getByRole('button', { name: 'Next' }))

    expect(await screen.findByText('Your name')).toBeInTheDocument()
  })

  it('prints a slip carrying the booking reference', async () => {
    const user = userEvent.setup()
    http.on('POST', '/booking/kiosk/lookup', [
      fixtures.patient({ full_name: 'Ramesh Yadav', age: 67 }),
    ])
    http.on('GET', '/departments', [fixtures.department()])
    http.on('GET', '/doctors', [fixtures.doctor()])
    http.on('POST', '/booking/kiosk/book', {
      booking_reference: 'OPD5A7FWDT',
      patient_name: 'Ramesh Yadav',
      doctor_name: 'Dr. Anil Sharma',
      department_name: 'General Medicine',
      room: 'OPD 12',
      appointment_date: '2026-08-18',
      slot_start: '09:30:00',
      message_hi: 'आपका बुकिंग नंबर OPD5A7FWDT है।',
      message_en: 'Your booking number is OPD5A7FWDT.',
    })

    renderKiosk()
    await startBooking(user)
    await tapDigits('9812345671')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(await screen.findByText('Ramesh Yadav'))
    await user.click(await screen.findByText('General Medicine'))
    await user.click(await screen.findByText('Dr. Anil Sharma'))
    await user.click(await screen.findByText('Today'))
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))

    expect(await screen.findByText('OPD5A7FWDT')).toBeInTheDocument()
    expect(screen.getByText('Your booking number is OPD5A7FWDT.')).toBeInTheDocument()
    expect(screen.getByText('OPD 12')).toBeInTheDocument()
  })

  it('shows the backend message when the booking is refused', async () => {
    const user = userEvent.setup()
    http.on('POST', '/booking/kiosk/lookup', [fixtures.patient({ full_name: 'Ramesh Yadav' })])
    http.on('GET', '/departments', [fixtures.department()])
    http.on('GET', '/doctors', [fixtures.doctor()])
    http.onError(
      'POST',
      '/booking/kiosk/book',
      'conflict',
      'This patient already has an appointment with this doctor that day',
      409,
    )

    renderKiosk()
    await startBooking(user)
    await tapDigits('9812345671')
    await user.click(screen.getByRole('button', { name: 'Next' }))
    await user.click(await screen.findByText('Ramesh Yadav'))
    await user.click(await screen.findByText('General Medicine'))
    await user.click(await screen.findByText('Dr. Anil Sharma'))
    await user.click(await screen.findByText('Today'))
    await user.click(await screen.findByRole('button', { name: 'Confirm' }))

    // The person at the panel needs the reason, not a dead button.
    expect(
      await screen.findByText(/already has an appointment with this doctor/i),
    ).toBeInTheDocument()
  })

  it('cancel clears the number so the next person starts fresh', async () => {
    const user = userEvent.setup()
    renderKiosk()
    await startBooking(user)
    await tapDigits('9812345671')

    await user.click(screen.getByRole('button', { name: 'Cancel' }))
    await startBooking(user)

    // A half-finished booking left on a shared screen is a privacy leak.
    expect(display()).toHaveTextContent('__________')
  })
})

describe('language', () => {
  it('opens in Hindi', async () => {
    renderApp(<Kiosk />, { deviceKey: KEY, lang: 'hi' })
    expect(screen.getByText('क्या आप डॉक्टर से मिलना चाहते हैं?')).toBeInTheDocument()
  })

  it('switches to English on one tap', async () => {
    const user = userEvent.setup()
    renderApp(<Kiosk />, { deviceKey: KEY, lang: 'hi' })

    await user.click(screen.getByRole('button', { name: 'English' }))

    await waitFor(() =>
      expect(screen.getByText('Would you like to see a doctor?')).toBeInTheDocument(),
    )
  })
})
