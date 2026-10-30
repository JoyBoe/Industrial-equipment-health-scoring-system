import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../services/api'
import { describeError, useApi, useDebounced } from './useApi'

/**
 * `useApi` owns request lifecycle for every view. Its error handling in
 * particular decides whether the operator sees a stale figure or a truthful
 * "cannot reach the API" banner.
 */

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true })
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useApi', () => {
  it('starts loading and then resolves', async () => {
    const { result } = renderHook(() => useApi(async () => ({ value: 1 }), []))

    expect(result.current.loading).toBe(true)

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.data).toEqual({ value: 1 })
    expect(result.current.error).toBeNull()
  })

  it('captures an error and stops loading', async () => {
    const error = new ApiError('boom', 500)
    const { result } = renderHook(() =>
      useApi(async () => {
        throw error
      }, []),
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(result.current.error).toBe(error)
  })

  it('re-fetches when dependencies change', async () => {
    const fetcher = vi.fn(async (signal) => signal)
    const { result, rerender } = renderHook(
      ({ id }) => useApi(() => fetcher(id), [id]),
      { initialProps: { id: 'a' } },
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(1)

    rerender({ id: 'b' })
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  })

  it('does not re-fetch when unrelated props change', async () => {
    // A new inline closure is created on every render, so the fetcher must be
    // held in a ref. Were it a plain dependency, any state change in the
    // component would re-issue the request.
    const fetcher = vi.fn(async () => 'ok')
    const { result, rerender } = renderHook(
      // `unrelated` is never read: it exists only to force a rerender.
      ({ unrelated: _unrelated }) => useApi(() => fetcher(), ['stable']),
      { initialProps: { unrelated: 1 } },
    )

    await waitFor(() => expect(result.current.loading).toBe(false))
    rerender({ unrelated: 2 })
    rerender({ unrelated: 3 })

    expect(fetcher).toHaveBeenCalledTimes(1)
    expect(result.current.data).toBe('ok')
  })

  it('re-fetches when a declared dependency changes', async () => {
    const fetcher = vi.fn(async (id) => id)
    const { rerender } = renderHook(
      ({ id }) => useApi(() => fetcher(id), [id]),
      { initialProps: { id: 'a' } },
    )

    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
    rerender({ id: 'b' })
    await waitFor(() => expect(fetcher).toHaveBeenCalledTimes(2))
  })

  it('aborts the in-flight request on unmount', async () => {
    // Without this, every filter change leaks a request and a late response
    // can overwrite fresher data.
    let capturedSignal = null
    const { result, unmount } = renderHook(() =>
      useApi(async (signal) => {
        capturedSignal = signal
        return new Promise(() => {})  // never resolves
      }, []),
    )

    expect(result.current.loading).toBe(true)
    expect(capturedSignal).not.toBeNull()

    unmount()
    expect(capturedSignal.aborted).toBe(true)
  })

  it('aborts the previous request when dependencies change', async () => {
    const signals = []
    const { rerender } = renderHook(
      ({ id }) =>
        useApi(
          (signal) => {
            signals.push(signal)
            return new Promise(() => {})
          },
          [id],
        ),
      { initialProps: { id: 1 } },
    )

    rerender({ id: 2 })

    expect(signals[0].aborted).toBe(true)
    expect(signals[1].aborted).toBe(false)
  })

  it('does not set state from an aborted request', async () => {
    // An abort must not clear the error state or flip loading, or a cancelled
    // request would look like a failure.
    const { result, unmount } = renderHook(() =>
      useApi(
        (signal) =>
          new Promise((_, reject) => {
            signal.addEventListener('abort', () => {
              const abort = new Error('aborted')
              abort.name = 'AbortError'
              reject(abort)
            })
          }),
        [],
      ),
    )

    unmount()
    await act(async () => {})
    expect(result.current.error).toBeNull()
  })

  it('supports refresh', async () => {
    const fetcher = vi.fn(async () => 'value')
    const { result } = renderHook(() => useApi(fetcher, []))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(1)

    await act(async () => {
      await result.current.refresh()
    })
    expect(fetcher).toHaveBeenCalledTimes(2)
  })

  it('does not fetch when disabled', async () => {
    const fetcher = vi.fn(async () => 'value')
    const { result } = renderHook(() => useApi(fetcher, [], { enabled: false }))

    expect(fetcher).not.toHaveBeenCalled()
    expect(result.current.loading).toBe(false)
  })

  it('polls on an interval and stops on unmount', async () => {
    const fetcher = vi.fn(async () => 'value')
    const { result, unmount } = renderHook(() => useApi(fetcher, [], { pollMs: 1000 }))

    await waitFor(() => expect(result.current.loading).toBe(false))
    expect(fetcher).toHaveBeenCalledTimes(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2500)
    })
    expect(fetcher.mock.calls.length).toBeGreaterThan(1)

    const countAtUnmount = fetcher.mock.calls.length
    unmount()
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    expect(fetcher.mock.calls.length).toBe(countAtUnmount)
    expect(result.current.error).toBeNull()
  })

  it('records when data was fetched', async () => {
    const { result } = renderHook(() => useApi(async () => 'ok', []))

    await waitFor(() => expect(result.current.fetchedAt).not.toBeNull())
  })
})

describe('describeError', () => {
  it('returns null for no error', () => {
    expect(describeError(null)).toBeNull()
  })

  it('returns the message from an ApiError', () => {
    expect(describeError(new ApiError('Server exploded', 500))).toBe('Server exploded')
  })

  it('returns null for an abort', () => {
    const abort = new Error('aborted')
    abort.name = 'AbortError'
    expect(describeError(abort)).toBeNull()
  })

  it('handles a message-less error', () => {
    expect(describeError({})).toBe('Unexpected error')
  })
})

describe('useDebounced', () => {
  it('delays propagation of a fast-changing value', async () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounced(value, 300),
      { initialProps: { value: 'a' } },
    )

    expect(result.current).toBe('a')

    rerender({ value: 'ab' })
    rerender({ value: 'abc' })
    expect(result.current).toBe('a')

    await act(async () => {
      await vi.advanceTimersByTimeAsync(400)
    })
    expect(result.current).toBe('abc')
  })

  it('settles on the last value only', async () => {
    const { result, rerender } = renderHook(
      ({ value }) => useDebounced(value, 200),
      { initialProps: { value: 1 } },
    )

    rerender({ value: 2 })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })
    rerender({ value: 3 })
    await act(async () => {
      await vi.advanceTimersByTimeAsync(100)
    })

    // The timer restarted, so 2 must never have been emitted.
    expect(result.current).toBe(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(200)
    })
    expect(result.current).toBe(3)
  })
})
