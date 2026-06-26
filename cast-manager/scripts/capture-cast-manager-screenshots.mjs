import { createRequire } from 'node:module'
import fs from 'node:fs/promises'
import path from 'node:path'

const require = createRequire(import.meta.url)
const { chromium } = require('../frontend/node_modules/@playwright/test')
const base = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:8004'
const out = path.resolve('diagnostics/cast-manager-light-ui')
await fs.mkdir(out, { recursive: true })

const files = [
  { name: 'Arrival.2016.1080p.mkv', path: '/home/REDACTED_USER/watch_list/Arrival.2016.1080p.mkv', extension: '.mkv', size: 9_800_000_000, mtime: 1770000000, starred: true },
  { name: 'Ocean-demo.mp4', path: '/home/REDACTED_USER/watch_list/Ocean-demo.mp4', extension: '.mp4', size: 480_000_000, mtime: 1769000000 },
  { name: 'poster.jpg', path: '/home/REDACTED_USER/watch_list/poster.jpg', extension: '.jpg', size: 2_400_000, mtime: 1768000000 },
  { name: 'notes.nfo', path: '/home/REDACTED_USER/watch_list/notes.nfo', extension: '.nfo', size: 2400, mtime: 1767000000 },
  { name: 'Movies', path: '/home/REDACTED_USER/watch_list/Movies', isDirectory: true, is_directory: 1, size: 0, mtime: 1766000000 },
]

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1440, height: 1000 }, deviceScaleFactor: 1 })
const consoleErrors = []
page.on('console', (message) => { if (message.type() === 'error') consoleErrors.push(message.text()) })
page.on('pageerror', (error) => consoleErrors.push(error.message))

await page.route('**/api/**', async (route) => {
  const request = route.request()
  const url = new URL(request.url())
  const p = url.pathname
  let body = { success: true }
  if (p === '/api/config') body = { mediaRoot: '/home/REDACTED_USER/watch_list', fileManagerRoot: '/', serverUrl: base, features: { hls: false, vlc: true, castDoctor: true, diagnostics: true } }
  else if (p === '/api/files') body = { success: true, path: '/home/REDACTED_USER/watch_list', root: '/home/REDACTED_USER/watch_list', files }
  else if (p === '/api/files/recent') body = request.method() === 'GET' ? { files: [{ file_path: files[0].path, filename: files[0].name, file_type: 'video', action: 'cast', accessed_at: '2026-06-19T18:00:00Z' }, { file_path: files[1].path, filename: files[1].name, file_type: 'video', action: 'preview', accessed_at: '2026-06-19T17:00:00Z' }] } : { success: true }
  else if (p === '/api/files/starred') body = { files: [files[0]] }
  else if (p === '/api/shares') body = { shares: [{ id: 'demo-share', file_path: files[1].path, filename: files[1].name, permissions: 'view', access_count: 2 }] }
  else if (p === '/api/files/trash') body = { files: [] }
  else if (p === '/api/activity') body = { activities: [{ id: 1, action: 'cast', file_path: files[0].path, created_at: '2026-06-19T18:00:00Z' }] }
  else if (p === '/api/cast/status') body = { success: true, activeSession: true, state: 'playing', currentTime: 1640, duration: 6960, title: 'Arrival (2016)', deviceName: 'REDACTED_DEVICE', backend: 'vlc', session: { sessionId: 'visual-qa', title: 'Arrival (2016)', backend: 'vlc', duration: 6960 } }
  else if (p === '/api/cast/devices') body = { devices: [{ provider: 'chromecast', deviceId: 'bedroom-tv', name: 'REDACTED_DEVICE', selected: true }] }
  else if (p === '/api/cast/doctor') body = { status: 'healthy', checks: { ffmpeg: 'ok', vlc: 'ok', hls: 'disabled' } }
  else if (p.startsWith('/api/cast/diagnostics')) body = { sessionId: 'visual-qa', events: [{ state: 'playing', at: '2026-06-19T18:00:00Z' }] }
  else if (p === '/api/torrents') body = { torrents: [{ id: 7, name: 'Nature.Documentary.2160p', status: 'downloading', progress: .63, downloadSpeed: 8_400_000, uploadSpeed: 640_000 }, { id: 8, name: 'Concert.Live.1080p', status: 'seeding', progress: 1, downloadSpeed: 0, uploadSpeed: 1_200_000 }] }
  else if (p === '/api/storage/stats') body = { totalSpace: 4_000_000_000_000, usedSpace: 2_780_000_000_000, freeSpace: 1_220_000_000_000, largestFiles: [{ path: files[0].path, size: files[0].size }, { path: '/home/REDACTED_USER/watch_list/Amadeus.4K.mkv', size: 81_700_000_000 }] }
  else if (p === '/api/storage/dirs') body = { dirs: [{ name: 'Movies', path: '/home/REDACTED_USER/watch_list/Movies', size: 1_900_000_000_000, itemCount: 84 }, { name: 'Series', path: '/home/REDACTED_USER/watch_list/Series', size: 620_000_000_000, itemCount: 230 }] }
  else if (p === '/api/disk') body = { total: 4_000_000_000_000, used: 2_780_000_000_000, free: 1_220_000_000_000 }
  else if (p === '/api/media/info') body = { duration: 6960, size: files[0].size, container: 'matroska' }
  else if (p === '/api/media/analyze') body = { playbackMode: 'transcode', container: 'matroska', videoCodec: 'hevc', audioCodec: 'eac3', subtitles: [{ codec: 'hdmv_pgs_subtitle', language: 'eng' }], reasons: ['HEVC and E-AC-3 browser support is limited'] }
  else if (p === '/api/subtitles') body = { subtitles: [] }
  else if (p === '/api/thumbnail') body = { thumbnail: null, status: 'unavailable', reason: 'Visual QA fallback' }
  else if (p === '/api/url/analyze') body = { kind: 'html-embed', supported: false, castMethod: null, message: 'This is an HTML embed page, not a direct media stream. Cast Manager will not bypass logins, DRM, cookies, captchas, or anti-bot protections.' }
  else if (p === '/api/search') body = { results: files }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
})

