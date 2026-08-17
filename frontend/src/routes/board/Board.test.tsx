import { screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'
import { Board } from './Board'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'

const KEY = 'dev-device-key'

function renderBoard(options: { deviceKey?: string | undefined } = {}) {
  return renderApp(<Board />, {
    route: '/board/1',
    path: '/board/:doctorId',
    deviceKey: options.deviceKey,
    lang: 'en',
  })
}

describe('provisioning', () => {
  it('asks for a device key before showing anything', async () => {
    renderBoard()
    // Awaited rather than asserted synchronously so the hook's initial
    // (no-op) resolve settles inside the test rather than after it.
    expect(await screen.findByText('Display setup')).toBeInTheDocument()
  })

  it('makes no network call at all until a key is present', async () => {
    renderBoard()
    await screen.findByText('Display setup')
    expect(http.callsTo('GET', '/queue/doctors/1/board')).toHaveLength(0)
  })

  it('loads the board once a key is saved', async () => {
    const user = userEvent.setup()
    http.on('GET', '/queue/doctors/1/board', fixtures.board())
    renderBoard()

    await user.type(screen.getByPlaceholderText('dev-device-key'), KEY)
    await user.click(screen.getByRole('button', { name: 'Save' }))

    expect(await screen.findByText('Dr. Anil Sharma')).toBeInTheDocument()
  })

  it('offers to re-enter the key when the board cannot load', async () => {
    http.onError('GET', '/queue/doctors/1/board', 'unauthenticated', 'Invalid device key', 401)
    renderBoard({ deviceKey: 'wrong-key' })

    expect(await screen.findByText('Could not load the board')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Change device key' })).toBeInTheDocument()
  })
})

describe('display', () => {
  it('shows the token being served', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board({ now_serving: 3 }))
    renderBoard({ deviceKey: KEY })

    expect(await screen.findByText('3')).toBeInTheDocument()
    expect(screen.getByText('Now serving')).toBeInTheDocument()
  })

  it('says "please wait" rather than showing a stale token when nobody is called', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board({ now_serving: null }))
    renderBoard({ deviceKey: KEY })

    expect(await screen.findByText('Please wait')).toBeInTheDocument()
  })

  it('renders only the masked names the backend sent', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board())
    renderBoard({ deviceKey: KEY })

    // A public screen in a corridor must not broadcast who is at which clinic.
    expect(await screen.findByText('Ramesh Y.')).toBeInTheDocument()
    expect(screen.getByText('Asha D.')).toBeInTheDocument()
    expect(screen.queryByText(/Yadav/)).not.toBeInTheDocument()
    expect(screen.queryByText(/Devi/)).not.toBeInTheDocument()
  })

  it('marks a priority token so staff can see why it jumped', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board())
    const { container } = renderBoard({ deviceKey: KEY })

    await screen.findByText('Ramesh Y.')
    const rows = container.querySelectorAll('.board__row')
    expect(rows[0].className).toContain('board__row--priority')
    expect(rows[1].className).not.toContain('board__row--priority')
  })

  it('shows both languages at once, because nobody is there to toggle', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board())
    renderBoard({ deviceKey: KEY })

    expect(await screen.findByText('डॉक्टर उपलब्ध हैं।')).toBeInTheDocument()
    expect(screen.getByText('Doctor is available.')).toBeInTheDocument()
  })

  it('announces an absent doctor in both languages', async () => {
    http.on(
      'GET',
      '/queue/doctors/1/board',
      fixtures.board({
        doctor_present: false,
        status_line_hi: 'डॉक्टर अभी नहीं पहुँचे हैं। कृपया प्रतीक्षा करें।',
        status_line_en: 'Doctor has not arrived yet. Please wait.',
      }),
    )
    renderBoard({ deviceKey: KEY })

    expect(await screen.findByText('Doctor not arrived')).toBeInTheDocument()
    expect(screen.getByText('डॉक्टर अभी नहीं पहुँचे हैं। कृपया प्रतीक्षा करें।')).toBeInTheDocument()
  })

  it('tells people the hall is empty rather than showing a blank panel', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board({ next_tokens: [] }))
    renderBoard({ deviceKey: KEY })

    expect(await screen.findByText('Nobody waiting')).toBeInTheDocument()
  })

  it('sends the device key with every poll', async () => {
    http.on('GET', '/queue/doctors/1/board', fixtures.board())
    renderBoard({ deviceKey: KEY })

    await screen.findByText('Dr. Anil Sharma')
    expect(http.lastCallTo('GET', '/queue/doctors/1/board')?.headers['x-device-key']).toBe(KEY)
  })
})
