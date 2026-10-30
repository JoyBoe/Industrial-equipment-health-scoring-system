/**
 * Test setup.
 *
 * jsdom does not implement `matchMedia`, which `prefers-reduced-motion`
 * queries and Recharts both rely on. Stubbed here rather than in individual
 * tests so a missing stub surfaces once, not as a confusing failure in
 * whichever component happened to touch it first.
 */

import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

if (!window.matchMedia) {
  window.matchMedia = (query) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  })
}

// Recharts measures its container; jsdom reports zero for everything, which
// makes ResponsiveContainer render nothing and silently hides chart bugs.
// ResizingObserver is the specific missing API.
if (!global.ResizeObserver) {
  global.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  }
}

/**
 * jsdom and Node's `fetch` come from different implementations, so the
 * `AbortSignal` a component creates is not an instance of the one Node's
 * fetch requires. Every real request through the API client then fails with
 * "Expected signal to be an instance of AbortSignal", and the page renders a
 * misleading "cannot reach the API" banner even though the backend is fine.
 *
 * A Node AbortSignal cannot be constructed from inside jsdom, so the signal is
 * dropped at this boundary. That is a test-environment concession only:
 * cancellation itself is covered directly in useApi.test.js, which mocks the
 * transport and so exercises the abort path properly. Production code passes
 * the signal through untouched.
 */
const nodeFetch = globalThis.fetch?.bind(globalThis)

if (nodeFetch) {
  globalThis.fetch = (input, init = {}) => {
    // eslint-disable-next-line no-unused-vars
    const { signal: _signal, ...rest } = init ?? {}
    return nodeFetch(input, rest)
  }
}
