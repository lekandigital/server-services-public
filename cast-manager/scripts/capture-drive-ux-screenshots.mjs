import { chromium } from '@playwright/test'
import fs from 'node:fs/promises'
import path from 'node:path'

const base = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:4174'
const out = path.resolve('diagnostics/cast-manager-drive-ux')
await fs.mkdir(out, { recursive: true })

const roots = [
  { id: 'watch_list', label: 'Media Library', serverPath: '/home/REDACTED_USER/watch_list', routePrefix: '/file-manager/library' },
  { id: 'downloads', label: 'Downloads', serverPath: '/home/REDACTED_USER/downloads', routePrefix: '/file-manager/user/o/downloads' },
]
const video = { name: 'Arrival.2016.1080p.mkv', path: '/home/REDACTED_USER/downloads/Arrival.2016.1080p.mkv', type: 'video', extension: '.mkv', size: 9_800_000_000, mtime: 1770000000, starred: true }
const audio = { name: 'Soundtrack.flac', path: '/home/REDACTED_USER/downloads/Soundtrack.flac', type: 'audio', extension: '.flac', size: 680_000_000, mtime: 1769900000, starred: false }
const image = { name: 'poster.jpg', path: '/home/REDACTED_USER/downloads/poster.jpg', type: 'image', extension: '.jpg', size: 2_400_000, mtime: 1769800000, starred: false }
const movies = { name: 'Movies', path: '/home/REDACTED_USER/downloads/Movies', type: 'folder', isDirectory: true, size: 1_200_000_000_000, mtime: 1769700000, starred: true }
const action = { name: 'Action', path: '/home/REDACTED_USER/downloads/Movies/Action', type: 'folder', isDirectory: true, size: 500_000_000_000, mtime: 1769600000, starred: false }
const nestedVideo = { ...video, name: 'Fury.Road.mkv', path: '/home/REDACTED_USER/downloads/Movies/Fury.Road.mkv', starred: false }

const browser = await chromium.launch()
const page = await browser.newPage({ viewport: { width: 1536, height: 1000 }, colorScheme: 'light' })
const errors = []
page.on('console', (message) => { if (message.type() === 'error') errors.push(message.text()) })
page.on('pageerror', (error) => errors.push(error.message))
await page.addInitScript(() => {
  localStorage.clear()
  localStorage.setItem('cm_theme', 'light')
  Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (value) => { window.__copied = value } } })
})

