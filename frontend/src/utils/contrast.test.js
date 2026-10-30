import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import {
  RISK_STYLES,
  SEVERITY_STYLES,
  STATUS_ORDER,
  STATUS_STYLES,
} from './format'

/**
 * Contrast is invisible in a screenshot and easy to regress by nudging a hex
 * value, so the palette is asserted numerically here.
 *
 * Thresholds are WCAG 2.1: 4.5:1 for normal text, 3:1 for large text and for
 * the non-text boundaries that convey state.
 */

const here = dirname(fileURLToPath(import.meta.url))
const tailwindConfig = readFileSync(resolve(here, '../../tailwind.config.js'), 'utf8')

const HEX = /^#([0-9a-f]{6})$/i

function relativeLuminance(hex) {
  const match = HEX.exec(hex)
  if (!match) throw new Error(`Not a hex colour: ${hex}`)
  // Two hex digits per channel, at offsets 0, 2 and 4. Slicing a single digit
  // silently yields ~0.002 luminance for every colour, which would make every
  // assertion below meaningless.
  const channels = [0, 2, 4].map((i) => parseInt(match[1].slice(i, i + 2), 16) / 255)
  const [r, g, b] = channels.map((c) =>
    c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4,
  )
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function contrast(foreground, background) {
  const a = relativeLuminance(foreground)
  const b = relativeLuminance(background)
  const [hi, lo] = a > b ? [a, b] : [b, a]
  return (hi + 0.05) / (lo + 0.05)
}

/** Look a colour token up in the Tailwind config so the test tracks the real value. */
function ink(step) {
  const match = new RegExp(`${step}:\\s*'(#[0-9a-fA-F]{6})'`).exec(tailwindConfig)
  if (!match) throw new Error(`ink-${step} not found in tailwind.config.js`)
  return match[1]
}

const WHITE = '#ffffff'
const PAGE = '#f6f7f9'   // ink-50
const SURFACE = '#eceef1' // ink-100, table headers

const AA_TEXT = 4.5
const AA_LARGE_OR_NON_TEXT = 3

describe('ink palette contrast', () => {
  it.each([500, 600, 700, 800, 900])(
    'ink-%s meets AA for normal text on every surface it is used on',
    (step) => {
      for (const bg of [WHITE, PAGE, SURFACE]) {
        const ratio = contrast(ink(step), bg)
        expect(
          ratio,
          `ink-${step} on ${bg} is ${ratio.toFixed(2)}:1, below ${AA_TEXT}:1`,
        ).toBeGreaterThanOrEqual(AA_TEXT)
      }
    },
  )

  it('keeps ink-400 usable for non-text decoration only', () => {
    // ink-400 is 3.1:1 on white: fine for a border or a dot, not for text.
    const ratio = contrast(ink(400), WHITE)
    expect(ratio).toBeGreaterThanOrEqual(AA_LARGE_OR_NON_TEXT)
    expect(ratio).toBeLessThan(AA_TEXT)
  })

  it('documents the decorative-only role so it is not reused for text', () => {
    expect(tailwindConfig).toMatch(/decorative|non-text/i)
  })
})

describe('status contrast', () => {
  // Tailwind tint 50 is the light background; 800 the foreground.
  const TINTS = {
    emerald: ['#ecfdf5', '#065f46'],
    cyan: ['#ecfeff', '#155e75'],
    amber: ['#fffbeb', '#92400e'],
    orange: ['#fff7ed', '#9a3412'],
    red: ['#fef2f2', '#991b1b'],
    slate: ['#f1f5f9', '#475569'],
  }

  const BADGE_FOR = {
    Excellent: 'emerald',
    Healthy: 'cyan',
    Warning: 'amber',
    Poor: 'orange',
    Critical: 'red',
    Unknown: 'slate',
  }

  it.each(STATUS_ORDER)('%s badge text meets AA on its own background', (status) => {
    const [bg, fg] = TINTS[BADGE_FOR[status]]
    const ratio = contrast(fg, bg)
    expect(
      ratio,
      `${status} badge is ${ratio.toFixed(2)}:1, below ${AA_TEXT}:1`,
    ).toBeGreaterThanOrEqual(AA_TEXT)
  })

  it('keeps every status visually distinct', () => {
    const bars = STATUS_ORDER.map((s) => STATUS_STYLES[s].bar)
    expect(new Set(bars).size).toBe(STATUS_ORDER.length)
  })

  it('gives each risk level a distinct bar', () => {
    const bars = Object.values(RISK_STYLES).map((r) => r.bar)
    expect(new Set(bars).size).toBe(Object.keys(RISK_STYLES).length)
  })

  it('gives each severity a distinct style', () => {
    const cls = Object.values(SEVERITY_STYLES).map((s) => s.cls ?? s.text)
    expect(new Set(cls).size).toBe(Object.keys(SEVERITY_STYLES).length)
  })
})
