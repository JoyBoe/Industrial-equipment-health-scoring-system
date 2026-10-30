/**
 * Sensor time-series chart.
 *
 * Renders one or more channels against time, with the equipment's own alarm
 * limit drawn as a reference line. The limit line matters: without it a reader
 * cannot tell whether a spike is normal for this machine, which is the whole
 * point of per-type profiles.
 */

import { useMemo } from 'react'
import {
  CartesianGrid,
  Line,
  LineChart,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { ChartFrame, Legend, TimeRangePicker } from './ChartFrame'
import { CHANNEL_COLORS, CHANNEL_META, formatDateTime } from '../utils/format'

const AXIS_STYLE = { fontSize: 10, fill: '#64748b' }
const GRID_COLOR = '#eceef1'

function CustomTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded border border-ink-300 bg-white px-2.5 py-2 text-xs shadow-sm">
      <p className="mb-1 text-2xs text-ink-500">{formatDateTime(label)}</p>
      {payload.map((entry) => {
        const meta = CHANNEL_META[entry.dataKey] ?? { label: entry.dataKey, unit: '' }
        const value = entry.value
        return (
          <p key={entry.dataKey} className="tabular flex items-center gap-1.5 text-ink-800">
            <span
              className="h-1.5 w-1.5 rounded-full"
              style={{ backgroundColor: entry.color }}
              aria-hidden="true"
            />
            {meta.label}
            <span className="ml-auto font-medium">
              {value === null || value === undefined ? '—' : `${Number(value).toFixed(2)} ${meta.unit}`}
            </span>
          </p>
        )
      })}
    </div>
  )
}

export function SensorChart({
  data,
  loading,
  error,
  channels,
  limits = {},
  hours,
  onHoursChange,
  title = 'Sensor telemetry',
  height = 200,
  profileHint = 'this machine',
}) {
  // Memoised so the rows useMemo below does not re-run on every render.
  const selected = useMemo(
    () => (Array.isArray(channels) && channels.length ? channels : ['temperature']),
    [channels],
  )
  const isEmpty = !loading && !error && (!data || data.length === 0)

  // Drop rows where every selected channel is null, so a sparse series does
  // not render as a line dropping to zero.
  const rows = useMemo(() => {
    if (!data) return []
    return data.filter((row) =>
      selected.some((channel) => row[channel] !== null && row[channel] !== undefined),
    )
  }, [data, selected])

  const visibleLimits = selected
    .map((channel) => (limits[channel] ? { channel, value: limits[channel] } : null))
    .filter(Boolean)

  const unit = CHANNEL_META[selected[0]]?.unit ?? ''

  return (
    <ChartFrame
      title={title}
      subtitle={selected.map((c) => CHANNEL_META[c]?.label ?? c).join(', ')}
      loading={loading}
      error={error}
      isEmpty={isEmpty || rows.length === 0}
      emptyMessage="No telemetry in this window. Readings may not have been ingested for this period."
      height={height}
      actions={onHoursChange ? <TimeRangePicker value={hours} onChange={onHoursChange} /> : null}
      ariaLabel={`${selected.map((c) => CHANNEL_META[c]?.label ?? c).join(' and ')} over the last ${hours ?? 24} hours for ${profileHint}`}
      footer={
        visibleLimits.length > 0 ? (
          <Legend
            items={[
              ...selected.map((channel) => ({
                label: `${CHANNEL_META[channel]?.label ?? channel} (${CHANNEL_META[channel]?.unit ?? ''})`,
                color: CHANNEL_COLORS[channel],
              })),
              ...visibleLimits.map((limit) => ({
                label: `${CHANNEL_META[limit.channel]?.label ?? limit.channel} alarm limit`,
                color: '#b91c1c',
                dashed: true,
              })),
            ]}
          />
        ) : null
      }
    >
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={rows} margin={{ top: 6, right: 12, bottom: 0, left: -12 }}>
          <CartesianGrid stroke={GRID_COLOR} vertical={false} />
          <XAxis
            dataKey="timestamp"
            tick={AXIS_STYLE}
            tickLine={false}
            axisLine={{ stroke: GRID_COLOR }}
            minTickGap={48}
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
            width={52}
            tickFormatter={(value) => `${value}`}
            label={
              unit
                ? { value: unit, angle: 0, position: 'insideTopLeft', offset: -2, style: AXIS_STYLE }
                : undefined
            }
          />
          <Tooltip
            content={<CustomTooltip />}
            isAnimationActive={false}
          />
          {visibleLimits.map((limit) => (
            <ReferenceLine
              key={`limit-${limit.channel}`}
              y={limit.value}
              stroke="#b91c1c"
              strokeDasharray="4 3"
              strokeWidth={1}
            />
          ))}
          {selected.map((channel) => (
            <Line
              key={channel}
              type="monotone"
              dataKey={channel}
              stroke={CHANNEL_COLORS[channel] ?? '#475569'}
              strokeWidth={1.5}
              dot={false}
              activeDot={{ r: 3 }}
              connectNulls={false}
              isAnimationActive={false}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </ChartFrame>
  )
}
