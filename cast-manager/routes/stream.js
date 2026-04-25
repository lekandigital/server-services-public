// ═══════════════════════════════════════════════════════════════
// Cast Manager v4 — Token-Based Public Streaming Routes
// ═══════════════════════════════════════════════════════════════

const express = require('express');
const path = require('path');
const { Client } = require('ssh2');
const { generateStreamToken, validateStreamToken, logActivity, trackRecent } = require('../db');

const router = express.Router();

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

// ─── Generate Token ──────────────────────────────────────────
// POST /api/stream/generate
router.post('/generate', (req, res) => {
  try {
    const { filePath, expiresIn } = req.body;
    if (!filePath) return res.status(400).json({ error: 'filePath required' });

    const filename = path.basename(filePath);
    const hours = parseInt(expiresIn) || 24;
    const { token, expiresAt } = generateStreamToken(filePath, filename, hours);

    // Build the stream URL using the request's host
    const proto = req.headers['x-forwarded-proto'] || req.protocol || 'http';
    const host = req.headers['x-forwarded-host'] || req.headers.host;
    const streamUrl = `${proto}://${host}/stream/${token}/${encodeURIComponent(filename)}`;

    logActivity('stream_url_generated', filePath, { token, expiresIn: hours });

    res.json({ streamUrl, expiresAt, token });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── Public Stream Endpoint ─────────────────────────────────
// GET /stream/:token/:filename
// This is mounted at the app level, not under /api/stream
function createPublicStreamHandler(sshConfig) {
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
      const mimeType = getMime(filePath);

      // Get file size via SSH
      const { Client: SSHClient } = require('ssh2');
      const conn = new SSHClient();

      // Helper to get file size
      const getFileSize = () => new Promise((resolve, reject) => {
        const sizeConn = new SSHClient();
        sizeConn.on('ready', () => {
          sizeConn.exec(`stat -c%s "${filePath}" 2>/dev/null || echo 0`, (err, stream) => {
            if (err) { sizeConn.end(); return reject(err); }
            let data = '';
            stream.on('data', (chunk) => { data += chunk; });
            stream.stderr.on('data', () => {});
            stream.on('close', () => { sizeConn.end(); resolve(parseInt(data.trim()) || 0); });
          });
        });
        sizeConn.on('error', reject);
        sizeConn.connect(sshConfig);
      });

      const fileSize = await getFileSize();
      if (fileSize === 0) return res.status(404).json({ error: 'File not found' });

      const range = req.headers.range;
      const streamConn = new SSHClient();

      streamConn.on('ready', () => {
        streamConn.sftp((err, sftp) => {
          if (err) { streamConn.end(); return res.status(500).end(); }

          if (range) {
            const parts = range.replace(/bytes=/, '').split('-');
            const start = parseInt(parts[0], 10);
            const end = parts[1] ? parseInt(parts[1], 10) : Math.min(start + 10 * 1024 * 1024 - 1, fileSize - 1);
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

      trackRecent(filePath, 'stream');
    } catch (err) {
      if (!res.headersSent) res.status(500).json({ error: err.message });
    }
  };
}

module.exports = { router, createPublicStreamHandler };
