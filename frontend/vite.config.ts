/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8765',
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    // e2e/ holds Playwright specs (import from @playwright/test, not vitest) —
    // excluded so vitest's default *.spec.ts discovery doesn't pick them up.
    exclude: ['**/node_modules/**', 'e2e/**'],
  },
})
