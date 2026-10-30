/**
 * Shared chart scaffolding: title, controls, loading and empty states.
 *
 * Every chart goes through this so they behave identically. In particular an
 * empty dataset renders an explicit "no data" message rather than an empty
 * axis box, which is indistinguishable from a broken chart.
 */


export function ChartFrame({
  title,
  subtitle,
  actions,
  loading,
  error,
  isEmpty,
  emptyMessage = 'No data for this window',
  height = 200,
  children,
  footer,
  ariaLabel,
}) {
  return (
    <section className="card flex flex-col">
      <div className="card-header">
        <div className="min-w-0">
          <h3 className="card-title truncate">{title}</h3>
          {subtitle ? <p className="text-2xs text-ink-500">{subtitle}</p> : null}
        </div>
        {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
      </div>

      <div
        className="flex-1 px-2 py-2"
        style={{ minHeight: height }}
        // Recharts renders a bare <svg> with no accessible name, so assistive
        // technology has nothing to announce. The surrounding headings and
        // legends carry the detail; this states what the figure is.
        role="img"
        aria-label={ariaLabel}
      >
        {loading ? (
          <div className="flex h-full items-center justify-center text-sm text-ink-500">
            Loading…
          </div>
        ) : error ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-red-700">
            {error.message ?? 'Unable to load chart data'}
          </div>
        ) : isEmpty ? (
          <div className="flex h-full items-center justify-center px-4 text-center text-sm text-ink-500">
            {emptyMessage}
          </div>
        ) : (
          children
        )}
      </div>

      {footer ? (
        <div className="border-t border-ink-100 px-4 py-1.5 text-2xs text-ink-500">
          {footer}
        </div>
      ) : null}
    </section>
  )
}

export function TimeRangePicker({ value, onChange, options }) {
  const ranges = options ?? [
    { label: '1h', hours: 1 },
    { label: '6h', hours: 6 },
    { label: '24h', hours: 24 },
    { label: '48h', hours: 48 },
  ]

  return (
    <div className="flex items-center gap-0.5 rounded border border-ink-300 bg-white p-0.5">
      {ranges.map((range) => (
        <button
          key={range.hours}
          type="button"
          onClick={() => onChange(range.hours)}
          className={`rounded px-2 py-0.5 text-2xs font-medium transition-colors ${
            value === range.hours
              ? 'bg-ink-800 text-white'
              : 'text-ink-600 hover:bg-ink-100'
          }`}
        >
          {range.label}
        </button>
      ))}
    </div>
  )
}

export function Legend({ items }) {
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
      {items.map((item) => (
        <span key={item.label} className="flex items-center gap-1.5 text-2xs text-ink-600">
          <span
            className="h-0.5 w-3 rounded-full"
            style={{ backgroundColor: item.color }}
            aria-hidden="true"
          />
          {item.label}
          {item.dashed ? (
            <span className="text-ink-500">(limit)</span>
          ) : null}
        </span>
      ))}
    </div>
  )
}