await page.addInitScript(() => { localStorage.clear(); localStorage.setItem('cm_theme', 'light') })
await page.goto(base, { waitUntil: 'networkidle' })
await page.screenshot({ path: path.join(out, 'dashboard-light.png'), fullPage: true })
await page.screenshot({ path: path.join(out, 'now-playing-light.png'), fullPage: true })

const nav = (name) => page.locator('.sidebar-nav').getByRole('button', { name, exact: true })
await nav('Library').click()
await page.getByTestId('library-page').waitFor()
await page.screenshot({ path: path.join(out, 'library-list-light.png'), fullPage: true })
await page.getByRole('button', { name: 'Grid', exact: true }).click()
await page.screenshot({ path: path.join(out, 'library-grid-light.png'), fullPage: true })
await page.getByRole('button', { name: 'List', exact: true }).click()
await page.getByRole('button', { name: 'Preview', exact: true }).first().click()
await page.getByRole('dialog', { name: 'Media preview' }).waitFor()
await page.screenshot({ path: path.join(out, 'media-preview-video-light.png'), fullPage: true })
await page.getByRole('dialog', { name: 'Media preview' }).getByRole('button', { name: 'Cast', exact: true }).click()
await page.getByRole('dialog', { name: 'Cast setup' }).waitFor()
await page.screenshot({ path: path.join(out, 'cast-panel-light.png'), fullPage: true })
await page.getByRole('button', { name: 'Close cast panel' }).click()
await page.getByRole('button', { name: 'Close preview' }).click()

await nav('Dashboard').click()
await page.getByLabel('Quick Cast URL').fill('https://ntvs.cx/embed?t=visual-regression')
await page.getByRole('button', { name: 'Analyze URL' }).click()
await page.getByTestId('quick-cast-card').getByText('html embed', { exact: true }).waitFor()
await page.screenshot({ path: path.join(out, 'quick-cast-light.png'), fullPage: true })

await nav('Torrents').click(); await page.getByTestId('torrents-page').waitFor(); await page.screenshot({ path: path.join(out, 'torrents-light.png'), fullPage: true })
await nav('Storage').click(); await page.getByTestId('storage-page').waitFor(); await page.screenshot({ path: path.join(out, 'storage-light.png'), fullPage: true })
await nav('Settings').click(); await page.getByTestId('settings-page').waitFor(); await page.screenshot({ path: path.join(out, 'settings-light.png'), fullPage: true })
await nav('Diagnostics').click(); await page.getByTestId('diagnostics-page').waitFor(); await page.screenshot({ path: path.join(out, 'diagnostics-light.png'), fullPage: true })

await page.setViewportSize({ width: 390, height: 844 })
await page.evaluate(() => localStorage.setItem('cm_section', 'dashboard'))
await page.reload({ waitUntil: 'networkidle' })
await page.screenshot({ path: path.join(out, 'mobile-light.png'), fullPage: true })

await browser.close()
if (consoleErrors.length) throw new Error(`Console errors during screenshot pass:\n${consoleErrors.join('\n')}`)
console.log(`Saved ${12} verified screenshots to ${out}`)
