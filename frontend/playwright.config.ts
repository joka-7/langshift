import { defineConfig } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const E2E_DATA_DIR = path.join(__dirname, 'e2e', '.data')

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  reporter: process.env.CI ? [['list'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: 'http://127.0.0.1:8765',
    trace: 'retain-on-failure',
  },
  // Build the SPA, then serve it from the FastAPI process itself (main.py mounts
  // frontend/dist at /). A single same-origin server is far more robust in CI than
  // running the Vite dev server and the API as two separate webServers, and it
  // exercises the actual production build. `python`/uvicorn import the editable-
  // installed repo_translator package, so cwd here only matters for `npm run build`.
  webServer: {
    command: 'npm run build && python -m uvicorn repo_translator.webui.main:app --host 127.0.0.1 --port 8765',
    cwd: __dirname,
    url: 'http://127.0.0.1:8765/api/languages',
    reuseExistingServer: false,
    timeout: 120_000,
    env: { REPO_TRANSLATOR_DATA_DIR: E2E_DATA_DIR },
  },
})
