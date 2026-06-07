// ═══════════════════════════════════════════════════════════════
// Cast Manager v4 — Token-Based Public Streaming Routes
// ═══════════════════════════════════════════════════════════════

const express = require('express');
const path = require('path');
const { Client } = require('ssh2');
const { generateStreamToken, validateStreamToken, logActivity, trackRecent } = require('../db');

// MIME type mapping
const MIME_TYPES = {
  '.mp4': 'video/mp4', '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo',
  '.webm': 'video/webm', '.mov': 'video/quicktime', '.m4v': 'video/mp4',
  '.ts': 'video/mp2t', '.flv': 'video/x-flv', '.wmv': 'video/x-ms-wmv',
  '.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.aac': 'audio/aac',
  '.ogg': 'audio/ogg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
  '.wma': 'audio/x-ms-wma', '.opus': 'audio/opus',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
  '.pdf': 'application/pdf', '.txt': 'text/plain',
};

function getMime(filePath) {
  return MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
}

function shEsc(str) {
  return `'${String(str || '').replace(/'/g, "'\\''")}'`;
}

function htmlEsc(str) {
  return String(str || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function isVideoFile(filePath) {
  return ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v', '.ts', '.flv', '.wmv']
    .includes(path.extname(filePath).toLowerCase());
}

function isAudioFile(filePath) {
  return ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus', '.wma']
    .includes(path.extname(filePath).toLowerCase());
}

function publicBase(req) {
  const proto = req.headers['x-forwarded-proto'] || req.protocol || 'http';
  const host = req.headers['x-forwarded-host'] || req.headers.host;
  return `${proto}://${host}`;
}

function streamUrls(req, token, filename) {
  const basePath = `/stream/${encodeURIComponent(token)}/${encodeURIComponent(filename)}`;
  const baseUrl = `${publicBase(req)}${basePath}`;
  return {
    streamUrl: baseUrl,
    playerUrl: baseUrl,
    mediaUrl: `${baseUrl}?media=1`,
    directUrl: `${baseUrl}?raw=1`,
  };
}

function createStreamRouter(options = {}) {
  const router = express.Router();
  const canAccessPath = typeof options.canAccessPath === 'function' ? options.canAccessPath : () => true;

  // ─── Generate Token ──────────────────────────────────────────
  // POST /api/stream/generate
  router.post('/generate', async (req, res) => {
    try {
      const { filePath, expiresIn } = req.body;
      if (!filePath) return res.status(400).json({ error: 'filePath required' });
      if (!(await canAccessPath(filePath))) return res.status(403).json({ error: 'Cannot generate a stream URL for this path' });

      const filename = path.basename(filePath);
      const hours = parseInt(expiresIn) || 24;
      const { token, expiresAt } = generateStreamToken(filePath, filename, hours);

      const urls = streamUrls(req, token, filename);

      logActivity('stream_url_generated', filePath, { token, expiresIn: hours });

      res.json({
        ...urls,
        expiresAt,
        token,
        filename,
        mediaKind: isVideoFile(filePath) ? 'video' : isAudioFile(filePath) ? 'audio' : 'file',
      });
    } catch (err) {
      res.status(500).json({ error: err.message });
    }
  });

  return router;
}

// ─── Public Stream Endpoint ─────────────────────────────────
// GET /stream/:token/:filename
// This is mounted at the app level, not under /api/stream
function createPublicStreamHandler(sshConfig, options = {}) {
  const canAccessPath = typeof options.canAccessPath === 'function' ? options.canAccessPath : () => true;

  function getFileSize(filePath) {
    return new Promise((resolve, reject) => {
      const sizeConn = new Client();
      sizeConn.on('ready', () => {
        sizeConn.exec(`stat -c%s ${shEsc(filePath)} 2>/dev/null || echo 0`, (err, stream) => {
          if (err) { sizeConn.end(); return reject(err); }
          let data = '';
          stream.on('data', (chunk) => { data += chunk; });
          stream.stderr.on('data', () => {});
          stream.on('close', () => { sizeConn.end(); resolve(parseInt(data.trim(), 10) || 0); });
        });
      });
      sizeConn.on('error', reject);
      sizeConn.connect(sshConfig);
    });
  }

  function renderPlayerPage(req, res, tokenData, urls, kind) {
    const filename = tokenData.filename || path.basename(tokenData.file_path);
    const title = htmlEsc(filename);
    const media = kind === 'audio'
      ? `<audio controls preload="metadata" src="${htmlEsc(urls.mediaUrl)}"></audio>`
      : kind === 'video'
        ? `<video controls playsinline preload="metadata" src="${htmlEsc(urls.mediaUrl)}"></video>`
        : `<p class="notice">This file type does not have an in-browser media player.</p>`;
    res.setHeader('Content-Type', 'text/html; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.send(`<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>${title}</title>
  <style>
    :root{color-scheme:dark;background:#0b0f16;color:#e6edf3;font-family:system-ui,-apple-system,Segoe UI,sans-serif}
    body{margin:0;min-height:100vh;display:grid;place-items:center;padding:24px}
    main{width:min(1100px,100%)}
    h1{font-size:18px;font-weight:650;margin:0 0 12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
    video,audio{width:100%;max-height:78vh;background:#000;border:1px solid #263244;border-radius:8px}
    audio{background:#111827;padding:24px;box-sizing:border-box}
    .bar{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px;align-items:center}
    a,button{border:1px solid #303b4d;background:#111827;color:#e6edf3;border-radius:6px;padding:8px 10px;text-decoration:none;font:inherit;cursor:pointer}
    a:hover,button:hover{border-color:#4b80ff;color:#9ec5ff}
    .notice{border:1px solid #303b4d;border-radius:8px;padding:24px;background:#111827}
    .hint{color:#9aa6b2;font-size:12px;margin-top:10px;line-height:1.5}
  </style>
</head>
<body>
  <main>
    <h1>${title}</h1>
    ${media}
    <div class="bar">
      <button onclick="navigator.clipboard.writeText(location.href)">Copy player link</button>
      <a href="${htmlEsc(urls.directUrl)}">Direct file</a>
      <a href="${htmlEsc(urls.mediaUrl)}">Browser-compatible stream</a>
    </div>
    <div class="hint">The player stream transcodes audio for browser playback when needed. Use Direct file for VLC or download-style clients.</div>
  </main>
</body>
</html>`);
  }

  function streamBrowserCompatible(res, filePath, kind) {
    if (kind !== 'video' && kind !== 'audio') {
      res.status(415).json({ error: 'This file type does not support browser-compatible streaming' });
      return;
    }

    const conn = new Client();
    conn.on('ready', () => {
      const input = shEsc(filePath);
      const cmd = kind === 'audio'
        ? `ffmpeg -hide_banner -loglevel error -i ${input} -vn -c:a libmp3lame -b:a 192k -f mp3 pipe:1`
        : `ffmpeg -hide_banner -loglevel error -i ${input} -map 0:v:0? -map 0:a:0? -c:v libx264 -preset veryfast -crf 23 -c:a aac -b:a 192k -ac 2 -movflags frag_keyframe+empty_moov+default_base_moof -f mp4 pipe:1`;
      res.writeHead(200, {
        'Content-Type': kind === 'audio' ? 'audio/mpeg' : 'video/mp4',
        'Content-Disposition': `inline; filename="${path.basename(filePath).replace(/"/g, '')}"`,
        'Transfer-Encoding': 'chunked',
        'Cache-Control': 'no-cache',
        'Access-Control-Allow-Origin': '*',
      });
      conn.exec(cmd, (err, stream) => {
        if (err) { conn.end(); if (!res.headersSent) res.status(500).end(); else res.end(); return; }
        stream.pipe(res);
        stream.on('close', () => conn.end());
        stream.stderr.on('data', () => {});
        res.on('close', () => { try { stream.destroy(); } catch (_) {} conn.end(); });
      });
    });
    conn.on('error', (err) => { if (!res.headersSent) res.status(500).json({ error: err.message }); });
    conn.connect(sshConfig);
  }

  async function streamOriginal(req, res, filePath, mimeType) {
    const fileSize = await getFileSize(filePath);
    if (fileSize === 0) return res.status(404).json({ error: 'File not found' });

    const range = req.headers.range;
    const streamConn = new Client();

    streamConn.on('ready', () => {
      streamConn.sftp((err, sftp) => {
        if (err) { streamConn.end(); return res.status(500).end(); }

        if (range) {
          const parts = range.replace(/bytes=/, '').split('-');
          const start = parseInt(parts[0], 10);
          const end = parts[1] ? parseInt(parts[1], 10) : Math.min(start + 10 * 1024 * 1024 - 1, fileSize - 1);
          if (!Number.isFinite(start) || !Number.isFinite(end) || start < 0 || end < start || start >= fileSize) {
            streamConn.end();
            res.writeHead(416, { 'Content-Range': `bytes */${fileSize}` });
            return res.end();
          }
          const chunkSize = end - start + 1;

          res.writeHead(206, {
            'Content-Range': `bytes ${start}-${end}/${fileSize}`,
            'Accept-Ranges': 'bytes',
            'Content-Length': chunkSize,
            'Content-Type': mimeType,
            'Content-Disposition': 'inline',
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
          });

          const readStream = sftp.createReadStream(filePath, { start, end: end + 1 });
          readStream.pipe(res);
          readStream.on('end', () => streamConn.end());
          readStream.on('error', () => { streamConn.end(); if (!res.headersSent) res.status(500).end(); });
        } else {
          res.writeHead(200, {
            'Accept-Ranges': 'bytes',
            'Content-Length': fileSize,
            'Content-Type': mimeType,
            'Content-Disposition': 'inline',
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
          });

          const readStream = sftp.createReadStream(filePath);
          readStream.pipe(res);
          readStream.on('end', () => streamConn.end());
          readStream.on('error', () => { streamConn.end(); if (!res.headersSent) res.status(500).end(); });
        }
      });
    });

    streamConn.on('error', (err) => {
      if (!res.headersSent) res.status(500).json({ error: err.message });
    });
    streamConn.connect(sshConfig);
  }

  return async (req, res) => {
    try {
      const { token } = req.params;
      const tokenData = validateStreamToken(token);

      if (!tokenData) {
        return res.status(404).send(`
          <html><body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#0d1117;color:#e6edf3">
          <div style="text-align:center"><h1>Link Expired</h1><p>This stream link has expired or is invalid.</p></div>
          </body></html>
        `);
      }

      const filePath = tokenData.file_path;
      if (!(await canAccessPath(filePath))) return res.status(403).json({ error: 'Cannot stream this path' });
      const mimeType = getMime(filePath);
      const filename = tokenData.filename || path.basename(filePath);
      const urls = streamUrls(req, token, filename);
      const kind = isVideoFile(filePath) ? 'video' : isAudioFile(filePath) ? 'audio' : 'file';

      trackRecent(filePath, 'stream');
      if (req.query.media === '1') return streamBrowserCompatible(res, filePath, kind);
      if (req.query.raw === '1' || req.headers.range || kind === 'file') return streamOriginal(req, res, filePath, mimeType);
      return renderPlayerPage(req, res, tokenData, urls, kind);
    } catch (err) {
      if (!res.headersSent) res.status(500).json({ error: err.message });
    }
  };
}

module.exports = { router: createStreamRouter, createStreamRouter, createPublicStreamHandler };
