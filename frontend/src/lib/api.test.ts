import { describe, expect, it } from 'vitest'
import { ApiError, api, deviceKey, tokens } from './api'
import { http } from '../test/harness'
import * as fixtures from '../test/fixtures'

describe('error handling', () => {
  it('unpacks the backend error envelope', async () => {
    http.onError(
      'POST',
      '/booking/appointments',
      'conflict',
      'This clinic is fully booked',
      409,
      { capacity: 24, booked: 24 },
    )

    // The three fields matter separately: the message is shown, the code is
    // branched on, and details carry the numbers a screen wants to display.
    await expect(
      api.book({ doctor_id: 1, appointment_date: '2026-08-18' }),
    ).rejects.toMatchObject({
      name: 'ApiError',
      status: 409,
      code: 'conflict',
      message: 'This clinic is fully booked',
      details: { capacity: 24, booked: 24 },
    })
  })

  it('distinguishes two 409s by code, not by message text', async () => {
    http.onError('POST', '/booking/appointments', 'conflict', 'The doctor is on leave', 409)
    const error = await api
      .book({ doctor_id: 1, appointment_date: '2026-08-18' })
      .catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).status).toBe(409)
    expect((error as ApiError).code).toBe('conflict')
  })

  it('flattens FastAPI validation errors, which use a different shape', async () => {
    http.onRequest('POST', '/auth/register', () => ({
      status: 422,
      body: {
        detail: [
          { loc: ['body', 'phone'], msg: 'Enter a valid 10-digit Indian mobile number' },
          { loc: ['body', 'password'], msg: 'String should have at least 8 characters' },
        ],
      },
    }))

    await expect(
      api.register({ phone: '123', full_name: 'X', password: 'short' }),
    ).rejects.toThrow(/valid 10-digit.*at least 8 characters/s)
  })

  it('does not invent a message when the body is empty', async () => {
    http.onRequest('GET', '/auth/me', () => ({ status: 500, body: undefined }))
    await expect(api.me()).rejects.toThrow('Request failed (500)')
  })
})

describe('credentials on the wire', () => {
  it('sends the bearer token on user calls', async () => {
    tokens.set('access-123', 'refresh-456')
    http.on('GET', '/booking/appointments', [])

    await api.myAppointments()

    expect(http.lastCallTo('GET', '/booking/appointments')?.headers.authorization).toBe(
      'Bearer access-123',
    )
  })

  it('omits the header entirely when signed out', async () => {
    http.on('GET', '/departments', [])
    await api.departments()
    expect(http.lastCallTo('GET', '/departments')?.headers.authorization).toBeUndefined()
  })

  it('sends the device key — and no bearer token — on kiosk calls', async () => {
    // A kiosk is shared hardware. Leaking the last user's session onto it is
    // exactly the failure worth a test.
    tokens.set('access-123', 'refresh-456')
    deviceKey.set('dev-device-key')
    http.on('POST', '/booking/kiosk/lookup', [])

    await api.kioskLookup('9876543210')

    const call = http.lastCallTo('POST', '/booking/kiosk/lookup')
    expect(call?.headers['x-device-key']).toBe('dev-device-key')
    expect(call?.headers.authorization).toBeUndefined()
  })

  it('sends the device key on the corridor board', async () => {
    deviceKey.set('dev-device-key')
    http.on('GET', '/queue/doctors/1/board', fixtures.board())

    await api.board(1)

    expect(http.lastCallTo('GET', '/queue/doctors/1/board')?.headers['x-device-key']).toBe(
      'dev-device-key',
    )
  })

  it('does not log a user out when only the device key is cleared', async () => {
    tokens.set('access-123', 'refresh-456')
    deviceKey.set('key')
    deviceKey.clear()
    expect(tokens.access).toBe('access-123')
  })
})

describe('request shape', () => {
  it('normalises the phone before it reaches the server', async () => {
    http.on('POST', '/auth/login', {
      access_token: 'a',
      refresh_token: 'r',
      token_type: 'bearer',
      expires_in: 3600,
    })

    await api.login('9876543210', 'DemoPass123')

    expect(http.lastCallTo('POST', '/auth/login')?.body).toEqual({
      phone: '9876543210',
      password: 'DemoPass123',
    })
  })

  it('sets JSON content-type only when there is a body', async () => {
    http.on('GET', '/departments', [])
    http.on('POST', '/queue/doctors/1/call-next', { called: null, reason: null, remaining_waiting: 0 })

    await api.departments()
    await api.callNext(1)

    expect(http.lastCallTo('GET', '/departments')?.headers['content-type']).toBeUndefined()
    // call-next posts no body, so it should not claim to send JSON either.
    expect(http.lastCallTo('POST', '/queue/doctors/1/call-next')?.headers['content-type']).toBeUndefined()
  })

  it('passes the booking channel through as a query parameter', async () => {
    http.on('POST', '/booking/appointments', fixtures.appointment())
    await api.book({ doctor_id: 1, appointment_date: '2026-08-18' }, 'mobile_app')
    expect(http.lastCallTo('POST', '/booking/appointments')?.url).toContain('channel=mobile_app')
  })
})

describe('token storage', () => {
  it('round-trips and clears', () => {
    expect(tokens.access).toBeNull()
    tokens.set('a', 'r')
    expect(tokens.access).toBe('a')
    expect(tokens.refresh).toBe('r')
    tokens.clear()
    expect(tokens.access).toBeNull()
    expect(tokens.refresh).toBeNull()
  })

  it('trims a device key pasted with whitespace', () => {
    deviceKey.set('  dev-device-key \n')
    expect(deviceKey.get()).toBe('dev-device-key')
  })
})
