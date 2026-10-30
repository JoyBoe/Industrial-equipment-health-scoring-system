/**
 * Application root.
 *
 * Owns the two polls that must not be duplicated per page — API reachability
 * and the open-alert count — and passes them down through context, so opening
 * three views does not mean three liveness requests and three badge counts
 * drifting apart.
 */

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import { Layout } from './components/Layout'
import { ErrorBoundary } from './components/ErrorBoundary'
import { DashboardPage } from './pages/DashboardPage'
import { EquipmentDetailPage } from './pages/EquipmentDetailPage'
import { EquipmentListPage } from './pages/EquipmentListPage'
import { AlertsPage } from './pages/AlertsPage'
import { SystemStatusContext } from './context/SystemStatus'
import { useApi } from './hooks/useApi'
import { api } from './services/api'
import { POLL_INTERVAL_MS } from './utils/constants'

export default function App() {
  return (
    <BrowserRouter>
      <SystemShell />
    </BrowserRouter>
  )
}

function SystemShell() {
  // Polled here, once, rather than in each page.
  const health = useApi((signal) => api.systemHealth({ signal }), [], {
    pollMs: POLL_INTERVAL_MS,
  })
  const alertSummary = useApi(
    (signal) => api.alerts.summary({ signal }),
    [],
    { pollMs: POLL_INTERVAL_MS },
  )

  // `undefined` while the first request is in flight, so the indicator shows
  // nothing rather than falsely reporting "Offline".
  const status = health.error
    ? { online: false, reason: health.error.message }
    : health.data
      ? { online: true, reason: null }
      : null

  const value = {
    online: status?.online ?? null,
    reason: status?.reason ?? null,
    alertCount: alertSummary.data?.unresolved_total ?? 0,
    criticalCount: alertSummary.data?.critical ?? 0,
    refreshAlerts: alertSummary.refresh,
  }

  return (
    <SystemStatusContext.Provider value={value}>
      <Layout
        alertCount={value.alertCount}
        apiOnline={value.online}
        offlineReason={value.reason}
      >
        <ErrorBoundary>
          <Routes>
            <Route path="/" element={<DashboardPage />} />
            <Route path="/alerts" element={<AlertsPage />} />
            <Route path="/equipment" element={<EquipmentListPage />} />
            <Route path="/equipment/:equipmentId" element={<EquipmentDetailPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </ErrorBoundary>
      </Layout>
    </SystemStatusContext.Provider>
  )
}
