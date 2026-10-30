/**
 * Health, risk, and anomaly summary cards for the detail page.
 */

import { RiskBadge, StatusBadge, Notice } from './ui'
import {
  STATUS_STYLES,
  formatDateTime,
  formatInteger,
  formatPercent,
} from '../utils/format'

function MetricCard({ title, children, className = '' }) {
  return (
    <div className={`card px-4 py-3 ${className}`}>
      <p className="text-2xs font-semibold uppercase tracking-wider text-ink-500">{title}</p>
      {children}
    </div>
  )
}

/**
 * Large health score with a band bar.
 *
 * Shows the band boundaries so the score has context: 88 and 72 are both
 * "not good" but they sit in different bands and imply different actions.
 */
export function HealthScoreCard({ health, onReassess, assessing }) {
  const score = health?.health_score
  const status = health?.status
  const hasScore = score !== null && score !== undefined
  const style = STATUS_STYLES[status ?? 'Unknown'] ?? STATUS_STYLES.Unknown

  return (
    <MetricCard title="Health score" className="sm:col-span-2">
      <div className="mt-1 flex items-end gap-3">
        <span className={`tabular text-4xl font-semibold leading-none ${style.text}`}>
          {hasScore ? Number(score).toFixed(1) : '—'}
        </span>
        <span className="mb-1">
          <StatusBadge status={status} />
        </span>
      </div>

      <div className="relative mt-3 h-2 w-full overflow-hidden rounded-full bg-ink-100">
        {/* Band boundaries as faint dividers. */}
        {[25, 50, 75, 90].map((boundary) => (
          <span
            key={boundary}
            className="absolute top-0 h-full w-px bg-white/70"
            style={{ left: `${boundary}%` }}
            aria-hidden="true"
          />
        ))}
        <div
          className="h-full rounded-full"
          style={{
            width: `${hasScore ? Math.max(score, 1.5) : 0}%`,
            backgroundColor: style.bar,
          }}
        />
      </div>

      <div className="tabular mt-1 flex justify-between text-2xs text-ink-500">
        <span>0</span>
        <span>25</span>
        <span>50</span>
        <span>75</span>
        <span>100</span>
      </div>

      <div className="mt-2 flex items-center justify-between gap-2">
        <span className="text-2xs text-ink-500">
          {health?.timestamp ? `Assessed ${formatDateTime(health.timestamp)}` : 'Never assessed'}
        </span>
        {onReassess ? (
          <button
            type="button"
            className="btn-secondary"
            onClick={onReassess}
            disabled={assessing}
          >
            {assessing ? 'Reassessing…' : 'Reassess'}
          </button>
        ) : null}
      </div>

      {health?.delta !== null && health?.delta !== undefined ? (
        <p className="mt-1 text-2xs text-ink-500">
          {health.delta > 0 ? '▲' : health.delta < 0 ? '▼' : '■'}{' '}
          {Math.abs(health.delta).toFixed(1)} points since previous assessment
          {health.previous_score !== null && health.previous_score !== undefined
            ? ` (${Number(health.previous_score).toFixed(1)})`
            : ''}
        </p>
      ) : null}
    </MetricCard>
  )
}

