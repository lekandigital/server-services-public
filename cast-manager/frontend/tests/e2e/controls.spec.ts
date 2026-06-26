import { test, expect, type Page, type Route } from '@playwright/test'

const BASE = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:8004'
const video = { name: 'Audit-video.mkv', path: '/home/REDACTED_USER/watch_list/Audit-video.mkv', extension: '.mkv', size: 8_000_000_000, mtime: 1770000000, starred: false }
const image = { name: 'Audit-poster.jpg', path: '/home/REDACTED_USER/watch_list/Audit-poster.jpg', extension: '.jpg', size: 2_000_000, mtime: 1769000000 }
const text = { name: 'Audit-notes.nfo', path: '/home/REDACTED_USER/watch_list/Audit-notes.nfo', extension: '.nfo', size: 1200, mtime: 1768000000 }
const folder = { name: 'Audit folder', path: '/home/REDACTED_USER/watch_list/Audit folder', isDirectory: true, is_directory: 1, size: 0, mtime: 1767000000 }

async function installApiMock(page: Page) {
  await page.route('**/api/**', async (route: Route) => {
    const request = route.request(); const url = new URL(request.url()); const p = url.pathname
    let body: any = { success: true }
    if (p === '/api/config') body = { mediaRoot: '/home/REDACTED_USER/watch_list', serverUrl: BASE, features: { hls: false, vlc: true, castDoctor: true, diagnostics: true } }
    else if (p === '/api/files') body = { path: url.searchParams.get('path') || '/home/REDACTED_USER/watch_list', root: '/home/REDACTED_USER/watch_list', files: [folder, video, image, text] }
    else if (p === '/api/files/recent') body = request.method() === 'GET' ? { files: [{ file_path: video.path, filename: video.name, file_type: 'video', action: 'cast' }] } : { success: true }
    else if (p === '/api/files/starred') body = { files: [{ ...video, starred: true }] }
    else if (p === '/api/shares') body = { shares: [{ id: 'audit-link', file_path: video.path, filename: video.name, permissions: 'view' }] }
    else if (p === '/api/files/trash') body = { files: [{ id: 5, original_path: text.path, filename: text.name, size: text.size }] }
    else if (p === '/api/activity') body = { activities: [{ id: 1, action: 'preview', file_path: video.path, created_at: new Date().toISOString() }] }
    else if (p === '/api/cast/status') body = { activeSession: true, state: 'playing', currentTime: 100, duration: 7200, title: 'Audit video', deviceName: 'REDACTED_DEVICE', backend: 'vlc', session: { sessionId: 'audit', title: 'Audit video', duration: 7200 } }
    else if (p === '/api/cast/devices') body = { devices: [{ provider: 'chromecast', deviceId: 'tv', name: 'REDACTED_DEVICE', selected: true }] }
    else if (p === '/api/cast/doctor') body = { status: 'healthy' }
    else if (p.startsWith('/api/cast/diagnostics')) body = { events: [] }
    else if (p === '/api/cast/preflight') body = { success: true, summary: 'Ready to cast' }
    else if (p === '/api/torrents') body = { torrents: [{ id: 1, name: 'Active torrent', status: 'downloading', progress: .4, downloadSpeed: 1200 }, { id: 2, name: 'Paused torrent', status: 'paused', progress: .2 }] }
    else if (p.includes('/api/torrents/') && p.endsWith('/info')) body = { id: 1, tracker: 'audit', files: [] }
    else if (p === '/api/storage/stats') body = { totalSpace: 1000, usedSpace: 600, freeSpace: 400, largestFiles: [{ path: video.path, size: video.size }] }
    else if (p === '/api/storage/dirs') body = { dirs: [{ name: 'Movies', path: '/home/REDACTED_USER/watch_list/Movies', size: 500, itemCount: 2 }] }
    else if (p === '/api/disk') body = { total: 1000, used: 600, free: 400 }
    else if (p === '/api/media/info') body = { duration: 7200, container: 'matroska' }
    else if (p === '/api/media/analyze') body = { playbackMode: 'transcode', container: 'matroska', videoCodec: 'hevc', audioCodec: 'eac3', subtitles: [] }
    else if (p === '/api/subtitles') body = { subtitles: [] }
    else if (p === '/api/thumbnail') body = { thumbnail: null, status: 'unavailable' }
    else if (p === '/api/files/read') body = { content: 'Safe <b>plain text</b> preview', truncated: false }
    else if (p === '/api/stream/generate') body = { url: `${BASE}/stream/audit/video.mkv`, token: 'audit' }
    else if (p === '/api/share') body = { shareId: 'audit-link', shareUrl: `${BASE}/s/audit-link` }
    else if (p === '/api/url/analyze') body = { kind: 'direct-media', supported: true, castMethod: 'direct', message: 'Direct media URL detected. Ready to cast.' }
    else if (p === '/api/search') body = { results: [video, image, text] }
    await route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
  })
}

