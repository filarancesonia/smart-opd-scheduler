/** Display formatting. The backend speaks ISO; people do not. */

/** "09:30:00" -> "09:30" */
export function clock(value: string | null | undefined): string {
  if (!value) return '—'
  return value.slice(0, 5)
}

/** "2026-08-18" -> "18 Aug 2026" */
export function longDate(value: string | null | undefined, lang: 'hi' | 'en' = 'en'): string {
  if (!value) return '—'
  const date = new Date(`${value}T00:00:00`)
  if (Number.isNaN(date.getTime())) return value
  return date.toLocaleDateString(lang === 'hi' ? 'hi-IN' : 'en-IN', {
    day: 'numeric',
    month: 'short',
    year: 'numeric',
  })
}

/** An ISO instant as a wall clock time. */
export function timeOf(value: string | null | undefined): string {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false })
}

export function isoDate(date: Date): string {
  const offset = date.getTimezoneOffset()
  return new Date(date.getTime() - offset * 60_000).toISOString().slice(0, 10)
}

export function today(): string {
  return isoDate(new Date())
}

export function addDays(iso: string, days: number): string {
  const date = new Date(`${iso}T00:00:00`)
  date.setDate(date.getDate() + days)
  return isoDate(date)
}

/** 0.873 -> "87%" */
export function percent(rate: number | null | undefined, digits = 0): string {
  if (rate === null || rate === undefined) return '—'
  return `${(rate * 100).toFixed(digits)}%`
}

export function minutes(value: number | null | undefined, suffix = 'min'): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value)} ${suffix}`
}

/** Title-case a snake_case status for display. */
export function humanise(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/** Map a domain status onto one of the four pill tones. */
export function toneFor(status: string | null | undefined): 'ok' | 'warn' | 'danger' | 'info' | '' {
  switch (status) {
    case 'present':
    case 'completed':
    case 'on_duty_as_rostered':
    case 'sent':
    case 'delivered':
    case 'verified':
      return 'ok'
    case 'called':
    case 'in_progress':
    case 'checked_in':
    case 'booked':
      return 'info'
    case 'stale':
    case 'skipped':
    case 'wrong_room':
    case 'present_off_roster':
    case 'queued':
      return 'warn'
    case 'absent':
    case 'no_show':
    case 'cancelled':
    case 'failed':
    case 'absent_while_rostered':
      return 'danger'
    default:
      return ''
  }
}
