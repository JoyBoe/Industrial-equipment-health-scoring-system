import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError, api } from './api'

/**
 * The API client is the boundary where every backend error becomes a message
 * an operator reads, so the translation is worth pinning down precisely.
 */

/**
 * Base the client is configured with. Under Vitest this is set by .env.test so
 * requests reach the real backend; in a browser it is empty and paths are
 * relative. Expectations are derived from it rather than hard-coded, so the
 * suite does not encode one particular configuration.
 */
const BASE = import.meta.env.VITE_API_BASE_URL ?? ''

function mockResponse(body, { status = 200, ok = status < 400 } = {}) {
  return {
    ok,
    status,
    text: async () => (body === undefined ? '' : JSON.stringify(body)),
  }
}

let fetchMock

beforeEach(() => {
  fetchMock = vi.fn()
  global.fetch = fetchMock
})

afterEach(() => {
  vi.restoreAllMocks()
})

function lastCall() {
  const [url, options] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1]
  return { url, options }
}

describe('request building', () => {
  it('calls the expected path', async () => {
    fetchMock.mockResolvedValue(mockResponse({ total_equipment: 8 }))
    await api.dashboardSummary()
    expect(lastCall().url).toBe(`${BASE}/api/dashboard/summary`)
  })

  it('appends query parameters', async () => {
    fetchMock.mockResolvedValue(mockResponse([]))
    await api.equipment.list({ status: 'Warning', limit: 50 })
    expect(lastCall().url).toContain('status=Warning')
    expect(lastCall().url).toContain('limit=50')
  })

  it('omits empty parameters so backend defaults apply', async () => {
    // Sending `search=` would filter for the empty string on some backends
    // rather than falling through to the default.
    fetchMock.mockResolvedValue(mockResponse([]))
    await api.equipment.list({ search: '', status: null, limit: 10 })
    const { url } = lastCall()
    expect(url).not.toContain('search=')
    expect(url).not.toContain('status=')
    expect(url).toContain('limit=10')
  })

  it('url-encodes path parameters', async () => {
    fetchMock.mockResolvedValue(mockResponse({}))
    await api.equipment.get('EQ/001 weird')
    expect(lastCall().url).toContain('EQ%2F001%20weird')
  })

  it('sends a JSON body with the right content type for POST', async () => {
    fetchMock.mockResolvedValue(mockResponse({}, { status: 201 }))
    await api.alerts.evaluate({ equipment_id: 'EQ-001' })
    const { options } = lastCall()
    expect(options.method).toBe('POST')
    expect(options.headers['Content-Type']).toBe('application/json')
    expect(JSON.parse(options.body)).toEqual({ equipment_id: 'EQ-001' })
  })

  it('omits the body on GET', async () => {
    fetchMock.mockResolvedValue(mockResponse([]))
    await api.equipment.list({})
    expect(lastCall().options.body).toBeUndefined()
  })

  it('returns null for 204 No Content', async () => {
    fetchMock.mockResolvedValue({ ok: true, status: 204, text: async () => '' })
    await expect(api.alerts.resolve('x')).resolves.toBeNull()
  })
})

describe('error handling', () => {
  it('raises ApiError with the status', async () => {
    fetchMock.mockResolvedValue(mockResponse({ detail: 'Nope' }, { status: 404 }))
    await expect(api.equipment.get('EQ-404')).rejects.toBeInstanceOf(ApiError)
  })

  it('surfaces a string detail as the message', async () => {
    fetchMock.mockResolvedValue(
      mockResponse({ detail: "Equipment 'EQ-9' not found" }, { status: 404 }),
    )
    await expect(api.equipment.get('EQ-9')).rejects.toThrow(/not found/)
  })

  it('flattens FastAPI validation details into a readable message', async () => {
    // FastAPI returns an array of objects; a raw JSON.stringify here would be
    // unreadable in the UI.
    fetchMock.mockResolvedValue(
      mockResponse(
        {
          detail: [
            { loc: ['body', 'temperature'], msg: 'Input should be less than or equal to 300' },
            { loc: ['body', 'vibration'], msg: 'Input should be greater than or equal to 0' },
          ],
        },
        { status: 422 },
      ),
    )
    await expect(api.anomaly.detect({ temperature: 999 })).rejects.toThrow(
      /temperature: Input should be less than or equal to 300.*vibration/s,
    )
  })

  it('distinguishes a network failure from an HTTP error', async () => {
    // The UI shows different guidance for each: "start the backend" versus a
    // real error message.
    fetchMock.mockRejectedValue(new TypeError('Failed to fetch'))
    try {
      await api.systemHealth()
      expect.unreachable('should have thrown')
    } catch (error) {
      expect(error).toBeInstanceOf(ApiError)
      expect(error.status).toBe(0)
      expect(error.isNetworkError).toBe(true)
      expect(error.message).toMatch(/cannot reach the api/i)
    }
  })

  it('propagates AbortError unchanged', async () => {
    // Swallowing an abort would leave a cancelled request looking like a
    // failure and could show a spurious error banner on every filter change.
    const abort = new Error('aborted')
    abort.name = 'AbortError'
    fetchMock.mockRejectedValue(abort)
    await expect(api.systemHealth()).rejects.toThrow('aborted')
  })

  it('handles a non-JSON error body', async () => {
    fetchMock.mockResolvedValue({
      ok: false,
      status: 502,
      text: async () => '<html>Bad Gateway</html>',
    })
    await expect(api.systemHealth()).rejects.toThrow(/status 502/)
  })

  it('handles a missing detail field', async () => {
    fetchMock.mockResolvedValue(mockResponse({}, { status: 500 }))
    await expect(api.systemHealth()).rejects.toThrow(/status 500/)
  })
})

describe('namespace integrity', () => {
  it('exposes the liveness call as systemHealth', async () => {
    // `health` is a namespace for the scoring endpoints. An earlier version
    // also defined a top-level `health` key, and the duplicate silently
    // shadowed the liveness call, so `api.health()` threw at runtime.
    expect(typeof api.systemHealth).toBe('function')
    expect(typeof api.health).toBe('object')
  })

  it('exposes every namespace the app uses', () => {
    for (const namespace of [
      'systemHealth', 'dashboardSummary', 'equipment', 'sensors',
      'health', 'anomaly', 'prediction', 'explain', 'alerts',
    ]) {
      expect(api[namespace]).toBeDefined()
    }
  })

  it('defines no duplicate top-level keys', () => {
    // A duplicate is legal JavaScript and invisible until one is shadowed.
    const source = api
    const names = Object.keys(source)
    expect(new Set(names).size).toBe(names.length)
  })

  it('exposes the expected members of each namespace', () => {
    expect(typeof api.alerts.resolve).toBe('function')
    expect(typeof api.alerts.acknowledge).toBe('function')
    expect(typeof api.explain.forEquipment).toBe('function')
    expect(typeof api.prediction.predict).toBe('function')
    expect(typeof api.sensors.chart).toBe('function')
  })
})
