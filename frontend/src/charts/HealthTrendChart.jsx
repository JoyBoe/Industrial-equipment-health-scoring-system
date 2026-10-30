/**
 * Health score history chart.
 *
 * Status bands are shaded behind the line so a reader can see at a glance
 * whether a machine is sitting just inside a band or on its edge. The point is
 * trend, so the y-axis is zoomed to a useful band rather than always 0-100.
 */

import { useMemo } from 'react'
import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ChartFrame } from './ChartFrame'
import { STATUS_STYLES, formatDateTime } from '../utils/format'

const AXIS_STYLE = { fontSize: 10, fill: '#64748b' }
const GRID_COLOR = '#eceef1'

/** Status bands, highest score first so the first match wins. */
const BANDS = [
  { min: 90, max: 100, status: 'Excellent' },
  { min: 75, max: 90, status: 'Healthy' },
  { min: 50, max: 75, status: 'Warning' },
  { min: 25, max: 50, status: 'Poor' },
  { min: 0, max: 25, status: 'Critical' },
]

function TrendTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  const point = payload[0]?.payload
  return (
    <div className="rounded border border-ink-300 bg-white px-2.5 py-2 text-xs shadow-sm">
      <p className="text-2xs text-ink-500">{formatDateTime(label)}</p>
      <p className="tabular mt-0.5 text-sm font-semibold text-ink-900">
        {point?.health_score?.toFixed(1)}
      </p>
      {point?.status ? (
        <p className="text-2xs text-ink-600">Status: {point.status}</p>
      ) : null}
    </div>
  )
}

export function HealthTrendChart({ data, loading, error, thresholds, height = 220 }) {
  const points = useMemo(
    () => (data ?? []).filter((p) => p.health_score !== null && p.health_score !== undefined),
    [data],
  )

  // Zoom the axis to the observed range with headroom, so a machine sitting
  // at 97-99 does not look like a flat line on a 0-100 axis.
  const domain = useMemo(() => {
    if (points.length === 0) return [0, 100]
    const values = points.map((p) => p.health_score)
    const min = Math.min(...values)
    const max = Math.max(...values)
    const pad = Math.max((max - min) * 0.25, 3)
    return [Math.max(0, Math.floor(min - pad)), Math.min(100, Math.ceil(max + pad))]
  }, [points])

  const isEmpty = !loading && !error && points.length === 0

  return (
    <ChartFrame
      title="Health score history"
      subtitle={
        points.length > 0
          ? `${points.length} assessments · latest ${points[points.length - 1].health_score.toFixed(1)}`
          : 'No assessments recorded'
      }
      loading={loading}
      error={error}
      isEmpty={isEmpty}
      emptyMessage="No health assessments yet. Use 'Reassess from telemetry' to create the first one."
      height={height}
      ariaLabel={
        points.length > 0
          ? `Health score history, ${points.length} assessments from ${points[0].health_score.toFixed(0)} to ${points[points.length - 1].health_score.toFixed(0)}`
          : 'Health score history, no assessments recorded'
      }
      footer={
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          {BANDS.map((band) => (
            <span key={band.status} className="flex items-center gap-1.5 text-2xs text-ink-600">
              <span
                className="h-2 w-3 rounded-sm opacity-40"
                style={{ backgroundColor: STATUS_STYLES[band.status].bar }}
                aria-hidden="true"
              />
              {band.status}
              <span className="tabular">{band.min}–{band.max}</span>
            </span>
          ))}
        </div>
      }
    >
      <ResponsiveContainer width="100%" height={height}>
        <ComposedChart data={points} margin={{ top: 6, right: 12, bottom: 0, left: -12 }}>
          <defs>
            <linearGradient id="scoreFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#0e7490" stopOpacity={0.18} />
              <stop offset="100%" stopColor="#0e7490" stopOpacity={0.02} />
            </linearGradient>
          </defs>

          <CartesianGrid stroke={GRID_COLOR} vertical={false} />

          {/* Status bands, only those visible in the current domain. */}
          {BANDS.map((band) => {
            const from = Math.max(band.min, domain[0])
            const to = Math.min(band.max, domain[1])
            if (to <= from) return null
            return (
              <ReferenceArea
                key={band.status}
                y1={from}
                y2={to}
                fill={STATUS_STYLES[band.status].bar}
                fillOpacity={0.07}
                stroke="none"
                ifOverflow="extendDomain"
              />
            )
          })}

          <XAxis
            dataKey="timestamp"
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={{ stroke: GRID_COLOR }}
            minTickGap={56}
            tickFormatter={(value) => {
              const date = new Date(value)
              if (Number.isNaN(date.getTime())) return value
              return date.toLocaleTimeString(undefined, {
                hour: '2-digit',
                minute: '2-digit',
                hour12: false,
              })
            }}
          />
          <YAxis
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={false}
            width={44}
            domain={domain}
          />
          <Tooltip content={<TrendTooltip />} isAnimationActive={false} />

          {thresholds?.warning !== undefined ? (
            <ReferenceLine
              y={thresholds.warning}
              stroke="#b45309"
              strokeDasharray="4 3"
              strokeWidth={1}
              label={{ value: 'Warning', position: 'right', style: AXIS_STYLE }}
            />
          ) : null}

          <Area
            type="monotone"
            dataKey="health_score"
            stroke="none"
            fill="url(#scoreFill)"
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="health_score"
            stroke="#0e7490"
            strokeWidth={2}
            dot={points.length <= 40 ? { r: 2, fill: '#0e7490' } : false}
            activeDot={{ r: 4 }}
            isAnimationActive={false}
          />
        </ComposedChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
