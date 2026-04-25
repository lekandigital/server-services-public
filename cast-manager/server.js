require('dotenv').config();
const express = require('express');
const { Client } = require('ssh2');
const path = require('path');
const multer = require('multer');
const http = require('http');
const fs = require('fs');
const crypto = require('crypto');

// Database + route modules
const db = require('./db');
const { initDB, logActivity, trackRecent, starFile, unstarFile, isStarred, getStarred,
  addToTrash, getTrash, getTrashItem, removeFromTrash, getRecent,
  createShare, getShare, listShares, revokeShare, updateShare,
  createTag, getTags, tagFile, untagFile, getFileTags, getFilesByTag,
  indexFile, searchFiles, clearFileIndex, getActivity,
  generateStreamToken, listStreamTokens, revokeStreamToken,
} = db;
const { router: streamRouter, createPublicStreamHandler } = require('./routes/stream');
const QRCode = require('qrcode');
const archiver = require('archiver');
const { nanoid } = require('nanoid');
const WebSocket = require('ws');

// MIME type mapping for streaming
const MIME_TYPES = {
  '.mp4': 'video/mp4', '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo',
  '.webm': 'video/webm', '.mov': 'video/quicktime', '.m4v': 'video/mp4',
  '.ts': 'video/mp2t', '.flv': 'video/x-flv', '.wmv': 'video/x-ms-wmv',
  '.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.aac': 'audio/aac',
  '.ogg': 'audio/ogg', '.wav': 'audio/wav', '.m4a': 'audio/mp4',
  '.wma': 'audio/x-ms-wma', '.opus': 'audio/opus',
  '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png',
  '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml',
  '.pdf': 'application/pdf', '.txt': 'text/plain', '.srt': 'text/plain',
  '.vtt': 'text/vtt', '.ass': 'text/plain', '.sub': 'text/plain',
};
function getMimeType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return MIME_TYPES[ext] || 'application/octet-stream';
}

// Path safety check: must be under /home/REDACTED_USER/ and not a critical path
function isPathSafe(filePath) {
  const homeDir = '/home/REDACTED_USER';
  const critical = [homeDir, '/', '/home', '/root', '/etc', '/var', '/usr', '/bin', '/sbin'];
  return filePath.startsWith(homeDir + '/') && !critical.includes(filePath) && !filePath.includes('..');
}

// Shell escape helper
function escCmd(str) {
  return typeof str === 'string' ? `'${str.replace(/'/g, "'\\''")}'` : "''";
}

const app = express();
const upload = multer({ dest: '/tmp/cast_uploads/' });

app.use(express.json({ limit: '50mb' }));
app.use(express.urlencoded({ extended: true }));
app.use(express.static(path.join(__dirname, 'public')));

