import { screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { DoctorConsole } from './DoctorConsole'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'

function stubConsole(
  over: {
    presence?: Parameters<typeof fixtures.presence>[0]
    queue?: Parameters<typeof fixtures.queue>[0] | null
    optimisation?: Parameters<typeof fixtures.optimisation>[0] | null
  } = {},
) {
  http.on('GET', '/doctors', [fixtures.doctor({ user_id: 2 })])
  http.on('GET', '/presence/doctors/1', fixtures.presence(over.presence))

  if (over.queue === null) {
    http.onError('GET', '/queue/doctors/1', 'not_found', 'No queue has been opened', 404)
  } else {
    http.on('GET', '/queue/doctors/1', fixtures.queue(over.queue))
  }

  if (over.optimisation === null) {
    http.onError('GET', '/scheduling/doctors/1/optimise', 'conflict', 'No clinic', 409)
  } else {
    http.on('GET', '/scheduling/doctors/1/optimise', fixtures.optimisation(over.optimisation))
  }
}

const renderConsole = () =>
  renderApp(<DoctorConsole />, { as: { id: 2, role: 'doctor' }, lang: 'en' })

describe('presence', () => {
  it('offers to mark arrival when the doctor is not recorded as present', async () => {
    stubConsole({ presence: { status: 'unknown', deviation: 'absent_while_rostered', minutes_late: 76 } })
    renderConsole()

    // Awaited: the button also renders in the pre-load state, so asserting on
    // it alone would pass before presence had actually arrived.
    expect(await screen.findByText('76 minutes late')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Mark me arrived' })).toBeInTheDocument()
  })

  it('hides the arrival button once presence is recorded', async () => {
    stubConsole()
    renderConsole()

    await screen.findByText('Dr. Anil Sharma')
    expect(screen.queryByRole('button', { name: 'Mark me arrived' })).not.toBeInTheDocument()
  })

  it('records presence through Room 1 when the button is pressed', async () => {
    const user = userEvent.setup()
    stubConsole({ presence: { status: 'unknown', deviation: 'absent_while_rostered' } })
    http.on('POST', '/presence/manual', fixtures.presence())
    renderConsole()

    await user.click(await screen.findByRole('button', { name: 'Mark me arrived' }))

    await waitFor(() => expect(http.lastCallTo('POST', '/presence/manual')).toBeDefined())
    expect(http.lastCallTo('POST', '/presence/manual')?.body).toMatchObject({
      doctor_id: 1,
      status: 'present',
    })
  })

  it('warns that nobody can be called while the doctor is absent', async () => {
    stubConsole({ presence: { status: 'unknown' } })
    renderConsole()

    expect(
      await screen.findByText(/Patients cannot be called until your presence is recorded/i),
    ).toBeInTheDocument()
  })
})

describe('queue', () => {
  it('prompts to open a queue when none is running', async () => {
    stubConsole({ queue: null })
    renderConsole()

    expect(await screen.findByText('Queue not open yet')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open queue' })).toBeInTheDocument()
  })

  it('marks a priority patient so the ordering is explainable', async () => {
    // A senior citizen holding token 2 being called before token 1 needs a
    // visible reason, or it looks like the queue is broken.
    stubConsole({
      queue: {
        entries: [
          fixtures.queueEntry({
            id: 2,
            token_number: 2,
            position: 1,
            patient_name: 'Ramesh Yadav',
            priority_tier: 1,
          }),
          fixtures.queueEntry({ id: 1, token_number: 1, position: 2, patient_name: 'Asha Devi' }),
        ],
      },
    })
    renderConsole()

    const rows = await screen.findAllByRole('row')
    // Header row first, then the priority patient ahead of the earlier token.
    expect(rows[1]).toHaveTextContent('Ramesh Yadav')
    expect(rows[1]).toHaveTextContent('Priority')
    expect(rows[2]).toHaveTextContent('Asha Devi')
  })

  it('disables Call next when nobody is waiting', async () => {
    stubConsole({ queue: { entries: [], waiting_count: 0 } })
    renderConsole()

    expect(await screen.findByRole('button', { name: 'Call next' })).toBeDisabled()
  })

  it('offers Start once a patient has been called', async () => {
    stubConsole({
      queue: {
        now_serving: 1,
        entries: [fixtures.queueEntry({ status: 'called' })],
      },
    })
    renderConsole()

    expect(await screen.findByRole('button', { name: 'Start consultation' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: 'Call next' })).not.toBeInTheDocument()
  })

  it('offers Complete once a consultation is under way', async () => {
    stubConsole({
      queue: { entries: [fixtures.queueEntry({ status: 'in_progress' })] },
    })
    renderConsole()

    expect(await screen.findByRole('button', { name: 'Complete' })).toBeInTheDocument()
  })

  it('completes through the API, which is what feeds Room 4', async () => {
    const user = userEvent.setup()
    stubConsole({ queue: { entries: [fixtures.queueEntry({ id: 9, status: 'in_progress' })] } })
    http.on('POST', '/queue/entries/9/complete', fixtures.queueEntry({ status: 'completed' }))
    renderConsole()

    await user.click(await screen.findByRole('button', { name: 'Complete' }))

    await waitFor(() => expect(http.lastCallTo('POST', '/queue/entries/9/complete')).toBeDefined())
  })

  it('shows the running average once consultations have finished', async () => {
    stubConsole({ queue: { completed_count: 4, observed_avg_minutes: 7.5 } })
    renderConsole()

    expect(await screen.findByText('avg 7.5 min')).toBeInTheDocument()
  })
})

describe('the AI plan', () => {
  it('says when the session window came from live presence', async () => {
    stubConsole({ optimisation: { used_live_presence: true } })
    renderConsole()

    expect(await screen.findByText('from live presence')).toBeInTheDocument()
  })

  it('says when it only had the roster to go on', async () => {
    stubConsole({ optimisation: { used_live_presence: false } })
    renderConsole()

    expect(await screen.findByText('from roster')).toBeInTheDocument()
  })

  it('reports whether predictions came from a trained model', async () => {
    stubConsole({
      optimisation: {
        engine: {
          duration: { trained: false, source: 'heuristic' },
          no_show: { trained: false, source: 'heuristic' },
        },
      },
    })
    renderConsole()

    expect(await screen.findByText(/Duration estimates: heuristic/)).toBeInTheDocument()
  })

  it('presents a negative result as the cost of priority, not as a failure', async () => {
    // Seeing a senior citizen first genuinely costs average waiting time.
    // Labelling that "-2% improvement" would read as a broken optimiser.
    stubConsole({ optimisation: { improvement_pct: -2 } })
    renderConsole()

    expect(await screen.findByText('+2%')).toBeInTheDocument()
    expect(screen.getByText('cost of priority')).toBeInTheDocument()
  })

  it('shows a genuine gain as a reduction', async () => {
    stubConsole({ optimisation: { improvement_pct: 33.3 } })
    renderConsole()

    expect(await screen.findByText('−33.3%')).toBeInTheDocument()
    expect(screen.getByText('total waiting')).toBeInTheDocument()
  })
})
