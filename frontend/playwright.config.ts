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
    baseURL: 'http://127.0.0.1:5173',
    trace: 'retain-on-failure',
  },
  webServer: [
    {
      command: 'python -m uvicorn repo_translator.webui.main:app --host 127.0.0.1 --port 8765',
      cwd: path.join(__dirname, '..'),
      url: 'http://127.0.0.1:8765/api/languages',
      reuseExistingServer: false,
      env: { REPO_TRANSLATOR_DATA_DIR: E2E_DATA_DIR },
    },
    {
      command: 'npm run dev -- --port 5173 --strictPort',
      cwd: __dirname,
      url: 'http://127.0.0.1:5173',
      reuseExistingServer: false,
    },
  ],
})
