import { describe, expect, it } from 'vitest'
import {
  ALERT_TYPE_LABELS,
  CHANNEL_META,
  RISK_STYLES,
  STATUS_ORDER,
  STATUS_STYLES,
  formatDateTime,
  formatInteger,
  formatNumber,
  formatPercent,
  formatRelative,
  formatScore,
  humanise,
  riskStyle,
  scoreColor,
  severityStyle,
  statusStyle,
} from './format'

describe('status vocabulary', () => {
  it('orders bands worst-last so a legend reads naturally', () => {
    expect(STATUS_ORDER).toEqual(['Excellent', 'Healthy', 'Warning', 'Poor', 'Critical'])
  })

  it('has a style for every band', () => {
    for (const status of STATUS_ORDER) {
      expect(STATUS_STYLES[status]).toBeDefined()
    }
  })

  it('falls back to Unknown for an unrecognised status', () => {
    // The backend could return a value this build does not know about after a
    // deployment. Rendering nothing would be worse than rendering "Unknown".
    expect(statusStyle('Brand New Band')).toBe(STATUS_STYLES.Unknown)
  })

  it('gives every status a distinct bar colour', () => {
    const bars = STATUS_ORDER.map((s) => STATUS_STYLES[s].bar)
    expect(new Set(bars).size).toBe(bars.length)
  })

  it('has a style for every risk level', () => {
    for (const risk of ['Low', 'Moderate', 'High', 'Severe']) {
      expect(RISK_STYLES[risk]).toBeDefined()
    }
    expect(riskStyle('Nonsense')).toBe(RISK_STYLES.Unknown)
  })
})

describe('scoreColor', () => {
  it.each([
    [100, STATUS_STYLES.Excellent.bar],
    [90, STATUS_STYLES.Excellent.bar],
    [89.9, STATUS_STYLES.Healthy.bar],
    [75, STATUS_STYLES.Healthy.bar],
    [74.9, STATUS_STYLES.Warning.bar],
    [50, STATUS_STYLES.Warning.bar],
    [49.9, STATUS_STYLES.Poor.bar],
    [25, STATUS_STYLES.Poor.bar],
    [24.9, STATUS_STYLES.Critical.bar],
    [0, STATUS_STYLES.Critical.bar],
  ])('maps %s to the band colour', (score, expected) => {
    expect(scoreColor(score)).toBe(expected)
  })

  it('matches the status style when a status is supplied', () => {
    // The score bar and the status badge must not disagree.
    expect(scoreColor(95)).toBe(STATUS_STYLES.Excellent.bar)
    expect(scoreColor(95)).toBe(statusStyle('Excellent').bar)
  })

  it('handles a missing score', () => {
    expect(scoreColor(null)).toBe(STATUS_STYLES.Unknown.bar)
    expect(scoreColor(undefined)).toBe(STATUS_STYLES.Unknown.bar)
  })
})

describe('formatters', () => {
  it('formats a score to one decimal', () => {
    expect(formatScore(71.26)).toBe('71.3')
    expect(formatScore(100)).toBe('100.0')
  })

  it('renders an em dash for a missing score', () => {
    // A blank cell in a monitoring table must be visibly "no value", not 0 or
    // a NaN that reads as a fault.
    expect(formatScore(null)).toBe('—')
    expect(formatScore(undefined)).toBe('—')
  })

  it('formats percentages', () => {
    expect(formatPercent(0.186)).toBe('18.6%')
    expect(formatPercent(0.186, 0)).toBe('19%')
    expect(formatPercent(null)).toBe('—')
  })

  it('formats numbers and integers', () => {
    expect(formatNumber(1.234, 2)).toBe('1.23')
    expect(formatNumber(NaN)).toBe('—')
    expect(formatInteger(23040)).toBe('23,040')
  })

  it('formats a zero rather than treating it as missing', () => {
    expect(formatNumber(0)).toBe('0.0')
    expect(formatInteger(0)).toBe('0')
    expect(formatPercent(0)).toBe('0.0%')
  })

  it('formats timestamps and rejects unparseable ones', () => {
    expect(formatDateTime('2026-01-01T12:00:00Z')).not.toBe('—')
    expect(formatDateTime('not a date')).toBe('—')
    expect(formatDateTime(null)).toBe('—')
  })
})

describe('formatRelative', () => {
  it('reports never for a missing timestamp', () => {
    expect(formatRelative(null)).toBe('Never')
  })

  it('reports a recent reading as "Just now"', () => {
    expect(formatRelative(new Date().toISOString())).toBe('Just now')
  })

  it('reports minutes, hours and days', () => {
    const ago = (ms) => new Date(Date.now() - ms).toISOString()
    expect(formatRelative(ago(5 * 60 * 1000))).toBe('5m ago')
    expect(formatRelative(ago(3 * 3600 * 1000))).toBe('3h ago')
    expect(formatRelative(ago(2 * 86400 * 1000))).toBe('2d ago')
  })

  it('does not report a negative age for clock skew', () => {
    // A gateway slightly ahead must not produce "-3m ago".
    const future = new Date(Date.now() + 3 * 60 * 1000).toISOString()
    expect(formatRelative(future)).toBe('Just now')
  })
})

describe('labels', () => {
  it('humanises snake_case', () => {
    expect(humanise('anomaly_pattern')).toBe('Anomaly Pattern')
    expect(humanise('failure_risk')).toBe('Failure Risk')
  })

  it('handles empty input', () => {
    expect(humanise('')).toBe('')
    expect(humanise(null)).toBe('')
  })

  it('has a friendly label for every alert type the API can emit', () => {
    for (const type of [
      'low_health_score', 'high_temperature', 'excessive_vibration',
      'anomaly_detected', 'high_failure_risk', 'pressure_instability',
    ]) {
      expect(ALERT_TYPE_LABELS[type]).toBeTruthy()
    }
  })

  it('documents a unit for every sensor channel', () => {
    for (const channel of [
      'temperature', 'vibration', 'pressure', 'rpm',
      'current', 'voltage', 'power', 'load',
    ]) {
      expect(CHANNEL_META[channel]?.unit).toBeTruthy()
    }
  })
})

describe('severityStyle', () => {
  it('maps each severity and falls back safely', () => {
    expect(severityStyle('Critical').text).toBeTruthy()
    expect(severityStyle('Unexpected')).toBe(severityStyle('Info'))
  })
})
