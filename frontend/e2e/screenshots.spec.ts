import { test, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const OUT_DIR = path.resolve(__dirname, '../../docs/screenshots')
const FIXTURE_REPO = path.join(__dirname, 'fixtures', 'ts_repo')

async function snap(page: Page, filename: string) {
  await page.screenshot({
    path: path.join(OUT_DIR, filename),
    fullPage: true,
    animations: 'disabled',
  })
}

test.describe('capture README screenshots', () => {
  test.skip(!!process.env.CI, 'Run locally to regenerate README screenshots')

  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 1280, height: 900 })
  })

  test('translate form', async ({ page }) => {
    await page.goto('/')
    await page.getByLabel('Input path').fill(FIXTURE_REPO)
    await page.getByLabel('From language').selectOption('typescript')
    await page.getByLabel('To language').selectOption('python')
    await page.getByLabel('Provider').selectOption('offline')

    await snap(page, 'translate-form.png')
  })

  test('cost estimate', async ({ page }) => {
    await page.goto('/')
    await page.getByLabel('Input path').fill(FIXTURE_REPO)
    await page.getByLabel('From language').selectOption('typescript')
    await page.getByLabel('To language').selectOption('python')
    await page.getByLabel('Provider').selectOption('offline')
    await page.getByLabel('Translate manifests').uncheck()

    await page.getByRole('button', { name: 'Estimate cost' }).click()
    await page.getByText('typescript → python via offline').waitFor()

    await snap(page, 'cost-estimate.png')
  })

  test('report and output browser', async ({ page }) => {
    await page.goto('/')
    await page.getByLabel('Input path').fill(FIXTURE_REPO)
    await page.getByLabel('From language').selectOption('typescript')
    await page.getByLabel('To language').selectOption('python')
    await page.getByLabel('Provider').selectOption('offline')
    await page.getByLabel('Translate manifests').uncheck()

    await page.getByRole('button', { name: 'Estimate cost' }).click()
    await page.getByText('typescript → python via offline').waitFor()
    await page.getByRole('button', { name: 'Confirm & start' }).click()
    await page.getByRole('heading', { name: 'Report' }).waitFor({ timeout: 30_000 })

    await snap(page, 'report.png')

    await page.getByRole('button', { name: 'main.py' }).click()
    await page.getByText('Source', { exact: true }).waitFor()

    await snap(page, 'output-browser.png')

    await page.getByRole('button', { name: 'History' }).click()
    const row = page.locator('tr', { hasText: FIXTURE_REPO }).first()
    await row.waitFor()

    await snap(page, 'history.png')
  })
})
