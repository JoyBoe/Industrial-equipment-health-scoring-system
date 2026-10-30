import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { DashboardPage } from './DashboardPage'
import { AlertsPage } from './AlertsPage'

/**
 * Page tests run against the real FastAPI backend over the Vite proxy.
 *
 * This is deliberate. Mocking the client would have let the two sides drift
 * apart silently -- a renamed response field or a changed status vocabulary
 * would pass every unit test and break in the browser. Running the real
 * contract means a backend change fails here first.
 *
 * Skipped automatically when the API is not reachable, so the suite still runs
 * in isolation.
 */

const API = import.meta.env.VITE_API_BASE_URL ?? ''

async function apiUp() {
  try {
    const response = await fetch(`${API}/api/health`)
    return response.ok
  } catch {
    return false
  }
}

// Resolved at collection time with a top-level await. Doing this in a
// beforeEach is too late: `maybe()` below runs while the describe body is
// being collected, before any hook executes, so the flag would still be its
// initial value and every test would skip.
const online = await apiUp()
if (!online) {
  // A missing backend is an environment problem, not a product failure.
  console.warn('[integration] backend unreachable; page tests will be skipped')
}

afterEach(() => {
  vi.restoreAllMocks()
})

// These hit a real HTTP backend, so the default 5 s Vitest timeout is not
// enough once a debounced search and a network round trip are in the path.
const INTEGRATION_TIMEOUT_MS = 20000

const maybe = (name, fn) =>
  online ? it(name, fn, INTEGRATION_TIMEOUT_MS) : it.skip(name, fn)

function renderPage(element, { route = '/' } = {}) {
  return render(<MemoryRouter initialEntries={[route]}>{element}</MemoryRouter>)
}

describe('DashboardPage', () => {
  maybe('renders the KPI row from the API', async () => {
    renderPage(<DashboardPage />)

    await waitFor(() => expect(screen.getByText('Total equipment')).toBeInTheDocument())
    expect(screen.getByText('Average health')).toBeInTheDocument()
    expect(screen.getByText('Open alerts')).toBeInTheDocument()
  })

  maybe('renders the equipment table with real rows', async () => {
    renderPage(<DashboardPage />)

    await waitFor(() => expect(screen.getByText('Equipment ID')).toBeInTheDocument(), {
      timeout: 10000,
    })
    // The seeded fleet always contains these identifiers.
    expect(await screen.findByText('EQ-001', {}, { timeout: 10000 })).toBeInTheDocument()
  })

  maybe('filters the table by search', async () => {
    renderPage(<DashboardPage />)

    const search = await screen.findByLabelText('Search', {}, { timeout: 10000 })
    await userEvent.type(search, 'EQ-007')

    // Scoped to the table: the same identifier can also appear in the
    // needs-attention list above it.
    const table = await screen.findByRole('table', {}, { timeout: 10000 })
    await waitFor(
      () => expect(within(table).getByText('EQ-007')).toBeInTheDocument(),
      { timeout: 10000 },
    )
    await waitFor(
      () => expect(within(table).queryByText('EQ-004')).not.toBeInTheDocument(),
      { timeout: 10000 },
    )
  })

  maybe('shows a filter result count', async () => {
    renderPage(<DashboardPage />)
    await waitFor(() => expect(screen.getByText(/Showing \d+ of \d+ units/)).toBeInTheDocument(), {
      timeout: 10000,
    })
  })

  maybe('links each row to its detail page', async () => {
    renderPage(<DashboardPage />)

    const link = await screen.findByRole('link', { name: 'EQ-001' }, { timeout: 10000 })
    expect(link).toHaveAttribute('href', '/equipment/EQ-001')
  })

  maybe('surfaces an API failure instead of showing an empty fleet', async () => {
    // An empty table plus no error reads as "healthy fleet". That is the most
    // dangerous possible reading of a backend outage.
    const original = global.fetch
    global.fetch = vi.fn(async () => {
      throw new TypeError('Failed to fetch')
    })
    try {
      renderPage(<DashboardPage />)
      await waitFor(
        () => expect(screen.getAllByText(/Unable to load data/i).length).toBeGreaterThan(0),
        { timeout: 10000 },
      )
    } finally {
      global.fetch = original
    }
  })
})

describe('AlertsPage', () => {
  maybe('renders counts and the filters', async () => {
    renderPage(<AlertsPage />, { route: '/alerts' })

    // "Open" is both a count card and a filter button, so the button role is
    // used to disambiguate.
    await waitFor(
      () => expect(screen.getByRole('button', { name: 'Open' })).toBeInTheDocument(),
      { timeout: 10000 },
    )
    expect(screen.getByLabelText('Severity')).toBeInTheDocument()
    expect(screen.getByLabelText('Type')).toBeInTheDocument()
  })

  maybe('switches between open and resolved', async () => {
    renderPage(<AlertsPage />, { route: '/alerts' })

    await screen.findByLabelText('Severity', {}, { timeout: 10000 })
    await userEvent.click(screen.getByRole('button', { name: 'Resolved' }))

    // The results counter re-renders for the new state, which is the signal
    // that the filter actually changed the request rather than only the
    // button styling.
    await waitFor(() => expect(screen.getByText(/^\d+ shown$/)).toBeInTheDocument(), {
      timeout: 10000,
    })
    // And any alert listed under Resolved offers no further Resolve action.
    expect(screen.queryAllByRole('button', { name: /^Resolve$/ })).toHaveLength(0)
  })

  maybe('shows an empty state when nothing matches', async () => {
    renderPage(<AlertsPage />, { route: '/alerts' })

    const search = await screen.findByLabelText('Equipment', {}, { timeout: 10000 })
    await userEvent.type(search, 'EQ-NONEXISTENT')

    await waitFor(
      () => expect(screen.getByText(/No alerts match these filters/i)).toBeInTheDocument(),
      { timeout: 10000 },
    )
  })

  maybe('does not show a resolve button for a resolved alert', async () => {
    renderPage(<AlertsPage />, { route: '/alerts' })

    await screen.findByLabelText('Severity', {}, { timeout: 10000 })
    await userEvent.click(screen.getByRole('button', { name: 'Resolved' }))

    // If any alert is listed, none of them may offer a Resolve action.
    await waitFor(() => {
      const groups = screen.queryAllByRole('button', { name: /^Resolve$/ })
      expect(groups).toHaveLength(0)
    }, { timeout: 10000 })
  })
})