// CORS for network access
app.use((req, res, next) => {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS');
  res.header('Access-Control-Allow-Headers', 'Content-Type');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

// Config from env
const CFG = {
  sshHost: process.env.SSH_HOST || 'REDACTED_SERVER_IP',
  sshUser: process.env.SSH_USER || 'o',
  sshPass: process.env.SSH_PASSWORD || 'REDACTED_PASSWORD',
  transUser: process.env.TRANSMISSION_USER || 'transmission',
  transPass: process.env.TRANSMISSION_PASS || 'transmission',
  downloadDir: process.env.DOWNLOAD_DIR || '/home/REDACTED_USER/watch_list',
  chromecastName: process.env.CHROMECAST_NAME || 'REDACTED_DEVICE',
  cattPath: process.env.CATT_PATH || '/home/REDACTED_USER/.local/bin/catt',
  port: parseInt(process.env.PORT || '3000', 10),
};

// ─── SSH Helper ──────────────────────────────────────────────
function sshExec(command, timeout = 30000) {
  return new Promise((resolve, reject) => {
    const conn = new Client();
    let stdout = '';
    let stderr = '';
    const timer = setTimeout(() => {
      conn.end();
      reject(new Error('SSH command timed out'));
    }, timeout);

    conn.on('ready', () => {
      conn.exec(command, (err, stream) => {
        if (err) { clearTimeout(timer); conn.end(); return reject(err); }
        stream.on('close', (code) => {
          clearTimeout(timer);
          conn.end();
          resolve({ stdout: stdout.trim(), stderr: stderr.trim(), code });
        });
        stream.on('data', (d) => { stdout += d.toString(); });
        stream.stderr.on('data', (d) => { stderr += d.toString(); });
      });
    });
    conn.on('error', (err) => { clearTimeout(timer); reject(err); });
    conn.connect({
      host: CFG.sshHost,
      port: 22,
      username: CFG.sshUser,
      password: CFG.sshPass,
      readyTimeout: 10000,
    });
  });
}

// Transmission-remote helper
function trCmd(args) {
  return sshExec(`transmission-remote -n '${CFG.transUser}:${CFG.transPass}' ${args}`);
}

// Catt helper
function cattCmd(args, timeout = 15000) {
  return sshExec(`${CFG.cattPath} -d '${CFG.chromecastName}' ${args}`, timeout);
}

// ─── TORRENT ENDPOINTS ──────────────────────────────────────
app.get('/api/torrents', async (req, res) => {
  try {
    const { stdout } = await trCmd('-l');
    const lines = stdout.split('\n');
    if (lines.length < 2) return res.json({ torrents: [], stats: {} });

    const torrents = [];
    // skip header and summary line
    for (let i = 1; i < lines.length - 1; i++) {
      const line = lines[i].trim();
      if (!line) continue;
      // Parse transmission-remote -l output
      // ID  Done  Have  ETA  Up  Down  Ratio  Status  Name
      const match = line.match(
        /^\s*(\d+)\*?\s+([\d.]+%|n\/a)\s+(.+?)\s{2,}(.+?)\s{2,}([\d.]+)\s+([\d.]+)\s+([\d.]+|None)\s+(.+?)\s{2,}(.+)$/
      );
      if (match) {
        torrents.push({
          id: parseInt(match[1]),
          done: match[2],
          have: match[3],
          eta: match[4],
          up: match[5],
          down: match[6],
          ratio: match[7],
          status: match[8],
          name: match[9].trim(),
        });
      }
    }

    // Parse summary line
    const summaryLine = lines[lines.length - 1] || '';
    const stats = { total: torrents.length, summaryLine };

    res.json({ torrents, stats });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/torrents', async (req, res) => {
  try {
    const { magnet, magnets } = req.body;
    const links = magnets || (magnet ? [magnet] : []);
    if (!links.length) return res.status(400).json({ error: 'No magnet link provided' });

    const results = [];
    for (const link of links) {
      try {
        const { stdout, stderr } = await trCmd(`-a "${link}"`);
        results.push({ link: link.substring(0, 80), success: true, message: stdout || 'Added' });
      } catch (e) {
        results.push({ link: link.substring(0, 80), success: false, message: e.message });
      }
    }
    res.json({ results });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/torrents/upload', upload.single('torrent'), async (req, res) => {
  try {
    if (!req.file) return res.status(400).json({ error: 'No file uploaded' });
    const localPath = req.file.path;
    const remotePath = `/tmp/${req.file.originalname}`;

    // Upload via SFTP then add
    const conn = new Client();
    conn.on('ready', () => {
      conn.sftp((err, sftp) => {
        if (err) { conn.end(); return res.status(500).json({ error: err.message }); }
        sftp.fastPut(localPath, remotePath, async (err) => {
          conn.end();
          fs.unlinkSync(localPath);
          if (err) return res.status(500).json({ error: err.message });
          try {
            const { stdout } = await trCmd(`-a "${remotePath}"`);
            res.json({ success: true, message: stdout });
          } catch (e) {
            res.status(500).json({ error: e.message });
          }
        });
      });
    });
    conn.on('error', (err) => res.status(500).json({ error: err.message }));
    conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/torrents/:id/pause', async (req, res) => {
  try {
    await trCmd(`-t ${req.params.id} -S`);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/torrents/:id/resume', async (req, res) => {
  try {
    await trCmd(`-t ${req.params.id} -s`);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/torrents/:id', async (req, res) => {
  try {
    const deleteData = req.query.deleteData === 'true';
    await trCmd(`-t ${req.params.id} ${deleteData ? '-rad' : '-r'}`);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/torrents/:id/priority', async (req, res) => {
  try {
    const { priority } = req.body; // high, normal, low
    const flag = priority === 'high' ? '-ph' : priority === 'low' ? '-pl' : '-pn';
    await trCmd(`-t ${req.params.id} -Bh ${flag}`);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/torrents/batch', async (req, res) => {
  try {
    const { ids, action } = req.body; // action: pause, resume, remove
    const results = [];
    for (const id of ids) {
      try {
        if (action === 'pause') await trCmd(`-t ${id} -S`);
        else if (action === 'resume') await trCmd(`-t ${id} -s`);
        else if (action === 'remove') await trCmd(`-t ${id} -r`);
        results.push({ id, success: true });
      } catch (e) {
        results.push({ id, success: false, error: e.message });
      }
    }
    res.json({ results });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/torrents/:id/info', async (req, res) => {
  try {
    const { stdout } = await trCmd(`-t ${req.params.id} -i`);
    res.json({ info: stdout });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/torrents/pause-all', async (req, res) => {
  try { await trCmd('-t all -S'); res.json({ success: true }); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/torrents/resume-all', async (req, res) => {
  try { await trCmd('-t all -s'); res.json({ success: true }); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── FILE BROWSER ENDPOINTS ─────────────────────────────────
app.get('/api/files', async (req, res) => {
  try {
    const dir = req.query.path || CFG.downloadDir;
    // Get file listing with details
    const { stdout } = await sshExec(
      `find "${dir}" -maxdepth 1 -printf '%y\\t%s\\t%T@\\t%p\\n' 2>/dev/null | sort -t$'\\t' -k4`
    );
    const files = [];
    const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v'];
    const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus'];
    const subExts = ['.srt', '.ass', '.vtt', '.sub'];
    const folders = [];

    for (const line of stdout.split('\n')) {
      if (!line.trim()) continue;
      const [type, size, mtime, ...pathParts] = line.split('\t');
      const filePath = pathParts.join('\t');
      if (filePath === dir) continue;
      const name = path.basename(filePath);
      const ext = path.extname(name).toLowerCase();
      let fileType = 'other';
      if (type === 'd') fileType = 'folder';
      else if (videoExts.includes(ext)) fileType = 'video';
      else if (audioExts.includes(ext)) fileType = 'audio';
      else if (subExts.includes(ext)) fileType = 'subtitle';
      else if (ext === '.torrent') fileType = 'torrent';

      const entry = {
        name,
        path: filePath,
        type: fileType,
        size: parseInt(size) || 0,
        mtime: parseFloat(mtime) || 0,
        ext,
      };
      files.push(entry);
      if (fileType === 'folder') folders.push(entry);
    }

    // Get folder sizes in parallel (du -sb for byte-accurate sizes)
    if (folders.length > 0) {
      try {
        const duPaths = folders.map(f => `"${f.path}"`).join(' ');
        const { stdout: duOut } = await sshExec(
          `du -sb ${duPaths} 2>/dev/null`
        );
        const sizeMap = {};
        for (const line of duOut.split('\n')) {
          if (!line.trim()) continue;
          const tabIdx = line.indexOf('\t');
          if (tabIdx > 0) {
            const s = parseInt(line.substring(0, tabIdx)) || 0;
            const p = line.substring(tabIdx + 1).trim();
            sizeMap[p] = s;
          }
        }
        for (const f of folders) {
          if (sizeMap[f.path] !== undefined) {
            f.size = sizeMap[f.path];
          }
        }
      } catch (e) { /* du failed, folder sizes stay at 0 */ }
    }

    // Count items in folders
    if (folders.length > 0) {
      try {
        const countPaths = folders.map(f => `"${f.path}"`).join(' ');
        const { stdout: countOut } = await sshExec(
          `for d in ${countPaths}; do echo "$(find "$d" -maxdepth 1 -not -path "$d" 2>/dev/null | wc -l)\t$d"; done`
        );
        const countMap = {};
        for (const line of countOut.split('\n')) {
          if (!line.trim()) continue;
          const tabIdx = line.indexOf('\t');
          if (tabIdx > 0) {
            countMap[line.substring(tabIdx + 1).trim()] = parseInt(line.substring(0, tabIdx)) || 0;
          }
        }
        for (const f of folders) {
          f.itemCount = countMap[f.path] || 0;
        }
      } catch (e) { /* skip item counts */ }
    }

    // Sort: folders first, then by name
    files.sort((a, b) => {
      if (a.type === 'folder' && b.type !== 'folder') return -1;
      if (a.type !== 'folder' && b.type === 'folder') return 1;
      return a.name.localeCompare(b.name);
    });

    res.json({ files, currentPath: dir, parentPath: path.dirname(dir) });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/files/info', async (req, res) => {
  try {
    const { filePath } = req.body;
    const { stdout } = await sshExec(
      `ffprobe -v error -show_entries format=duration,size,bit_rate -show_entries stream=codec_name,codec_type,width,height,channels -of json "${filePath}"`
    );
    res.json(JSON.parse(stdout));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/files/duration', async (req, res) => {
  try {
    const { filePath } = req.body;
    const { stdout } = await sshExec(
      `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "${filePath}"`
    );
    res.json({ duration: parseFloat(stdout) || 0 });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/files/delete', async (req, res) => {
  try {
    const { filePath, permanent, sudoPwd } = req.body;
    if (!isPathSafe(filePath)) {
      return res.status(403).json({ error: 'Cannot delete this path' });
    }
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    if (permanent) {
      await sshExec(`${sudoPrefix}rm -rf ${escCmd(filePath)}`);
      logActivity('delete_permanent', filePath);
    } else {
      // Move to trash
      const trashDir = process.env.TRASH_DIR || '/home/REDACTED_USER/.cast_manager/trash';
      const filename = path.basename(filePath);
      const trashName = `${Date.now()}_${filename}`;
      const trashPath = `${trashDir}/${trashName}`;
      await sshExec(`mkdir -p ${escCmd(trashDir)}`);
      // Get file size and type before moving
      const { stdout: sizeStr } = await sshExec(`stat -c%s ${escCmd(filePath)} 2>/dev/null || echo 0`);
      const size = parseInt(sizeStr.trim()) || 0;
      const ext = path.extname(filename).toLowerCase();
      const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v'];
      const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus'];
      const fileType = videoExts.includes(ext) ? 'video' : audioExts.includes(ext) ? 'audio' : 'other';
      await sshExec(`${sudoPrefix}mv ${escCmd(filePath)} ${escCmd(trashPath)}`);
      addToTrash(filePath, trashPath, filename, fileType, size);
      logActivity('trash', filePath, { trashPath });
    }
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/files/rename', async (req, res) => {
  try {
    const { oldPath, newName, sudoPwd } = req.body;
    if (!isPathSafe(oldPath)) {
      return res.status(403).json({ error: 'Cannot rename this path' });
    }
    const dir = path.dirname(oldPath);
    const newPath = path.join(dir, newName);
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    await sshExec(`${sudoPrefix}mv ${escCmd(oldPath)} ${escCmd(newPath)}`);
    res.json({ success: true, newPath });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/files/copy', async (req, res) => {
  try {
    const { filePath, destName, sudoPwd } = req.body;
    if (!isPathSafe(filePath)) {
      return res.status(403).json({ error: 'Cannot copy this path' });
    }
    const dir = path.dirname(filePath);
    const newPath = path.join(dir, destName);
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    await sshExec(`${sudoPrefix}cp -r ${escCmd(filePath)} ${escCmd(newPath)}`, 120000);
    res.json({ success: true, newPath });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/files/mkdir', async (req, res) => {
  try {
    const { parentPath, name, sudoPwd } = req.body;
    const newDir = path.join(parentPath, name);
    if (!isPathSafe(newDir)) {
      return res.status(403).json({ error: 'Cannot create directory here' });
    }
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    await sshExec(`${sudoPrefix}mkdir -p ${escCmd(newDir)}`);
    res.json({ success: true, path: newDir });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/files/move', async (req, res) => {
  try {
    const { sourcePath, destDir, sudoPwd } = req.body;
    if (!isPathSafe(sourcePath) || !isPathSafe(destDir + '/x')) {
      return res.status(403).json({ error: 'Cannot move this path' });
    }
    const fileName = path.basename(sourcePath);
    const destPath = path.join(destDir, fileName);
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    await sshExec(`${sudoPrefix}mv ${escCmd(sourcePath)} ${escCmd(destPath)}`);
    res.json({ success: true, newPath: destPath });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/files/download', async (req, res) => {
  try {
    const filePath = req.query.path;
    if (!filePath) {
      return res.status(400).json({ error: 'No file path provided' });
    }
    const fileName = path.basename(filePath);

    // Get file size first
    const { stdout: sizeStr } = await sshExec(`stat -c%s "${filePath}" 2>/dev/null || echo 0`);
    const fileSize = parseInt(sizeStr) || 0;

    const conn = new Client();
    conn.on('ready', () => {
      conn.sftp((err, sftp) => {
        if (err) { conn.end(); return res.status(500).json({ error: err.message }); }
        const readStream = sftp.createReadStream(filePath);

        res.set('Content-Disposition', `attachment; filename="${encodeURIComponent(fileName)}"`);
        res.set('Content-Type', 'application/octet-stream');
        if (fileSize > 0) res.set('Content-Length', String(fileSize));

        readStream.pipe(res);
        readStream.on('end', () => conn.end());
        readStream.on('error', (e) => { conn.end(); if (!res.headersSent) res.status(500).end(); });
      });
    });
    conn.on('error', (err) => {
      if (!res.headersSent) res.status(500).json({ error: err.message });
    });
    conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
  } catch (err) {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  }
});

// ─── STREAMING ENDPOINT (Range support + FFmpeg transcode for browser) ───
app.get('/api/files/stream', async (req, res) => {
  try {
    const filePath = req.query.path;
    if (!filePath) return res.status(400).json({ error: 'No file path provided' });

    const ext = path.extname(filePath).toLowerCase();
    const mimeType = getMimeType(filePath);
    const { stdout: sizeStr } = await sshExec(`stat -c%s "${filePath}" 2>/dev/null || echo 0`);
    const fileSize = parseInt(sizeStr.trim()) || 0;
    if (fileSize === 0) return res.status(404).json({ error: 'File not found or empty' });

    // Check if this is a media file that might need transcoding
    const videoExts = ['.mkv', '.avi', '.wmv', '.flv', '.ts', '.m4v', '.mov', '.mp4', '.webm'];
    const audioExts = ['.flac', '.wma', '.ogg', '.wav', '.m4a', '.aac', '.mp3', '.opus'];
    const isMedia = videoExts.includes(ext) || audioExts.includes(ext);
    const isVideo = videoExts.includes(ext);

    if (isMedia && !req.query.raw) {
      // Probe audio codec to check if transcoding is needed
      try {
        const { stdout: probeOut } = await sshExec(
          `ffprobe -v quiet -print_format json -show_streams -select_streams a:0 "${filePath}" 2>/dev/null`
        );
        const probeData = JSON.parse(probeOut);
        const audioStream = probeData.streams && probeData.streams[0];
        const audioCodec = audioStream ? audioStream.codec_name : '';

        const incompatibleCodecs = ['ac3', 'eac3', 'dts', 'truehd', 'dts_hd_ma', 'dts_hd', 'mlp', 'flac', 'pcm_s16le', 'pcm_s24le'];
        const needsTranscode = incompatibleCodecs.some(c => audioCodec.includes(c));

        if (needsTranscode) {
          // Check transcoding cache first
          const cacheDir = '/tmp/cast_manager_cache';
          const cacheHash = crypto.createHash('md5').update(filePath).digest('hex');
          const cachePath = `${cacheDir}/${cacheHash}.mp4`;

          const { stdout: cacheCheck } = await sshExec(`test -f "${cachePath}" && stat -c%s "${cachePath}" 2>/dev/null || echo 0`);
          const cacheSize = parseInt(cacheCheck.trim()) || 0;

          if (cacheSize > 0) {
            // Serve from cache with range support
            const range = req.headers.range;
            const conn = new Client();
            conn.on('ready', () => {
              conn.sftp((err, sftp) => {
                if (err) { conn.end(); return res.status(500).end(); }
                if (range) {
                  const parts = range.replace(/bytes=/, '').split('-');
                  const start = parseInt(parts[0], 10);
                  const end = parts[1] ? parseInt(parts[1], 10) : Math.min(start + 10*1024*1024 - 1, cacheSize - 1);
                  res.writeHead(206, {
                    'Content-Range': `bytes ${start}-${end}/${cacheSize}`,
                    'Accept-Ranges': 'bytes',
                    'Content-Length': end - start + 1,
                    'Content-Type': 'video/mp4',
                    'Cache-Control': 'no-cache',
                    'Access-Control-Allow-Origin': '*',
                    'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
                  });
                  const rs = sftp.createReadStream(cachePath, { start, end: end + 1 });
                  rs.pipe(res);
                  rs.on('end', () => conn.end());
                  rs.on('error', () => { conn.end(); if (!res.headersSent) res.status(500).end(); });
                } else {
                  res.writeHead(200, {
                    'Accept-Ranges': 'bytes',
                    'Content-Length': cacheSize,
                    'Content-Type': 'video/mp4',
                    'Access-Control-Allow-Origin': '*',
                  });
                  const rs = sftp.createReadStream(cachePath);
                  rs.pipe(res);
                  rs.on('end', () => conn.end());
                  rs.on('error', () => { conn.end(); if (!res.headersSent) res.status(500).end(); });
                }
              });
            });
            conn.on('error', () => { if (!res.headersSent) res.status(500).end(); });
            conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
            return;
          }

          // No cache — transcode on-the-fly (fragmented MP4, streamable)
          res.writeHead(200, {
            'Content-Type': 'video/mp4',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
          });

          const transcodeConn = new Client();
          transcodeConn.on('ready', () => {
            // Transcode: copy video, re-encode audio to AAC, fragmented MP4 for streaming
            const cmd = `mkdir -p ${cacheDir} && ffmpeg -i "${filePath}" -c:v copy -c:a aac -b:a 192k -ac 2 -movflags frag_keyframe+empty_moov+default_base_moof -f mp4 pipe:1 2>/dev/null | tee "${cachePath}"`;
            transcodeConn.exec(cmd, (err, stream) => {
              if (err) { transcodeConn.end(); return res.end(); }
              stream.pipe(res);
              stream.on('close', () => transcodeConn.end());
              stream.stderr.on('data', () => {}); // discard stderr
              res.on('close', () => { stream.destroy(); transcodeConn.end(); });
            });
          });
          transcodeConn.on('error', () => { if (!res.headersSent) res.status(500).end(); });
          transcodeConn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
          return;
        }
      } catch (probeErr) {
        // ffprobe failed — fall through to direct streaming
      }
    }

    // Direct streaming (compatible codec or non-media file)
    const range = req.headers.range;
    const conn = new Client();

    conn.on('ready', () => {
      conn.sftp((err, sftp) => {
        if (err) { conn.end(); return res.status(500).json({ error: err.message }); }

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
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
          });

          const readStream = sftp.createReadStream(filePath, { start, end: end + 1 });
          readStream.pipe(res);
          readStream.on('end', () => conn.end());
          readStream.on('error', () => { conn.end(); if (!res.headersSent) res.status(500).end(); });
        } else {
          res.writeHead(200, {
            'Accept-Ranges': 'bytes',
            'Content-Length': fileSize,
            'Content-Type': mimeType,
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': 'Content-Range, Content-Length',
          });

          const readStream = sftp.createReadStream(filePath);
          readStream.pipe(res);
          readStream.on('end', () => conn.end());
          readStream.on('error', () => { conn.end(); if (!res.headersSent) res.status(500).end(); });
        }
      });
    });

    conn.on('error', (err) => {
      if (!res.headersSent) res.status(500).json({ error: err.message });
    });
    conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
  } catch (err) {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  }
});

// ─── THUMBNAIL ENDPOINTS ────────────────────────────────────
app.post('/api/thumbnail', async (req, res) => {
  try {
    const { filePath, type } = req.body;
    const thumbDir = `${CFG.downloadDir}/.thumbnails`;
    await sshExec(`mkdir -p "${thumbDir}"`);

    // Create filename hash from path
    const thumbName = Buffer.from(filePath).toString('base64').replace(/[/+=]/g, '_') + '.jpg';
    const thumbPath = `${thumbDir}/${thumbName}`;

    // Check if already exists
    const { code: existsCode } = await sshExec(`test -f "${thumbPath}" && echo exists`);
    if (existsCode === 0) {
      return res.json({ thumbnail: `/api/thumbnail/serve/${thumbName}` });
    }

    if (type === 'video') {
      // Get duration first to extract frame at 10%
      const { stdout: durStr } = await sshExec(
        `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "${filePath}"`
      );
      const dur = parseFloat(durStr) || 60;
      const seekTo = Math.floor(dur * 0.1);
      await sshExec(
        `ffmpeg -y -ss ${seekTo} -i "${filePath}" -vframes 1 -q:v 3 -vf scale=400:-1 "${thumbPath}" 2>/dev/null`,
        60000
      );
    } else if (type === 'audio') {
      await sshExec(
        `ffmpeg -y -i "${filePath}" -an -vcodec copy "${thumbPath}" 2>/dev/null`,
        30000
      );
    }

    // Check if generation succeeded
    const { stdout: check } = await sshExec(`test -f "${thumbPath}" && echo ok`);
    if (check.includes('ok')) {
      res.json({ thumbnail: `/api/thumbnail/serve/${thumbName}` });
    } else {
      res.json({ thumbnail: null });
    }
  } catch (err) {
    res.json({ thumbnail: null });
  }
});

app.get('/api/thumbnail/serve/:name', async (req, res) => {
  try {
    const thumbPath = `${CFG.downloadDir}/.thumbnails/${req.params.name}`;
    const conn = new Client();
    conn.on('ready', () => {
      conn.sftp((err, sftp) => {
        if (err) { conn.end(); return res.status(500).end(); }
        const readStream = sftp.createReadStream(thumbPath);
        res.set('Content-Type', 'image/jpeg');
        res.set('Cache-Control', 'public, max-age=86400');
        readStream.pipe(res);
        readStream.on('end', () => conn.end());
        readStream.on('error', () => { conn.end(); res.status(404).end(); });
      });
    });
    conn.on('error', () => res.status(500).end());
    conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
  } catch (err) {
    res.status(500).end();
  }
});

// ─── CHROMECAST / CATT ENDPOINTS ─────────────────────────────
app.get('/api/devices', async (req, res) => {
  try {
    const { stdout } = await sshExec(`${CFG.cattPath} scan -t 5`, 15000);
    const devices = [];
    for (const line of stdout.split('\n')) {
      if (!line.trim()) continue;
      // Format: DeviceName - IP:PORT
      const match = line.match(/^(.+?)\s+-\s+(\d+\.\d+\.\d+\.\d+)/);
      if (match) {
        devices.push({ name: match[1].trim(), ip: match[2] });
      }
    }
    res.json({ devices });
  } catch (err) {
    res.json({ devices: [{ name: CFG.chromecastName, ip: 'REDACTED_CHROMECAST_IP' }] });
  }
});

app.post('/api/devices/select', (req, res) => {
  const { name } = req.body;
  if (name) CFG.chromecastName = name;
  res.json({ success: true, device: CFG.chromecastName });
});

app.get('/api/cast/status', async (req, res) => {
  try {
    const { stdout, stderr } = await cattCmd('status', 8000);
    const output = stdout || stderr;

    // Parse status output
    const info = { state: 'idle', title: '', currentTime: 0, duration: 0, volumeLevel: 100 };

    if (output.includes('Nothing is currently playing')) {
      info.state = 'idle';
    } else {
      // Try to parse time and duration
      const timeMatch = output.match(/Time:\s*([\d:.]+)\s*\/\s*([\d:.]+)/i);
      if (timeMatch) {
        info.currentTime = parseTimeToSeconds(timeMatch[1]);
        info.duration = parseTimeToSeconds(timeMatch[2]);
      }

      const stateMatch = output.match(/State:\s*(\w+)/i);
      if (stateMatch) {
        info.state = stateMatch[1].toLowerCase();
      }

      const titleMatch = output.match(/Title:\s*(.+)/i);
      if (titleMatch) info.title = titleMatch[1].trim();

      const volMatch = output.match(/Volume:\s*(\d+)/i);
      if (volMatch) info.volumeLevel = parseInt(volMatch[1]);

      if (!info.state || info.state === 'idle') {
        if (info.currentTime > 0) info.state = 'playing';
      }
    }

    res.json(info);
  } catch (err) {
    res.json({ state: 'idle', title: '', currentTime: 0, duration: 0, volumeLevel: 100 });
  }
});

function parseTimeToSeconds(timeStr) {
  if (!timeStr) return 0;
  const parts = timeStr.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] || 0;
}

app.post('/api/cast', async (req, res) => {
  try {
    const { filePath, seekTo } = req.body;

    // Check audio codec for compatibility
    const { stdout: codecInfo } = await sshExec(
      `ffprobe -v error -show_entries stream=codec_name -select_streams a:0 -of default=noprint_wrappers=1:nokey=1 "${filePath}"`
    );
    const audioCodec = codecInfo.trim().toLowerCase();
    const incompatibleCodecs = ['eac3', 'dts', 'truehd', 'ac3', 'dts_hd_ma', 'dts_hd', 'mlp'];
    const needsTranscode = incompatibleCodecs.some(c => audioCodec.includes(c));
    let castPath = filePath;

    if (needsTranscode) {
      // Transcode to AAC - write to /tmp since source dirs may be owned by debian-transmission
      const baseName = path.basename(filePath).replace(/\.[^.]+$/, '_aac.mkv');
      const transcodedPath = `/tmp/cast_transcodes/${baseName}`;
      const tempPath = transcodedPath + '.part';
      const { stdout: checkExist } = await sshExec(`test -f "${transcodedPath}" && echo exists`);

      if (!checkExist.includes('exists')) {
        await sshExec('mkdir -p /tmp/cast_transcodes');
        // Write transcode script via base64 to avoid shell quoting issues
        const script = `#!/bin/bash\nffmpeg -y -i "${filePath}" -c:v copy -c:a aac -ac 2 -b:a 256k -f matroska "${tempPath}" >>/tmp/ffmpeg_transcode.log 2>&1 && mv "${tempPath}" "${transcodedPath}"\n`;
        const b64 = Buffer.from(script).toString('base64');
        await sshExec(`echo '${b64}' | base64 -d > /tmp/cast_transcode.sh && chmod +x /tmp/cast_transcode.sh`);
        await sshExec(`nohup setsid /tmp/cast_transcode.sh </dev/null >/dev/null 2>&1 &`, 5000);
        res.json({
          success: true,
          transcoding: true,
          audioCodec,
          message: `Transcoding ${audioCodec.toUpperCase()} audio to AAC stereo. This may take a few minutes for large files. You'll be notified when it's ready to cast.`,
          transcodedPath,
        });
        return;
      }
      castPath = transcodedPath;
    }

    // Cast the file
    const seekArg = seekTo ? `-t ${seekTo}` : '';
    await cattCmd(`cast ${seekArg} "${castPath}"`, 30000);
    res.json({ success: true, transcoded: needsTranscode, audioCodec });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/cast/controls', async (req, res) => {
  try {
    const { action, value } = req.body;
    let result;
    switch (action) {
      case 'play': result = await cattCmd('play'); break;
      case 'pause': result = await cattCmd('pause'); break;
      case 'stop': result = await cattCmd('stop'); break;
      case 'seek': result = await cattCmd(`seek ${value}`); break;
      case 'volume': result = await cattCmd(`volume ${value}`); break;
      case 'skip': result = await cattCmd('skip'); break;
      default: return res.status(400).json({ error: 'Unknown action' });
    }
    res.json({ success: true, output: result?.stdout });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/stream', async (req, res) => {
  try {
    const { url } = req.body;
    // Determine if site-based or direct cast
    const sitePatterns = ['youtube.com', 'youtu.be', 'twitch.tv', 'vimeo.com', 'dailymotion.com'];
    const isSite = sitePatterns.some(p => url.includes(p));
    const cmd = isSite ? `cast_site "${url}"` : `cast "${url}"`;
    await cattCmd(cmd, 30000);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── AUDIO METADATA ─────────────────────────────────────────
app.post('/api/audio/metadata', async (req, res) => {
  try {
    const { filePath } = req.body;
    const { stdout } = await sshExec(
      `ffprobe -v error -show_entries format_tags=artist,album,title,genre,track -show_entries format=duration -of json "${filePath}"`
    );
    res.json(JSON.parse(stdout));
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── SUBTITLES ──────────────────────────────────────────────
app.post('/api/subtitles', async (req, res) => {
  try {
    const { filePath } = req.body;
    const dir = path.dirname(filePath);
    const base = path.basename(filePath, path.extname(filePath));
    const { stdout } = await sshExec(
      `find "${dir}" -maxdepth 1 -name "${base}*" \\( -name "*.srt" -o -name "*.ass" -o -name "*.vtt" -o -name "*.sub" \\) 2>/dev/null`
    );
    const subs = stdout.split('\n').filter(s => s.trim());
    res.json({ subtitles: subs });
  } catch (err) {
    res.json({ subtitles: [] });
  }
});

app.post('/api/cast/subtitles', async (req, res) => {
  try {
    const { filePath, subtitlePath, seekTo } = req.body;
    const seekArg = seekTo ? `-t ${seekTo}` : '';
    await cattCmd(`cast ${seekArg} --subtitles "${subtitlePath}" "${filePath}"`, 30000);
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── DISK SPACE ─────────────────────────────────────────────
app.get('/api/disk', async (req, res) => {
  try {
    const { stdout } = await sshExec(`df -h "${CFG.downloadDir}" | tail -1`);
    const parts = stdout.split(/\s+/);
    res.json({ size: parts[1], used: parts[2], available: parts[3], usePercent: parts[4] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── TRANSCODE STATUS ───────────────────────────────────────
app.post('/api/transcode/status', async (req, res) => {
  try {
    const { filePath } = req.body;
    const baseName = path.basename(filePath).replace(/\.[^.]+$/, '_aac.mkv');
    const transcodedPath = `/tmp/cast_transcodes/${baseName}`;
    const tempPath = transcodedPath + '.part';

    // Check if the final (renamed) file exists - this means transcoding completed
    const { stdout } = await sshExec(`test -f "${transcodedPath}" && echo exists`);
    const ready = stdout.includes('exists');

    // Check if ffmpeg is still running for this file
    const escapedName = path.basename(filePath).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const { stdout: procs } = await sshExec(`pgrep -af "ffmpeg.*${escapedName}" 2>/dev/null | head -1`);
    const processing = !!procs.trim();

    // Also check if the temp file exists (transcoding in progress)
    const { stdout: tmpCheck } = await sshExec(`test -f "${tempPath}" && stat -c%s "${tempPath}" 2>/dev/null || echo 0`);
    const tmpSize = parseInt(tmpCheck) || 0;

    res.json({ ready, processing: processing || tmpSize > 0, transcodedPath, tmpSize });
  } catch (err) {
    res.json({ ready: false, processing: false });
  }
});

// ─── MOUNT v4 ROUTES ────────────────────────────────────────
const sshConfig = { host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass };

// Token-based stream API
app.use('/api/stream', streamRouter);

// Public stream endpoint (no auth — token IS auth)
app.get('/stream/:token/:filename', createPublicStreamHandler(sshConfig));

// ─── MEDIA INFO ENDPOINT ────────────────────────────────────
app.get('/api/media/info', async (req, res) => {
  try {
    const filePath = req.query.path;
    if (!filePath) return res.status(400).json({ error: 'path required' });
    const { stdout } = await sshExec(
      `ffprobe -v quiet -print_format json -show_format -show_streams "${filePath}" 2>/dev/null`
    );
    const info = JSON.parse(stdout);
    const videoStream = (info.streams || []).find(s => s.codec_type === 'video');
    const audioStream = (info.streams || []).find(s => s.codec_type === 'audio');
    const incompatible = ['ac3', 'eac3', 'dts', 'truehd', 'dts_hd_ma', 'flac', 'pcm_s16le', 'pcm_s24le'];
    const audioCodec = audioStream ? audioStream.codec_name : 'unknown';
    res.json({
      filename: path.basename(filePath),
      size: parseInt(info.format?.size) || 0,
      duration: parseFloat(info.format?.duration) || 0,
      videoCodec: videoStream?.codec_name || null,
      audioCodec,
      audioChannels: audioStream?.channels || 0,
      needsTranscode: incompatible.some(c => audioCodec.includes(c)),
      resolution: videoStream ? `${videoStream.width}x${videoStream.height}` : null,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── STARRED ENDPOINTS ──────────────────────────────────────
app.post('/api/files/star', (req, res) => {
  try {
    const { path: filePath } = req.body;
    if (!filePath) return res.status(400).json({ error: 'path required' });
    starFile(filePath);
    logActivity('star', filePath);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/files/star', (req, res) => {
  try {
    const { path: filePath } = req.body;
    if (!filePath) return res.status(400).json({ error: 'path required' });
    unstarFile(filePath);
    logActivity('unstar', filePath);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/files/starred', (req, res) => {
  try {
    const starred = getStarred();
    res.json({ files: starred });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── RECENT FILES ENDPOINTS ─────────────────────────────────
app.get('/api/files/recent', (req, res) => {
  try {
    const limit = parseInt(req.query.limit) || 50;
    const recent = getRecent(limit);
    res.json({ files: recent });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── TRASH ENDPOINTS ────────────────────────────────────────
app.get('/api/files/trash', (req, res) => {
  try {
    const items = getTrash();
    res.json({ files: items });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/files/restore', async (req, res) => {
  try {
    const { id } = req.body;
    const item = getTrashItem(id);
    if (!item) return res.status(404).json({ error: 'Trash item not found' });
    // Restore: move back to original location
    const parentDir = path.dirname(item.original_path);
    await sshExec(`mkdir -p "${parentDir}"`);
    await sshExec(`mv "${item.trash_path}" "${item.original_path}"`);
    removeFromTrash(id);
    logActivity('restore', item.original_path);
    res.json({ success: true, restoredPath: item.original_path });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/files/trash/empty', async (req, res) => {
  try {
    const { sudoPwd } = req.body || {};
    const items = getTrash();
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    for (const item of items) {
      await sshExec(`${sudoPrefix}rm -rf ${escCmd(item.trash_path)}`);
      removeFromTrash(item.id);
    }
    logActivity('empty_trash', null, { count: items.length });
    res.json({ success: true, deleted: items.length });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/files/trash/:id', async (req, res) => {
  try {
    const { sudoPwd } = req.body || {};
    const item = getTrashItem(parseInt(req.params.id));
    if (!item) return res.status(404).json({ error: 'Item not found' });
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    await sshExec(`${sudoPrefix}rm -rf ${escCmd(item.trash_path)}`);
    removeFromTrash(item.id);
    logActivity('delete_permanent', item.original_path);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── SHARE ENDPOINTS ────────────────────────────────────────
app.post('/api/share', (req, res) => {
  try {
    const { path: filePath, permissions, password, expiresIn } = req.body;
    if (!filePath) return res.status(400).json({ error: 'path required' });
    const shareId = nanoid(10);
    const filename = path.basename(filePath);
    const passwordHash = password ? crypto.createHash('sha256').update(password).digest('hex') : null;
    const expiresAt = expiresIn ? new Date(Date.now() + expiresIn * 60 * 60 * 1000).toISOString() : null;
    createShare(shareId, filePath, filename, false, permissions || 'view', passwordHash, expiresAt);
    const proto = req.headers['x-forwarded-proto'] || req.protocol || 'http';
    const host = req.headers['x-forwarded-host'] || req.headers.host;
    const shareUrl = `${proto}://${host}/s/${shareId}`;
    logActivity('share_created', filePath, { shareId, permissions });
    res.json({ shareId, shareUrl, expiresAt });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/shares', (req, res) => {
  try { res.json({ shares: listShares() }); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/shares/:id', (req, res) => {
  try {
    revokeShare(req.params.id);
    logActivity('share_revoked', null, { shareId: req.params.id });
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.patch('/api/shares/:id', (req, res) => {
  try {
    const { permissions, expiresIn, password } = req.body;
    const updates = {};
    if (permissions) updates.permissions = permissions;
    if (expiresIn !== undefined) updates.expires_at = expiresIn ? new Date(Date.now() + expiresIn * 60 * 60 * 1000).toISOString() : null;
    if (password !== undefined) updates.password_hash = password ? crypto.createHash('sha256').update(password).digest('hex') : null;
    updateShare(req.params.id, updates);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// Public share page
app.get('/s/:shareId', async (req, res) => {
  try {
    const share = getShare(req.params.shareId);
    if (!share) {
      return res.status(404).send(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Not Found</title></head>
        <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#0d1117;color:#e6edf3">
        <div style="text-align:center"><h1>Link Not Found</h1><p>This share link has expired or been revoked.</p></div></body></html>`);
    }
    // If password protected and no auth
    if (share.password_hash && req.query.pw !== share.password_hash) {
      const pwProvided = req.query.password;
      if (pwProvided) {
        const hash = crypto.createHash('sha256').update(pwProvided).digest('hex');
        if (hash !== share.password_hash) {
          return res.status(403).send(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Password Required</title></head>
            <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#0d1117;color:#e6edf3">
            <div style="text-align:center"><h1>Incorrect Password</h1>
            <form method="GET"><input type="password" name="password" placeholder="Password" style="padding:8px;border-radius:4px;border:1px solid #444;background:#1a1a2e;color:#fff;margin:8px">
            <button type="submit" style="padding:8px 16px;border-radius:4px;border:none;background:#3b82f6;color:#fff;cursor:pointer">Submit</button></form></div></body></html>`);
        }
      } else {
        return res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Password Required</title></head>
          <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#0d1117;color:#e6edf3">
          <div style="text-align:center"><h1>🔒 Password Required</h1>
          <form method="GET"><input type="password" name="password" placeholder="Enter password" style="padding:8px;border-radius:4px;border:1px solid #444;background:#1a1a2e;color:#fff;margin:8px">
          <button type="submit" style="padding:8px 16px;border-radius:4px;border:none;background:#3b82f6;color:#fff;cursor:pointer">Open</button></form></div></body></html>`);
      }
    }
    logActivity('share_accessed', share.file_path, { shareId: req.params.shareId });
    const filename = share.filename || path.basename(share.file_path);
    const ext = path.extname(filename).toLowerCase();
    const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v'];
    const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus'];
    const imageExts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'];
    const isVideo = videoExts.includes(ext);
    const isAudio = audioExts.includes(ext);
    const isImage = imageExts.includes(ext);
    const downloadUrl = `/api/files/download?path=${encodeURIComponent(share.file_path)}`;
    let preview = '';
    if (isVideo) preview = `<video controls playsinline style="width:100%;max-height:70vh;border-radius:8px" src="/api/files/stream?path=${encodeURIComponent(share.file_path)}"></video>`;
    else if (isAudio) preview = `<div style="font-size:72px;margin:20px 0">🎵</div><audio controls style="width:100%" src="/api/files/stream?path=${encodeURIComponent(share.file_path)}"></audio>`;
    else if (isImage) preview = `<img src="/api/files/stream?path=${encodeURIComponent(share.file_path)}&raw=1" style="max-width:100%;max-height:70vh;border-radius:8px" alt="${filename}">`;
    const expiresHtml = share.expires_at ? `<p style="color:#666;font-size:13px">Expires: ${new Date(share.expires_at).toLocaleString()}</p>` : '';
    res.send(`<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
      <title>${filename} — Cast Manager</title>
      <style>*{box-sizing:border-box;margin:0;padding:0}body{font-family:system-ui,-apple-system,sans-serif;background:#0d1117;color:#e6edf3;min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:40px 20px}
      .container{max-width:800px;width:100%}.header{text-align:center;margin-bottom:32px}.logo{font-size:14px;color:#666;margin-bottom:8px}
      h1{font-size:1.5rem;word-break:break-word;margin-bottom:8px}.preview{margin:24px 0;text-align:center;background:#161b22;border-radius:12px;padding:20px;border:1px solid #30363d}
      .btn{display:inline-flex;align-items:center;gap:8px;padding:12px 24px;border-radius:8px;font-size:14px;font-weight:600;text-decoration:none;cursor:pointer;border:none;transition:all .2s}
      .btn-primary{background:#3b82f6;color:#fff}.btn-primary:hover{background:#2563eb}.btn-secondary{background:#21262d;color:#e6edf3;border:1px solid #30363d}.btn-secondary:hover{background:#30363d}
      .actions{display:flex;gap:12px;justify-content:center;margin-top:24px;flex-wrap:wrap}</style></head>
      <body><div class="container"><div class="header"><div class="logo">📺 Cast Manager</div>
      <h1>${filename}</h1>${expiresHtml}</div>
      <div class="preview">${preview || `<div style="font-size:72px;margin:20px 0">📄</div><p>${filename}</p>`}</div>
      <div class="actions">${share.permissions !== 'view' ? `<a href="${downloadUrl}" class="btn btn-primary">⬇ Download</a>` : ''}
      <a href="${downloadUrl}" class="btn btn-secondary" ${share.permissions === 'view' ? 'style="display:none"' : ''}>Open in Player</a></div>
      </div></body></html>`);
  } catch (err) {
    res.status(500).send('Error loading share');
  }
});

// ─── TAG ENDPOINTS ──────────────────────────────────────────
app.get('/api/tags', (req, res) => {
  try { res.json({ tags: getTags() }); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/tags', (req, res) => {
  try {
    const { name, color } = req.body;
    if (!name) return res.status(400).json({ error: 'name required' });
    const tag = createTag(name, color);
    res.json({ tag });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/files/tag', (req, res) => {
  try {
    const { path: filePath, tags } = req.body;
    if (!filePath || !tags) return res.status(400).json({ error: 'path and tags required' });
    for (const tagName of tags) {
      let tag = createTag(tagName);
      tagFile(filePath, tag.id);
    }
    logActivity('tag', filePath, { tags });
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/files/tag', (req, res) => {
  try {
    const { path: filePath, tags } = req.body;
    if (!filePath || !tags) return res.status(400).json({ error: 'path and tags required' });
    for (const tagName of tags) {
      const allTags = getTags();
      const tag = allTags.find(t => t.name === tagName);
      if (tag) untagFile(filePath, tag.id);
    }
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/files/tags', (req, res) => {
  try {
    const filePath = req.query.path;
    if (!filePath) return res.status(400).json({ error: 'path required' });
    res.json({ tags: getFileTags(filePath) });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── SEARCH ENDPOINT ────────────────────────────────────────
app.get('/api/search', async (req, res) => {
  try {
    const { q, type, page } = req.query;
    if (!q || !q.trim()) return res.json({ results: [] });
    // First try the indexed search
    let results = searchFiles(q.trim());
    // If no indexed results, do a live filesystem search
    if (results.length === 0) {
      const { stdout } = await sshExec(
        `find "${CFG.downloadDir}" -iname "*${q.replace(/["\\]/g, '')}*" -not -name '.*' 2>/dev/null | head -50`
      );
      results = stdout.split('\n').filter(l => l.trim()).map(p => ({
        path: p.trim(),
        name: path.basename(p.trim()),
        extension: path.extname(p.trim()).toLowerCase(),
        is_directory: 0,
      }));
    }
    // Filter by type if specified
    if (type && type !== 'all') {
      const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v'];
      const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus'];
      const imageExts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'];
      results = results.filter(r => {
        const ext = r.extension || path.extname(r.name).toLowerCase();
        if (type === 'video') return videoExts.includes(ext);
        if (type === 'audio') return audioExts.includes(ext);
        if (type === 'image') return imageExts.includes(ext);
        if (type === 'folder') return r.is_directory;
        return true;
      });
    }
    logActivity('search', null, { query: q });
    res.json({ results: results.slice(0, 50) });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.post('/api/search/reindex', async (req, res) => {
  try {
    clearFileIndex();
    const { stdout } = await sshExec(
      `find "${CFG.downloadDir}" -not -name '.*' -printf '%y\\t%s\\t%T@\\t%p\\n' 2>/dev/null`
    );
    let count = 0;
    for (const line of stdout.split('\n')) {
      if (!line.trim()) continue;
      const [type, size, mtime, ...pathParts] = line.split('\t');
      const filePath = pathParts.join('\t');
      const name = path.basename(filePath);
      const ext = path.extname(name).toLowerCase();
      indexFile(filePath, name, ext, parseInt(size) || 0, type === 'd', path.dirname(filePath), parseFloat(mtime) || 0);
      count++;
    }
    res.json({ success: true, indexed: count });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── STORAGE STATS ──────────────────────────────────────────
app.get('/api/storage/stats', async (req, res) => {
  try {
    const { stdout: dfOut } = await sshExec(`df -B1 "${CFG.downloadDir}" | tail -1`);
    const parts = dfOut.split(/\s+/);
    const totalSpace = parseInt(parts[1]) || 0;
    const usedSpace = parseInt(parts[2]) || 0;
    const freeSpace = parseInt(parts[3]) || 0;
    // Get breakdown by type
    const { stdout: breakdown } = await sshExec(`
      echo "video:$(find "${CFG.downloadDir}" -type f \\( -name '*.mkv' -o -name '*.mp4' -o -name '*.avi' -o -name '*.mov' -o -name '*.webm' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "audio:$(find "${CFG.downloadDir}" -type f \\( -name '*.mp3' -o -name '*.flac' -o -name '*.m4a' -o -name '*.aac' -o -name '*.ogg' -o -name '*.wav' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "images:$(find "${CFG.downloadDir}" -type f \\( -name '*.jpg' -o -name '*.jpeg' -o -name '*.png' -o -name '*.gif' -o -name '*.webp' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "documents:$(find "${CFG.downloadDir}" -type f \\( -name '*.pdf' -o -name '*.doc*' -o -name '*.txt' -o -name '*.md' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "archives:$(find "${CFG.downloadDir}" -type f \\( -name '*.zip' -o -name '*.tar*' -o -name '*.rar' -o -name '*.7z' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
    `);
    const bd = {};
    for (const line of breakdown.split('\n')) {
      const [key, val] = line.split(':');
      if (key && val) bd[key.trim()] = parseInt(val.trim()) || 0;
    }
    bd.other = Math.max(0, usedSpace - Object.values(bd).reduce((a, b) => a + b, 0));
    // Largest files
    const { stdout: largest } = await sshExec(
      `find "${CFG.downloadDir}" -type f -printf '%s\\t%p\\n' 2>/dev/null | sort -rn | head -10`
    );
    const largestFiles = largest.split('\n').filter(l => l.trim()).map(l => {
      const [s, ...p] = l.split('\t');
      return { size: parseInt(s) || 0, path: p.join('\t') };
    });
    res.json({ totalSpace, usedSpace, freeSpace, breakdown: bd, largestFiles });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── DIRECTORY USAGE (ncdu-like) ────────────────────────────
app.get('/api/storage/dirs', async (req, res) => {
  try {
    const dir = req.query.path || CFG.downloadDir;
    // du -d1 gives one-level deep directory sizes
    const { stdout } = await sshExec(
      `du -sb "${dir}"/*/ 2>/dev/null; du -sb "${dir}" 2>/dev/null | tail -1`
    );
    const dirs = [];
    let totalSize = 0;

    for (const line of stdout.split('\n')) {
      if (!line.trim()) continue;
      const tabIdx = line.indexOf('\t');
      if (tabIdx <= 0) continue;
      const size = parseInt(line.substring(0, tabIdx)) || 0;
      const dirPath = line.substring(tabIdx + 1).trim();
      if (dirPath === dir) {
        totalSize = size;
        continue;
      }
      const name = path.basename(dirPath.replace(/\/$/, ''));
      // show all directories including hidden ones
      dirs.push({ name, path: dirPath.replace(/\/$/, ''), size });
    }

    // Sort by size descending
    dirs.sort((a, b) => b.size - a.size);

    // Get item counts for each directory
    if (dirs.length > 0) {
      try {
        const countCmd = dirs.map(d => `echo "$(find "${d.path}" -maxdepth 1 -not -path "${d.path}" -not -name '.*' 2>/dev/null | wc -l)\t${d.path}"`).join('; ');
        const { stdout: countOut } = await sshExec(countCmd);
        const countMap = {};
        for (const line of countOut.split('\n')) {
          const ti = line.indexOf('\t');
          if (ti > 0) countMap[line.substring(ti + 1).trim()] = parseInt(line.substring(0, ti)) || 0;
        }
        for (const d of dirs) d.itemCount = countMap[d.path] || 0;
      } catch (e) { /* skip counts */ }
    }

    // Calculate files-only size (total minus sum of directories)
    const dirSum = dirs.reduce((s, d) => s + d.size, 0);
    const filesSize = Math.max(0, totalSize - dirSum);

    res.json({
      dirs,
      currentPath: dir,
      parentPath: path.dirname(dir),
      totalSize,
      filesSize,
    });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── ACTIVITY LOG ───────────────────────────────────────────
app.get('/api/activity', (req, res) => {
  try {
    const page = parseInt(req.query.page) || 1;
    const type = req.query.type || null;
    const activities = getActivity(page, 50, type);
    res.json({ activities });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── STREAM TOKEN MANAGEMENT ────────────────────────────────
app.get('/api/stream/tokens', (req, res) => {
  try { res.json({ tokens: listStreamTokens() }); }
  catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/stream/tokens/:token', (req, res) => {
  try {
    revokeStreamToken(req.params.token);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── QR CODE GENERATION ─────────────────────────────────────
app.get('/api/qrcode', async (req, res) => {
  try {
    const { text } = req.query;
    if (!text) return res.status(400).json({ error: 'text required' });
    const svg = await QRCode.toString(text, { type: 'svg', width: 200 });
    res.type('image/svg+xml').send(svg);
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── FILE UPLOAD (SFTP to server) ───────────────────────────
app.post('/api/files/upload', upload.array('files', 20), async (req, res) => {
  try {
    if (!req.files || req.files.length === 0) {
      return res.status(400).json({ error: 'No files provided' });
    }
    const destDir = req.body.path || CFG.downloadDir;
    if (!isPathSafe(destDir + '/x')) {
      return res.status(403).json({ error: 'Cannot upload to this path' });
    }
    const results = [];
    for (const file of req.files) {
      const remotePath = path.join(destDir, file.originalname);
      try {
        await new Promise((resolve, reject) => {
          const conn = new Client();
          conn.on('ready', () => {
            conn.sftp((err, sftp) => {
              if (err) { conn.end(); return reject(err); }
              sftp.fastPut(file.path, remotePath, (err) => {
                conn.end();
                fs.unlinkSync(file.path);
                if (err) reject(err); else resolve();
              });
            });
          });
          conn.on('error', reject);
          conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
        });
        logActivity('upload', remotePath, { size: file.size });
        trackRecent(remotePath, 'upload', file.originalname);
        results.push({ name: file.originalname, success: true });
      } catch (e) {
        try { fs.unlinkSync(file.path); } catch (_) {}
        results.push({ name: file.originalname, success: false, error: e.message });
      }
    }
    broadcastNotification({ type: 'upload_complete', count: results.filter(r => r.success).length });
    res.json({ results });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── BULK DOWNLOAD (ZIP) ────────────────────────────────────
app.post('/api/files/download/bulk', async (req, res) => {
  try {
    const { paths } = req.body;
    if (!paths || !paths.length) return res.status(400).json({ error: 'paths required' });
    res.set('Content-Type', 'application/zip');
    res.set('Content-Disposition', `attachment; filename="cast_manager_download.zip"`);
    const archive = archiver('zip', { zlib: { level: 5 } });
    archive.pipe(res);
    for (const filePath of paths) {
      const conn = new Client();
      await new Promise((resolve, reject) => {
        conn.on('ready', () => {
          conn.sftp((err, sftp) => {
            if (err) { conn.end(); return reject(err); }
            const rs = sftp.createReadStream(filePath);
            archive.append(rs, { name: path.basename(filePath) });
            rs.on('end', () => { conn.end(); resolve(); });
            rs.on('error', () => { conn.end(); resolve(); });
          });
        });
        conn.on('error', reject);
        conn.connect({ host: CFG.sshHost, port: 22, username: CFG.sshUser, password: CFG.sshPass });
      });
    }
    await archive.finalize();
  } catch (err) {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  }
});

// ─── WEBSOCKET NOTIFICATIONS ────────────────────────────────
let wss;
const wsClients = new Set();

function broadcastNotification(data) {
  const msg = JSON.stringify(data);
  for (const client of wsClients) {
    if (client.readyState === WebSocket.OPEN) {
      client.send(msg);
    }
  }
}

function setupWebSocket(server) {
  wss = new WebSocket.Server({ server, path: '/api/ws/notifications' });
  wss.on('connection', (ws) => {
    wsClients.add(ws);
    ws.on('close', () => wsClients.delete(ws));
    ws.on('error', () => wsClients.delete(ws));
    ws.send(JSON.stringify({ type: 'connected', message: 'Cast Manager v4 WebSocket connected' }));
  });
  console.log('[WS] WebSocket server running on /api/ws/notifications');
}

// Initialize database
initDB();

// Background index on startup (async, non-blocking)
(async () => {
  try {
    const { stdout } = await sshExec(
      `find "${CFG.downloadDir}" -not -name '.*' -printf '%y\\t%s\\t%T@\\t%p\\n' 2>/dev/null | head -5000`
    );
    let count = 0;
    for (const line of stdout.split('\n')) {
      if (!line.trim()) continue;
      const [type, size, mtime, ...pathParts] = line.split('\t');
      const filePath = pathParts.join('\t');
      const name = path.basename(filePath);
      const ext = path.extname(name).toLowerCase();
      indexFile(filePath, name, ext, parseInt(size) || 0, type === 'd', path.dirname(filePath), parseFloat(mtime) || 0);
      count++;
    }
    console.log(`[INDEX] Indexed ${count} files for search`);
  } catch (e) {
    console.log('[INDEX] Background indexing failed (server may be unreachable):', e.message);
  }
})();

// ─── SERVER START ────────────────────────────────────────────
const server = http.createServer(app);
setupWebSocket(server);

server.listen(CFG.port, '0.0.0.0', () => {
  console.log(`Cast Manager v4 running at http://0.0.0.0:${CFG.port}`);
  const nets = require('os').networkInterfaces();
  for (const iface of Object.values(nets)) {
    for (const net of iface) {
      if (net.family === 'IPv4' && !net.internal) {
        console.log(`  Network: http://${net.address}:${CFG.port}`);
      }
    }
  }
});
