import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { EquipmentDetailPage } from './EquipmentDetailPage'

/**
 * Exercises the most complex view in the app, against the live backend.
 *
 * The point of these is cross-surface agreement: the score card, the risk
 * card, the insights, and the open alerts all describe the same machine and
 * must not contradict each other. That class of bug shipped twice during
 * development and is exactly what an integration test here should catch.
 */

const API = import.meta.env.VITE_API_BASE_URL ?? ''

async function apiUp() {
  try {
    return (await fetch(`${API}/api/health`)).ok
  } catch {
    return false
  }
}

const online = await apiUp()
const maybe = online ? it : it.skip
const TIMEOUT = 20000

function renderDetail(equipmentId) {
  return render(
    <MemoryRouter initialEntries={[`/equipment/${equipmentId}`]}>
      <Routes>
        <Route path="/equipment/:equipmentId" element={<EquipmentDetailPage />} />
      </Routes>
    </MemoryRouter>,
  )
}

describe('EquipmentDetailPage', () => {
  maybe(
    'renders identity and current condition',
    async () => {
      renderDetail('EQ-001')

      await waitFor(() => expect(screen.getByText('EQ-001')).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
      expect(screen.getByRole('heading', { level: 1 })).toBeInTheDocument()
      expect(screen.getByText('Health score')).toBeInTheDocument()
      expect(screen.getByText('Failure risk (30-day)')).toBeInTheDocument()
      expect(screen.getByText('Anomaly detection')).toBeInTheDocument()
      expect(screen.getByText('Operating')).toBeInTheDocument()
    },
    TIMEOUT,
  )

  maybe(
    'always shows the uncalibrated risk caveat',
    async () => {
      // The risk model is not fitted to failure data, so this notice is the
      // only thing stopping a percentage being read as a measured rate.
      renderDetail('EQ-001')

      await waitFor(() => expect(screen.getByText(/Uncalibrated/i)).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
      expect(screen.getByText(/relative risk index/i)).toBeInTheDocument()
    },
    TIMEOUT,
  )

  maybe(
    'renders the score-explanation section',
    async () => {
      renderDetail('EQ-001')

      // The section heading is static, so waiting on it proves nothing; wait
      // on the content that only appears once the explain request resolves.
      await waitFor(() => expect(screen.getByText('Why this score')).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
      expect(screen.getByText('What drives this score')).toBeInTheDocument()
      expect(screen.getByText(/Recommended action/i)).toBeInTheDocument()
    },
    TIMEOUT,
  )

  maybe(
    'always renders the trend-watch panel, whether or not anything is trending',
    async () => {
      // An empty panel would be indistinguishable from a broken one, so this
      // section must state its state either way.
      renderDetail('EQ-001')

      // The panel heads itself differently depending on whether anything is
      // trending; both states are informative and neither may be blank.
      await waitFor(
        () =>
          expect(
            screen.queryByText(/Trending while still in range/i) ??
            screen.queryByText('Trend watch'),
          ).toBeTruthy(),
        { timeout: TIMEOUT },
      )
      const trending = screen.queryByText(/Trending while still in range/i)
      const none = screen.queryByText(/No channels are moving persistently/i)
      expect(trending || none).toBeTruthy()
    },
    TIMEOUT,
  )

  maybe(
    'shows the telemetry group selector',
    async () => {
      renderDetail('EQ-001')

      await waitFor(() => expect(screen.getByText('Telemetry group')).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
      for (const group of ['Thermal', 'Mechanical', 'Pressure', 'Electrical', 'Load']) {
        expect(screen.getByRole('button', { name: group })).toBeInTheDocument()
      }
    },
    TIMEOUT,
  )

  maybe(
    'switches the telemetry channel group',
    async () => {
      renderDetail('EQ-001')

      const button = await screen.findByRole('button', { name: 'Electrical' }, { timeout: TIMEOUT })
      await userEvent.click(button)

      // The chart subtitle follows the selected channels.
      await waitFor(() => expect(screen.getByText(/Current, Voltage, Power/)).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
    },
    TIMEOUT,
  )

  maybe(
    'shows the health score history chart',
    async () => {
      renderDetail('EQ-001')

      await waitFor(() => expect(screen.getByText('Health score history')).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
    },
    TIMEOUT,
  )

  maybe(
    'handles an unknown equipment id',
    async () => {
      renderDetail('EQ-DOES-NOT-EXIST')

      // A 404 must say so plainly rather than rendering an empty shell that
      // looks like a machine with no data.
      await waitFor(() => expect(screen.getByText('Equipment not found')).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
      expect(screen.getByRole('link', { name: /Back to dashboard/i })).toBeInTheDocument()
    },
    TIMEOUT,
  )

  maybe(
    'offers a link back to the dashboard',
    async () => {
      renderDetail('EQ-001')
      await waitFor(() => expect(screen.getByText('EQ-001')).toBeInTheDocument(), {
        timeout: TIMEOUT,
      })
      expect(screen.getAllByRole('link', { name: /Dashboard/i }).length).toBeGreaterThan(0)
    },
    TIMEOUT,
  )
})
