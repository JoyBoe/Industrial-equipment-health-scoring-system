/**
 * Display helpers.
 *
 * Status vocabulary is defined once here so the dashboard and the detail page
 * can never disagree about a colour or a label.
 */

export const STATUS_ORDER = ['Excellent', 'Healthy', 'Warning', 'Poor', 'Critical']

/**
 * Muted, high-contrast status colours. Each is paired with a text label at
 * every point of use, so the dashboard never depends on colour alone to
 * convey severity.
 */
export const STATUS_STYLES = {
  Excellent: { bg: 'bg-emerald-50', text: 'text-emerald-800', border: 'border-emerald-200', dot: 'bg-emerald-600', bar: '#15803d' },
  Healthy:   { bg: 'bg-cyan-50',    text: 'text-cyan-800',    border: 'border-cyan-200',    dot: 'bg-cyan-600',    bar: '#0e7490' },
  Warning:   { bg: 'bg-amber-50',   text: 'text-amber-800',   border: 'border-amber-200',   dot: 'bg-amber-600',   bar: '#b45309' },
  Poor:      { bg: 'bg-orange-50',  text: 'text-orange-800',  border: 'border-orange-200',  dot: 'bg-orange-600',  bar: '#c2410c' },
  Critical:  { bg: 'bg-red-50',     text: 'text-red-800',     border: 'border-red-200',     dot: 'bg-red-600',     bar: '#b91c1c' },
  Unknown:   { bg: 'bg-slate-100',  text: 'text-slate-600',   border: 'border-slate-200',   dot: 'bg-slate-400',   bar: '#64748b' },
}

export const RISK_STYLES = {
  Low:      { bg: 'bg-slate-100', text: 'text-slate-700',   border: 'border-slate-200',   bar: '#64748b' },
  Moderate: { bg: 'bg-amber-50',  text: 'text-amber-800',   border: 'border-amber-200',   bar: '#b45309' },
  High:     { bg: 'bg-orange-50', text: 'text-orange-800',  border: 'border-orange-200',  bar: '#c2410c' },
  Severe:   { bg: 'bg-red-50',    text: 'text-red-800',     border: 'border-red-200',     bar: '#b91c1c' },
  Unknown:  { bg: 'bg-slate-100', text: 'text-slate-600',   border: 'border-slate-200',   bar: '#94a3b8' },
}

export const SEVERITY_STYLES = {
  Critical: { bg: 'bg-red-50',    text: 'text-red-800',    border: 'border-red-200' },
  Warning:  { bg: 'bg-amber-50',  text: 'text-amber-800',  border: 'border-amber-200' },
  Info:     { bg: 'bg-slate-100', text: 'text-slate-700',  border: 'border-slate-200' },
}

export const CHANNEL_META = {
  temperature: { label: 'Temperature', unit: '°C' },
  vibration:   { label: 'Vibration',   unit: 'mm/s' },
  pressure:    { label: 'Pressure',    unit: 'bar' },
  rpm:         { label: 'Speed',       unit: 'rpm' },
  current:     { label: 'Current',     unit: 'A' },
  voltage:     { label: 'Voltage',     unit: 'V' },
  power:       { label: 'Power',       unit: 'kW' },
  load:        { label: 'Load',        unit: '%' },
}

/** Chart series colours, distinct in hue and in lightness. */
export const CHANNEL_COLORS = {
  temperature: '#b91c1c',
  vibration:   '#7c3aed',
  pressure:    '#0e7490',
  rpm:         '#0f766e',
  current:     '#b45309',
  voltage:     '#475569',
  power:       '#1d4ed8',
  load:        '#4d7c0f',
}

export function statusStyle(status) {
  return STATUS_STYLES[status] ?? STATUS_STYLES.Unknown
}

export function riskStyle(risk) {
  return RISK_STYLES[risk] ?? RISK_STYLES.Unknown
}

export function severityStyle(severity) {
  return SEVERITY_STYLES[severity] ?? SEVERITY_STYLES.Info
}

/** Health score colour, derived from the score rather than the status label. */
export function scoreColor(score) {
  if (score === null || score === undefined) return STATUS_STYLES.Unknown.bar
  if (score >= 90) return STATUS_STYLES.Excellent.bar
  if (score >= 75) return STATUS_STYLES.Healthy.bar
  if (score >= 50) return STATUS_STYLES.Warning.bar
  if (score >= 25) return STATUS_STYLES.Poor.bar
  return STATUS_STYLES.Critical.bar
}

export function formatScore(score) {
  if (score === null || score === undefined) return '—'
  return Number(score).toFixed(1)
}

export function formatPercent(value, digits = 1) {
  if (value === null || value === undefined) return '—'
  return `${(Number(value) * 100).toFixed(digits)}%`
}

export function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toLocaleString(undefined, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })
}

export function formatInteger(value) {
  if (value === null || value === undefined) return '—'
  return Number(value).toLocaleString()
}

/** Absolute date/time. The API returns UTC-naive strings, which parse as local. */
export function formatDateTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleString(undefined, {
    year: 'numeric',
    month: 'short',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

export function formatTime(value) {
  if (!value) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  return date.toLocaleTimeString(undefined, {
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  })
}

/**
 * Coarse "time ago" for last-updated columns.
 *
 * Deliberately coarse: an industrial dashboard needs to know whether data is
 * current, not the exact second, and a ticking relative timestamp on every
 * row is visual noise.
 */
export function formatRelative(value) {
  if (!value) return 'Never'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '—'
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000)
  if (seconds < 0) return 'Just now'
  if (seconds < 60) return 'Just now'
  const minutes = Math.floor(seconds / 60)
  if (minutes < 60) return `${minutes}m ago`
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  if (days < 30) return `${days}d ago`
  return formatDateTime(value)
}

/** Renders a parameter name from snake_case, e.g. anomaly_pattern. */
export function humanise(value) {
  if (!value) return ''
  return String(value)
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase())
}

export const ALERT_TYPE_LABELS = {
  low_health_score: 'Low health score',
  high_temperature: 'High temperature',
  excessive_vibration: 'Excessive vibration',
  anomaly_detected: 'Anomaly detected',
  high_failure_risk: 'High failure risk',
  pressure_instability: 'Pressure instability',
}
