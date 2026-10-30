/**
 * Error boundary.
 *
 * A monitoring dashboard that renders a blank page when one component throws
 * is worse than useless during an incident: an operator cannot tell the
 * difference between "the system has nothing to report" and "the dashboard
 * broke". The boundary keeps the shell and navigation alive and states plainly
 * what failed.
 */

import { Component, Fragment, createElement } from 'react'

export class ErrorBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { error: null, resetKey: 0 }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // Kept on the console because there is no telemetry sink yet; an operator
    // reporting a problem will be asked for this.
    console.error('Unhandled UI error:', error, info?.componentStack)
  }

  handleReset = () => {
    // The key change is essential, not cosmetic. React remembers a thrown error
    // against the child element's position, so clearing `error` alone
    // re-renders the same element and it throws again -- the Retry button
    // silently does nothing. Remounting the subtree is what actually recovers.
    this.setState((prev) => ({ error: null, resetKey: prev.resetKey + 1 }))
  }

  render() {
    const { error, resetKey } = this.state
    if (!error) {
      return createElement(Fragment, { key: resetKey }, this.props.children)
    }

    return (
      <div className="card border-red-200 bg-red-50 p-4">
        <div className="flex items-start gap-3">
          <span className="mt-0.5 flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-red-100 text-xs font-bold text-red-700">
            !
          </span>
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-red-900">This view failed to render</p>
            <p className="mt-0.5 break-words text-sm text-red-800">{error.message}</p>
            <p className="mt-1 text-xs text-red-700">
              The rest of the dashboard is still usable. Use the navigation above, or retry
              this view.
            </p>
            <div className="mt-2 flex gap-2">
              <button type="button" className="btn-secondary" onClick={this.handleReset}>
                Retry
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => window.location.assign('/')}
              >
                Back to dashboard
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }
}
