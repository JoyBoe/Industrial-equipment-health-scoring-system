/**
 * Shared dashboard constants.
 */

/**
 * Assessment window, in readings, used for fleet and detail views.
 *
 * This must match across every surface that reports a condition. When the
 * dashboard scored a 60-reading window while the alert engine evaluated 1440,
 * a machine with an open Critical vibration alert showed "Excellent, no action
 * required" on its own detail page -- the two views were measuring different
 * things and neither was wrong on its own.
 *
 * 1440 readings at the 1-minute ingest cadence is 24 hours, which spans a
 * full duty cycle.
 */
export const ASSESSMENT_WINDOW = 1440

/** Hours to pass to endpoints that take an hours-based window. */
export const ASSESSMENT_WINDOW_HOURS = 24

/** Poll interval for live-updating views, in milliseconds. */
export const POLL_INTERVAL_MS = 30000
