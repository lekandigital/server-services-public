import { test, expect, type Route } from '@playwright/test'

// Runs against the deployed app (set CAST_MANAGER_URL) but mocks the API so we
// can simulate a SLOW HLS restart-seek and prove the scrubber handle holds at
// the requested target instead of snapping back to the stale receiver position.
const BASE = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:8004'

test('scrubber holds the target during a slow HLS restart-seek (no snap-back)', async ({ page }) => {
  let serverTime = 100 // receiver-reported position; only catches up AFTER the restart
  const RESTART_MS = 2000

  await page.route('**/api/**', async (route: Route) => {
    const req = route.request()
    const p = new URL(req.url()).pathname
    let body: unknown = { success: true }
    if (p === '/api/config') body = { mediaRoot: '/x', serverUrl: BASE, features: { hls: true, vlc: true, castDoctor: true, diagnostics: true } }
    else if (p === '/api/files') body = { path: '/x', root: '/x', files: [] }
    else if (p === '/api/cast/devices') body = { devices: [{ provider: 'chromecast', deviceId: 'tv', name: 'REDACTED_DEVICE', selected: true }] }
    else if (p === '/api/cast/status') body = { activeSession: true, state: 'playing', currentTime: serverTime, duration: 7200, title: 'HoTD', deviceName: 'REDACTED_DEVICE', backend: 'hls', session: { sessionId: 's', title: 'HoTD', duration: 7200 } }
    else if (p === '/api/cast/controls' && req.method() === 'POST') {
      const post = (req.postDataJSON?.() || {}) as { action?: string; value?: number }
      if (post.action === 'seek') {
        const target = Number(post.value)
        await new Promise((r) => setTimeout(r, RESTART_MS)) // re-transcode from offset
        serverTime = target
        body = { success: true, state: 'playing', currentTime: target, fallbackUsed: true, verified: true }
      }
    }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })

  await page.addInitScript(() => localStorage.clear())
  await page.goto(`${BASE}/app/`)

  const bar = page.getByTestId('now-playing-bar')
  await expect(bar).toBeVisible({ timeout: 15000 })
  const scrubber = bar.locator('input.scrubber').first()
  await expect(scrubber).toHaveJSProperty('value', '100')

  // Drag to 3600 (60:00) and release.
  await scrubber.evaluate((el: HTMLInputElement) => {
    el.value = '3600'
    el.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await scrubber.evaluate((el: HTMLInputElement) => {
    el.dispatchEvent(new Event('change', { bubbles: true }))
  })

  // While the 2s "restart" is in flight, the handle must STAY at 3600.
  // (Pre-fix bug: it snaps back to the stale receiver position, 100.)
  await page.waitForTimeout(700)
  await expect(scrubber).toHaveJSProperty('value', '3600')

  // After the restart completes and status catches up, still at the target.
  await page.waitForTimeout(RESTART_MS + 800)
  await expect(scrubber).toHaveJSProperty('value', '3600')
})
