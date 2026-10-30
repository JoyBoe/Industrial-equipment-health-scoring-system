/**
 * Score-explanation panel.
 *
 * Renders the explanation the backend generates from its own arithmetic. This
 * component only formats: it does not compute, infer, or embellish. That is
 * deliberate — the text shown here is the same text the API returned, so an
 * operator who disagrees with it can go and check the numbers in the
 * attribution table directly beneath.
 *
 * Deliberately NOT labelled "AI insights": the content is deterministic
 * arithmetic, so an AI-flavoured label would overclaim and misrepresent a
 * panel whose whole value is that it is auditable.
 */

import { ALERT_TYPE_LABELS, humanise, formatDateTime } from '../utils/format'

export function ExplanationPanel({ explanation, loading, error }) {
  if (loading) {
    return (
      <div className="card px-4 py-8 text-center text-sm text-ink-500">
        Loading explanation…
      </div>
    )
  }

  if (error) {
    return (
      <div className="card border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800">
        {error.message ?? 'Unable to load the explanation'}
      </div>
    )
  }

  if (!explanation) return null

  return (
    <div className="space-y-3">
      {/* Headline explanation */}
      <section className="card">
        <div className="card-header">
          <h3 className="card-title">Why this score</h3>
          <span className="text-2xs text-ink-500">
            {explanation.generated_at ? formatDateTime(explanation.generated_at) : ''}
          </span>
        </div>
        <div className="space-y-3 px-4 py-3">
          <p className="text-sm leading-relaxed text-ink-800">
            {explanation.explanation}
          </p>

          <div>
            <p className="label">Recommended action</p>
            <p className="text-sm leading-relaxed text-ink-800">
              {explanation.recommendation}
            </p>
          </div>
        </div>
      </section>

      {/* Counterfactual attribution */}
      {explanation.attribution?.length > 0 ? (
        <AttributionTable items={explanation.attribution} />
      ) : null}

      {/* Channels masked by policy */}
      {explanation.suppressed_by_policy?.length > 0 ? (
        <section className="card border-amber-200 bg-amber-50">
          <div className="card-header border-amber-200">
            <h3 className="card-title text-amber-900">Also out of range, but not charged</h3>
          </div>
          <ul className="divide-y divide-amber-200">
            {explanation.suppressed_by_policy.map((item) => (
              <li key={item.parameter} className="px-4 py-2.5">
                <p className="text-sm font-medium text-amber-900">{humanise(item.parameter)}</p>
                <p className="mt-0.5 text-2xs leading-relaxed text-amber-800">{item.reason}</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {/* Drift: movement while still inside limits */}
      <DriftPanel trending={explanation.trending_parameters} />

      {/* Trend against the machine's own history */}
      <TrendPanel trend={explanation.trend} />
    </div>
  )
}

function AttributionTable({ items }) {
  return (
    <section className="card">
      <div className="card-header">
        <h3 className="card-title">What each channel costs</h3>
        <span className="text-2xs text-ink-500">If this channel alone returned to normal</span>
      </div>
      <div className="overflow-x-auto">
        <table className="min-w-full">
          <thead className="bg-ink-50">
            <tr>
              <th scope="col" className="table-header-cell">Channel</th>
              <th scope="col" className="table-header-cell text-right">Reading</th>
              <th scope="col" className="table-header-cell text-right">Normal range</th>
              <th scope="col" className="table-header-cell text-right">Points cost</th>
              <th scope="col" className="table-header-cell text-right">If fixed</th>
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr key={item.parameter} className="border-t border-ink-100">
                <td className="table-cell">
                  <span className={item.is_dominant ? 'font-semibold' : ''}>
                    {humanise(item.parameter)}
                  </span>
                  {item.is_dominant ? (
                    <span className="ml-1.5 rounded bg-ink-800 px-1.5 py-0.5 text-2xs font-medium text-white">
                      primary
                    </span>
                  ) : null}
                </td>
                <td className="table-cell tabular text-right">{item.value}</td>
                <td className="table-cell tabular text-right text-ink-500">
                  {item.expected_range?.[0]}–{item.expected_range?.[1]}
                </td>
                <td className="table-cell tabular text-right font-medium text-ink-800">
                  −{Number(item.marginal_contribution).toFixed(1)}
                </td>
                <td className="table-cell text-right text-2xs text-ink-600">
                  {item.would_fix ?? '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function DriftPanel({ trending }) {
  if (!trending || trending.length === 0) {
    return (
      <section className="card px-4 py-3">
        <h3 className="card-title">Trend watch</h3>
        <p className="mt-1 text-2xs text-ink-500">
          No channels are moving persistently away from normal. Early-stage wear often shows
          here first, while every reading is still inside its acceptable range.
        </p>
      </section>
    )
  }

  return (
    <section className="card border-cyan-200">
      <div className="card-header border-cyan-200">
        <h3 className="card-title text-cyan-900">Trending while still in range</h3>
        <span className="text-2xs text-cyan-700">Not yet causing a score penalty</span>
      </div>
      <ul className="divide-y divide-ink-100">
        {trending.map((item) => (
          <li key={item.parameter} className="flex items-center justify-between px-4 py-2">
            <span className="text-sm text-ink-800">{humanise(item.parameter)}</span>
            <span className="tabular text-2xs text-ink-500">
              drift {item.drift.toFixed(1)}σ
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}

function TrendPanel({ trend }) {
  if (!trend?.available) {
    return (
      <section className="card px-4 py-3">
        <h3 className="card-title">Health trend</h3>
        <p className="mt-1 text-2xs text-ink-500">
          {trend?.reason ?? 'No assessment history available for this machine yet.'}
        </p>
      </section>
    )
  }

  return (
    <section className="card px-4 py-3">
      <h3 className="card-title">Health trend</h3>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-2xs sm:grid-cols-4">
        <div>
          <dt className="text-ink-500">Change</dt>
          <dd
            className={`tabular font-semibold ${
              trend.change_over_window > 0
                ? 'text-emerald-700'
                : trend.change_over_window < 0
                  ? 'text-red-700'
                  : 'text-ink-700'
            }`}
          >
            {trend.change_over_window > 0 ? '+' : ''}
            {Number(trend.change_over_window).toFixed(1)}
          </dd>
        </div>
        <div>
          <dt className="text-ink-500">Window average</dt>
          <dd className="tabular font-medium text-ink-800">
            {Number(trend.average_score).toFixed(1)}
          </dd>
        </div>
        <div>
          <dt className="text-ink-500">Lowest</dt>
          <dd className="tabular font-medium text-ink-800">
            {Number(trend.min_score).toFixed(1)}
          </dd>
        </div>
        <div>
          <dt className="text-ink-500">Direction</dt>
          <dd className="font-medium capitalize text-ink-800">{trend.direction}</dd>
        </div>
      </dl>
    </section>
  )
}

export function AlertBadge({ type }) {
  return <span>{ALERT_TYPE_LABELS[type] ?? humanise(type)}</span>
}
