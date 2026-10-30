/**
 * Data-fetching hooks.
 *
 * `useApi` is the only place that knows about loading and error state, so no
 * component reimplements it (and therefore none of them can forget the abort
 * cleanup that would otherwise leak requests on every filter change).
 */

import { useCallback, useEffect, useRef, useState } from 'react'
import { ApiError } from '../services/api'

/**
 * Run a fetcher and expose { data, error, loading, refresh }.
 *
 * @param fetcher function receiving an AbortSignal, returning a promise
 * @param deps    dependency array; the fetch re-runs when this changes
 * @param options { enabled, pollMs }
 */
export function useApi(fetcher, deps = [], options = {}) {
  const { enabled = true, pollMs = 0 } = options
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(enabled)
  const [fetchedAt, setFetchedAt] = useState(null)

  // Keep the latest fetcher in a ref so changing it does not retrigger the
  // effect; callers pass inline closures on every render.
  const fetcherRef = useRef(fetcher)
  fetcherRef.current = fetcher

  const run = useCallback(async (signal) => {
    try {
      const result = await fetcherRef.current(signal)
      if (signal?.aborted) return
      setData(result)
      setError(null)
      setFetchedAt(new Date())
    } catch (err) {
      if (signal?.aborted || err.name === 'AbortError') return
      setError(err)
    } finally {
      if (!signal?.aborted) setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) {
      setLoading(false)
      return undefined
    }

    const controller = new AbortController()
    setLoading(true)
    run(controller.signal)

    let timer = null
    if (pollMs > 0) {
      timer = setInterval(() => run(controller.signal), pollMs)
    }

    return () => {
      controller.abort()
      if (timer) clearInterval(timer)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, pollMs])

  const refresh = useCallback(() => {
    const controller = new AbortController()
    setLoading(true)
    return run(controller.signal)
  }, [run])

  return { data, error, loading, refresh, fetchedAt }
}

/**
 * Debounce a rapidly changing value, for search boxes and range sliders.
 */
export function useDebounced(value, delayMs = 300) {
  const [debounced, setDebounced] = useState(value)

  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delayMs)
    return () => clearTimeout(timer)
  }, [value, delayMs])

  return debounced
}

/** Human-readable message for any thrown value. */
export function describeError(error) {
  if (!error) return null
  if (error instanceof ApiError) return error.message
  if (error.name === 'AbortError') return null
  return error.message || 'Unexpected error'
}