const nav = (page: Page, name: string) => page.locator('.sidebar-nav').getByRole('button', { name, exact: true })

test('button actions and visible controls are wired without dead clicks', async ({ page }) => {
  await installApiMock(page)
  await page.addInitScript(() => localStorage.clear())
  await page.goto(BASE)

  await page.getByRole('button', { name: 'Refresh current view' }).click()
  await page.getByRole('button', { name: 'Open library' }).click()
  await expect(page.getByTestId('library-page')).toBeVisible()
  await page.getByLabel('Search files').fill('audit')
  await page.getByLabel('Filter by type').selectOption('video')
  await page.getByLabel('Sort library').selectOption('size')
  await page.getByRole('button', { name: 'Grid', exact: true }).click()
  await page.getByRole('button', { name: 'List', exact: true }).click()
  await page.getByRole('button', { name: 'Preview', exact: true }).first().click()
  const preview = page.getByRole('dialog', { name: 'File details' })
  await preview.getByRole('button', { name: 'Add to queue' }).click()
  await preview.getByRole('button', { name: 'Copy stream URL' }).click()
  const popupPromise = page.waitForEvent('popup')
  await preview.getByRole('button', { name: 'Download' }).click()
  ;(await popupPromise).close()
  await preview.getByRole('button', { name: 'Analyze again' }).click()
  await preview.getByRole('button', { name: 'Copy app link' }).first().click()
  await preview.getByRole('button', { name: 'Cast', exact: true }).click()
  const castPanel = page.getByRole('dialog', { name: 'Cast setup' })
  await castPanel.getByLabel('Cast device').selectOption({ index: 0 })
  await castPanel.getByRole('button', { name: 'Refresh devices' }).click()
  await castPanel.getByLabel('Backend').selectOption('ffmpeg-live')
  await castPanel.getByLabel('Start position').selectOption('custom')
  await castPanel.getByLabel('Custom start time in seconds').fill('90')
  await castPanel.getByRole('button', { name: 'Analyze & preflight' }).click()
  await castPanel.getByRole('button', { name: 'Close cast panel' }).click()
  await preview.getByRole('button', { name: 'Close preview' }).click()

  const more = () => page.getByRole('button', { name: `More actions for ${video.name}` })
  await more().click(); await page.getByRole('button', { name: 'Copy stream URL' }).click()
  await more().click(); await page.getByRole('button', { name: 'Star', exact: true }).click()
  await more().click(); await page.getByRole('button', { name: 'Create share link' }).click()
  await more().click(); page.once('dialog', (dialog) => dialog.accept('Renamed.mkv')); await page.getByRole('button', { name: 'Rename' }).click()
  await more().click(); page.once('dialog', (dialog) => dialog.accept('/home/REDACTED_USER/watch_list/Movies')); await page.getByRole('button', { name: 'Move', exact: true }).click()
  await more().click(); page.once('dialog', (dialog) => dialog.accept()); await page.getByRole('button', { name: 'Move to Trash' }).click()

  await page.getByLabel('Filter by type').selectOption('image')
  await page.getByRole('button', { name: 'Preview', exact: true }).first().click(); await preview.getByRole('button', { name: 'Close preview' }).click()
  await page.getByLabel('Filter by type').selectOption('text')
  await page.getByRole('button', { name: 'Read', exact: true }).click(); await expect(preview).toContainText('Safe <b>plain text</b> preview'); await preview.getByRole('button', { name: 'Close preview' }).click()

  await nav(page, 'Recent').click(); await page.getByRole('button', { name: 'Show in Library' }).click()
  await nav(page, 'Starred').click(); await page.getByRole('button', { name: 'Unstar' }).click()
  await nav(page, 'Shared').click(); await page.getByRole('button', { name: 'Copy link' }).click(); page.once('dialog', (dialog) => dialog.accept()); await page.getByRole('button', { name: 'Revoke' }).click()
  await nav(page, 'Trash').click(); await page.getByRole('button', { name: 'Restore' }).click(); page.once('dialog', (dialog) => dialog.accept()); await page.getByRole('button', { name: 'Delete forever' }).click()
  await nav(page, 'Activity').click(); await page.getByLabel('Filter activity').fill('preview'); await page.getByRole('button', { name: 'Refresh', exact: true }).click()

  await nav(page, 'Torrents').click()
  await page.locator('textarea').fill('magnet:?xt=urn:btih:audit')
  await page.getByRole('button', { name: 'Add magnet' }).click()
  await page.locator('input[type=file]').setInputFiles({ name: 'audit.torrent', mimeType: 'application/x-bittorrent', buffer: Buffer.from('audit') })
  const torrentPage = page.getByTestId('torrents-page')
  await torrentPage.getByRole('button', { name: 'Pause all' }).click(); await torrentPage.getByRole('button', { name: 'Resume all' }).click()
  await torrentPage.getByRole('button', { name: 'Pause', exact: true }).first().click(); await torrentPage.getByRole('button', { name: 'Resume', exact: true }).last().click()
  await torrentPage.getByLabel('Torrent priority').first().selectOption('high')
  await torrentPage.getByRole('button', { name: 'Info', exact: true }).first().click(); await page.getByRole('button', { name: 'Close torrent info' }).click()
  page.once('dialog', (dialog) => dialog.accept()); await torrentPage.getByRole('button', { name: 'Remove', exact: true }).first().click()

  await nav(page, 'Playlists').click(); page.once('dialog', (dialog) => dialog.accept('Audit playlist')); await page.getByRole('button', { name: 'Save queue as playlist' }).click(); await page.getByRole('button', { name: 'Delete' }).click()
  await nav(page, 'Queue').click(); await page.getByRole('button', { name: 'Clear queue' }).click()

  await nav(page, 'Settings').click()
  await page.getByLabel('Theme').selectOption('light'); await page.getByLabel('Default Library view').selectOption('grid'); await page.getByRole('button', { name: 'Refresh devices' }).click(); await page.getByLabel('Default cast backend').selectOption('vlc'); await page.getByLabel('Diagnostics verbosity').selectOption('verbose'); await page.getByRole('button', { name: 'Save settings' }).first().click(); await page.getByRole('button', { name: 'Run Cast Doctor' }).click()
  await expect(page.getByTestId('diagnostics-page')).toBeVisible(); await page.getByRole('button', { name: 'Refresh checks' }).click(); await page.getByRole('button', { name: 'Copy debug info' }).click(); await page.getByRole('button', { name: 'Clear timeline' }).click()

  await page.getByRole('button', { name: 'Seek back 10 seconds' }).click(); await page.getByRole('button', { name: 'Pause' }).click(); await page.getByRole('button', { name: 'Seek forward 30 seconds' }).click(); await page.locator('#cast-volume').fill('70'); await page.locator('#cast-volume').dispatchEvent('change'); await page.getByRole('button', { name: 'Cast diagnostics' }).click(); await page.getByRole('button', { name: 'Stop cast' }).click()
})
