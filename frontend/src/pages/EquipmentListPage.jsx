/**
 * Equipment list.
 *
 * The dashboard table answers "what needs attention?". This page answers "what
 * do we have?" — the full register, including retired units, with lifecycle
 * actions. Kept separate rather than merged, because the two have different
 * jobs and merging them would force one page to compromise on both.
 */

import { useCallback, useState } from 'react'
import { Link } from 'react-router-dom'
import { api } from '../services/api'
import { useApi, useDebounced } from '../hooks/useApi'
import { FilterBar } from '../components/EquipmentTable'
import { EmptyState, ErrorState, Notice } from '../components/ui'
import { formatDateTime } from '../utils/format'

const DEFAULT_FILTERS = {
  search: '',
  status: '',
  equipment_type: '',
  location: '',
  sort_by: 'equipment_id',
  sort_order: 'asc',
}

export function EquipmentListPage() {
  const [filters, setFilters] = useState(DEFAULT_FILTERS)
  const [activeOnly, setActiveOnly] = useState(true)
  const [notice, setNotice] = useState(null)
  const [busy, setBusy] = useState(null)

  const debouncedSearch = useDebounced(filters.search, 300)

  const equipment = useApi(
    (signal) =>
      api.equipment.list(
        { ...filters, search: debouncedSearch, limit: 500, active_only: activeOnly },
        { signal },
      ),
    [debouncedSearch, filters.status, filters.equipment_type, filters.location,
     filters.sort_by, filters.sort_order, activeOnly],
  )

  const options = useApi((signal) => api.equipment.filterOptions({ signal }), [])

  const assess = useCallback(
    async (equipmentId) => {
      setBusy(equipmentId)
      setNotice(null)
      try {
        await api.health.assess(equipmentId, { window: 1440 })
        equipment.refresh()
        setNotice({
          kind: 'ok',
          text: `Reassessed ${equipmentId} from its recent telemetry.`,
        })
      } catch (error) {
        setNotice({ kind: 'error', error })
      } finally {
        setBusy(null)
      }
    },
    [equipment],
  )

  const rows = equipment.data ?? []

  return (
    <div className="space-y-4">
      {equipment.error ? (
        <ErrorState error={equipment.error} onRetry={equipment.refresh} />
      ) : null}

      {notice?.kind === 'ok' ? (
        <Notice tone="info">{notice.text}</Notice>
      ) : null}
      {notice?.kind === 'error' ? (
        <ErrorState error={notice.error} title="Reassessment failed" />
      ) : null}

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h1 className="text-lg font-semibold text-ink-900">Equipment register</h1>
          <p className="text-sm text-ink-600">
            Every monitored unit, with its current assessed condition.
          </p>
        </div>
        <label className="flex items-center gap-2 text-2xs text-ink-600">
          <input
            type="checkbox"
            checked={!activeOnly}
            onChange={(event) => setActiveOnly(!event.target.checked)}
            className="h-3.5 w-3.5 rounded border-ink-300"
          />
          Include retired units
        </label>
      </div>

      <FilterBar
        filters={filters}
        onChange={setFilters}
        options={options.data}
        resultCount={rows.length}
        totalCount={rows.length}
        onReset={() => setFilters(DEFAULT_FILTERS)}
      />

      <div className="space-y-2">
        {rows.map((row) => (
          <EquipmentRow
            key={row.equipment_id}
            row={row}
            onAssess={() => assess(row.equipment_id)}
            assessing={busy === row.equipment_id}
            onError={(error) => setNotice({ kind: 'error', error })}
          />
        ))}
      </div>

      {!equipment.loading && rows.length === 0 ? (
        <div className="card">
          <EmptyState
            title="No equipment matches these filters"
            message="Adjust the search, or include retired units."
          />
        </div>
      ) : null}
    </div>
  )
}

function EquipmentRow({ row, onAssess, assessing }) {
  return (
    <div className="card flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3">
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <Link
            to={`/equipment/${encodeURIComponent(row.equipment_id)}`}
            className="font-mono text-xs font-semibold text-ink-800 hover:underline"
          >
            {row.equipment_id}
          </Link>
          <Link
            to={`/equipment/${encodeURIComponent(row.equipment_id)}`}
            className="text-sm font-medium text-ink-900 hover:underline"
          >
            {row.equipment_name}
          </Link>
          {row.is_active ? null : (
            <span className="rounded bg-ink-100 px-1.5 py-0.5 text-2xs text-ink-500">
              Retired
            </span>
          )}
        </div>
        <p className="text-2xs text-ink-500">
          {row.equipment_type} · {row.location}
          {row.manufacturer ? ` · ${row.manufacturer}` : ''}
          {row.rated_power_kw ? ` · ${row.rated_power_kw} kW` : ''}
        </p>
      </div>

      <div className="w-20 text-right">
        <p className="text-2xs text-ink-500">Health</p>
        <p className="tabular text-sm font-semibold text-ink-900">
          {row.health_score === null || row.health_score === undefined
            ? '—'
            : Number(row.health_score).toFixed(1)}
        </p>
      </div>

      <div className="w-24">
        <p className="text-2xs text-ink-500">Status</p>
        <p className="text-sm text-ink-700">{row.status ?? 'Not assessed'}</p>
      </div>

      <div className="w-20 text-right">
        <p className="text-2xs text-ink-500">Hours</p>
        <p className="tabular text-sm text-ink-700">
          {row.operating_hours ? Math.round(row.operating_hours).toLocaleString() : '—'}
        </p>
      </div>

      <div className="w-36 text-right">
        <p className="text-2xs text-ink-500">Last updated</p>
        <p className="text-sm text-ink-700">{formatDateTime(row.last_updated)}</p>
      </div>

      <button
        type="button"
        className="btn-secondary"
        onClick={onAssess}
        disabled={assessing}
      >
        {assessing ? 'Assessing…' : 'Reassess'}
      </button>
    </div>
  )
}
