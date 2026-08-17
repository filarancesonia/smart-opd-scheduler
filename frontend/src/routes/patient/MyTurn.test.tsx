import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { MyTurn } from './MyTurn'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'
import { today } from '../../lib/format'

function renderMyTurn(lang: 'hi' | 'en' = 'en') {
  return renderApp(<MyTurn />, { as: { role: 'patient' }, lang })
}

const checkedIn = () =>
  fixtures.appointment({ appointment_date: today(), status: 'checked_in' })

describe('when not in a queue', () => {
  it('explains how to get a token instead of showing an empty screen', async () => {
    http.on('GET', '/booking/appointments', [])
    renderMyTurn()

    expect(await screen.findByText('You are not in a queue')).toBeInTheDocument()
    expect(screen.getByText(/Show your booking number at reception/i)).toBeInTheDocument()
  })

  it('ignores a booking that has not been checked in yet', async () => {
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: today(), status: 'booked' }),
    ])
    renderMyTurn()

    // A queue position only exists once someone has physically arrived.
    expect(await screen.findByText('You are not in a queue')).toBeInTheDocument()
  })

  it("ignores yesterday's appointment", async () => {
    http.on('GET', '/booking/appointments', [
      fixtures.appointment({ appointment_date: '2020-01-01', status: 'checked_in' }),
    ])
    renderMyTurn()

    expect(await screen.findByText('You are not in a queue')).toBeInTheDocument()
  })
})

describe('when waiting', () => {
  it('shows the token, people ahead and the estimate', async () => {
    http.on('GET', '/booking/appointments', [checkedIn()])
    http.on('GET', '/queue/doctors/1/my-position', fixtures.myPosition())
    renderMyTurn()

    expect(await screen.findByText('7')).toBeInTheDocument()
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('22 min')).toBeInTheDocument()
  })

  it("repeats the backend's sentence rather than composing its own", async () => {
    // The wording is written once, on the server, in both languages — so a
    // voice call and this screen can never contradict each other.
    http.on('GET', '/booking/appointments', [checkedIn()])
    http.on('GET', '/queue/doctors/1/my-position', fixtures.myPosition())
    renderMyTurn('en')

    expect(
      await screen.findByText('Your turn is in about 22 minutes. 2 people are ahead of you.'),
    ).toBeInTheDocument()
  })

  it('uses the Hindi sentence when the language is Hindi', async () => {
    http.on('GET', '/booking/appointments', [checkedIn()])
    http.on('GET', '/queue/doctors/1/my-position', fixtures.myPosition())
    renderMyTurn('hi')

    expect(
      await screen.findByText('आपकी बारी लगभग 22 मिनट में है। आपसे 2 लोग आगे हैं।'),
    ).toBeInTheDocument()
  })

  it('prints no estimate at all while the doctor is absent', async () => {
    http.on('GET', '/booking/appointments', [checkedIn()])
    http.on(
      'GET',
      '/queue/doctors/1/my-position',
      fixtures.myPosition({
        doctor_present: false,
        estimated_wait_minutes: null,
        estimated_call_time: null,
        message_en: 'The doctor has not arrived yet. You will be told as soon as they do.',
      }),
    )
    renderMyTurn()

    expect(await screen.findByText(/has not arrived yet/i)).toBeInTheDocument()
    expect(screen.getByText('Not arrived')).toBeInTheDocument()
    // A guessed number would be worse than admitting we cannot say.
    expect(screen.queryByText(/\d+ min$/)).not.toBeInTheDocument()
  })
})

describe('when called', () => {
  it('tells the patient which room to go to', async () => {
    http.on('GET', '/booking/appointments', [checkedIn()])
    http.on(
      'GET',
      '/queue/doctors/1/my-position',
      fixtures.myPosition({
        status: 'called',
        people_ahead: 0,
        message_en: 'You are being called. Please go to room OPD 12.',
      }),
    )
    renderMyTurn()

    expect(await screen.findByText(/Please go to room OPD 12/i)).toBeInTheDocument()
    expect(screen.getByText('Called')).toBeInTheDocument()
  })
})
