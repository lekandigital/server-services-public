#!/usr/bin/env node
/**
 * Playwright Cast Manager portal UI audit.
 */
import { chromium } from 'playwright';
import fs from 'fs';
import path from 'path';

const BASE = process.env.CAST_MANAGER_URL || 'http://REDACTED_SERVER_IP:8004';
const UI_DIR = process.env.AUDIT_UI_DIR || 'diagnostics/cast-manager-audit/latest/portal-ui';
fs.mkdirSync(UI_DIR, { recursive: true });

const results = {
  baseUrl: BASE,
  timestamp: new Date().toISOString(),
  consoleErrors: [],
  networkFailures: [],
  nonJsonApi: [],
  screenshots: [],
  sections: {},
  videoPlayback: {},
  recentPostTest: {},
  thumbnailTests: [],
};

const sections = [
  'home', 'recent', 'starred', 'shared', 'torrents', 'queue', 'playlists',
  'library', 'trash', 'activity', 'settings', 'storage',
];

async function shot(page, name) {
  const file = path.join(UI_DIR, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  results.screenshots.push(file);
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const page = await context.newPage();

  page.on('console', (msg) => {
    if (msg.type() === 'error') results.consoleErrors.push(msg.text());
  });

  page.on('response', async (resp) => {
    const url = resp.url();
    if (!url.includes('/api/')) return;
    const status = resp.status();
    if (status >= 400) {
      results.networkFailures.push({ url, status, method: resp.request().method() });
    }
    const ct = resp.headers()['content-type'] || '';
    if (url.includes('/api/') && !ct.includes('json') && !ct.includes('text/vtt') && !ct.includes('image/')) {
      try {
        const body = (await resp.text()).slice(0, 200);
        if (body.includes('Cannot POST') || body.includes('<!DOCTYPE') || body.includes('<html')) {
          results.nonJsonApi.push({ url, status, body });
        }
      } catch (_) {}
    }
  });

  page.on('requestfailed', (req) => {
    results.networkFailures.push({
      url: req.url(),
      method: req.method(),
      failure: req.failure()?.errorText || 'failed',
    });
  });

  await page.goto(BASE, { waitUntil: 'networkidle', timeout: 60000 });
  await shot(page, '00-initial-load');

  for (const sec of sections) {
    try {
      await page.evaluate((name) => {
        if (typeof showSection === 'function') showSection(name);
      }, sec);
      await page.waitForTimeout(1500);
      await shot(page, `section-${sec}`);
      const errCount = await page.locator('.toast-error, .error-state').count();
      results.sections[sec] = { loaded: true, errors: errCount };
    } catch (e) {
      results.sections[sec] = { loaded: false, errors: String(e) };
    }
  }

  // File browsing in library
  try {
    await page.evaluate(() => showSection('library'));
    await page.waitForTimeout(2000);
    await shot(page, 'library-loaded');
    const folders = page.locator('.file-row, .file-card, [data-path]');
    const count = await folders.count();
    results.sections.libraryBrowse = { itemCount: count };
    if (count > 0) {
      await folders.first().click({ timeout: 5000 }).catch(() => {});
      await page.waitForTimeout(1500);
      await shot(page, 'library-after-click');
    }
  } catch (e) {
    results.sections.libraryBrowse = { error: String(e) };
  }

  // Recent POST test (known bug)
  try {
    const postResult = await page.evaluate(async (base) => {
      const r = await fetch(`${base}/api/files/recent`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: '/test', action: 'play_video' }),
      });
      const ct = r.headers.get('content-type') || '';
      const text = await r.text();
      return { status: r.status, contentType: ct, body: text.slice(0, 300) };
    }, BASE);
    results.recentPostTest = postResult;
  } catch (e) {
    results.recentPostTest = { error: String(e) };
  }

  // Find and open a video file if possible
  try {
    await page.evaluate(() => showSection('library'));
    await page.waitForTimeout(1000);
    const videoRow = page.locator('.file-row, .file-card').filter({ hasText: /\.mp4|\.mkv/i }).first();
    if (await videoRow.count()) {
      await videoRow.click();
      await page.waitForTimeout(3000);
      await shot(page, 'video-before-play');
      const video = page.locator('video').first();
      if (await video.count()) {
        const meta = await video.evaluate((el) => ({
          duration: el.duration,
          readyState: el.readyState,
          networkState: el.networkState,
          error: el.error ? el.error.message : null,
        }));
        results.videoPlayback.metadata = meta;
        await video.click().catch(() => {});
        await page.waitForTimeout(2000);
        try {
          await video.evaluate((el) => el.play());
          await page.waitForTimeout(2000);
          const t1 = await video.evaluate((el) => el.currentTime);
          results.videoPlayback.afterPlay = { currentTime: t1 };
          await video.evaluate((el) => {
            if (el.duration && isFinite(el.duration)) el.currentTime = el.duration * 0.1;
          });
          await page.waitForTimeout(1500);
          await shot(page, 'video-after-seek-10pct');
          await video.evaluate((el) => {
            if (el.duration && isFinite(el.duration)) el.currentTime = el.duration * 0.5;
          });
          await page.waitForTimeout(1500);
          await shot(page, 'video-after-seek-50pct');
          await video.evaluate((el) => {
            if (el.duration && isFinite(el.duration)) el.currentTime = el.duration * 0.9;
          });
          await page.waitForTimeout(1500);
          await shot(page, 'video-after-seek-90pct');
          results.videoPlayback.scrubTests = 'attempted 10/50/90%';
        } catch (e) {
          results.videoPlayback.playError = String(e);
        }
      } else {
        results.videoPlayback.note = 'No <video> element after opening file';
      }
    } else {
      results.videoPlayback.note = 'No video file row found in library';
    }
  } catch (e) {
    results.videoPlayback.error = String(e);
  }

  // Thumbnail visibility in library/home
  try {
    const thumbs = await page.locator('img[src*="thumbnail"], img.thumbnail, .thumb img').all();
    for (let i = 0; i < Math.min(thumbs.length, 10); i++) {
      const src = await thumbs[i].getAttribute('src');
      const ok = await thumbs[i].evaluate((img) => img.complete && img.naturalWidth > 0);
      results.thumbnailTests.push({ src, loaded: ok });
    }
    await shot(page, 'thumbnails-state');
  } catch (e) {
    results.thumbnailTests.push({ error: String(e) });
  }

  fs.writeFileSync(path.join(UI_DIR, 'portal-ui-results.json'), JSON.stringify(results, null, 2));
  await browser.close();
  console.log('Portal UI audit complete:', UI_DIR);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
