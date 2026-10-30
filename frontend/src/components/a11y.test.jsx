import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { EquipmentTable, FilterBar } from './EquipmentTable'
import { Layout } from './Layout'
import { KpiCard } from './ui'

/**
 * Accessibility regressions are silent: a removed skip link or a dropped
 * aria-live still renders a perfectly good-looking page. These assert the
 * affordances that are easy to break and invisible in a screenshot.
 */

describe('Layout accessibility', () => {
  function renderLayout(props = {}) {
    return render(
      <MemoryRouter>
        <Layout {...props}>
          <p>content</p>
        </Layout>
      </MemoryRouter>,
    )
  }

  it('offers a skip link as the first focusable element', () => {
    // Without it, a keyboard user tabs through the whole nav on every page.
    renderLayout()
    expect(screen.getByRole('link', { name: /skip to main content/i })).toBeInTheDocument()
  })

  it('points the skip link at the main landmark', () => {
    renderLayout()

    const link = screen.getByRole('link', { name: /skip to main content/i })
    const target = link.getAttribute('href')

    expect(target).toBe('#main-content')
    expect(document.querySelector(target)).not.toBeNull()
  })

  it('exposes a main landmark', () => {
    renderLayout()
    expect(screen.getByRole('main')).toBeInTheDocument()
  })

  it('announces open-alert count changes', () => {
    // The badge updates itself as alerts open and resolve.
    renderLayout({ alertCount: 5 })

    const badge = screen.getByLabelText('5 open alerts')
    expect(badge).toHaveAttribute('aria-live', 'polite')
  })

  it('keeps the risk-calibration caveat in the footer', () => {
    renderLayout()
    expect(
      screen.getByText(/relative indices rather than measured failure rates/i)
    ).toBeInTheDocument()
  })
})

describe('KpiCard', () => {
  it('announces figure changes politely', () => {
    // KPI values refresh on their own; a screen reader user should learn that
    // a refresh landed rather than see the number silently change.
    render(<KpiCard label="Total equipment" value={8} />)
    expect(screen.getByText('8')).toHaveAttribute('aria-live', 'polite')
  })
})

describe('EquipmentTable', () => {
  const rows = [
    {
      equipment_id: 'EQ-001', equipment_name: 'Hydraulic Pump A',
      equipment_type: 'Hydraulic Pump', location: 'Line 1',
      health_score: 71.3, status: 'Warning', risk_level: 'Moderate',
      failure_probability: 0.2, operating_hours: 12480, last_updated: '2026-01-01T00:00:00',
    },
  ]

  it('uses a column header scope for every column', () => {
    render(
      <MemoryRouter>
        <EquipmentTable rows={rows} loading={false} />
      </MemoryRouter>,
    )

    const headers = screen.getAllByRole('columnheader')
    expect(headers.length).toBeGreaterThan(0)
    for (const header of headers) {
      expect(header).toHaveAttribute('scope', 'col')
    }
  })

  it('gives each unit a descriptive link', () => {
    render(
      <MemoryRouter>
        <EquipmentTable rows={rows} loading={false} />
      </MemoryRouter>,
    )

    const link = screen.getByRole('link', { name: 'EQ-001' })
    expect(link).toHaveAttribute('href', '/equipment/EQ-001')
  })

  it('shows a skeleton rather than a single row while loading', () => {
    // One "Loading..." row collapses the table to a line and then snaps to
    // full height as data arrives.
    const { container } = render(
      <MemoryRouter>
        <EquipmentTable rows={[]} loading />
      </MemoryRouter>,
    )

    const pulses = container.querySelectorAll('.animate-pulse')
    expect(pulses.length).toBeGreaterThan(5)
  })

  it('shows an empty state when the filter matches nothing', () => {
    render(
      <MemoryRouter>
        <EquipmentTable rows={[]} loading={false} />
      </MemoryRouter>,
    )
    expect(screen.getByText(/No equipment matches these filters/i)).toBeInTheDocument()
  })
})

describe('FilterBar', () => {
  const props = {
    filters: { search: '', status: '', equipment_type: '', location: '', sort_by: 'health_score', sort_order: 'asc' },
    onChange: () => {},
    options: { equipment_types: ['Hydraulic Pump'], locations: ['Line 1'] },
    resultCount: 8,
    totalCount: 8,
    onReset: () => {},
  }

  it('labels every control', () => {
    // An unlabelled select is unusable with a screen reader.
    render(<FilterBar {...props} />)

    for (const name of ['Search', 'Status', 'Equipment type', 'Location', 'Sort']) {
      expect(screen.getByLabelText(name)).toBeInTheDocument()
    }
  })

  it('reports how many units are shown', () => {
    render(<FilterBar {...props} resultCount={3} totalCount={8} />)
    expect(screen.getByText('Showing 3 of 8 units')).toBeInTheDocument()
  })

  it('propagates a sort change', async () => {
    let received = null
    render(<FilterBar {...props} onChange={(next) => { received = next }} />)

    await userEvent.selectOptions(screen.getByLabelText('Sort'), 'equipment_name:asc')

    expect(received).toMatchObject({ sort_by: 'equipment_name', sort_order: 'asc' })
  })

  it('resets to defaults', async () => {
    let reset = false
    render(<FilterBar {...props} onReset={() => { reset = true }} />)

    await userEvent.click(screen.getByRole('button', { name: /reset/i }))

    expect(reset).toBe(true)
  })
})
