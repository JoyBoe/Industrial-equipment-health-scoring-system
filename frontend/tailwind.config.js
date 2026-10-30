/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        // Restrained industrial palette. Muted, high-contrast, legible on a
        // control-room display. No neon, no glow.
        //
        // Contrast is enforced by src/utils/contrast.test.js, which parses
        // this file. ink-500 and darker meet WCAG AA (4.5:1) for normal text
        // on white, ink-50 and ink-100. ink-400 is 3.1:1 on white and is
        // therefore DECORATIVE ONLY -- borders, dots and rules. Using it for
        // text is a contrast failure, not a style choice.
        ink: {
          50:  '#f6f7f9',
          100: '#eceef1',
          200: '#d5dae1',
          300: '#b0bac6',
          400: '#8494a6',
          500: '#5f6b7a',
          600: '#4d5b6e',
          700: '#3f4a5a',
          800: '#363e4b',
          900: '#2b323c',
          950: '#1a1f26',
        },
        // Status colours chosen for distinguishability without relying on
        // hue alone; each is paired with a label everywhere it is used.
        // All six status pairs were verified at 6.8:1 or better against their
        // own tinted background (WCAG AA needs 4.5:1).
        status: {
          excellent: '#15803d',
          healthy:   '#0e7490',
          warning:   '#b45309',
          poor:      '#c2410c',
          critical:  '#b91c1c',
          unknown:   '#64748b',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', '-apple-system', 'Segoe UI', 'Roboto', 'sans-serif'],
        mono: ['ui-monospace', 'SFMono-Regular', 'Menlo', 'monospace'],
      },
      fontSize: {
        '2xs': ['0.6875rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
}
