import { screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { AdminDashboard } from './AdminDashboard'
import { http } from '../../test/harness'
import { fixtures, renderApp } from '../../test/render'

function stubDashboard(
  over: {
    live?: Parameters<typeof fixtures.liveOverview>[0]
    summary?: Parameters<typeof fixtures.healthSummary>[0]
    waits?: unknown
    attendance?: unknown
    channels?: unknown
  } = {},
) {
  http.on('GET', '/analytics/live', fixtures.liveOverview(over.live))
  http.on('GET', '/analytics/health-department', fixtures.healthSummary(over.summary))
  http.on(
    'GET',
    '/analytics/attendance',
    over.attendance ?? {
      rows: [
        {
          doctor_id: 1,
          doctor_name: 'Dr. Anil Sharma',
          department_name: 'General Medicine',
          days_rostered: 30,
          days_present: 28,
          days_absent: 2,
          days_on_leave: 1,
          attendance_rate: 0.94,
          average_minutes_late: 27,
          days_late: 12,
          punctuality_rate: 0.55,
        },
      ],
    },
  )
  http.on(
    'GET',
    '/analytics/wait-times',
    over.waits ?? {
      start_date: '2026-07-19',
      end_date: '2026-08-18',
      overall: {
        label: 'All departments',
        sample_size: 40,
        mean_minutes: 18.4,
        median_minutes: 15,
        p90_minutes: 52,
        max_minutes: 70,
      },
      by_department: [
        {
          label: 'General Medicine',
          sample_size: 40,
          mean_minutes: 18.4,
          median_minutes: 15,
          p90_minutes: 52,
          max_minutes: 70,
        },
      ],
      by_doctor: [],
    },
  )
  http.on('GET', '/analytics/channels', over.channels ?? fixtures.healthSummary().bookings_by_channel)
}

const renderDashboard = () =>
  renderApp(<AdminDashboard />, { as: { role: 'admin' }, lang: 'en' })

describe('alerts', () => {
  it('puts alerts first, because that is what a control tower is for', async () => {
    stubDashboard()
    renderDashboard()

    expect(
      await screen.findByText('Dr. Meena Verma absent 100% of Saturdays'),
    ).toBeInTheDocument()
  })

  it('says so plainly when nothing needs attention', async () => {
    stubDashboard({ summary: { alerts: [] } })
    renderDashboard()

    expect(await screen.findByText('Nothing needs attention')).toBeInTheDocument()
  })
})

describe('live overview', () => {
  it('counts present doctors against the total', async () => {
    stubDashboard()
    renderDashboard()

    expect(await screen.findByText('2 / 3')).toBeInTheDocument()
    expect(screen.getByText('Doctors present')).toBeInTheDocument()
  })

  it('separates absent-while-rostered from on-leave', async () => {
    // Being on approved leave is not the same as failing to turn up, and a
    // dashboard that conflates them will get someone unfairly disciplined.
    stubDashboard({ live: { doctors_absent_while_rostered: 1, doctors_on_leave: 2 } })
    renderDashboard()

    await screen.findByText('Absent while rostered')
    expect(screen.getByText('2 on leave')).toBeInTheDocument()
  })

  it('flags the missing doctor by name with how late they are', async () => {
    stubDashboard()
    renderDashboard()

    expect(await screen.findByText('Dr. Meena Verma')).toBeInTheDocument()
    expect(screen.getByText('Absent While Rostered')).toBeInTheDocument()
    expect(screen.getByText('· 76m')).toBeInTheDocument()
  })

  it('links each doctor to their corridor board', async () => {
    stubDashboard()
    renderDashboard()

    const links = await screen.findAllByRole('link', { name: 'Board' })
    expect(links[0]).toHaveAttribute('href', '/board/1')
    expect(links[1]).toHaveAttribute('href', '/board/2')
  })

  it('handles a hospital with no doctors registered yet', async () => {
    stubDashboard({ live: { doctors: [], doctors_total: 0, doctors_present: 0 } })
    renderDashboard()

    expect(await screen.findByText('No doctors registered')).toBeInTheDocument()
  })
})

describe('reports', () => {
  it('shows attendance as a percentage per doctor', async () => {
    stubDashboard()
    renderDashboard()

    expect(await screen.findByText('94%')).toBeInTheDocument()
    expect(screen.getByText('55%')).toBeInTheDocument()
    expect(screen.getByText('27 m')).toBeInTheDocument()
  })

  it('shows mean, median and p90 waits', async () => {
    stubDashboard()
    renderDashboard()

    // "Mean" appears as both a stat label and a table column heading.
    expect((await screen.findAllByText('Mean')).length).toBeGreaterThan(0)
    expect(screen.getAllByText('P90').length).toBeGreaterThan(0)
    // The headline stat rounds (nobody waits "18.4 minutes"); the per-department
    // table keeps the decimal for comparison.
    expect(screen.getByText('18 m')).toBeInTheDocument()
    expect(screen.getByText('52 m')).toBeInTheDocument()
    expect(screen.getByText('18.4m')).toBeInTheDocument()
  })

  it('explains an empty wait-time report rather than showing zeros', async () => {
    stubDashboard({
      waits: {
        start_date: '2026-07-19',
        end_date: '2026-08-18',
        overall: null,
        by_department: [],
        by_doctor: [],
      },
    })
    renderDashboard()

    // Zeros would read as "nobody waits", which is a very different claim.
    expect(await screen.findByText('No waits recorded yet')).toBeInTheDocument()
  })

  it('shows the booking channel mix', async () => {
    stubDashboard()
    renderDashboard()

    expect(await screen.findByText('Kiosk')).toBeInTheDocument()
    expect(screen.getByText('60 · 50%')).toBeInTheDocument()
    expect(screen.getByText('Ivr')).toBeInTheDocument()
  })

  it('carries the point of the channel report in words', async () => {
    stubDashboard()
    renderDashboard()

    expect(
      await screen.findByText(/A channel nobody uses is a channel that failed/i),
    ).toBeInTheDocument()
  })

  it('summarises for the Health Department', async () => {
    stubDashboard()
    renderDashboard()

    expect(await screen.findByText('120')).toBeInTheDocument()
    expect(screen.getByText('96 completed')).toBeInTheDocument()
    expect(screen.getByText('12%')).toBeInTheDocument()
  })
})
