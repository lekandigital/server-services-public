import { test, expect, type Page } from '@playwright/test'

const BASE = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:8004'
const sections = ['Dashboard', 'Drive / Files', 'Recent', 'Starred', 'Shared', 'Torrents', 'Queue', 'Playlists', 'Storage', 'Trash', 'Activity', 'Settings', 'Diagnostics']

async function nav(page: Page, label: string) {
  await page.locator('.sidebar-nav').getByRole('button', { name: label, exact: true }).click()
}

test.beforeEach(async ({ page }) => {
  await page.addInitScript(() => {
    localStorage.removeItem('cm_section')
    localStorage.setItem('cm_theme', 'light')
    localStorage.setItem('cm_library_path', '/home/REDACTED_USER/watch_list')
  })
})

test.describe('Cast Manager light UI reliability', () => {
  test('loads in light mode with every required navigation section and no console errors', async ({ page }) => {
    const consoleErrors: string[] = []
    const pageErrors: string[] = []
    page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
    page.on('pageerror', (error) => pageErrors.push(error.message))
    await page.goto(BASE, { waitUntil: 'domcontentloaded' })
    await expect(page.locator('.sidebar-brand')).toContainText('Cast Manager')
    await expect(page.locator('html')).toHaveCSS('color-scheme', 'light')
    for (const label of sections) await expect(page.locator('.sidebar-nav').getByRole('button', { name: label, exact: true })).toBeVisible()
    await page.waitForTimeout(1200)
    expect(consoleErrors, `console errors: ${consoleErrors.join('\n')}`).toEqual([])
    expect(pageErrors, `page errors: ${pageErrors.join('\n')}`).toEqual([])
  })

  test('every nav section renders a useful page', async ({ page }) => {
    await page.goto(BASE)
    for (const label of sections.slice(1)) {
      await nav(page, label)
      await expect(page.locator('.app-content')).toContainText(label === 'Drive / Files' ? 'Drive' : label, { ignoreCase: true })
      await expect(page.locator('.app-content')).not.toContainText('<!DOCTYPE html>')
    }
  })

  test('Library opens the configured media root, loads files, and switches list/grid', async ({ page }) => {
    await page.route('**/api/thumbnail', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ thumbnail: null, status: 'unavailable', reason: 'test' }) }))
    await page.goto(BASE)
    await nav(page, 'Media Library')
    await expect(page.getByTestId('library-page')).toBeVisible()
    await expect(page.locator('.breadcrumbs')).toContainText('Home')
    await expect(page.locator('.file-table-wrap, .friendly-empty').first()).toBeVisible({ timeout: 20000 })
    expect(await page.evaluate(() => localStorage.getItem('cm_library_path'))).toBe('/home/REDACTED_USER/watch_list')
    await page.getByRole('button', { name: 'Grid', exact: true }).click()
    await expect(page.locator('.file-grid, .friendly-empty').first()).toBeVisible()
    await page.getByRole('button', { name: 'List', exact: true }).click()
  })

  test('Library opens a media preview and cast panel when a video is available', async ({ page }) => {
    await page.route('**/api/thumbnail', async (route) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ thumbnail: null, status: 'unavailable' }) }))
    await page.goto(BASE)
    await nav(page, 'Media Library')
    await page.getByLabel('Filter by type').selectOption('video')
    const firstPreview = page.getByRole('button', { name: 'Preview', exact: true }).first()
    if (await firstPreview.isVisible({ timeout: 8000 }).catch(() => false)) {
      await firstPreview.click()
      await expect(page.getByRole('dialog', { name: 'File details' })).toBeVisible()
      await expect(page.getByRole('dialog', { name: 'File details' })).toContainText(/Browser playback|Metadata|Analyzing|checking/i)
      await page.getByRole('dialog', { name: 'File details' }).getByRole('button', { name: 'Cast', exact: true }).click()
      await expect(page.getByRole('dialog', { name: 'Cast setup' })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Analyze & preflight' })).toBeVisible()
      await expect(page.getByRole('button', { name: 'Start cast' })).toBeVisible()
      await page.getByRole('button', { name: 'Close cast panel' }).click()
    }
  })

  test('Quick Cast analyzes the regression embed and explains why it is unsupported', async ({ page }) => {
    await page.goto(BASE)
    const url = 'https://ntvs.cx/embed?t=U3kvVGsxU1RSbFNVNzYraHQ0eDVFWktGeGVzQkNwSHdpdVRML1F2aWtWSTBIc21HY2FsQ2UyTjZwOHpTUnd5ZnROVEdUTS9oR0xWWHliUGp4WXZ2YlE9PQ~~'
    await page.getByLabel('Quick Cast URL').fill(url)
    await page.getByRole('button', { name: 'Analyze URL' }).click()
    await expect(page.getByTestId('quick-cast-card')).toContainText('HTML embed page', { ignoreCase: true })
    await expect(page.getByTestId('quick-cast-card')).toContainText(/will not bypass logins, DRM, cookies, captchas/i)
    await expect(page.getByRole('button', { name: 'Cast URL' })).toBeDisabled()
  })

  test('Torrents, Settings, and Diagnostics expose reliable primary controls', async ({ page }) => {
    await page.goto(BASE)
    await nav(page, 'Torrents')
    await expect(page.getByTestId('torrents-page')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Choose .torrent file' })).toBeVisible()
    await expect(page.getByRole('button', { name: /Add magnet/ })).toBeDisabled()
    await nav(page, 'Settings')
    await expect(page.getByTestId('settings-page')).toBeVisible()
    await expect(page.getByLabel('Theme')).toHaveValue('light')
    await page.getByLabel('Default Library view').selectOption('grid')
    await page.getByRole('button', { name: 'Save settings' }).first().click()
    expect(await page.evaluate(() => localStorage.getItem('cm_view_mode'))).toBe('grid')
    await nav(page, 'Diagnostics')
    await expect(page.getByTestId('diagnostics-page')).toBeVisible()
    await expect(page.getByRole('button', { name: 'Copy debug info' })).toBeVisible()
    await expect(page.locator('.app-content')).not.toContainText('<html')
  })

  test('all visible buttons have a label and disabled controls explain why', async ({ page }) => {
    await page.goto(BASE)
    for (const label of sections) {
      if (label !== 'Dashboard') await nav(page, label)
      const buttons = page.locator('.app-content button:visible')
      for (let i = 0; i < await buttons.count(); i++) {
        const button = buttons.nth(i)
        const accessibleName = (await button.getAttribute('aria-label')) || (await button.innerText()).trim()
        expect(accessibleName, `${label} button ${i} has no accessible label`).not.toBe('')
        if (await button.isDisabled()) {
          const title = await button.getAttribute('title')
          expect(title, `${label} disabled button “${accessibleName}” has no reason`).toBeTruthy()
        }
      }
    }
  })
})