await page.route('**/api/**', async (route) => {
  const request = route.request()
  const url = new URL(request.url())
  const p = url.pathname
  let body = { success: true }
  if (p === '/api/config') body = { success: true, mediaRoot: '/home/REDACTED_USER/watch_list', fileRoots: roots, defaultRootId: 'watch_list', serverUrl: base, features: { hls: false, vlc: true, castDoctor: true, diagnostics: true, cast: true, torrents: true, shares: true, trash: true, starred: true, newFolder: true } }
  else if (p === '/api/files') {
    const requested = url.searchParams.get('path') || '/home/REDACTED_USER/watch_list'
    if (requested.endsWith('/Missing')) return route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ success: false, error: 'The folder could not be read from the media server.' }) })
    const files = requested === '/home/REDACTED_USER/downloads' ? [movies, video, audio, image] : requested === movies.path ? [action, nestedVideo] : []
    const root = requested.startsWith('/home/REDACTED_USER/downloads') ? roots[1] : roots[0]
    body = { success: true, currentPath: requested, path: requested, rootPath: root.serverPath, root: root.serverPath, files }
  } else if (p === '/api/files/starred') body = { files: [movies, video] }
  else if (p === '/api/files/recent') body = request.method() === 'GET' ? { files: [{ file_path: video.path, filename: video.name, file_type: 'video', action: 'cast' }] } : { success: true }
  else if (p === '/api/shares') body = { shares: [{ id: 'arrival-demo', file_path: video.path, filename: video.name, permissions: 'view' }] }
  else if (p === '/api/files/trash') body = { files: [] }
  else if (p === '/api/activity') body = { activities: [] }
  else if (p === '/api/storage/stats') body = { totalSpace: 4_000_000_000_000, usedSpace: 2_520_000_000_000, freeSpace: 1_480_000_000_000, largestFiles: [] }
  else if (p === '/api/storage/dirs') body = { dirs: [] }
  else if (p === '/api/cast/status') body = { success: true, activeSession: false, state: 'idle' }
  else if (p === '/api/cast/devices') body = { devices: [{ provider: 'chromecast', deviceId: 'bedroom', name: 'REDACTED_DEVICE', selected: true }] }
  else if (p === '/api/cast/doctor') body = { status: 'healthy', checks: { ffmpeg: 'ok', vlc: 'ok' } }
  else if (p.startsWith('/api/cast/diagnostics')) body = { events: [] }
  else if (p === '/api/cast/preflight') body = { success: true, summary: 'Direct play is unavailable; automatic VLC/ffmpeg fallback is ready.' }
  else if (p === '/api/media/info') body = { duration: 6960, size: video.size, container: 'matroska' }
  else if (p === '/api/media/analyze') body = { playbackMode: 'transcode', container: 'matroska', videoCodec: 'hevc', audioCodec: 'eac3', subtitles: [] }
  else if (p === '/api/subtitles') body = { subtitles: [] }
  else if (p === '/api/thumbnail') body = { thumbnail: null, status: 'unavailable' }
  else if (p === '/api/stream/generate') body = { url: `${base}/stream/demo/Arrival.2016.1080p.mkv`, token: 'demo' }
  else if (p === '/api/share') body = { shareId: 'arrival-demo', shareUrl: `${base}/s/arrival-demo` }
  else if (p === '/api/torrents') body = { torrents: [] }
  else if (p === '/api/url/analyze') body = { kind: 'direct-media', supported: true, castMethod: 'direct', message: 'Direct media URL ready.' }
  await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
})

const shot = (name) => page.screenshot({ path: path.join(out, name), fullPage: true })
await page.goto(`${base}/dashboard`, { waitUntil: 'networkidle' }); await shot('dashboard.png')
await page.goto(`${base}/file-manager/user/o/downloads`, { waitUntil: 'networkidle' }); await shot('file-manager-downloads.png'); await shot('starred-sidebar.png')
await page.getByRole('button', { name: 'Movies', exact: true }).click(); await page.getByRole('button', { name: 'Action', exact: true }).waitFor(); await shot('file-manager-nested-folder.png')
await page.goBack(); await page.getByRole('button', { name: 'Preview', exact: true }).first().click(); await page.getByRole('dialog', { name: 'File details' }).waitFor(); await shot('file-details-panel.png')
await page.getByRole('button', { name: 'Close preview' }).click(); await page.getByRole('button', { name: `More actions for ${video.name}` }).click(); await shot('file-action-menu.png')
await page.getByRole('button', { name: 'Cast', exact: true }).first().click(); await page.getByRole('dialog', { name: 'Cast setup' }).waitFor(); await shot('cast-panel.png')
await page.getByRole('button', { name: 'Close cast panel' }).click(); await page.goto(`${base}/starred`, { waitUntil: 'networkidle' }); await page.getByTestId('starred-page').waitFor(); await shot('starred-page.png')
await page.goto(`${base}/file-manager/user/o/downloads`, { waitUntil: 'networkidle' }); await page.getByRole('button', { name: 'Copy folder URL' }).click(); await page.getByText('Folder URL copied').waitFor(); await shot('copy-url-success.png')
if (errors.length) throw new Error(`Browser errors during primary screenshot QA:\n${errors.join('\n')}`)
errors.length = 0
await page.goto(`${base}/file-manager/user/o/downloads/Missing`, { waitUntil: 'networkidle' }); await page.getByRole('button', { name: 'Diagnostics', exact: true }).last().click(); await page.getByTestId('diagnostics-page').waitFor(); await shot('diagnostics-error.png')

await browser.close()
console.log(`Saved 10 Drive UX screenshots to ${out}`)
