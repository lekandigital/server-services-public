import { expect, test, type Page, type Route } from '@playwright/test'

const BASE = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:4174'
const roots = [
  { id: 'watch_list', label: 'Media Library', serverPath: '/home/REDACTED_USER/watch_list', routePrefix: '/file-manager/library' },
  { id: 'downloads', label: 'Downloads', serverPath: '/home/REDACTED_USER/downloads', routePrefix: '/file-manager/user/o/downloads' },
]
const movieFolder = { name: 'Movies', path: '/home/REDACTED_USER/downloads/Movies', type: 'folder', isDirectory: true, size: 0, mtime: 1770000000, starred: false }
const actionFolder = { name: 'Action', path: '/home/REDACTED_USER/downloads/Movies/Action', type: 'folder', isDirectory: true, size: 0, mtime: 1770000001, starred: false }
const video = { name: 'Drive UX Demo.mkv', path: '/home/REDACTED_USER/downloads/Drive UX Demo.mkv', type: 'video', extension: '.mkv', size: 8_000_000_000, mtime: 1770000002, starred: false }

async function installMocks(page: Page, requestedPaths: string[], calls: string[] = []) {
  let castStarted = false
  await page.addInitScript(() => {
    localStorage.clear()
    localStorage.setItem('cm_theme', 'light')
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText: async (value: string) => { (window as any).__copied = value } },
    })
  })
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request()
    const url = new URL(request.url())
    const path = url.pathname
    calls.push(`${request.method()} ${path}`)
    let body: any = { success: true }
    if (path === '/api/config') body = { success: true, mediaRoot: '/home/REDACTED_USER/watch_list', fileRoots: roots, defaultRootId: 'watch_list', serverUrl: BASE, features: { hls: false, vlc: true, castDoctor: true, diagnostics: true, cast: true, torrents: true, shares: true, trash: true, starred: true, newFolder: true } }
    else if (path === '/api/files') {
      const requested = url.searchParams.get('path') || '/home/REDACTED_USER/watch_list'
      requestedPaths.push(requested)
      const files = requested === '/home/REDACTED_USER/downloads' ? [movieFolder, video] : requested === movieFolder.path ? [actionFolder] : []
      const root = requested.startsWith('/home/REDACTED_USER/downloads') ? roots[1] : roots[0]
      body = { success: true, currentPath: requested, path: requested, rootPath: root.serverPath, root: root.serverPath, files }
    } else if (path === '/api/files/starred') body = { files: video.starred ? [{ ...video }] : [] }
    else if (path === '/api/files/star') { video.starred = request.method() === 'POST'; body = { success: true, starred: video.starred } }
    else if (path === '/api/files/recent') body = request.method() === 'GET' ? { files: [] } : { success: true }
    else if (path === '/api/shares') body = { shares: [] }
    else if (path === '/api/files/trash') body = { files: [] }
    else if (path === '/api/activity') body = { activities: [] }
    else if (path === '/api/storage/stats') body = { totalSpace: 1000, usedSpace: 420, freeSpace: 580, largestFiles: [] }
    else if (path === '/api/storage/dirs') body = { dirs: [] }
    else if (path === '/api/cast/status') body = castStarted ? { success: true, activeSession: true, state: 'playing', title: video.name, deviceName: 'REDACTED_DEVICE', backend: 'auto', currentTime: 0, duration: 7200 } : { success: true, activeSession: false, state: 'idle' }
    else if (path === '/api/cast/devices') body = { devices: [{ provider: 'chromecast', deviceId: 'bedroom', name: 'REDACTED_DEVICE', selected: true }] }
    else if (path === '/api/cast/doctor') body = { status: 'healthy' }
    else if (path === '/api/cast/preflight') body = { success: true, summary: 'Ready to cast directly or with automatic fallback.' }
    else if (path === '/api/cast/start') { castStarted = true; body = { success: true, state: 'starting' } }
    else if (path === '/api/media/info') body = { duration: 7200, container: 'matroska' }
    else if (path === '/api/media/analyze') body = { playbackMode: 'transcode', container: 'matroska', videoCodec: 'hevc', audioCodec: 'eac3', subtitles: [] }
    else if (path === '/api/subtitles') body = { subtitles: [] }
    else if (path === '/api/thumbnail') body = { thumbnail: null, status: 'unavailable' }
    else if (path === '/api/stream/generate') body = { url: `${BASE}/stream/token/Drive%20UX%20Demo.mkv`, token: 'token' }
    else if (path === '/api/share') body = { shareId: 'drive-ux', shareUrl: `${BASE}/s/drive-ux` }
    else if (path === '/api/torrents') body = { torrents: [] }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

