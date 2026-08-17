import { describe, expect, it } from 'vitest'
import { addDays, clock, humanise, isoDate, percent, minutes, toneFor } from './format'

describe('clock', () => {
  it('trims seconds off a backend time', () => {
    expect(clock('09:30:00')).toBe('09:30')
  })

  it('renders a dash rather than "null" for a missing time', () => {
    expect(clock(null)).toBe('—')
    expect(clock(undefined)).toBe('—')
  })
})

describe('isoDate and addDays', () => {
  it('formats a local date without drifting a day through UTC', () => {
    // A naive toISOString() on a positive-offset timezone late in the evening
    // returns tomorrow, which would book people on the wrong day.
    const lateEvening = new Date(2026, 7, 18, 23, 30)
    expect(isoDate(lateEvening)).toBe('2026-08-18')
  })

  it('handles an early-morning local time too', () => {
    expect(isoDate(new Date(2026, 7, 18, 0, 15))).toBe('2026-08-18')
  })

  it('adds days across a month boundary', () => {
    expect(addDays('2026-08-30', 3)).toBe('2026-09-02')
  })

  it('adds days across a leap day', () => {
    expect(addDays('2028-02-28', 1)).toBe('2028-02-29')
  })
})

describe('percent', () => {
  it('renders a rate as a whole percentage', () => {
    expect(percent(0.873)).toBe('87%')
    expect(percent(1)).toBe('100%')
    expect(percent(0)).toBe('0%')
  })

  it('distinguishes missing from zero', () => {
    expect(percent(null)).toBe('—')
    expect(percent(0)).toBe('0%')
  })
})

describe('minutes', () => {
  it('rounds and labels', () => {
    expect(minutes(18.4)).toBe('18 min')
    expect(minutes(0)).toBe('0 min')
    expect(minutes(null)).toBe('—')
  })
})

describe('humanise', () => {
  it('turns a backend status into words', () => {
    expect(humanise('absent_while_rostered')).toBe('Absent While Rostered')
    expect(humanise('no_show')).toBe('No Show')
    expect(humanise(null)).toBe('—')
  })
})

describe('toneFor', () => {
  it('maps good outcomes to ok', () => {
    for (const status of ['present', 'completed', 'on_duty_as_rostered', 'sent']) {
      expect(toneFor(status)).toBe('ok')
    }
  })

  it('maps failures to danger', () => {
    for (const status of ['absent', 'no_show', 'cancelled', 'absent_while_rostered']) {
      expect(toneFor(status)).toBe('danger')
    }
  })

  it('treats a stale presence as a warning, not a failure', () => {
    // "We have not heard from the reader" is genuinely different from
    // "the doctor is not here", and the colour should not claim otherwise.
    expect(toneFor('stale')).toBe('warn')
    expect(toneFor('wrong_room')).toBe('warn')
  })

  it('falls back to neutral for anything unrecognised', () => {
    expect(toneFor('some_future_status')).toBe('')
    expect(toneFor(null)).toBe('')
  })
})
