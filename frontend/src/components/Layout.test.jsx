import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it } from 'vitest'
import { Layout } from './Layout'
import { SystemStatusContext, useSystemStatus } from '../context/SystemStatus'

/**
 * The layout carries the offline banner, which is a safety feature rather than
 * decoration: if the API is unreachable every figure on screen is stale, and
 * that must never be mistaken for a healthy fleet.
 */

function renderLayout(props = {}, children = <p>content</p>) {
  return render(
    <MemoryRouter>
      <Layout {...props}>{children}</Layout>
    </MemoryRouter>,
  )
}

describe('Layout', () => {
  it('renders the product identity and navigation', () => {
    renderLayout()

    expect(screen.getByText('Equipment Health')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Dashboard' })).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Equipment' })).toBeInTheDocument()
  })

  it('renders children', () => {
    renderLayout({}, <p>page body</p>)
    expect(screen.getByText('page body')).toBeInTheDocument()
  })

  describe('connection indicator', () => {
    it('shows nothing while connectivity is unknown', () => {
      // Reporting "Offline" before the first response would be a false alarm.
      renderLayout({ apiOnline: null })
      expect(screen.queryByText('Offline')).not.toBeInTheDocument()
      expect(screen.queryByText('Connected')).not.toBeInTheDocument()
    })

    it('reports connected', () => {
      renderLayout({ apiOnline: true })
      expect(screen.getByText('Connected')).toBeInTheDocument()
      expect(screen.queryByText('Offline')).not.toBeInTheDocument()
    })

    it('reports offline with the reason', () => {
      renderLayout({ apiOnline: false, offlineReason: 'Connection refused' })
      expect(screen.getByText('Offline')).toBeInTheDocument()
    })
  })

  describe('offline banner', () => {
    it('is absent while online', () => {
      renderLayout({ apiOnline: true })
      expect(screen.queryByText(/Not connected to the API/i)).not.toBeInTheDocument()
    })

    it('is absent while connectivity is unknown', () => {
      renderLayout({ apiOnline: null })
      expect(screen.queryByText(/Not connected to the API/i)).not.toBeInTheDocument()
    })

    it('appears when the API is unreachable, with a remedy', () => {
      renderLayout({ apiOnline: false, offlineReason: 'Cannot reach the API' })

      expect(screen.getByText(/Not connected to the API/i)).toBeInTheDocument()
      expect(screen.getByText(/Cannot reach the API/)).toBeInTheDocument()
      expect(screen.getByText(/may be out of date/i)).toBeInTheDocument()
      expect(screen.getByText('make run')).toBeInTheDocument()
    })

    it('falls back to a generic reason', () => {
      renderLayout({ apiOnline: false, offlineReason: null })
      expect(screen.getByText(/backend is unreachable/i)).toBeInTheDocument()
    })
  })

  describe('alert badge', () => {
    it('is hidden when there are no open alerts', () => {
      renderLayout({ alertCount: 0 })
      const link = screen.getByRole('link', { name: 'Alerts' })
      expect(link.textContent).toBe('Alerts')
    })

    it('shows the open count', () => {
      renderLayout({ alertCount: 12 })
      expect(screen.getByText('12')).toBeInTheDocument()
    })
  })

  it('carries the standing risk-calibration caveat in the footer', () => {
    // This is the last line of defence if a probability is ever displayed
    // without its inline caveat.
    //
    // Stated as a rule rather than as a current state: the model can now be
    // calibrated from recorded failures, so a footer asserting "uncalibrated"
    // outright would become false the moment an operator fitted it.
    renderLayout()
    expect(
      screen.getByText(/relative indices rather than measured failure rates/i)
    ).toBeInTheDocument()
    expect(screen.getByText(/fitted to your failure history/i)).toBeInTheDocument()
  })
})

describe('SystemStatusContext', () => {
  it('provides a usable default so components render outside the provider', () => {
    // Every page reads this context; a missing provider must not crash the app.
    render(<Consumer />)
    // Defaults: connectivity unknown, no alerts, no critical alerts.
    expect(screen.getByText('null|0|0')).toBeInTheDocument()
  })

  it('exposes the provided values', () => {
    render(
      <SystemStatusContext.Provider
        value={{ online: true, reason: null, alertCount: 3, criticalCount: 1, refreshAlerts: () => {} }}
      >
        <Consumer />
      </SystemStatusContext.Provider>,
    )

    expect(screen.getByText('true|3|1')).toBeInTheDocument()
  })
})

function Consumer() {
  const { online, alertCount, criticalCount } = useSystemStatus()
  return <span>{`${online}|${alertCount}|${criticalCount}`}</span>
}
