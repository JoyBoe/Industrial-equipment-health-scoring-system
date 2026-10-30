import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'
import {
  EmptyState,
  ErrorState,
  KpiCard,
  Notice,
  RiskBadge,
  ScoreCell,
  SeverityBadge,
  Skeleton,
  StatusBadge,
} from './ui'
import { ErrorBoundary } from './ErrorBoundary'
import { ApiError } from '../services/api'

describe('StatusBadge', () => {
  it('always includes the status text, not only a colour', () => {
    // The dashboard must remain readable with colour-vision deficiency or on
    // a washed-out control-room monitor.
    for (const status of ['Excellent', 'Healthy', 'Warning', 'Poor', 'Critical']) {
      render(<StatusBadge status={status} />)
      expect(screen.getByText(status)).toBeInTheDocument()
    }
  })

  it('says "Not assessed" for a machine with no assessment', () => {
    // "Not assessed" is materially different from "Excellent"; conflating them
    // would make an unscored machine look healthy.
    render(<StatusBadge status={null} />)
    expect(screen.getByText('Not assessed')).toBeInTheDocument()
  })

  it('falls back rather than rendering nothing for an unknown status', () => {
    render(<StatusBadge status="Brand New Band" />)
    expect(screen.getByText('Brand New Band')).toBeInTheDocument()
  })
})

describe('ScoreCell', () => {
  it('renders the score to one decimal', () => {
    render(<ScoreCell score={71.26} status="Warning" />)
    expect(screen.getByText('71.3')).toBeInTheDocument()
  })

  it('renders a dash when unscored', () => {
    render(<ScoreCell score={null} status={null} />)
    expect(screen.getByText('—')).toBeInTheDocument()
  })

  it('keeps zero visible rather than blank', () => {
    // A score of 0 is a real, critical reading and must not look like "no data".
    render(<ScoreCell score={0} status="Critical" />)
    expect(screen.getByText('0.0')).toBeInTheDocument()
  })
})

describe('RiskBadge', () => {
  it('shows the level and the probability', () => {
    render(<RiskBadge risk="High" probability={0.42} />)
    expect(screen.getByText('High')).toBeInTheDocument()
    expect(screen.getByText('42%')).toBeInTheDocument()
  })

  it('flags an uncalibrated figure in its tooltip', () => {
    // The tooltip is the only place a casual reader would see the caveat.
    render(<RiskBadge risk="Moderate" probability={0.2} calibrated={false} />)
    const badge = screen.getByText('Moderate').closest('span')
    expect(badge).toHaveAttribute(
      'title',
      expect.stringContaining('Uncalibrated'),
    )
  })

  it('renders nothing extra without a probability', () => {
    render(<RiskBadge risk="Low" probability={null} />)
    expect(screen.getByText('Low')).toBeInTheDocument()
  })
})

describe('SeverityBadge', () => {
  it('renders the severity as text', () => {
    render(<SeverityBadge severity="Critical" />)
    expect(screen.getByText('Critical')).toBeInTheDocument()
  })
})

describe('KpiCard', () => {
  it('shows the label, value, and subtext', () => {
    render(<KpiCard label="Total equipment" value={8} subtext="All assessed" />)
    expect(screen.getByText('Total equipment')).toBeInTheDocument()
    expect(screen.getByText('8')).toBeInTheDocument()
    expect(screen.getByText('All assessed')).toBeInTheDocument()
  })
})

describe('ErrorState', () => {
  it('shows the message and a retry action', async () => {
    const onRetry = vi.fn()
    render(
      <ErrorState error={new ApiError('Server exploded', 500)} onRetry={onRetry} />,
    )

    expect(screen.getByText('Server exploded')).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('gives start-the-backend guidance for a network failure', () => {
    // A different remedy than a server error, so it needs a different prompt.
    render(<ErrorState error={new ApiError('Cannot reach the API', 0)} />)
    expect(screen.getByText(/make run/)).toBeInTheDocument()
  })

  it('omits the retry button when no handler is supplied', () => {
    render(<ErrorState error={new ApiError('Nope', 500)} />)
    expect(screen.queryByRole('button', { name: /retry/i })).not.toBeInTheDocument()
  })
})

describe('EmptyState and Notice', () => {
  it('renders the empty message', () => {
    render(<EmptyState title="Nothing here" message="Adjust the filters" />)
    expect(screen.getByText('Nothing here')).toBeInTheDocument()
    expect(screen.getByText('Adjust the filters')).toBeInTheDocument()
  })

  it('renders a notice with an optional title', () => {
    render(
      <Notice tone="warn" title="Uncalibrated.">
        Relative index only.
      </Notice>,
    )
    expect(screen.getByText('Uncalibrated.')).toBeInTheDocument()
    expect(screen.getByText(/Relative index only/)).toBeInTheDocument()
  })
})

describe('Skeleton', () => {
  it('renders a placeholder without text', () => {
    const { container } = render(<Skeleton className="h-6 w-10" />)
    expect(container.firstChild).toHaveClass('animate-pulse')
  })
})

describe('ErrorBoundary', () => {
  function Boom({ shouldThrow }) {
    if (shouldThrow) throw new Error('Component exploded')
    return <p>All good</p>
  }

  it('renders children when nothing throws', () => {
    render(
      <ErrorBoundary>
        <Boom shouldThrow={false} />
      </ErrorBoundary>,
    )
    expect(screen.getByText('All good')).toBeInTheDocument()
  })

  it('catches a crash and shows a recoverable message', () => {
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    render(
      <ErrorBoundary>
        <Boom shouldThrow />
      </ErrorBoundary>,
    )

    // A monitoring dashboard must not go blank during an incident.
    expect(screen.getByText('This view failed to render')).toBeInTheDocument()
    expect(screen.getByText('Component exploded')).toBeInTheDocument()
    expect(screen.getByText(/still usable/i)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /retry/i })).toBeInTheDocument()
    spy.mockRestore()
  })

  it('recovers when a transient error resolves on retry', async () => {
    // The realistic case: the boundary remounts its subtree, which re-runs
    // the failing data fetch, and the second attempt succeeds. Note that
    // clearing `error` alone would NOT recover here -- React remembers the
    // throw against the element position, which is why the remount matters.
    const spy = vi.spyOn(console, 'error').mockImplementation(() => {})
    // An explicit flag rather than a call counter: React retries a throwing
    // mount once before surfacing the error, so a counter would self-heal
    // before the boundary ever rendered its fallback.
    let failing = true

    function Flaky() {
      if (failing) throw new Error('Transient render failure')
      return <p>Recovered</p>
    }

    render(
      <ErrorBoundary>
        <Flaky />
      </ErrorBoundary>,
    )
    expect(screen.getByText('This view failed to render')).toBeInTheDocument()

    failing = false
    await userEvent.click(screen.getByRole('button', { name: /retry/i }))

    await waitFor(() => expect(screen.getByText('Recovered')).toBeInTheDocument())
    spy.mockRestore()
  })
})
