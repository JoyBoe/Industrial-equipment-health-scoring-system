import { RISK_STYLES, STATUS_STYLES } from '../utils/format'

/**
 * Small presentational components shared across pages.
 *
 * Each status is always accompanied by a text label, never conveyed by colour
 * alone, so the dashboard remains usable with colour-vision deficiency or on a
 * washed-out control-room monitor.
 */

export function StatusBadge({ status, size = 'md' }) {
  if (!status) {
    return (
      <span className="inline-flex items-center rounded border border-slate-200 bg-slate-100 px-2 py-0.5 text-2xs font-medium text-slate-600">
        Not assessed
      </span>
    )
  }

  const style = STATUS_STYLES[status] ?? STATUS_STYLES.Unknown
  const sizing = size === 'sm' ? 'px-1.5 py-0.5 text-2xs' : 'px-2 py-0.5 text-xs'

  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border font-medium ${style.bg} ${style.text} ${style.border} ${sizing}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${style.dot}`} aria-hidden="true" />
      {status}
    </span>
  )
}

export function RiskBadge({ risk, probability, calibrated }) {
  if (!risk) {
    return <span className="text-sm text-ink-500">—</span>
  }
  const style = RISK_STYLES[risk] ?? RISK_STYLES.Unknown
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded border px-2 py-0.5 text-xs font-medium ${style.bg} ${style.text} ${style.border}`}
      title={
        calibrated === false
          ? 'Uncalibrated risk index — a relative measure, not a measured failure rate'
          : undefined
      }
    >
      {risk}
      {probability !== null && probability !== undefined ? (
        <span className="tabular opacity-70">
          {(probability * 100).toFixed(0)}%
        </span>
      ) : null}
    </span>
  )
}

export function SeverityBadge({ severity }) {
  const map = {
    Critical: { cls: 'border-red-200 bg-red-50 text-red-800' },
    Warning: { cls: 'border-amber-200 bg-amber-50 text-amber-800' },
    Info: { cls: 'border-slate-200 bg-slate-100 text-slate-700' },
  }
  const style = map[severity] ?? map.Info
  return (
    <span className={`inline-flex rounded border px-2 py-0.5 text-2xs font-semibold uppercase tracking-wide ${style.cls}`}>
      {severity}
    </span>
  )
}

/**
 * Health score with a proportional bar.
 *
 * The number is the primary signal; the bar is a secondary cue. Both derive
 * from the same value.
 */
export function ScoreCell({ score, status, showBar = true }) {
  const hasScore = score !== null && score !== undefined
  const styles = STATUS_STYLES[status ?? 'Unknown'] ?? STATUS_STYLES.Unknown
  const color = hasScore
    ? score >= 90 ? STATUS_STYLES.Excellent.bar
    : score >= 75 ? STATUS_STYLES.Healthy.bar
    : score >= 50 ? STATUS_STYLES.Warning.bar
    : score >= 25 ? STATUS_STYLES.Poor.bar
    : STATUS_STYLES.Critical.bar
    : styles.bar

  return (
    <div className="flex items-center gap-2">
      <span className="tabular w-11 text-right text-sm font-semibold text-ink-900">
        {hasScore ? Number(score).toFixed(1) : '—'}
      </span>
      {showBar ? (
        <div className="h-1.5 w-16 overflow-hidden rounded-full bg-ink-200">
          <div
            className="h-full rounded-full"
            style={{ width: `${hasScore ? Math.max(score, 2) : 0}%`, backgroundColor: color }}
          />
        </div>
      ) : null}
    </div>
  )
}

export function KpiCard({ label, value, subtext, tone = 'neutral', icon }) {
  const tones = {
    neutral: 'text-ink-900',
    good: 'text-emerald-700',
    caution: 'text-amber-700',
    bad: 'text-red-700',
  }
  return (
    <div className="card px-4 py-3">
      <div className="flex items-start justify-between">
        <p className="text-2xs font-semibold uppercase tracking-wider text-ink-500">
          {label}
        </p>
        {icon ? <span className="text-ink-400" aria-hidden="true">{icon}</span> : null}
      </div>
      <p
        className={`tabular mt-1.5 text-2xl font-semibold leading-none ${tones[tone] ?? tones.neutral}`}
        // KPI figures change on their own as the dashboard polls. Announcing
        // them politely means a screen reader user learns a refresh landed
        // instead of the value silently changing under them.
        aria-live="polite"
      >
        {value}
      </p>
      {subtext ? <p className="mt-1 text-2xs text-ink-500">{subtext}</p> : null}
    </div>
  )
}

export function ErrorState({ error, onRetry, title = 'Unable to load data' }) {
  return (
    <div className="card border-red-200 bg-red-50 p-4">
      <div className="flex items-start gap-3">
        <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-red-100 text-xs font-bold text-red-700">
          !
        </span>
        <div className="min-w-0 flex-1">
          <p className="text-sm font-semibold text-red-900">{title}</p>
          <p className="mt-0.5 break-words text-sm text-red-800">
            {error?.message ?? 'The request could not be completed.'}
          </p>
          {error?.status === 0 ? (
            <p className="mt-1 text-xs text-red-700">
              Start the backend with <code className="font-mono">make run</code>, then retry.
            </p>
          ) : null}
        </div>
        {onRetry ? (
          <button type="button" className="btn-secondary shrink-0" onClick={onRetry}>
            Retry
          </button>
        ) : null}
      </div>
    </div>
  )
}

export function EmptyState({ title, message, action }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-4 py-12 text-center">
      <p className="text-sm font-medium text-ink-700">{title}</p>
      {message ? <p className="max-w-md text-sm text-ink-500">{message}</p> : null}
      {action}
    </div>
  )
}

export function LoadingRow({ colSpan, label = 'Loading…' }) {
  return (
    <tr>
      <td colSpan={colSpan} className="px-4 py-10 text-center text-sm text-ink-500">
        {label}
      </td>
    </tr>
  )
}

export function Skeleton({ className = '' }) {
  return <div className={`animate-pulse rounded bg-ink-200 ${className}`} />
}

/**
 * Small inline notice.
 *
 * Used for the "risk figures are uncalibrated" caveat, which must be visible
 * wherever a probability is shown rather than buried in documentation.
 */
export function Notice({ tone = 'info', title, children }) {
  const tones = {
    info: 'border-ink-200 bg-ink-50 text-ink-700',
    warn: 'border-amber-200 bg-amber-50 text-amber-900',
  }
  return (
    <div className={`rounded border px-3 py-2 text-xs ${tones[tone] ?? tones.info}`}>
      {title ? <span className="font-semibold">{title} </span> : null}
      {children}
    </div>
  )
}
