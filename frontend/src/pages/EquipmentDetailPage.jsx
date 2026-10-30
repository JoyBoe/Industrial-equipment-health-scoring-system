/**
 * Equipment detail page.
 *
 * Layout follows how an operator reads a machine: identity and current
 * condition first, then the reason for that condition, then the evidence.
 */

import { useCallback, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../services/api'
import { useApi } from '../hooks/useApi'
import { HealthTrendChart } from '../charts/HealthTrendChart'
import { SensorChart } from '../charts/SensorChart'
import {
  AnomalyCard,
  HealthScoreCard,
  OperatingCard,
  RiskCard,
} from '../components/HealthSummaryCards'
import { ExplanationPanel } from '../components/ExplanationPanel'
import { ErrorState, Notice, SeverityBadge, StatusBadge } from '../components/ui'
import { ALERT_TYPE_LABELS, CHANNEL_META, formatDateTime, humanise } from '../utils/format'
import { ASSESSMENT_WINDOW, POLL_INTERVAL_MS } from '../utils/constants'

/** Channel groups offered as presets, plus the individual thermal channels. */
const CHANNEL_PRESETS = [
  { key: 'thermal', label: 'Thermal', channels: ['temperature'] },
  { key: 'mechanical', label: 'Mechanical', channels: ['vibration', 'rpm'] },
  { key: 'pressure', label: 'Pressure', channels: ['pressure'] },
  { key: 'electrical', label: 'Electrical', channels: ['current', 'voltage', 'power'] },
  { key: 'load', label: 'Load', channels: ['load'] },
]

export function EquipmentDetailPage() {
  const { equipmentId } = useParams()
  const [hours, setHours] = useState(24)
  const [preset, setPreset] = useState('thermal')
  const [assessing, setAssessing] = useState(false)
  const [assessError, setAssessError] = useState(null)

  const equipment = useApi(
    (signal) => api.equipment.get(equipmentId, { signal }),
    [equipmentId],
  )

  const health = useApi(
    (signal) => api.health.current(equipmentId, { signal }),
    [equipmentId],
  )

  const trend = useApi(
    (signal) => api.health.trend(equipmentId, { limit: 200 }, { signal }),
    [equipmentId],
    { pollMs: POLL_INTERVAL_MS },
  )

  const explanation = useApi(
    (signal) => api.explain.forEquipment(equipmentId, { window: ASSESSMENT_WINDOW }, { signal }),
    [equipmentId],
  )

  const anomaly = useApi(
    (signal) => api.anomaly.summary(equipmentId, { signal }),
    [equipmentId],
  )

  const telemetry = useApi(
    (signal) => api.sensors.chart(equipmentId, { hours, max_points: 400 }, { signal }),
    [equipmentId, hours],
  )

  // Open alerts are listed alongside the current condition. An alert records an
  // event that has not been closed out, so showing it next to the score is the
  // honest presentation: a machine can be operating normally now while still
  // carrying an unresolved alert from earlier.
  const alerts = useApi(
    (signal) =>
      api.alerts.list({ equipment_id: equipmentId, resolved: false, limit: 20 }, { signal }),
    [equipmentId],
    { pollMs: POLL_INTERVAL_MS },
  )

  const thresholds = useApi((signal) => api.health.thresholds({ signal }), [])

  const handleReassess = useCallback(async () => {
    setAssessing(true)
    setAssessError(null)
    try {
      await api.health.assess(equipmentId, { window: ASSESSMENT_WINDOW })
      health.refresh()
      trend.refresh()
      explanation.refresh()
    } catch (error) {
      setAssessError(error)
    } finally {
      setAssessing(false)
    }
  }, [equipmentId, health, trend, explanation])

  if (equipment.error) {
    const notFound = equipment.error.status === 404
    return (
      <div className="space-y-4">
        <Link to="/" className="text-sm text-ink-600 hover:underline">
          ← Back to dashboard
        </Link>
        <ErrorState
          error={equipment.error}
          title={notFound ? 'Equipment not found' : 'Unable to load equipment'}
        />
      </div>
    )
  }

  if (!equipment.data) {
    return <div className="card px-4 py-10 text-center text-sm text-ink-500">Loading…</div>
  }

  const unit = equipment.data
  const activePreset = CHANNEL_PRESETS.find((p) => p.key === preset) ?? CHANNEL_PRESETS[0]
  const readings = telemetry.data?.readings ?? []

  // Derive the displayed condition from the explain payload rather than from
  // the stored assessment. The stored row is whatever was last written -- by a
  // different window, or a different strategy -- so the score card could show
  // 99.4 "Excellent" directly above an explanation of 71.3 "Warning" for
  // the same machine. One computation, one number.
  const condition = explanation.data
    ? {
        health_score: explanation.data.health_score,
        status: explanation.data.status,
        risk_level: explanation.data.risk_level,
        failure_probability: explanation.data.failure_probability,
        delta: health.data?.delta ?? null,
        previous_score: health.data?.previous_score ?? null,
        timestamp:
          health.data?.timestamp ?? explanation.data.observed_at ?? null,
      }
    : health.data

  // Alarm limits come from `operating_bands`, not from attribution. Attribution
  // only reports channels that are already out of band, so on a healthy
  // machine it is empty and the reference lines would vanish exactly when an
  // operator most needs to judge whether a reading is approaching its limit.
  const limits = {}
  for (const band of explanation.data?.operating_bands ?? []) {
    limits[band.parameter] = band.alarm_high
  }

  return (
    <div className="space-y-4">
      {/* Identity header */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <Link to="/" className="text-2xs text-ink-500 hover:underline">
            ← Dashboard
          </Link>
          <div className="mt-0.5 flex flex-wrap items-center gap-2">
            <h1 className="text-lg font-semibold text-ink-900">{unit.equipment_name}</h1>
            <span className="font-mono text-xs text-ink-500">{unit.equipment_id}</span>
            <StatusBadge status={unit.status} />
          </div>
          <p className="text-sm text-ink-600">
            {unit.equipment_type} · {unit.location}
            {unit.manufacturer ? ` · ${unit.manufacturer}` : ''}
          </p>
        </div>
      </div>

      {assessError ? <ErrorState error={assessError} title="Reassessment failed" /> : null}

      {/* Current condition */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <HealthScoreCard
          health={condition}
          onReassess={handleReassess}
          assessing={assessing}
        />
        <RiskCard
          risk={
            explanation.data
              ? {
                  failure_probability: explanation.data.failure_probability,
                  risk_level: explanation.data.risk_level,
                  horizon_probabilities: explanation.data.risk?.horizon_probabilities,
                  dominant_driver: explanation.data.risk?.dominant_driver,
                  calibrated: explanation.data.risk?.calibrated,
                }
              : null
          }
        />
        <AnomalyCard anomaly={anomaly.data} />
        <OperatingCard equipment={unit} />
      </section>

      {health.error?.status === 404 ? (
        <Notice tone="info" title="Not yet assessed.">
          This machine has telemetry but no health assessment. Use{' '}
          <em>Reassess</em> above to create the first one from its recent readings.
        </Notice>
      ) : null}

      {/* Open alerts */}
      {alerts.data && alerts.data.items.length > 0 ? (
        <OpenAlerts items={alerts.data.items} onResolved={alerts.refresh} />
      ) : null}

      {/* Charts */}
      <section className="grid grid-cols-1 gap-3 xl:grid-cols-2">
        <HealthTrendChart
          data={trend.data?.points ?? []}
          loading={trend.loading}
          error={trend.error}
          thresholds={thresholds.data?.status_thresholds}
        />
        <SensorChart
          title={`${activePreset.label} telemetry`}
          data={readings}
          loading={telemetry.loading}
          error={telemetry.error}
          channels={activePreset.channels}
          limits={limits}
          profileHint={unit.equipment_type}
          hours={hours}
          onHoursChange={setHours}
          height={220}
        />
      </section>

      {/* Channel selector for the telemetry chart */}
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-2xs font-semibold uppercase tracking-wider text-ink-500">
          Telemetry group
        </span>
        <div className="flex flex-wrap gap-1">
          {CHANNEL_PRESETS.map((option) => (
            <button
              key={option.key}
              type="button"
              onClick={() => setPreset(option.key)}
              className={`rounded border px-2.5 py-1 text-2xs font-medium transition-colors ${
                preset === option.key
                  ? 'border-ink-800 bg-ink-800 text-white'
                  : 'border-ink-300 bg-white text-ink-600 hover:bg-ink-50'
              }`}
            >
              {option.label}
            </button>
          ))}
        </div>
        <span className="ml-auto text-2xs text-ink-500">
          {readings.length} points · {CHANNEL_META[activePreset.channels[0]]?.unit} units
        </span>
      </div>

      {/* Explanation */}
      <section>
        <h2 className="mb-2 text-sm font-semibold text-ink-800">What drives this score</h2>
        <ExplanationPanel
          explanation={explanation.data}
          loading={explanation.loading}
          error={explanation.error}
        />
      </section>

      <p className="text-2xs text-ink-500">
        Telemetry shown: {readings.length > 0 ? formatDateTime(readings[0].timestamp) : '—'} to{' '}
        {readings.length > 0 ? formatDateTime(readings[readings.length - 1].timestamp) : '—'}
      </p>
    </div>
  )
}

/**
 * Unresolved alerts for this machine.
 *
 * Resolve is offered inline because the common operator action on a detail page
 * is "I have looked at this, close it" -- not "navigate away to close it".
 */
function OpenAlerts({ items, onResolved }) {
  const [busy, setBusy] = useState(null)
  const [error, setError] = useState(null)

  const handleResolve = async (alertId) => {
    setBusy(alertId)
    setError(null)
    try {
      await api.alerts.resolve(alertId, 'Closed from the equipment detail page')
      onResolved?.()
    } catch (err) {
      setError(err)
    } finally {
      setBusy(null)
    }
  }

  return (
    <section className="card">
      <div className="card-header">
        <h2 className="card-title">Open alerts</h2>
        <span className="text-2xs text-ink-500">
          {items.length} unresolved · raised at a past reading, still open
        </span>
      </div>
      {error ? (
        <p className="border-t border-red-100 bg-red-50 px-4 py-2 text-2xs text-red-700">
          {error.message}
        </p>
      ) : null}
      <ul className="divide-y divide-ink-100">
        {items.map((alert) => (
          <li key={alert.alert_id} className="flex flex-wrap items-start gap-3 px-4 py-3">
            <SeverityBadge severity={alert.severity} />
            <div className="min-w-0 flex-1">
              <p className="text-sm font-medium text-ink-800">
                {ALERT_TYPE_LABELS[alert.alert_type] ?? humanise(alert.alert_type)}
              </p>
              <p className="mt-0.5 text-2xs leading-relaxed text-ink-600">
                {alert.description}
              </p>
              {alert.recommended_action ? (
                <p className="mt-1 text-2xs leading-relaxed text-ink-500">
                  <span className="font-medium">Action:</span>{' '}
                  {alert.recommended_action}
                </p>
              ) : null}
              <p className="mt-1 text-2xs text-ink-500">
                Raised {formatDateTime(alert.timestamp)}
                {alert.observed_value ? ` · observed ${alert.observed_value}` : ''}
              </p>
            </div>
            <button
              type="button"
              className="btn-secondary shrink-0"
              onClick={() => handleResolve(alert.alert_id)}
              disabled={busy === alert.alert_id}
            >
              {busy === alert.alert_id ? 'Closing…' : 'Resolve'}
            </button>
          </li>
        ))}
      </ul>
    </section>
  )
}
