import { test, expect, type Page } from '@playwright/test'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const FIXTURE_REPO = path.join(__dirname, 'fixtures', 'ts_repo')

async function runOfflineTranslation(page: Page) {
  await page.goto('/')

  await page.getByLabel('Input path').fill(FIXTURE_REPO)
  await page.getByLabel('From language').selectOption('typescript')
  await page.getByLabel('To language').selectOption('python')
  await page.getByLabel('Provider').selectOption('offline')
  await page.getByLabel('Translate manifests').uncheck()

  await page.getByRole('button', { name: 'Estimate cost' }).click()
  await expect(page.getByText('Cost estimate')).toBeVisible()
  await expect(page.getByText('typescript → python via offline')).toBeVisible()

  await page.getByRole('button', { name: 'Confirm & start' }).click()
  // A one-file offline translation can finish before this assertion runs, so
  // requiring the in-flight state makes the test lose a race it never needed to
  // enter. What matters is that the click started a run: accept either the
  // progress view or the report it turns into.
  await expect(
    page.getByText('Translating...').or(page.getByRole('heading', { name: 'Report' })),
  ).toBeVisible()

  await expect(page.getByRole('heading', { name: 'Report' })).toBeVisible({ timeout: 30_000 })
}

test.describe('end-to-end translation flow', () => {
  test('estimates, runs an offline translation, and browses the output', async ({ page }) => {
    await runOfflineTranslation(page)

    const reportView = page.locator('.report-view')
    await expect(reportView.getByText('Translated', { exact: true })).toBeVisible()
    await expect(reportView.locator('.stat', { hasText: 'Translated' }).locator('.stat-value')).toHaveText('1')
    await expect(reportView.locator('.stat', { hasText: 'Failed' }).locator('.stat-value')).toHaveText('0')

    await page.getByRole('button', { name: 'main.py' }).click()
    await expect(page.getByText('Source', { exact: true })).toBeVisible()
    await expect(page.getByText('Translated', { exact: true }).last()).toBeVisible()
    await expect(page.locator('.file-view-panes')).toContainText('def add')
  })

  test('persists the run to history and reopens its report', async ({ page }) => {
    await runOfflineTranslation(page)

    await page.getByRole('button', { name: 'History' }).click()
    const row = page.locator('tr', { hasText: FIXTURE_REPO }).first()
    await expect(row).toBeVisible()
    await expect(row).toContainText('typescript → python')
    await expect(row).toContainText('offline')

    await row.getByRole('button', { name: 'View' }).click()
    await expect(page.getByRole('heading', { name: 'Output' })).toBeVisible()
  })
})