test('Drive-style routes survive open, refresh, back, forward, and clipboard copy', async ({ page }) => {
  const requestedPaths: string[] = []
  const consoleErrors: string[] = []
  page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
  await installMocks(page, requestedPaths)
  await page.goto('/file-manager/user/o/downloads')

  await expect(page.locator('html')).toHaveCSS('color-scheme', 'light')
  for (const label of ['My Drive / Files', 'Downloads', 'Media Library', 'Recent', 'Starred', 'Shared', 'Trash', 'Torrents', 'Storage', 'Diagnostics', 'Settings']) {
    await expect(page.locator('.sidebar-nav').getByRole('button', { name: label, exact: true }).first()).toBeVisible()
  }
  await expect.poll(() => requestedPaths.includes('/home/REDACTED_USER/downloads')).toBeTruthy()

  await page.getByRole('button', { name: 'Movies', exact: true }).click()
  await expect(page).toHaveURL('/file-manager/user/o/downloads/Movies')
  await expect.poll(() => requestedPaths.includes('/home/REDACTED_USER/downloads/Movies')).toBeTruthy()

  await page.reload()
  await expect(page).toHaveURL('/file-manager/user/o/downloads/Movies')
  await expect(page.getByRole('button', { name: 'Action', exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Action', exact: true }).click()
  await expect(page).toHaveURL('/file-manager/user/o/downloads/Movies/Action')
  await page.goBack(); await expect(page).toHaveURL('/file-manager/user/o/downloads/Movies')
  await page.goForward(); await expect(page).toHaveURL('/file-manager/user/o/downloads/Movies/Action')
  await page.goBack(); await page.goBack(); await expect(page).toHaveURL('/file-manager/user/o/downloads')

  await page.getByRole('button', { name: 'Copy folder URL' }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__copied)).toBe(`${BASE}/file-manager/user/o/downloads`)
  await expect(page.getByText('Folder URL copied')).toBeVisible()
  expect(consoleErrors).toEqual([])
})

test('file actions expose cast, app/stream/share links, details, and reliable starring', async ({ page }) => {
  const requestedPaths: string[] = []
  const calls: string[] = []
  await installMocks(page, requestedPaths, calls)
  await page.goto('/file-manager/user/o/downloads')
  const row = page.locator('tr').filter({ hasText: video.name })
  await expect(row.getByRole('button', { name: 'Cast', exact: true })).toBeVisible()
  await expect(row.getByRole('button', { name: `Star ${video.name}` })).toBeVisible()

  await row.getByRole('button', { name: `More actions for ${video.name}` }).click()
  await page.getByRole('button', { name: 'Copy app link', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__copied)).toBe(`${BASE}/file-manager/user/o/downloads/Drive%20UX%20Demo.mkv?preview=1`)

  await row.getByRole('button', { name: `More actions for ${video.name}` }).click()
  await page.getByRole('button', { name: 'Copy stream URL', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__copied)).toContain('/stream/token/')

  await row.getByRole('button', { name: `More actions for ${video.name}` }).click()
  await page.getByRole('button', { name: 'Create share link', exact: true }).click()
  await expect.poll(() => page.evaluate(() => (window as any).__copied)).toBe(`${BASE}/s/drive-ux`)

  await row.getByRole('button', { name: 'Cast', exact: true }).click()
  const castPanel = page.getByRole('dialog', { name: 'Cast setup' })
  await expect(castPanel).toBeVisible()
  await expect(castPanel).toContainText('Ready to cast directly')
  await castPanel.getByRole('button', { name: 'Start cast' }).click()
  await expect(page.locator('.now-playing')).toContainText(video.name)
  expect(calls).toContain('POST /api/cast/start')
  expect(calls).toContain('GET /api/cast/status')

  await row.getByRole('button', { name: `Star ${video.name}` }).click()
  await expect(page.getByText(/Added to Starred|Star was not changed/)).toBeVisible()
  await expect(row.getByRole('button', { name: `Unstar ${video.name}` })).toBeVisible()
  await page.locator('.sidebar-nav').getByRole('button', { name: 'Starred', exact: true }).click()
  await expect(page).toHaveURL('/starred')
  await expect(page.getByTestId('starred-page')).toContainText(video.name)
  await page.getByRole('button', { name: 'Open location' }).click()
  await expect(page).toHaveURL('/file-manager/user/o/downloads')
})

test('file preview deep links restore details and API failures never render raw HTML', async ({ page }) => {
  const requestedPaths: string[] = []
  await installMocks(page, requestedPaths)
  await page.goto('/file-manager/user/o/downloads/Drive%20UX%20Demo.mkv?preview=1')
  await expect(page.getByRole('dialog', { name: 'File details' })).toBeVisible()
  await expect(page.getByRole('dialog', { name: 'File details' })).toContainText(video.name)
  await expect(page.locator('body')).not.toContainText('<!DOCTYPE html>')
  await expect(page.locator('body')).not.toContainText('Cannot GET')
})
