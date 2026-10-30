import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      // Proxy the API in development so the browser sees a single origin.
      // This also sidesteps CORS entirely while developing.
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
  test: {
    environment: 'jsdom',
    // jsdom defaults to http://localhost:3000, which the backend's CORS policy
    // does not allow, so every request from a page test would fail preflight
    // and surface as a misleading "cannot reach the API". Presenting the dev
    // server origin makes the browser-enforced policy match development.
    environmentOptions: {
      jsdom: { url: 'http://127.0.0.1:5173' },
    },
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    // Co-located tests live beside the code they exercise.
    include: ['src/**/*.{test,spec}.{js,jsx}'],
    // The chart library is heavy and mostly exercised through integration
    // tests; a slower environment is not worth it for these units.
    environmentMatchGlobs: [['**/charts/**', 'jsdom']],
    coverage: {
      reporter: ['text', 'html'],
      include: ['src/**/*.{js,jsx}'],
      exclude: ['src/main.jsx', 'src/test/**'],
    },
  },
})
