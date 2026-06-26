#!/usr/bin/env node
import { chromium } from 'playwright';
import { execSync, spawn } from 'child_process';
import fs from 'fs';

const embed = process.env.NTVS_EMBED
  || 'https://ntvs.cx/embed?t=U3kvVGsxU1RSbFNVNzYraHQ0eDVFWktGeGVzQkNwSHdpdVRML1F2aWtWSTBIc21HY2FsQ2UyTjZwOHpTUnd5ZnROVEdUTS9oR0xWWHliUGp4WXZ2YlE9PQ~~';
const out = process.env.NTVS_OUT || '/home/REDACTED_USER/watch_list/.ntvs-live.mp4';
const base = process.env.CAST_MANAGER_URL || 'http://127.0.0.1:8004';
const clipSec = Number(process.env.NTVS_CLIP_SEC || 45);

const browser = await chromium.launch({ headless: true, args: ['--no-sandbox'] });
const page = await browser.newPage();
let m3u8 = null;
page.on('request', (r) => { if (r.url().includes('playlist.m3u8')) m3u8 = r.url(); });
await page.goto(embed, { waitUntil: 'networkidle', timeout: 90000 }).catch(() => {});
await page.waitForTimeout(5000);
await browser.close();
if (!m3u8) {
  console.error('No m3u8 found — stream may be offline');
  process.exit(1);
}
console.log('m3u8:', m3u8);

try { fs.unlinkSync(out); } catch { /* ok */ }
const hdr = 'Referer: https://embed.st/\r\nOrigin: https://embed.st\r\nUser-Agent: Mozilla/5.0\r\n';
await new Promise((resolve, reject) => {
  const ff = spawn('ffmpeg', [
    '-hide_banner', '-loglevel', 'warning', '-y',
    '-headers', hdr,
    '-i', m3u8,
    '-map', '0:v:1?', '-map', '0:a:0?',
    '-c:v', 'libx264', '-preset', 'ultrafast',
    '-c:a', 'aac', '-b:a', '128k',
    '-t', String(clipSec),
    out,
  ]);
  ff.stderr.on('data', (d) => process.stderr.write(d));
  ff.on('close', (code) => (code === 0 ? resolve() : reject(new Error(`ffmpeg exit ${code}`))));
});

const size = fs.statSync(out).size;
console.log('recorded bytes:', size);
if (size < 100_000) {
  console.error('Recording too small');
  process.exit(1);
}

try {
  execSync(`curl -sS -m 5 -X POST ${base}/api/cast/controls -H 'Content-Type: application/json' -d '{"action":"stop"}'`, { stdio: 'ignore' });
} catch { /* ok */ }

const payload = JSON.stringify({
  filePath: out,
  backend: 'auto',
  subtitle: { mode: 'off' },
  autoTranscode: 'auto',
});
const castRes = execSync(
  `curl -sS -m 120 -X POST ${base}/api/cast/start -H 'Content-Type: application/json' -d '${payload.replace(/'/g, "'\\''")}'`,
  { encoding: 'utf8' },
);
console.log(castRes);
