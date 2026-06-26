#!/usr/bin/env node
/**
 * Record live embed with audio, upload, cast via cast-manager. Loops for continuous playback.
 * Run: cd cast-manager/frontend && node scripts/cast-ppv-live-loop.mjs
 */
import { chromium } from '@playwright/test';
import fs from 'fs';
import { execSync, spawn } from 'child_process';

const embed = process.argv[2] || 'https://embedindia.st/embed/wc/2026-06-18/sui-bih';
const SEC = Number(process.env.CLIP_SEC || 120);
const REMOTE = process.env.REMOTE || 'o@REDACTED_SERVER_IP';
const SSH_KEY = process.env.SSH_KEY || `${process.env.HOME}/.ssh/pinn_rtx3090`;
const OUT = '/tmp/sui-bih-live.mp4';
const REMOTE_PATH = '/home/REDACTED_USER/watch_list/.sui-bih-live.mp4';
const CAST_URL = process.env.CAST_URL || 'http://127.0.0.1:8004';

async function capture() {
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
  if (!ok) throw new Error('stream did not start');

  await page.evaluate((sec) => {
    const video = document.querySelector('video');
    const stream = video.captureStream();
    const mime = MediaRecorder.isTypeSupported('video/webm;codecs=vp9,opus')
      ? 'video/webm;codecs=vp9,opus' : 'video/webm';
    window.__recDone = new Promise((resolve, reject) => {
      const rec = new MediaRecorder(stream, { mimeType: mime, videoBitsPerSecond: 4000000 });
      rec.ondataavailable = (e) => { if (e.data.size) (window.__recChunks ||= []).push(e.data); };
      rec.onerror = (e) => reject(e.error || new Error('rec'));
      rec.onstop = async () => {
        const blob = new Blob(window.__recChunks, { type: mime });
        const buf = await blob.arrayBuffer();
        const bytes = new Uint8Array(buf);
        let bin = '';
        for (let i = 0; i < bytes.length; i += 0x8000) bin += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
        window.__recB64 = btoa(bin);
        resolve(window.__recB64.length);
      };
      rec.start(1000);
      setTimeout(() => rec.stop(), sec * 1000);
    });
  }, SEC);

  console.log(`recording ${SEC}s...`);
  await page.waitForTimeout(SEC * 1000 + 4000);
  await page.evaluate(() => window.__recDone);
  const b64 = await page.evaluate(() => window.__recB64);
  await browser.close();

  const webm = '/tmp/sui-bih-capture.webm';
  fs.writeFileSync(webm, Buffer.from(b64, 'base64'));
  execSync(`ffmpeg -hide_banner -loglevel error -y -i ${webm} -c:v libx264 -preset ultrafast -c:a aac -b:a 128k ${OUT}`);
  const streams = execSync(`ffprobe -v error -show_entries stream=codec_type -of csv=p=0 ${OUT}`, { encoding: 'utf8' }).trim();
  console.log('streams:', streams);
  if (!streams.includes('audio')) throw new Error('no audio in capture');
}

async function deployAndCast(first) {
  execSync(`scp -i ${SSH_KEY} ${OUT} ${REMOTE}:${REMOTE_PATH}`);
  if (first) {
    execSync(`ssh -i ${SSH_KEY} ${REMOTE} '/home/REDACTED_USER/.local/bin/catt -d "REDACTED_DEVICE" stop 2>/dev/null || true'`, { stdio: 'inherit' });
  }
  const res = execSync(
    `ssh -i ${SSH_KEY} ${REMOTE} "curl -sS -m 120 -X POST ${CAST_URL}/api/cast/start -H 'Content-Type: application/json' -d '{\\"filePath\\":\\"${REMOTE_PATH}\\",\\"backend\\":\\"auto\\",\\"subtitle\\":{\\"mode\\":\\"off\\"}}'"`,
    { encoding: 'utf8' },
  );
  const j = JSON.parse(res);
  if (!j.success) throw new Error(JSON.stringify(j));
  console.log('cast:', j.receiverObserved?.state || j.success, 'duration:', j.analysis?.duration);
}

let first = true;
for (;;) {
  try {
    console.log('\n--- cycle start ---', new Date().toISOString());
    await capture();
    await deployAndCast(first);
    first = false;
    // refresh before clip ends (~10s buffer)
    await new Promise((r) => setTimeout(r, (SEC - 12) * 1000));
  } catch (err) {
    console.error('cycle error:', err.message);
    await new Promise((r) => setTimeout(r, 15000));
  }
}
