#!/usr/bin/env node
/**
 * Live pipe: browser -> ffmpeg MPEG-TS -> local HTTP (no file on disk).
 * Run from cast-manager/frontend: node ../scripts/cast-ppv-live-pipe.mjs
 */
import { chromium } from '@playwright/test';
import { spawn, execSync } from 'child_process';
import http from 'http';
import { networkInterfaces } from 'os';

const embed = process.argv[2] || 'https://embedindia.st/embed/wc/2026-06-18/sui-bih';
const PORT = Number(process.env.PORT || 8765);

function lanIp() {
  for (const iface of Object.values(networkInterfaces())) {
    for (const a of iface || []) {
      if (a.family === 'IPv4' && !a.internal && a.address.startsWith('192.168.')) return a.address;
    }
  }
  try { return execSync('ipconfig getifaddr en0', { encoding: 'utf8' }).trim(); } catch { return '127.0.0.1'; }
}

const ff = spawn('ffmpeg', [
  '-hide_banner', '-loglevel', 'error',
  '-f', 'webm', '-i', 'pipe:0',
  '-c:v', 'libx264', '-preset', 'ultrafast', '-tune', 'zerolatency', '-g', '50',
  '-c:a', 'aac', '-b:a', '128k', '-ar', '48000',
  '-f', 'mpegts', 'pipe:1',
], { stdio: ['pipe', 'pipe', 'inherit'] });

const server = http.createServer((req, res) => {
  if (!req.url?.includes('stream')) { res.writeHead(404); return res.end(); }
  res.writeHead(200, {
    'Content-Type': 'video/mp2t',
    'Cache-Control': 'no-cache, no-store',
    'Connection': 'close',
  });
  ff.stdout.pipe(res);
  req.on('close', () => { try { res.end(); } catch {} });
});

await new Promise((resolve, reject) => {
  server.listen(PORT, '0.0.0.0', (err) => err ? reject(err) : resolve());
});

const relayUrl = `http://${lanIp()}:${PORT}/stream.ts`;
console.log('RELAY_URL=' + relayUrl);

const browser = await chromium.launch({ headless: true, channel: 'chrome' });
const page = await browser.newPage({ viewport: { width: 1280, height: 720 } });
await page.goto(embed, { waitUntil: 'domcontentloaded', timeout: 60000 });
await page.waitForTimeout(12000);
await page.mouse.click(640, 360);
await page.evaluate(() => { try { jwplayer().play(); } catch {} document.querySelector('video')?.play(); });
await page.waitForTimeout(5000);

const ok = await page.evaluate(() => {
  const v = document.querySelector('video');
  return !!(v && !v.paused && v.videoWidth > 0);
});
if (!ok) { console.error('stream did not start'); process.exit(1); }
console.log('playing, piping...');

await page.evaluate(() => {
  const video = document.querySelector('video');
  const stream = video.captureStream();
  const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp9,opus')
    ? 'video/webm;codecs=vp9,opus' : 'video/webm';
  const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 3500000 });
  rec.ondataavailable = async (e) => {
    if (!e.data.size) return;
    const buf = new Uint8Array(await e.data.arrayBuffer());
    window.__chunks = window.__chunks || [];
    window.__chunks.push(Array.from(buf));
  };
  rec.start(500);
});

process.on('SIGINT', () => process.exit(0));

for (;;) {
  const chunks = await page.evaluate(() => {
    const c = window.__chunks || [];
    window.__chunks = [];
    return c;
  });
  for (const arr of chunks) {
    ff.stdin.write(Buffer.from(arr));
  }
  await new Promise((r) => setTimeout(r, 150));
}
