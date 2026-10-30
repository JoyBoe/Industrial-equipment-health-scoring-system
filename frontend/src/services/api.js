/**
 * API client.
 *
 * A thin wrapper over fetch that normalises errors into a single shape. Every
 * call site gets `{ data, error }` semantics via thrown ApiError, so no
 * component has to reason about Response objects.
 */

const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  constructor(message, status, detail) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }

  /** True when the request failed because the server was unreachable. */
  get isNetworkError() {
    return this.status === 0
  }
}

function buildUrl(path, params) {
  const url = `${BASE}${path}`
  if (!params) return url
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    // Skip null/undefined/empty so the backend applies its own defaults
    // rather than receiving an empty string it would treat as a filter.
    if (value === null || value === undefined || value === '') return
    search.append(key, value)
  })
  const qs = search.toString()
  return qs ? `${url}?${qs}` : url
}

async function request(path, { params, method = 'GET', body, signal } = {}) {
  const url = buildUrl(path, params)

  let response
  try {
    response = await fetch(url, {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (cause) {
    if (cause.name === 'AbortError') throw cause
    throw new ApiError(
      'Cannot reach the API. Check that the backend is running.',
      0,
      String(cause),
    )
  }

  if (response.status === 204) return null

  const text = await response.text()
  let payload = null
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      payload = text
    }
  }

  if (!response.ok) {
    // FastAPI reports validation problems as a list of objects; a raw
    // stringify would be unreadable in the UI.
    const detail = payload?.detail
    let message
    if (typeof detail === 'string') {
      message = detail
    } else if (Array.isArray(detail)) {
      message = detail
        .map((d) => `${(d.loc ?? []).slice(1).join('.')}: ${d.msg}`)
        .join('; ')
    } else {
      message = `Request failed with status ${response.status}`
    }
    throw new ApiError(message, response.status, detail)
  }

  return payload
}

export const api = {
  // Named `systemHealth` rather than `health`: a `health` namespace also
  // exists below, and a duplicate key silently shadowed the liveness call.
  systemHealth: (opts) => request('/api/health', opts),
  dashboardSummary: (opts) => request('/api/dashboard/summary', opts),

  equipment: {
    list: (params, opts) => request('/api/equipment', { ...opts, params }),
    get: (id, opts) => request(`/api/equipment/${encodeURIComponent(id)}`, opts),
    filterOptions: (opts) => request('/api/equipment/filter-options', opts),
  },

  sensors: {
    channels: (opts) => request('/api/sensor/channels', opts),
    history: (id, params, opts) =>
      request(`/api/sensor/readings/${encodeURIComponent(id)}`, { ...opts, params }),
    chart: (id, params, opts) =>
      request(`/api/sensor/readings/${encodeURIComponent(id)}/chart`, { ...opts, params }),
    latest: (id, opts) =>
      request(`/api/sensor/readings/${encodeURIComponent(id)}/latest`, opts),
  },

  health: {
    current: (id, opts) =>
      request(`/api/equipment/${encodeURIComponent(id)}/health`, opts),
    trend: (id, params, opts) =>
      request(`/api/equipment/${encodeURIComponent(id)}/health/trend`, { ...opts, params }),
    assess: (id, params, opts) =>
      request(`/api/equipment/${encodeURIComponent(id)}/health/assess`, {
        ...opts,
        method: 'POST',
        params,
      }),
    scoreAll: (params, opts) =>
      request('/api/health/score-all', { ...opts, method: 'POST', params }),
    thresholds: (opts) => request('/api/health/thresholds', opts),
  },

  anomaly: {
    detect: (body, opts) =>
      request('/api/anomaly/detect', { ...opts, method: 'POST', body }),
    history: (id, params, opts) =>
      request(`/api/anomaly/${encodeURIComponent(id)}`, { ...opts, params }),
    summary: (id, opts) =>
      request(`/api/anomaly/${encodeURIComponent(id)}/summary`, opts),
  },

  prediction: {
    predict: (body, opts) => request('/api/predict', { ...opts, method: 'POST', body }),
    forEquipment: (id, params, opts) =>
      request(`/api/equipment/${encodeURIComponent(id)}/risk`, { ...opts, params }),
    modelInfo: (opts) => request('/api/predict/model-info', opts),
  },

  explain: {
    forValues: (body, opts) => request('/api/explain', { ...opts, method: 'POST', body }),
    forEquipment: (id, params, opts) =>
      request(`/api/equipment/${encodeURIComponent(id)}/explain`, { ...opts, params }),
  },

  alerts: {
    list: (params, opts) => request('/api/alerts', { ...opts, params }),
    summary: (opts) => request('/api/alerts/summary', opts),
    evaluate: (body, opts) =>
      request('/api/alerts/evaluate', { ...opts, method: 'POST', body }),
    resolve: (id, note, opts) =>
      request(`/api/alerts/${encodeURIComponent(id)}/resolve`, {
        ...opts,
        method: 'POST',
        body: note ? { note } : {},
      }),
    acknowledge: (id, opts) =>
      request(`/api/alerts/${encodeURIComponent(id)}/acknowledge`, {
        ...opts,
        method: 'POST',
      }),
  },
}
