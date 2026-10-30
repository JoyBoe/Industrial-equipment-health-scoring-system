/**
 * Application shell.
 *
 * Deliberately restrained: a fixed header, a compact nav, and a content area.
 * No animated sidebar, no gradients, no glow. A monitoring surface that moves
 * on its own is harder to read and implies activity that isn't happening.
 */

import { NavLink } from 'react-router-dom'

const NAV = [
  { to: '/', label: 'Dashboard', end: true },
  { to: '/alerts', label: 'Alerts' },
  { to: '/equipment', label: 'Equipment' },
]

export function Layout({ children, alertCount = 0, apiOnline = null, offlineReason = null }) {
  return (
    <div className="min-h-screen bg-ink-50">
      {/* First tab stop: lets a keyboard user jump past the navigation
          instead of traversing it on every page. */}
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:z-50
                   focus:rounded focus:bg-ink-900 focus:px-3 focus:py-2 focus:text-sm focus:text-white"
      >
        Skip to main content
      </a>

      <header className="border-b border-ink-200 bg-white">
        <div className="mx-auto flex h-12 max-w-[1600px] items-center gap-6 px-4">
          <div className="flex items-center gap-2">
            <span
              className="flex h-6 w-6 items-center justify-center rounded bg-ink-800 text-2xs font-bold text-white"
              aria-hidden="true"
            >
              EH
            </span>
            <div className="leading-none">
              <p className="text-sm font-semibold text-ink-900">Equipment Health</p>
              <p className="text-2xs text-ink-500">Condition monitoring</p>
            </div>
          </div>

          <nav className="flex items-center gap-1" aria-label="Main">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `rounded px-2.5 py-1 text-sm font-medium transition-colors ${
                    isActive
                      ? 'bg-ink-100 text-ink-900'
                      : 'text-ink-600 hover:bg-ink-50 hover:text-ink-800'
                  }`
                }
              >
                {item.label}
                {item.to === '/alerts' && alertCount > 0 ? (
                  <span
                    className="tabular ml-1.5 rounded bg-red-100 px-1.5 py-0.5 text-2xs font-semibold text-red-700"
                    // The count changes on its own as alerts open and resolve;
                    // without this a screen reader user is never told.
                    aria-live="polite"
                    aria-label={`${alertCount} open alerts`}
                  >
                    {alertCount}
                  </span>
                ) : null}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto flex items-center gap-3">
            {apiOnline !== null ? (
              <span
                className="flex items-center gap-1.5 text-2xs text-ink-500"
                title={
                  apiOnline
                    ? 'Connected to the API'
                    : `API unreachable${offlineReason ? `: ${offlineReason}` : ''}`
                }
              >
                <span
                  className={`h-1.5 w-1.5 rounded-full ${apiOnline ? 'bg-emerald-600' : 'bg-red-500'}`}
                  aria-hidden="true"
                />
                {apiOnline ? 'Connected' : 'Offline'}
              </span>
            ) : null}
          </div>
        </div>
      </header>

      {/* A persistent banner rather than a transient toast. If the API is
          unreachable every figure on the page is stale, and that must not be
          allowed to look like a healthy fleet. */}
      {apiOnline === false ? (
        <div className="border-b border-red-200 bg-red-50">
          <div className="mx-auto flex max-w-[1600px] items-start gap-2 px-4 py-2">
            <span className="mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-red-200 text-2xs font-bold text-red-800">
              !
            </span>
            <p className="text-2xs leading-relaxed text-red-800">
              <span className="font-semibold">Not connected to the API.</span>{' '}
              {offlineReason ?? 'The backend is unreachable.'} Any figures shown come from
              the last successful request and may be out of date. Start it with{' '}
              <code className="font-mono">make run</code>.
            </p>
          </div>
        </div>
      ) : null}

      <main id="main-content" tabIndex={-1} className="mx-auto max-w-[1600px] px-4 py-4 focus:outline-none">
        {children}
      </main>

      <footer className="mx-auto max-w-[1600px] px-4 pb-6 pt-2">
        <p className="text-2xs text-ink-500">
          Health scores are computed from sensor telemetry against per-equipment-type
          operating limits. Risk figures are relative indices rather than measured
          failure rates unless the model has been fitted to your failure history —
          check <code className="text-ink-600">GET /api/predict/calibration</code>.
        </p>
      </footer>
    </div>
  )
}
