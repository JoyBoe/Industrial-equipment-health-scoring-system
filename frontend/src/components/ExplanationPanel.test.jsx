import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import { AnomalyCard, HealthScoreCard, OperatingCard, RiskCard } from './HealthSummaryCards'
import { ExplanationPanel } from './ExplanationPanel'

/**
 * The insights surface is where a user finds out what to actually do, so its
 * states matter more than most: a machine that is fine, a machine in trouble,
 * and a machine that is fine *right now* but has an unresolved alert.
 */

const HEALTHY_EXPLANATION = {
  equipment_id: 'EQ-001',
  equipment_type: 'Industrial Motor',
  health_score: 99.4,
  status: 'Excellent',
  risk_level: 'Low',
  failure_probability: 0.014,
  explanation: 'Health score 99.4. All monitored parameters are within their normal operating ranges.',
  recommendation: 'No action required. Continue routine monitoring.',
  attribution: [],
  operating_bands: [],
  trending_parameters: [],
  suppressed_by_policy: [],
  risk: { calibrated: false },
  trend: { available: false, reason: 'No stored assessment history yet' },
}

const DEGRADED_EXPLANATION = {
  ...HEALTHY_EXPLANATION,
  health_score: 71.3,
  status: 'Warning',
  risk_level: 'Moderate',
  failure_probability: 0.214,
  explanation:
    'Health score 71.3. The score is reduced primarily because vibration has increased above the normal operating range (now 13) (-17 points).',
  recommendation: 'Inspect bearings and coupling alignment, then check mounting fasteners.',
  attribution: [
    {
      parameter: 'vibration', value: 13, nominal: 2.8, expected_range: [0, 6],
      alarm_limit: 12.5, unit: 'mm/s RMS', direct_penalty: 17.19,
      counterfactual_penalty: 88.5, marginal_contribution: 17.19,
      is_dominant: true, would_fix: 'Reduce vibration from 13 to at or below 6',
    },
    {
      parameter: 'temperature', value: 95, nominal: 68, expected_range: [0, 82],
      alarm_limit: 105, unit: 'degC', direct_penalty: 7.64,
      counterfactual_penalty: 74.0, marginal_contribution: 0,
      is_dominant: false, would_fix: 'Reduce temperature from 95 to at or below 82',
    },
  ],
  trending_parameters: [{ parameter: 'pressure', drift: 2.67 }],
  suppressed_by_policy: [
    {
      parameter: 'temperature',
      reason: 'Charged 7.6 points by the linear weighting, but another channel is pinning the score at its floor.',
    },
  ],
  trend: {
    available: true, samples: 3, first_score: 90.2, latest_score: 71.3,
    min_score: 71.3, max_score: 90.2, average_score: 80.0,
    change_over_window: -18.9, direction: 'declining',
  },
}

describe('HealthScoreCard', () => {
  it('shows the score and status', () => {
    render(<HealthScoreCard health={{ health_score: 71.3, status: 'Warning' }} />)
    expect(screen.getByText('71.3')).toBeInTheDocument()
    expect(screen.getByText('Warning')).toBeInTheDocument()
  })

  it('shows an em dash and "Never assessed" when unscored', () => {
    render(<HealthScoreCard health={{ health_score: null, status: null, timestamp: null }} />)
    expect(screen.getByText('—')).toBeInTheDocument()
    expect(screen.getByText('Never assessed')).toBeInTheDocument()
  })

  it('does not describe an unscored machine as healthy', () => {
    // The most dangerous rendering error here would be implying a good state
    // when nothing has been measured.
    const { container } = render(
      <HealthScoreCard health={{ health_score: null, status: null, timestamp: null }} />,
    )
    expect(container.textContent).not.toMatch(/excellent/i)
  })

  it('reports the change since the previous assessment', () => {
    render(
      <HealthScoreCard
        health={{ health_score: 71.3, status: 'Warning', delta: -18.7, previous_score: 90.0, timestamp: null }}
      />,
    )
    expect(screen.getByText(/18\.7 points since previous/)).toBeInTheDocument()
    expect(screen.getByText(/90\.0/)).toBeInTheDocument()
  })

  it('invokes the reassess action', async () => {
    const onReassess = vi.fn()
    render(
      <HealthScoreCard
        health={{ health_score: 90, status: 'Healthy' }}
        onReassess={onReassess}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: 'Reassess' }))
    expect(onReassess).toHaveBeenCalledOnce()
  })

  it('disables reassess while running', () => {
    render(
      <HealthScoreCard
        health={{ health_score: 90, status: 'Healthy' }}
        onReassess={() => {}}
        assessing
      />,
    )
    expect(screen.getByRole('button', { name: /Reassessing/ })).toBeDisabled()
  })
})

describe('RiskCard', () => {
  it('shows the probability and level', () => {
    render(<RiskCard risk={{ failure_probability: 0.214, risk_level: 'Moderate' }} />)
    expect(screen.getByText('21%')).toBeInTheDocument()
    expect(screen.getByText('Moderate')).toBeInTheDocument()
  })

  it('states plainly that the figure is uncalibrated', () => {
    // Without this the operator would read a percentage as a measured
    // failure frequency.
    render(
      <RiskCard risk={{ failure_probability: 0.214, risk_level: 'Moderate', calibrated: false }} />,
    )
    expect(screen.getByText(/Uncalibrated/i)).toBeInTheDocument()
    expect(screen.getByText(/relative risk index/i)).toBeInTheDocument()
  })

  it('omits the caveat once the model is calibrated', () => {
    render(
      <RiskCard risk={{ failure_probability: 0.2, risk_level: 'Moderate', calibrated: true }} />,
    )
    expect(screen.queryByText(/Uncalibrated/i)).not.toBeInTheDocument()
  })

  it('lists every horizon when supplied', () => {
    render(
      <RiskCard
        risk={{
          failure_probability: 0.2, risk_level: 'High', calibrated: true,
          horizon_probabilities: { 24: 0.02, 168: 0.11, 720: 0.35 },
        }}
      />,
    )
    // 24 h stays in hours; 168 h becomes days; 720 h becomes months, and
    // singular, not "1 months".
    expect(screen.getByText('24 hours')).toBeInTheDocument()
    expect(screen.getByText('7 days')).toBeInTheDocument()
    expect(screen.getByText('1 month')).toBeInTheDocument()
  })

  it('names the dominant driver', () => {
    render(
      <RiskCard
        risk={{ failure_probability: 0.2, risk_level: 'High', dominant_driver: 'vibration' }}
      />,
    )
    expect(screen.getByText('vibration')).toBeInTheDocument()
  })
})

