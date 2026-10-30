/**
 * Equipment table and filter bar.
 */

import { Link } from 'react-router-dom'
import { RiskBadge, ScoreCell, StatusBadge, EmptyState, Skeleton } from './ui'
import { STATUS_ORDER, formatRelative, humanise } from '../utils/format'

export function FilterBar({
  filters,
  onChange,
  options,
  resultCount,
  totalCount,
  onReset,
}) {
  const set = (key) => (event) => onChange({ ...filters, [key]: event.target.value })

  return (
    <div className="card">
      <div className="flex flex-wrap items-end gap-3 px-4 py-3">
        <div className="w-full sm:min-w-[200px] sm:flex-1">
          <label className="label" htmlFor="filter-search">Search</label>
          <input
            id="filter-search"
            type="search"
            className="input"
            placeholder="ID, name, or location"
            value={filters.search}
            onChange={set('search')}
          />
        </div>

        <div className="w-full sm:w-40">
          <label className="label" htmlFor="filter-status">Status</label>
          <select
            id="filter-status"
            className="input"
            value={filters.status}
            onChange={set('status')}
          >
            <option value="">All statuses</option>
            {STATUS_ORDER.map((status) => (
              <option key={status} value={status}>{status}</option>
            ))}
          </select>
        </div>

        <div className="w-full sm:w-44">
          <label className="label" htmlFor="filter-type">Equipment type</label>
          <select
            id="filter-type"
            className="input"
            value={filters.equipment_type}
            onChange={set('equipment_type')}
          >
            <option value="">All types</option>
            {(options?.equipment_types ?? []).map((type) => (
              <option key={type} value={type}>{type}</option>
            ))}
          </select>
        </div>

        <div className="w-full sm:w-44">
          <label className="label" htmlFor="filter-location">Location</label>
          <select
            id="filter-location"
            className="input"
            value={filters.location}
            onChange={set('location')}
          >
            <option value="">All locations</option>
            {(options?.locations ?? []).map((location) => (
              <option key={location} value={location}>{location}</option>
            ))}
          </select>
        </div>

        <div className="w-full sm:w-36">
          <label className="label" htmlFor="filter-sort">Sort</label>
          <select
            id="filter-sort"
            className="input"
            value={`${filters.sort_by}:${filters.sort_order}`}
            onChange={(event) => {
              const [sort_by, sort_order] = event.target.value.split(':')
              onChange({ ...filters, sort_by, sort_order })
            }}
          >
            <option value="equipment_id:asc">ID ascending</option>
            <option value="equipment_name:asc">Name A–Z</option>
            <option value="health_score:asc">Health (worst first)</option>
            <option value="health_score:desc">Health (best first)</option>
            <option value="last_updated:desc">Recently updated</option>
            <option value="operating_hours:desc">Operating hours</option>
          </select>
        </div>

        <button type="button" className="btn-secondary" onClick={onReset}>
          Reset
        </button>
      </div>

      <div className="border-t border-ink-100 px-4 py-1.5">
        <p className="text-2xs text-ink-500">
          Showing {resultCount} of {totalCount} units
        </p>
      </div>
    </div>
  )
}

/**
 * Placeholder rows that mirror the real column widths.
 *
 * A single "Loading..." row collapses the table to one line and then snaps to
 * full height, which is both ugly and disorienting when the table is the main
 * focus of the page.
 */
function TableSkeleton({ rows: count = 6, columns }) {
  return (
    <>
      {Array.from({ length: count }).map((_, rowIndex) => (
        <tr key={rowIndex} className="bg-white">
          {Array.from({ length: columns }).map((__, columnIndex) => (
            <td key={columnIndex} className="px-4 py-3 border-t border-ink-100">
              <Skeleton className="h-3.5 w-full max-w-[7rem]" />
            </td>
          ))}
        </tr>
      ))}
    </>
  )
}

export function EquipmentTable({ rows, loading }) {
  if (!loading && rows.length === 0) {
    return (
      <div className="card">
        <EmptyState
          title="No equipment matches these filters"
          message="Adjust the search or clear the filters to see the full fleet."
        />
      </div>
    )
  }

  return (
    <div className="card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="min-w-full divide-y divide-ink-200">
          <thead className="bg-ink-50">
            <tr>
              <th scope="col" className="table-header-cell">Equipment ID</th>
              <th scope="col" className="table-header-cell">Name</th>
              <th scope="col" className="table-header-cell">Type</th>
              <th scope="col" className="table-header-cell">Location</th>
              <th scope="col" className="table-header-cell">Health score</th>
              <th scope="col" className="table-header-cell">Status</th>
              <th scope="col" className="table-header-cell">Risk</th>
              <th scope="col" className="table-header-cell text-right">Operating hours</th>
              <th scope="col" className="table-header-cell text-right">Last updated</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-ink-100 bg-white">
            {loading && rows.length === 0 ? (
              <TableSkeleton rows={6} columns={9} />
            ) : (
              rows.map((row) => (
                <tr key={row.equipment_id} className="hover:bg-ink-50">
                  <td className="table-cell">
                    <Link
                      to={`/equipment/${encodeURIComponent(row.equipment_id)}`}
                      className="font-mono text-xs font-semibold text-ink-800 hover:text-ink-950 hover:underline"
                    >
                      {row.equipment_id}
                    </Link>
                  </td>
                  <td className="table-cell">
                    <span className="font-medium">{row.equipment_name}</span>
                  </td>
                  <td className="table-cell text-ink-600">{row.equipment_type}</td>
                  <td className="table-cell text-ink-600">{row.location}</td>
                  <td className="table-cell">
                    <ScoreCell score={row.health_score} status={row.status} />
                  </td>
                  <td className="table-cell">
                    <StatusBadge status={row.status} size="sm" />
                  </td>
                  <td className="table-cell">
                    <RiskBadge
                      risk={row.risk_level}
                      probability={row.failure_probability}
                    />
                  </td>
                  <td className="table-cell tabular text-right text-ink-600">
                    {row.operating_hours ? Math.round(row.operating_hours).toLocaleString() : '—'}
                  </td>
                  <td className="table-cell text-right text-2xs text-ink-500">
                    {formatRelative(row.last_updated)}
                  </td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

export function NeedsAttention({ items }) {
  if (!items || items.length === 0) return null

  return (
    <div className="card">
      <div className="card-header">
        <h2 className="card-title">Needs attention</h2>
        <span className="text-2xs text-ink-500">Lowest health scores first</span>
      </div>
      <ul className="divide-y divide-ink-100">
        {items.map((item) => (
          <li key={item.equipment_id}>
            <Link
              to={`/equipment/${encodeURIComponent(item.equipment_id)}`}
              className="flex items-center gap-3 px-4 py-2.5 hover:bg-ink-50"
            >
              <span className="font-mono text-xs font-semibold text-ink-700">
                {item.equipment_id}
              </span>
              <span className="min-w-0 flex-1 truncate text-sm text-ink-800">
                {item.equipment_name}
              </span>
              <span className="hidden text-2xs text-ink-500 sm:block">
                {humanise(item.equipment_type)}
              </span>
              <ScoreCell score={item.health_score} status={item.status} showBar={false} />
              <StatusBadge status={item.status} size="sm" />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  )
}
