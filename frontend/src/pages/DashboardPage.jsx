/**
 * Dashboard page: KPI row, needs-attention list, and the equipment table.
 */

import { useCallback, useMemo, useState } from 'react'
import { api } from '../services/api'
import { useApi, useDebounced } from '../hooks/useApi'
import { EquipmentTable, FilterBar, NeedsAttention } from '../components/EquipmentTable'
import { ErrorState, KpiCard, Skeleton } from '../components/ui'
import { STATUS_STYLES, formatInteger, formatRelative, formatScore } from '../utils/format'
import { ASSESSMENT_WINDOW, POLL_INTERVAL_MS } from '../utils/constants'

const DEFAULT_FILTERS = {
  search: '',
  status: '',
  equipment_type: '',
  location: '',
  sort_by: 'health_score',
  sort_order: 'asc',
}

export function DashboardPage() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [scoreError, setScoreError] = useState(null)
  const [scoring, setScoring] = useState(false)

  // Debounced so typing in the search box does not fire a request per keystroke.
  const debouncedSearch = useDebounced(filters.search, 300)

  const summary = useApi(
    (signal) => api.dashboardSummary({ signal }),
    [],
    { pollMs: POLL_INTERVAL_MS },
  )

  const options = useApi((signal) => api.equipment.filterOptions({ signal }), [])

  const equipment = useApi(
    (signal) =>
      api.equipment.list(
        {
          ...filters,
          search: debouncedSearch,
          limit: 200,
          active_only: true,
        },
        { signal },
      ),
    [
      debouncedSearch,
      filters.status,
      filters.equipment_type,
      filters.location,
      filters.sort_by,
      filters.sort_order,
    ],
  )

  const handleScoreAll = useCallback(async () => {
    setScoring(true)
    setScoreError(null)
    try {
      // `recent_worst`, not the default `latest`. The dashboard is a fleet
      // summary: a machine that was overheating four hours ago and has since
      // recovered should not read Excellent here while a Critical alert for
      // the same machine sits unresolved in the alerts list. Scoring only the
      // newest sample would make the two views contradict each other.
      await api.health.scoreAll({ window: ASSESSMENT_WINDOW, strategy: 'recent_worst' })
      summary.refresh()
      equipment.refresh()
    } catch (error) {
      setScoreError(error)
    } finally {
      setScoring(false)
    }
  }, [summary, equipment])

  const rows = equipment.data ?? []
  const summaryData = summary.data

  const statusCounts = summaryData?.counts_by_status
  const degraded =
    (statusCounts?.Warning ?? 0) + (statusCounts?.Poor ?? 0) + (statusCounts?.Critical ?? 0)

  const avgTone = useMemo(() => {
    if (!summaryData?.average_health_score) return 'neutral'
    if (summaryData.average_health_score >= 90) return 'good'
    if (summaryData.average_health_score >= 75) return 'good'
    if (summaryData.average_health_score >= 50) return 'caution'
    return 'bad'
  }, [summaryData])

  return (
    <div className="space-y-4">
      {summary.error ? (
        <ErrorState error={summary.error} onRetry={summary.refresh} />
      ) : null}
      {scoreError ? <ErrorState error={scoreError} title="Scoring failed" /> : null}

      {/* KPI row */}
      <section aria-label="Fleet summary" className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {summary.loading && !summaryData ? (
          Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="card px-4 py-3">
              <Skeleton className="h-3 w-20" />
              <Skeleton className="mt-2 h-6 w-14" />
            </div>
          ))
        ) : (
          <>
            <KpiCard
              label="Total equipment"
              value={formatInteger(summaryData?.total_equipment ?? 0)}
              subtext={
                summaryData?.unassessed_equipment
                  ? `${summaryData.unassessed_equipment} not yet assessed`
                  : 'All assessed'
              }
            />
            <KpiCard
              label="Healthy"
              value={formatInteger((statusCounts?.Excellent ?? 0) + (statusCounts?.Healthy ?? 0))}
              subtext="Excellent and healthy"
              tone="good"
            />
            <KpiCard
              label="Warning"
              value={formatInteger(statusCounts?.Warning ?? 0)}
              subtext="Score 50–74"
              tone={(statusCounts?.Warning ?? 0) > 0 ? 'caution' : 'neutral'}
            />
            <KpiCard
              label="Poor / Critical"
              value={formatInteger((statusCounts?.Poor ?? 0) + (statusCounts?.Critical ?? 0))}
              subtext="Score below 50"
              tone={(statusCounts?.Poor ?? 0) + (statusCounts?.Critical ?? 0) > 0 ? 'bad' : 'neutral'}
            />
            <KpiCard
              label="Average health"
              value={formatScore(summaryData?.average_health_score)}
              subtext={summaryData?.last_assessed_at ? `Updated ${formatRelative(summaryData.last_assessed_at)}` : 'No assessments yet'}
              tone={avgTone}
            />
            <KpiCard
              label="Open alerts"
              value={formatInteger(summaryData?.open_alerts ?? 0)}
              subtext={`${summaryData?.alerts_by_severity?.Critical ?? 0} critical`}
              tone={(summaryData?.alerts_by_severity?.Critical ?? 0) > 0 ? 'bad' : 'neutral'}
            />
          </>
        )}
      </section>

      {degraded > 0 && statusCounts ? (
        <section aria-label="Fleet health distribution" className="card px-4 py-3">
          <div className="mb-2 flex items-center justify-between">
            <h2 className="card-title">Fleet health distribution</h2>
            <span className="text-2xs text-ink-500">
              {summaryData?.total_equipment ?? 0} active units
            </span>
          </div>
          <div className="flex h-2 w-full overflow-hidden rounded-full bg-ink-100">
            {['Excellent', 'Healthy', 'Warning', 'Poor', 'Critical'].map((status) => {
              const count = statusCounts[status] ?? 0
              if (count === 0) return null
              const share = (count / (summaryData?.total_equipment || 1)) * 100
              return (
                <div
                  key={status}
                  className="h-full"
                  style={{ width: `${share}%`, backgroundColor: STATUS_STYLES[status].bar }}
                  title={`${status}: ${count}`}
                />
              )
            })}
          </div>
          <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1">
            {['Excellent', 'Healthy', 'Warning', 'Poor', 'Critical'].map((status) => (
              <span key={status} className="flex items-center gap-1.5 text-2xs text-ink-600">
                <span
                  className="h-2 w-2 rounded-sm"
                  style={{ backgroundColor: STATUS_STYLES[status].bar }}
                  aria-hidden="true"
                />
                {status}
                <span className="tabular font-medium text-ink-800">
                  {statusCounts?.[status] ?? 0}
                </span>
              </span>
            ))}
          </div>
        </section>
      ) : null}

      <NeedsAttention items={summaryData?.needs_attention} />

      <FilterBar
        filters={filters}
        onChange={setFilters}
        options={options.data}
        resultCount={rows.length}
        totalCount={summaryData?.total_equipment ?? rows.length}
        onReset={() => setFilters(DEFAULT_FILTERS)}
      />

      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold text-ink-800">Equipment</h2>
        <button
          type="button"
          className="btn-secondary"
          onClick={handleScoreAll}
          disabled={scoring}
        >
          {scoring ? 'Scoring…' : 'Recompute all scores'}
        </button>
      </div>

      {equipment.error ? (
        <ErrorState error={equipment.error} onRetry={equipment.refresh} />
      ) : (
        <EquipmentTable rows={rows} loading={equipment.loading} />
      )}
    </div>
  )
}