describe('AnomalyCard and OperatingCard', () => {
  it('reports normal when nothing was flagged', () => {
    render(<AnomalyCard anomaly={{ anomaly_detected: false, assessed: 100, detected: 0, detection_rate: 0, max_anomaly_score: 0.2, most_common_parameters: [] }} />)
    expect(screen.getByText('Normal')).toBeInTheDocument()
  })

  it('reports detection when flagged', () => {
    render(<AnomalyCard anomaly={{ anomaly_detected: true, assessed: 100, detected: 4, detection_rate: 0.04, max_anomaly_score: 0.9, most_common_parameters: ['vibration'] }} />)
    expect(screen.getByText('Detected')).toBeInTheDocument()
    expect(screen.getByText('vibration')).toBeInTheDocument()
  })

  it('handles a machine with no anomaly history', () => {
    render(<AnomalyCard anomaly={null} />)
    expect(screen.getByText(/No anomaly history/i)).toBeInTheDocument()
  })

  it('shows operating details', () => {
    render(
      <OperatingCard
        equipment={{ operating_hours: 12480, rated_power_kw: 15, manufacturer: 'Bosch', model_number: 'A10' }}
      />,
    )
    expect(screen.getByText('12,480')).toBeInTheDocument()
    expect(screen.getByText('15 kW')).toBeInTheDocument()
    expect(screen.getByText('Bosch')).toBeInTheDocument()
  })
})

describe('ExplanationPanel', () => {
  it('renders the generated explanation and recommendation verbatim', () => {
    // The panel must not paraphrase; the text is the backend's and an operator
    // can check it against the numbers beneath.
    render(<ExplanationPanel explanation={DEGRADED_EXPLANATION} />)
    expect(screen.getByText(/vibration has increased/)).toBeInTheDocument()
    expect(screen.getByText(/Inspect bearings/)).toBeInTheDocument()
  })

  it('shows a loading state', () => {
    render(<ExplanationPanel explanation={null} loading />)
    expect(screen.getByText(/Loading explanation/i)).toBeInTheDocument()
  })

  it('shows an error state', () => {
    render(<ExplanationPanel explanation={null} error={{ message: 'Backend unavailable' }} />)
    expect(screen.getByText('Backend unavailable')).toBeInTheDocument()
  })

  it('renders nothing when there is no explanation yet', () => {
    const { container } = render(<ExplanationPanel explanation={null} />)
    expect(container).toBeEmptyDOMElement()
  })

  describe('attribution table', () => {
    it('lists each channel and what fixing it is worth', () => {
      render(<ExplanationPanel explanation={DEGRADED_EXPLANATION} />)

      const table = screen.getByRole('table')
      expect(within(table).getByText('Vibration')).toBeInTheDocument()
      expect(within(table).getByText('−17.2')).toBeInTheDocument()
      expect(within(table).getByText(/Reduce vibration/)).toBeInTheDocument()
    })

    it('marks the primary cause', () => {
      render(<ExplanationPanel explanation={DEGRADED_EXPLANATION} />)
      expect(screen.getByText('primary')).toBeInTheDocument()
    })

    it('is omitted when every channel is in range', () => {
      render(<ExplanationPanel explanation={HEALTHY_EXPLANATION} />)
      expect(screen.queryByRole('table')).not.toBeInTheDocument()
    })
  })

  describe('policy-masked channels', () => {
    it('surfaces a channel that is out of range but not charged', () => {
      // Reading the factor list alone would tell the operator temperature is
      // fine, which is wrong.
      render(<ExplanationPanel explanation={DEGRADED_EXPLANATION} />)
      expect(screen.getByText(/Also out of range, but not charged/i)).toBeInTheDocument()
      expect(screen.getByText(/pinning the score at its floor/)).toBeInTheDocument()
    })
  })

  describe('drift', () => {
    it('lists channels trending while still in range', () => {
      render(<ExplanationPanel explanation={DEGRADED_EXPLANATION} />)
      expect(screen.getByText(/Trending while still in range/i)).toBeInTheDocument()
      expect(screen.getByText('Pressure')).toBeInTheDocument()
    })

    it('explains the absence of drift rather than showing an empty box', () => {
      render(<ExplanationPanel explanation={HEALTHY_EXPLANATION} />)
      expect(screen.getByText(/No channels are moving persistently/i)).toBeInTheDocument()
    })
  })

  describe('trend', () => {
    it('reports a declining trend with figures', () => {
      render(<ExplanationPanel explanation={DEGRADED_EXPLANATION} />)
      expect(screen.getByText('declining')).toBeInTheDocument()
      expect(screen.getByText('-18.9')).toBeInTheDocument()
    })

    it('reports the backend reason when unavailable', () => {
      render(<ExplanationPanel explanation={HEALTHY_EXPLANATION} />)
      expect(screen.getByText('No stored assessment history yet')).toBeInTheDocument()
    })
  })
})