export function RiskCard({ risk }) {
  const probability = risk?.failure_probability
  const calibrated = risk?.calibrated ?? false

  return (
    <MetricCard title="Failure risk (30-day)">
      <div className="mt-1 flex items-center gap-2">
        <span className="tabular text-2xl font-semibold leading-none text-ink-900">
          {probability === null || probability === undefined ? '—' : formatPercent(probability, 0)}
        </span>
        <RiskBadge risk={risk?.risk_level} probability={null} />
      </div>

      {risk?.horizon_probabilities ? (
        <dl className="mt-2 space-y-0.5">
          {Object.entries(risk.horizon_probabilities)
            .sort(([a], [b]) => Number(a) - Number(b))
            .map(([hours, value]) => (
              <div key={hours} className="tabular flex justify-between text-2xs text-ink-500">
                <dt>{formatHorizon(Number(hours))}</dt>
                <dd>{(value * 100).toFixed(1)}%</dd>
              </div>
            ))}
        </dl>
      ) : null}

      {risk?.dominant_driver ? (
        <p className="mt-2 text-2xs text-ink-600">
          Dominant driver: <span className="font-medium">{risk.dominant_driver}</span>
        </p>
      ) : null}

      {!calibrated ? (
        <div className="mt-2">
          <Notice tone="warn" title="Uncalibrated.">
            This is a relative risk index, not a measured failure rate. Use it to rank
            machines, not to predict dates.
          </Notice>
        </div>
      ) : null}
    </MetricCard>
  )
}

function formatHorizon(hours) {
  if (hours < 48) return pluralise(hours, 'hour')
  if (hours < 48 * 14) return pluralise(Math.round(hours / 24), 'day')
  return pluralise(Math.round(hours / 24 / 30), 'month')
}

/** "1 month", not "1 months". */
function pluralise(count, noun) {
  return `${count} ${noun}${count === 1 ? '' : 's'}`
}

export function AnomalyCard({ anomaly }) {
  const detected = anomaly?.anomaly_detected
  const score = anomaly?.max_anomaly_score

  return (
    <MetricCard title="Anomaly detection">
      <div className="mt-1 flex items-center gap-2">
        <span
          className={`text-2xl font-semibold leading-none ${
            detected ? 'text-red-700' : 'text-emerald-700'
          }`}
        >
          {detected ? 'Detected' : 'Normal'}
        </span>
        {score !== null && score !== undefined ? (
          <span className="tabular text-sm text-ink-500">({score.toFixed(2)})</span>
        ) : null}
      </div>

      {anomaly ? (
        <dl className="mt-2 space-y-0.5 text-2xs text-ink-600">
          <div className="flex justify-between">
            <dt>Assessments</dt>
            <dd className="tabular">{anomaly.assessed ?? '—'}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Flagged</dt>
            <dd className="tabular">{anomaly.detected ?? 0}</dd>
          </div>
          <div className="flex justify-between">
            <dt>Detection rate</dt>
            <dd className="tabular">{formatPercent(anomaly.detection_rate ?? 0, 1)}</dd>
          </div>
        </dl>
      ) : (
        <p className="mt-2 text-2xs text-ink-500">No anomaly history.</p>
      )}

      {anomaly?.most_common_parameters?.length > 0 ? (
        <div className="mt-2">
          <p className="text-2xs text-ink-500">Most implicated channels</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {anomaly.most_common_parameters.map((parameter) => (
              <span
                key={parameter}
                className="rounded bg-ink-100 px-1.5 py-0.5 text-2xs text-ink-700"
              >
                {parameter}
              </span>
            ))}
          </div>
        </div>
      ) : null}
    </MetricCard>
  )
}

export function OperatingCard({ equipment }) {
  return (
    <MetricCard title="Operating">
      <dl className="mt-1.5 space-y-1 text-2xs">
        <Row label="Operating hours" value={formatInteger(equipment?.operating_hours)} />
        <Row label="Rated power" value={equipment?.rated_power_kw ? `${equipment.rated_power_kw} kW` : '—'} />
        <Row label="Manufacturer" value={equipment?.manufacturer ?? '—'} />
        <Row label="Model" value={equipment?.model_number ?? '—'} />
        <Row
          label="Installed"
          value={equipment?.installation_date ? formatDateTime(equipment.installation_date).split(',')[0] : '—'}
        />
      </dl>
    </MetricCard>
  )
}

function Row({ label, value }) {
  return (
    <div className="flex justify-between gap-2">
      <dt className="text-ink-500">{label}</dt>
      <dd className="tabular truncate font-medium text-ink-800">{value}</dd>
    </div>
  )
}
