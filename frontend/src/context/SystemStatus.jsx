/**
 * Shared system status.
 *
 * API reachability and the open-alert count are polled once at the root and
 * read from here. Polling them per page meant opening a second view issued a
 * second liveness request, and the header badge could briefly disagree with the
 * alert count on the page beneath it.
 */

import { createContext, useContext } from 'react'

export const SystemStatusContext = createContext({
  online: null,
  reason: null,
  alertCount: 0,
  criticalCount: 0,
  refreshAlerts: () => {},
})

export function useSystemStatus() {
  return useContext(SystemStatusContext)
}
