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
const { initDB, logActivity, trackRecent, starFile, unstarFile, isStarred, getStarred, updateStarredMetadata,
  upsertCastDevice, listCastDevices, getCastDevice, getSelectedCastDevice, setSelectedCastDevice, updateCastDeviceCredentials,
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
const { analyzeMediaCompatibility, stableHash } = require('./lib/media-compatibility');
const { createHlsJobManager } = require('./lib/media/jobs');
const { prepareMediaForCast, summarizePrepared } = require('./lib/media/pipeline');
const { getReceiverBaseUrl } = require('./lib/media/urls');
const { createChromecastProvider } = require('./lib/cast/chromecast-provider');
const { createAirPlayProvider } = require('./lib/cast/airplay-provider');
const { createSessionStore } = require('./lib/cast/session-store');
const { makeProviderDeviceId, stripProviderPrefix } = require('./lib/cast/provider-interface');
const { normalizeStarredRow, inferKindFromPath, parentPathOf } = require('./lib/folders/starred');
const {
  buildBreadcrumbs,
  getRootLabel,
  hasHiddenSegment,
  isInsideRoot,
  isProtectedPath,
  joinChild,
  normalizeAbsolutePath,
  normalizeRoot,
  parentPath: safeParentPath,
  resolveSafePath,
  toRelativePath,
  validateItemName,
} = require('./lib/file-manager-paths');

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

const FILE_MANAGER_MAX_PREVIEW_BYTES = parseInt(process.env.FILE_MANAGER_MAX_PREVIEW_BYTES || `${1024 * 1024}`, 10);
const FILE_MANAGER_TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.json', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.xml',
  '.yml', '.yaml', '.csv', '.srt', '.vtt', '.ass', '.sub', '.log', '.sh', '.env',
  '.gitignore', '.dockerignore', '.conf', '.ini', '.toml',
]);

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
  res.header('Access-Control-Allow-Headers', 'Content-Type, X-Sudo-Password');
  if (req.method === 'OPTIONS') return res.sendStatus(200);
  next();
});

app.get('/file-manager', (req, res) => {
  res.sendFile(path.join(__dirname, 'public', 'index.html'));
});

// Config from env
const CFG = {
  sshHost: process.env.SSH_HOST || 'REDACTED_SERVER_IP',
  sshUser: process.env.SSH_USER || 'o',
  sshPass: process.env.SSH_PASSWORD || undefined,
  sshKeyPath: process.env.SSH_PRIVATE_KEY_PATH || process.env.SSH_KEY_PATH || '',
  transUser: process.env.TRANSMISSION_USER || 'transmission',
  transPass: process.env.TRANSMISSION_PASS || '',
  downloadDir: process.env.DOWNLOAD_DIR || '/home/REDACTED_USER/watch_list',
  fileManagerRoot: normalizeRoot(process.env.FILE_MANAGER_ROOT || '/'),
  fileManagerRootLabel: process.env.FILE_MANAGER_ROOT_LABEL || 'System Root',
  trashDir: normalizeRoot(process.env.TRASH_DIR || '/home/REDACTED_USER/.cast_manager/trash'),
  chromecastName: process.env.CHROMECAST_NAME || 'REDACTED_DEVICE',
  cattPath: process.env.CATT_PATH || '/home/REDACTED_USER/.local/bin/catt',
  port: parseInt(process.env.PORT || '8004', 10),
  publicHost: process.env.CAST_PUBLIC_HOST || process.env.SSH_HOST || 'REDACTED_SERVER_IP',
};
CFG.fileManagerRootLabel = CFG.fileManagerRootLabel || getRootLabel(CFG.fileManagerRoot, 'System Root');

function getSshConfig() {
  const config = {
    host: CFG.sshHost,
    port: 22,
    username: CFG.sshUser,
    readyTimeout: 10000,
  };
  if (CFG.sshPass) config.password = CFG.sshPass;
  if (CFG.sshKeyPath) {
    try {
      config.privateKey = fs.readFileSync(CFG.sshKeyPath);
    } catch (err) {
      console.warn(`[SSH] Could not read SSH_PRIVATE_KEY_PATH: ${err.message}`);
    }
  }
  if (!config.password && !config.privateKey && process.env.SSH_AUTH_SOCK) {
    config.agent = process.env.SSH_AUTH_SOCK;
  }
  return config;
}

const CRITICAL_SYSTEM_PATHS = new Set([
  '/',
  '/Applications',
  '/Library',
  '/System',
  '/bin',
  '/boot',
  '/dev',
  '/etc',
  '/home',
  '/lib',
  '/lib64',
  '/media',
  '/mnt',
  '/opt',
  '/private',
  '/proc',
  '/root',
  '/run',
  '/sbin',
  '/srv',
  '/sys',
  '/tmp',
  '/usr',
  '/var',
]);

const VIRTUAL_USAGE_PATHS = new Set(['/dev', '/proc', '/run', '/sys']);

function getSudoPassword(req) {
  return String(req.body?.sudoPwd || req.query?.sudoPwd || req.get('x-sudo-password') || '');
}

function makeSudoPrefix(sudoPwd) {
  return sudoPwd ? `printf '%s\\n' ${escCmd(sudoPwd)} | sudo -S -p '' ` : '';
}

function isPermissionFailure(stderr = '', code = 0) {
  return code !== 0 && /permission denied|operation not permitted|not permitted|sudo|password/i.test(String(stderr));
}

function commandFailure(code, stderr, fallbackCode, fallbackMessage) {
  if (isPermissionFailure(stderr, code)) {
    return makeFileManagerError('PERMISSION_DENIED', 'Permission denied. Enter the server sudo password to retry.');
  }
  return makeFileManagerError(fallbackCode, fallbackMessage);
}

// Path safety check: all file-manager operations are restricted to CFG.fileManagerRoot.
function isPathSafe(filePath) {
  try {
    const resolved = resolveSafePath(CFG.fileManagerRoot, filePath);
    return isInsideRoot(CFG.fileManagerRoot, resolved);
  } catch (_) {
    return false;
  }
}

function normalizeFileManagerPath(filePath) {
  return resolveSafePath(CFG.fileManagerRoot, filePath || CFG.fileManagerRoot);
}

function isRootPath(filePath) {
  return normalizeFileManagerPath(filePath) === CFG.fileManagerRoot;
}

function isCriticalSystemPath(filePath) {
  if (CFG.fileManagerRoot !== '/') return false;
  return CRITICAL_SYSTEM_PATHS.has(normalizeFileManagerPath(filePath));
}

function ensureNotCriticalSystemPath(filePath, action = 'changed') {
  if (isCriticalSystemPath(filePath)) {
    throw makeFileManagerError('INVALID_PATH', `System path ${normalizeFileManagerPath(filePath)} cannot be ${action} from the file manager`);
  }
}

function isTrashPathSafe(filePath) {
  try {
    const resolved = resolveSafePath(CFG.trashDir, filePath || CFG.trashDir);
    return isInsideRoot(CFG.trashDir, resolved);
  } catch (_) {
    return false;
  }
}

function fileManagerError(res, err, fallbackMessage = 'File operation failed') {
  const code = err?.code || 'FILE_OPERATION_FAILED';
  const status = {
    INVALID_PATH: 403,
    INVALID_NAME: 400,
    NOT_FOUND: 404,
    ALREADY_EXISTS: 409,
    NOT_A_DIRECTORY: 400,
    NOT_A_FILE: 400,
    PROTECTED_FILE: 403,
    PERMISSION_DENIED: 403,
    UNSUPPORTED_FILE: 415,
    FILE_TOO_LARGE: 413,
    WRITE_FAILED: 500,
    READ_FAILED: 500,
    DELETE_FAILED: 500,
  }[code] || 500;
  const message = err?.message || fallbackMessage;
  return res.status(status).json({
    ok: false,
    success: false,
    error: message,
    errorDetails: { code, message },
  });
}

function makeFileManagerError(code, message) {
  const err = new Error(message);
  err.code = code;
  return err;
}

async function assertRemotePathInsideRoot(filePath, options = {}) {
  const normalized = normalizeFileManagerPath(filePath);
  const requireExists = options.requireExists !== false;
  const realCmd = requireExists
    ? `realpath -e -- ${escCmd(normalized)}`
    : `realpath -m -- ${escCmd(normalized)}`;
  const sudoPrefix = makeSudoPrefix(options.sudoPwd || '');
  const { stdout, stderr, code } = await sshExec(`${sudoPrefix}${realCmd}`);
  if (requireExists && code !== 0) {
    if (isPermissionFailure(stderr, code)) {
      throw makeFileManagerError('PERMISSION_DENIED', 'Permission denied. Enter the server sudo password to retry.');
    }
    throw makeFileManagerError('NOT_FOUND', 'File or folder was not found');
  }
  const realPath = normalizeAbsolutePath(stdout || normalized);
  if (!isInsideRoot(CFG.fileManagerRoot, realPath)) {
    throw makeFileManagerError('INVALID_PATH', 'Resolved path is outside the configured file-manager root');
  }
  return normalized;
}

async function assertParentPathInsideRoot(filePath, options = {}) {
  const normalized = normalizeFileManagerPath(filePath);
  const parent = path.posix.dirname(normalized);
  await assertRemotePathInsideRoot(parent, options);
  return normalized;
}

function ensureNotProtectedFile(filePath, action = 'access') {
  if (isProtectedPath(CFG.fileManagerRoot, filePath)) {
    throw makeFileManagerError('PROTECTED_FILE', `Protected files cannot be ${action} from the file manager`);
  }
}

function classifyFileType(type, name) {
  const ext = path.extname(name).toLowerCase();
  const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v', '.ts', '.flv', '.wmv'];
  const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus', '.wma'];
  const subExts = ['.srt', '.ass', '.vtt', '.sub'];
  const imageExts = ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg', '.bmp'];
  if (type === 'd') return 'folder';
  if (type === 'l') return 'symlink';
  if (videoExts.includes(ext)) return 'video';
  if (audioExts.includes(ext)) return 'audio';
  if (subExts.includes(ext)) return 'subtitle';
  if (imageExts.includes(ext)) return 'image';
  if (ext === '.torrent') return 'torrent';
  return 'other';
}

function isLikelyTextFile(filePath, mimeType = '') {
  const name = path.basename(filePath).toLowerCase();
  const ext = path.extname(name);
  return FILE_MANAGER_TEXT_EXTENSIONS.has(ext)
    || FILE_MANAGER_TEXT_EXTENSIONS.has(name)
    || mimeType.startsWith('text/')
    || ['application/json', 'application/xml', 'application/javascript', 'application/x-sh'].includes(mimeType);
}

function buildFileMetadata(filePath, type, size, mtime, itemCount) {
  const name = path.basename(filePath);
  const ext = path.extname(name).toLowerCase();
  const fileType = classifyFileType(type, name);
  const hidden = hasHiddenSegment(CFG.fileManagerRoot, filePath) || name.startsWith('.');
  return {
    name,
    path: filePath,
    relativePath: toRelativePath(CFG.fileManagerRoot, filePath),
    type: fileType,
    size: parseInt(size, 10) || 0,
    mtime: parseFloat(mtime) || 0,
    modifiedAt: new Date((parseFloat(mtime) || 0) * 1000).toISOString(),
    ext,
    extension: ext,
    mimeType: getMimeType(filePath),
    isHidden: hidden,
    hidden,
    protected: isProtectedPath(CFG.fileManagerRoot, filePath),
    itemCount,
  };
}

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
    conn.connect(getSshConfig());
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

function sleepMs(ms) {
  return new Promise((r) => setTimeout(r, ms));
}

function secondsToHMS(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(ss).padStart(2, '0')}`;
}

function normalizeCastState(raw) {
  const value = String(raw || '').toLowerCase();
  if (!value) return 'unknown';
  if (value.includes('play')) return 'playing';
  if (value.includes('pause')) return 'paused';
  if (value.includes('buffer') || value.includes('load') || value.includes('connect')) return 'buffering';
  if (value.includes('idle') || value.includes('stop')) return 'idle';
  return 'unknown';
}

function sanitizeCastTitle(title) {
  const t = String(title || '').trim();
  if (!t) return '';
  try {
    // Avoid leaking full URLs/tokens in status responses.
    if (/^https?:\/\//i.test(t)) {
      const u = new URL(t);
      const base = (u.pathname || '').split('/').filter(Boolean).pop() || '';
      return base ? `${u.hostname}/${base}` : u.hostname;
    }
  } catch (_) { /* not a URL */ }
  return t;
}

function isFiniteNonNegativeNumber(value) {
  const n = Number(value);
  return Number.isFinite(n) && n >= 0;
}

function normalizeSeekTarget(value) {
  const n = Number(value);
  if (!Number.isFinite(n) || n < 0) return null;
  return Math.round(n);
}

function isTimeNear(actual, expected, toleranceSeconds = 8) {
  const a = Number(actual);
  const e = Number(expected);
  return Number.isFinite(a) && Number.isFinite(e) && Math.abs(a - e) <= toleranceSeconds;
}

async function cattCmdWithRetry(args, {
  attempts = 2,
  timeout = 15000,
  retryDelayMs = 250,
} = {}) {
  let lastErr;
  for (let i = 0; i < attempts; i++) {
    try {
      return await cattCmd(args, timeout);
    } catch (e) {
      lastErr = e;
      if (i < attempts - 1) await sleepMs(retryDelayMs);
    }
  }
  throw lastErr;
}

async function getNormalizedCastStatus() {
  const activeSession = !!(activeCastSession.filePath || activeCastSession.streamUrl);
  const fallbackAvailable = activeSession;
  const lastCommandAt = Number(activeCastSession.lastCommandAt) || 0;

  const base = {
    success: true,
    state: 'unknown',
    currentTime: 0,
    duration: 0,
    title: '',
    volumeLevel: 100,
    receiverReachable: true,
    activeSession,
    lastCommandAt,
    fallbackAvailable,
  };

  try {
    const { stdout, stderr } = await cattCmd('status', 8000);
    const output = stdout || stderr || '';

    let parsedStateRaw = '';
    let parsedTitle = '';
    let parsedCurrent = 0;
    let parsedDuration = 0;
    let parsedVol = 100;

    if (output.includes('Nothing is currently playing')) {
      parsedStateRaw = 'idle';
    } else {
      const timeMatch = output.match(/Time:\s*([\d:.]+)\s*\/\s*([\d:.]+)/i);
      if (timeMatch) {
        parsedCurrent = parseTimeToSeconds(timeMatch[1]);
        parsedDuration = parseTimeToSeconds(timeMatch[2]);
      }

      const stateMatch = output.match(/State:\s*(.+)/i);
      if (stateMatch) parsedStateRaw = String(stateMatch[1]).trim();

      const titleMatch = output.match(/Title:\s*(.+)/i);
      if (titleMatch) parsedTitle = titleMatch[1].trim();

      const volMatch = output.match(/Volume:\s*(\d+)/i);
      if (volMatch) parsedVol = parseInt(volMatch[1], 10);
    }

    base.state = normalizeCastState(parsedStateRaw || '');
    base.volumeLevel = Number.isFinite(parsedVol) ? parsedVol : 100;

    const cur = Number.isFinite(parsedCurrent) && parsedCurrent >= 0 ? parsedCurrent : 0;
    const dur = Number.isFinite(parsedDuration) && parsedDuration >= 0 ? parsedDuration : 0;
    base.currentTime = cur > 0 ? cur : (activeSession ? Math.max(0, Number(activeCastSession.lastKnownTime) || 0) : 0);
    base.duration = dur > 0 ? dur : (activeSession ? Math.max(0, Number(activeCastSession.duration) || 0) : 0);
    base.title = sanitizeCastTitle(parsedTitle || (activeSession ? activeCastSession.title : ''));

    if (activeSession) {
      if (Number.isFinite(base.currentTime) && base.currentTime >= 0) {
        activeCastSession.lastKnownTime = base.currentTime;
      }
      if (Number.isFinite(base.duration) && base.duration > 0) {
        activeCastSession.duration = base.duration;
      }
      if (base.title) {
        // Store original title internally (may be URL); response is sanitized.
        activeCastSession.title = parsedTitle || activeCastSession.title;
      }
      activeCastSession.state = base.state;
    }

    return base;
  } catch (err) {
    base.receiverReachable = false;
    base.state = activeSession ? normalizeCastState(activeCastSession.state) : 'unknown';
    base.currentTime = activeSession ? Math.max(0, Number(activeCastSession.lastKnownTime) || 0) : 0;
    base.duration = activeSession ? Math.max(0, Number(activeCastSession.duration) || 0) : 0;
    base.title = sanitizeCastTitle(activeSession ? activeCastSession.title : '');
    return base;
  }
}

function shouldFallbackAfterSeek(status, targetSeconds) {
  if (!status) return true;
  const normalizedState = normalizeCastState(status.state || status.rawState || '');
  if (normalizedState === 'idle' || normalizedState === 'unknown') return true;
  if (Number.isFinite(Number(status.currentTime))) {
    return !isTimeNear(Number(status.currentTime), targetSeconds, 10);
  }
  return false;
}

async function verifySeekSettled(targetSeconds) {
  await sleepMs(500);
  try {
    const status = await getNormalizedCastStatus();
    return { ok: !shouldFallbackAfterSeek(status, targetSeconds), status };
  } catch (err) {
    return { ok: false, error: err };
  }
}

async function getRemoteStat(filePath) {
  const p = String(filePath || '');
  const { stdout } = await sshExec(`stat -c '%s\t%Y' ${escCmd(p)} 2>/dev/null || echo '0\t0'`);
  const parts = String(stdout || '').trim().split('\t');
  const size = parseInt(parts[0], 10) || 0;
  const mtime = parseInt(parts[1], 10) || 0;
  return { size, mtime };
}

function buildMediaCacheKey({ filePath, stat, target, playbackMode, ffmpegPlan, streamSelection } = {}) {
  return stableHash(JSON.stringify({
    v: 3,
    filePath: String(filePath || ''),
    size: Number(stat?.size || 0),
    mtime: Number(stat?.mtime || 0),
    target: String(target || ''),
    playbackMode: String(playbackMode || ''),
    ffmpegPlan: ffmpegPlan || null,
    streamSelection: streamSelection || null,
  }));
}

function mediaLog(msg) {
  try { console.log(`[media] ${msg}`); } catch (_) {}
}

// ─── Subtitles (WebVTT for browser + Chromecast) ─────────────
// id -> { filePath, kind: 'sidecar'|'embedded', sidecarPath?, streamIndex?, label, createdAt }
const subtitleIndex = new Map();
const SUBTITLE_INDEX_TTL_MS = 30 * 60 * 1000;
const SUBTITLE_FILE_EXTENSIONS = new Set(['.srt', '.ass', '.vtt', '.sub']);

function pruneSubtitleIndex() {
  const now = Date.now();
  for (const [id, item] of subtitleIndex.entries()) {
    if (!item || (now - (item.createdAt || 0)) > SUBTITLE_INDEX_TTL_MS) subtitleIndex.delete(id);
  }
}

function buildSubtitleUrl(req, subtitleId) {
  const base = getCastBaseUrl(req);
  const url = `${base}/api/subtitles/${encodeURIComponent(String(subtitleId || '').trim())}.vtt`;
  mediaLog(`subtitle URL: ${url}`);
  return url;
}

async function ensureSubtitleVttRemote({ filePath, subtitleId, kind, sidecarPath, streamIndex } = {}) {
  const outDir = '/tmp/cast_subtitles_v1';
  const outPath = `${outDir}/${subtitleId}.vtt`;
  await sshExec(`mkdir -p ${escCmd(outDir)}`);
  const { stdout: exists } = await sshExec(`test -f ${escCmd(outPath)} && echo exists`);
  if (String(exists || '').includes('exists')) return outPath;

  if (kind === 'sidecar') {
    // Convert sidecar to WebVTT.
    await sshExec(`ffmpeg -y -hide_banner -loglevel error -i ${escCmd(sidecarPath)} -c:s webvtt ${escCmd(outPath)}`);
    return outPath;
  }
  // Extract embedded subtitle stream to WebVTT.
  const si = Number(streamIndex);
  if (!Number.isFinite(si) || si < 0) throw new Error('Invalid embedded subtitle stream index');
  await sshExec(`ffmpeg -y -hide_banner -loglevel error -i ${escCmd(filePath)} -map 0:s:${si} -c:s webvtt ${escCmd(outPath)}`);
  return outPath;
}

async function registerCustomSubtitlePath(filePath, subtitlePath) {
  const safeSubtitlePath = await assertRemotePathInsideRoot(subtitlePath);
  ensureNotProtectedFile(safeSubtitlePath, 'read');
  const ext = path.extname(safeSubtitlePath).toLowerCase();
  if (!SUBTITLE_FILE_EXTENSIONS.has(ext)) {
    throw makeFileManagerError('UNSUPPORTED_FILE', 'Subtitle must be an SRT, ASS, VTT, or SUB file');
  }
  const check = await sshExec(`test -f ${escCmd(safeSubtitlePath)}`);
  if (check.code !== 0) throw makeFileManagerError('NOT_A_FILE', 'Subtitle path is not a file');
  const id = stableHash(JSON.stringify({ v: 1, kind: 'custom-sidecar', filePath, sidecarPath: safeSubtitlePath }));
  subtitleIndex.set(id, {
    filePath,
    kind: 'sidecar',
    sidecarPath: safeSubtitlePath,
    label: `Custom: ${path.basename(safeSubtitlePath)}`,
    createdAt: Date.now(),
  });
  return id;
}

// Remote ffmpeg runs over SSH; probe the same host capabilities once per process.
let ffmpegCapsCache = null;
let ffmpegCapsProbePromise = null;

async function probeFfmpegCapabilities() {
  if (ffmpegCapsCache) return ffmpegCapsCache;
  if (!ffmpegCapsProbePromise) {
    ffmpegCapsProbePromise = (async () => {
      try {
        const enc = await sshExec('ffmpeg -hide_banner -encoders 2>/dev/null || true', 25000);
        const dec = await sshExec('ffmpeg -hide_banner -decoders 2>/dev/null || true', 25000);
        const hw = await sshExec('ffmpeg -hide_banner -hwaccels 2>/dev/null || true', 8000);
        const caps = {
          h264Nvenc: /\bh264_nvenc\b/.test(enc.stdout),
          hevcCuvid: /\bhevc_cuvid\b/.test(dec.stdout),
          cudaHwaccel: /\bcuda\b/.test(hw.stdout),
          libx264: /\blibx264\b/.test(enc.stdout),
        };
        ffmpegCapsCache = caps;
        mediaLog(`ffmpeg capabilities: h264_nvenc=${caps.h264Nvenc} cuda=${caps.cudaHwaccel} hevc_cuvid=${caps.hevcCuvid} libx264=${caps.libx264}`);
      } catch (err) {
        ffmpegCapsCache = {
          h264Nvenc: false,
          hevcCuvid: false,
          cudaHwaccel: false,
          libx264: true,
        };
        mediaLog(`ffmpeg capabilities probe failed (${err.message}); assuming libx264-only`);
      }
      return ffmpegCapsCache;
    })();
  }
  return ffmpegCapsProbePromise;
}

async function getLiveTranscodeEncoderPreference() {
  const raw = String(process.env.CAST_LIVE_TRANSCODE_ENCODER ?? 'auto').toLowerCase().trim();
  if (raw === 'libx264' || raw === 'cpu' || raw === 'x264') return 'libx264';
  if (raw === 'h264_nvenc' || raw === 'nvenc') return 'h264_nvenc';
  return 'auto';
}

async function resolveLiveFullTranscodeEncoder() {
  const pref = await getLiveTranscodeEncoderPreference();
  const caps = await probeFfmpegCapabilities();

  if (pref === 'libx264') {
    return { encoder: 'libx264', reason: 'CAST_LIVE_TRANSCODE_ENCODER=libx264' };
  }
  if (pref === 'h264_nvenc') {
    if (caps.h264Nvenc) {
      return { encoder: 'h264_nvenc', reason: 'CAST_LIVE_TRANSCODE_ENCODER=h264_nvenc' };
    }
    mediaLog('CAST_LIVE_TRANSCODE_ENCODER=h264_nvenc but h264_nvenc is missing from ffmpeg; falling back to libx264');
    return { encoder: 'libx264', reason: 'h264_nvenc unavailable' };
  }
  if (caps.h264Nvenc) {
    return { encoder: 'h264_nvenc', reason: 'auto' };
  }
  return { encoder: 'libx264', reason: 'auto (NVENC unavailable)' };
}

function isLocalhostHost(host) {
  const h = String(host || '').toLowerCase();
  return h.startsWith('localhost') || h.startsWith('127.0.0.1') || h.startsWith('[::1]');
}

function getCastBaseUrl(req) {
  const base = getReceiverBaseUrl(req, CFG);
  if (isLocalhostHost(new URL(base).host)) {
    mediaLog(`WARN receiver URL base is localhost (${base}) — set CAST_PUBLIC_BASE_URL.`);
  }
  return base;
}

// ─── Active cast session tracking ────────────────────────────
let activeCastSession = {
  filePath: null,
  resolvedPath: null, // e.g. transcoded path we actually cast
  streamUrl: null,
  backend: 'ffmpeg', // ffmpeg | vlc | hls | url (diagnostic)
  liveJobId: null,
  /** @type {{ encoder: string, cudaFirst?: boolean } | null} */
  liveEncode: null,
  playbackMode: null,
  analysisSummary: null,
  subtitlePath: null,
  vlcJobId: null,
  title: null,
  duration: 0,
  type: null,
  startedAt: null,
  startSeconds: 0,
  lastKnownTime: 0,
  lastCommandAt: 0,
  state: 'idle',
};

function clearActiveCastSession() {
  activeCastSession = {
    filePath: null,
    resolvedPath: null,
    streamUrl: null,
    backend: 'ffmpeg',
    liveJobId: null,
    liveEncode: null,
    playbackMode: null,
    analysisSummary: null,
    subtitlePath: null,
    vlcJobId: null,
    title: null,
    duration: 0,
    type: null,
    startedAt: null,
    startSeconds: 0,
    lastKnownTime: 0,
    lastCommandAt: 0,
    state: 'idle',
  };
}

async function castMediaAtPosition({ filePath, streamUrl, seconds = 0, title, type, subtitlePath, receiverSeek = true, backend } = {}) {
  const startSeconds = Math.max(0, Math.floor(Number(seconds) || 0));
  const seekArg = receiverSeek && startSeconds > 0 ? `-t ${secondsToHMS(startSeconds)}` : '';

  if (streamUrl) {
    const cmd = subtitlePath
      ? `cast ${seekArg} --subtitles "${subtitlePath}" "${streamUrl}"`
      : `cast ${seekArg} "${streamUrl}"`;
    await cattCmdWithRetry(cmd, { attempts: 2, timeout: 30000, retryDelayMs: 400 });

    activeCastSession.streamUrl = streamUrl;
    activeCastSession.filePath = filePath || null;
    activeCastSession.resolvedPath = null;
    activeCastSession.liveJobId = null;
    activeCastSession.liveEncode = null;
    activeCastSession.playbackMode = null;
    activeCastSession.analysisSummary = null;
    activeCastSession.subtitlePath = subtitlePath || null;
    activeCastSession.backend = backend || 'url';
    activeCastSession.vlcJobId = null;
    activeCastSession.title = title || streamUrl;
    activeCastSession.type = type || null;
    activeCastSession.startedAt = new Date().toISOString();
    activeCastSession.startSeconds = startSeconds;
    activeCastSession.lastKnownTime = startSeconds;
    activeCastSession.lastCommandAt = Date.now();
    activeCastSession.state = 'playing';
    return { success: true, startSeconds, mode: 'url' };
  }

  if (!filePath) throw new Error('filePath or streamUrl required');

  const cmd = subtitlePath
    ? `cast ${seekArg} --subtitles "${subtitlePath}" "${filePath}"`
    : `cast ${seekArg} "${filePath}"`;

  await cattCmdWithRetry(cmd, { attempts: 2, timeout: 30000, retryDelayMs: 400 });

  activeCastSession.filePath = filePath;
  activeCastSession.resolvedPath = filePath;
  activeCastSession.streamUrl = null;
  activeCastSession.liveJobId = null;
  activeCastSession.liveEncode = null;
  activeCastSession.playbackMode = null;
  activeCastSession.analysisSummary = null;
  activeCastSession.subtitlePath = subtitlePath || null;
  activeCastSession.backend = backend || 'ffmpeg';
  activeCastSession.vlcJobId = null;
  activeCastSession.title = title || path.basename(filePath);
  activeCastSession.type = type || null;
  activeCastSession.startedAt = new Date().toISOString();
  activeCastSession.startSeconds = startSeconds;
  activeCastSession.lastKnownTime = startSeconds;
  activeCastSession.lastCommandAt = Date.now();
  activeCastSession.state = 'playing';
  return { success: true, startSeconds, mode: 'file' };
}

// ─── VLC live cast jobs (experimental) ───────────────────────
const vlcCastJobs = new Map(); // jobId -> { createdAt, cancelled, cancelledAt, expiresAt, startSeconds, streamUrl, title }
const VLC_JOB_TTL_MS = 60 * 60 * 1000; // 1 hour
const VLC_JOB_CANCEL_TOMBSTONE_MS = 5 * 60 * 1000;

function pruneVlcCastJobs() {
  const now = Date.now();
  for (const [jobId, job] of vlcCastJobs.entries()) {
    if (!job) {
      vlcCastJobs.delete(jobId);
      continue;
    }
    if (job.cancelled) {
      const until = job.expiresAt != null ? job.expiresAt : (job.cancelledAt || 0) + VLC_JOB_CANCEL_TOMBSTONE_MS;
      if (now > until) vlcCastJobs.delete(jobId);
      continue;
    }
    if ((now - (job.createdAt || 0)) > VLC_JOB_TTL_MS) {
      job.cancelled = true;
      job.cancelReason = 'expired';
      job.cancelledAt = now;
      job.expiresAt = now + VLC_JOB_CANCEL_TOMBSTONE_MS;
      vlcCastJobs.set(jobId, job);
    }
  }
}

async function probeVlcAvailable() {
  const enabled = String(process.env.CAST_ENABLE_VLC_BACKEND || '0').toLowerCase();
  if (enabled !== '1' && enabled !== 'true' && enabled !== 'yes' && enabled !== 'on') return { ok: false, reason: 'CAST_ENABLE_VLC_BACKEND=0' };
  try {
    const { stdout } = await sshExec('command -v cvlc >/dev/null 2>&1 && echo ok || echo missing');
    if (String(stdout || '').includes('ok')) return { ok: true };
    return { ok: false, reason: 'cvlc not installed (apt install vlc)' };
  } catch (e) {
    return { ok: false, reason: `cvlc probe failed: ${e.message}` };
  }
}

async function allocateVlcPort() {
  const raw = String(process.env.CAST_VLC_PORT_RANGE || '18080-18180');
  const m = raw.match(/^\s*(\d+)\s*-\s*(\d+)\s*$/);
  const lo = m ? Math.min(parseInt(m[1], 10), parseInt(m[2], 10)) : 18080;
  const hi = m ? Math.max(parseInt(m[1], 10), parseInt(m[2], 10)) : 18180;
  const cmd = `python3 - <<'PY'\nimport socket\nlo=${lo}\nhi=${hi}\nfor port in range(lo,hi+1):\n    s=socket.socket()\n    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)\n    try:\n        s.bind(('0.0.0.0', port))\n        s.close()\n        print(port)\n        raise SystemExit(0)\n    except OSError:\n        try: s.close()\n        except Exception: pass\nprint('')\nPY`;
  const { stdout } = await sshExec(cmd);
  const p = parseInt(String(stdout || '').trim(), 10);
  if (!Number.isFinite(p) || p <= 0) throw new Error(`No free VLC port in range ${lo}-${hi}`);
  return p;
}

function buildPublicUrlWithPort(req, port, pathname, explicitBaseUrl) {
  const base = explicitBaseUrl || getCastBaseUrl(req);
  try {
    const u = new URL(base);
    u.port = String(port);
    u.pathname = String(pathname || '/');
    u.search = '';
    u.hash = '';
    return u.toString().replace(/\/+$/, '');
  } catch (_) {
    // Fallback: naive replace of :<port> if present
    return `${base.replace(/:\\d+$/, '')}:${port}${String(pathname || '/')}`;
  }
}

async function createVlcCastJob({ req, filePath, startSeconds = 0, title, publicBaseUrl } = {}) {
  if (!filePath) throw new Error('filePath required');
  if (!isPathSafe(filePath)) throw new Error('Cannot stream this path');
  ensureNotProtectedFile(filePath, 'streamed');

  pruneVlcCastJobs();
  const avail = await probeVlcAvailable();
  if (!avail.ok) throw new Error(`VLC backend unavailable: ${avail.reason}`);

  const jobId = stableHash(JSON.stringify({ v: 1, kind: 'vlc', filePath, startSeconds: Math.floor(Number(startSeconds) || 0), t: Date.now(), n: Math.random() }));
  const port = await allocateVlcPort();
  const streamPath = `/${encodeURIComponent(jobId)}`;
  const streamUrl = buildPublicUrlWithPort(req, port, streamPath, publicBaseUrl);

  // MPEG-TS first for maximum receiver tolerance.
  const sout = `#transcode{vcodec=h264,vb=12000,acodec=mp4a,ab=192,channels=2}:std{access=http,mux=ts,dst=:${port}${streamPath}}`;
  const ss = Math.max(0, Math.floor(Number(startSeconds) || 0));
  const input = escCmd(String(filePath));
  const logPath = `/tmp/cast_vlc_${jobId}.log`;

  const inner = [
    `nohup setsid cvlc -I dummy --no-video-title-show --start-time ${ss} ${input}`,
    `--sout ${escCmd(sout)}`,
    '--sout-keep',
    `</dev/null >${escCmd(logPath)} 2>&1 & echo $!`,
  ].join(' ');

  const { stdout } = await sshExec(`bash -lc ${escCmd(inner)}`);
  const pid = parseInt(String(stdout || '').trim(), 10);
  if (!Number.isFinite(pid) || pid <= 0) {
    throw new Error(`Failed to start cvlc (no pid). Check ${logPath}`);
  }

  const job = {
    jobId,
    backend: 'vlc',
    createdAt: Date.now(),
    cancelled: false,
    startSeconds: ss,
    streamUrl,
    title: title || path.basename(filePath),
    pid,
    port,
    logPath,
  };
  vlcCastJobs.set(jobId, job);
  mediaLog(`vlc job started: id=${jobId} pid=${pid} port=${port} url=${streamUrl}`);
  return job;
}

async function cancelVlcCastJob(jobId, reason = 'cancelled') {
  const jid = String(jobId || '').trim();
  if (!jid) return false;
  pruneVlcCastJobs();
  const job = vlcCastJobs.get(jid);
  if (!job) return false;

  job.cancelled = true;
  job.cancelReason = reason;
  job.cancelledAt = Date.now();
  job.expiresAt = Date.now() + VLC_JOB_CANCEL_TOMBSTONE_MS;
  vlcCastJobs.set(jid, job);

  const pid = Number(job.pid || 0);
  if (pid > 0) {
    try {
      await sshExec(`bash -lc ${escCmd(`kill -TERM ${pid} >/dev/null 2>&1 || true; sleep 0.2; kill -KILL ${pid} >/dev/null 2>&1 || true`)}`);
    } catch (_) {
      // best-effort
    }
  }
  return true;
}

// ─── Live cast jobs (in-memory) ──────────────────────────────
// jobId -> { filePath, playbackMode, analysis, createdAt, startSeconds, cachePath, cacheDir }
const liveCastJobs = new Map();
// jobId -> Set<{ conn, stream }>
const liveCastJobStreams = new Map();
const LIVE_JOB_TTL_MS = 60 * 60 * 1000; // 1 hour
const LIVE_JOB_CANCEL_TOMBSTONE_MS = 5 * 60 * 1000; // keep cancelled ids for 410 responses

function closeLiveJobStreams(jobId) {
  const jid = String(jobId || '').trim();
  if (!jid) return;
  const set = liveCastJobStreams.get(jid);
  if (!set) return;
  for (const ref of set) {
    try { ref.stream?.destroy?.(); } catch (_) {}
    try { ref.conn?.end?.(); } catch (_) {}
  }
  liveCastJobStreams.delete(jid);
}

/**
 * Mark a live job cancelled, tear down SSH/ffmpeg streams, keep a short tombstone for 410 responses.
 */
function markLiveJobCancelled(jobId, reason = 'cancelled') {
  const jid = String(jobId || '').trim();
  if (!jid) return false;
  const job = liveCastJobs.get(jid);
  if (!job) return false;

  job.cancelled = true;
  job.cancelReason = reason;
  job.cancelledAt = Date.now();
  job.expiresAt = Date.now() + LIVE_JOB_CANCEL_TOMBSTONE_MS;

  closeLiveJobStreams(jid);
  liveCastJobs.set(jid, job);
  return true;
}

function getLiveJobResponseState(jobId) {
  const jid = String(jobId || '').trim();
  if (!jid) return { status: 404, reason: 'Unknown live job' };

  pruneLiveJobs();

  const job = liveCastJobs.get(jid);
  if (!job) return { status: 404, reason: 'Unknown live job' };

  const now = Date.now();
  if (job.cancelled) return { status: 410, reason: job.cancelReason || 'Live job cancelled' };
  if (job.expiresAt != null && job.expiresAt < now) {
    return { status: 410, reason: 'Live job expired' };
  }

  return { status: 200, job };
}

/** For GET: JSON error body when headers not yet sent */
function sendLiveJobError(res, state) {
  if (!res.headersSent) {
    res.status(state.status).json({
      success: false,
      error: state.reason,
    });
  } else {
    try { res.end(); } catch (_) {}
  }
}

function pruneLiveJobs() {
  const now = Date.now();
  const toExpire = [];
  for (const [jobId, job] of liveCastJobs.entries()) {
    if (!job) {
      liveCastJobs.delete(jobId);
      liveCastJobStreams.delete(jobId);
      continue;
    }
    if (job.cancelled) {
      const until = job.expiresAt != null ? job.expiresAt : (job.cancelledAt || 0) + LIVE_JOB_CANCEL_TOMBSTONE_MS;
      if (now > until) {
        closeLiveJobStreams(jobId);
        liveCastJobs.delete(jobId);
      }
      continue;
    }
    if ((now - (job.createdAt || 0)) > LIVE_JOB_TTL_MS) {
      toExpire.push(jobId);
    }
  }
  for (const jobId of toExpire) {
    markLiveJobCancelled(jobId, 'expired');
  }
}

function buildLiveStreamUrl(req, jobId) {
  const base = getCastBaseUrl(req);
  const url = `${base}/api/cast/live/${encodeURIComponent(jobId)}`;
  mediaLog(`chromecast live URL: ${url}`);
  return url;
}

function makeAnalysisSummary(a) {
  if (!a) return null;
  return {
    container: a.container,
    formatName: a.formatName,
    duration: a.duration,
    videoCodec: a.videoCodec,
    videoStreamIndex: a.videoStreamIndex,
    videoWidth: a.videoWidth,
    videoHeight: a.videoHeight,
    audioCodec: a.audioCodec,
    audioStreamIndex: a.audioStreamIndex,
    audioStreamWasSwitched: a.audioStreamWasSwitched,
    playbackMode: a.playbackMode,
    reasons: a.reasons,
    ffmpegPlan: a.ffmpegPlan,
  };
}

function shouldUseReForLive(playbackMode) {
  const env = String(process.env.CAST_LIVE_USE_RE || '1').toLowerCase();
  if (env === '0' || env === 'false' || env === 'no') return false;
  return playbackMode === 'remux' || playbackMode === 'audio-transcode';
}

function buildLiveFfmpegCmd({ filePath, analysis, startSeconds = 0, teePath, fullTranscodeVariant } = {}) {
  const a = analysis || {};
  const ss = Number(startSeconds) > 0 ? `-ss ${Math.max(0, Math.floor(Number(startSeconds) || 0))}` : '';
  const mapVideo = a.videoStreamIndex != null ? `-map 0:${a.videoStreamIndex}` : '';
  const mapAudio = a.audioStreamIndex != null ? `-map 0:${a.audioStreamIndex}` : '';
  const re = shouldUseReForLive(a.playbackMode) ? '-re' : '';
  const inputTiming = '-fflags +genpts';
  const outputTiming = '-avoid_negative_ts make_zero -max_interleave_delta 0 -muxdelay 0 -muxpreload 0';
  const audioTiming = (a.audioStreamIndex != null && ['audio-transcode', 'full-transcode'].includes(a.playbackMode))
    ? '-af aresample=async=1:first_pts=0'
    : '';

  let codecs = '';
  let cudaHw = '';
  if (a.playbackMode === 'remux') {
    codecs = '-c:v copy -c:a copy';
  } else if (a.playbackMode === 'audio-transcode') {
    codecs = '-c:v copy -c:a aac -ac 2 -b:a 192k';
  } else if (a.playbackMode === 'full-transcode') {
    const v = fullTranscodeVariant || 'libx264';
    if (v === 'nvenc_cuda') {
      cudaHw = '-hwaccel cuda -hwaccel_output_format cuda';
      codecs = '-c:v h264_nvenc -preset p1 -tune ll -rc vbr -cq 28 -b:v 0 -c:a aac -ac 2 -b:a 160k';
    } else if (v === 'nvenc_plain') {
      codecs = '-c:v h264_nvenc -preset p1 -rc vbr -cq 28 -b:v 0 -c:a aac -ac 2 -b:a 160k';
    } else {
      codecs = '-c:v libx264 -preset superfast -crf 23 -c:a aac -ac 2 -b:a 192k';
    }
  }

  const mov = '-movflags frag_keyframe+empty_moov+default_base_moof';
  const out = `-f mp4 pipe:1`;
  const input = escCmd(String(filePath || ''));

  const tail = [cudaHw, inputTiming, ss, `-i ${input}`, mapVideo, mapAudio, codecs, audioTiming, mov, outputTiming, out].filter(Boolean).join(' ');
  const base = `ffmpeg -hide_banner -loglevel warning ${re} ${tail}`.replace(/\s+/g, ' ').trim();
  if (teePath) {
    const finalPath = String(teePath);
    const tmpPath = `${finalPath}.part`;
    // Write to .part and atomically move when ffmpeg ends (so we never "serve" partials as final).
    return `bash -lc ${escCmd(`${base} 2>/dev/null | tee "${tmpPath}"; mv -f "${tmpPath}" "${finalPath}"`)}`;
  }
  return `${base} 2>/dev/null`;
}

/** Chromecast live full-transcode (fMP4 over HTTP). Disabled via CAST_LIVE_FULLTRANSCODE=0. */
function liveFullTranscodeFeasible(analysis) {
  const env = String(process.env.CAST_LIVE_FULLTRANSCODE ?? '1').toLowerCase();
  if (env === '0' || env === 'false' || env === 'no' || env === 'off') return false;

  const maxPxRaw = process.env.CAST_LIVE_FULLTRANSCODE_MAX_PIXELS;
  const maxPx = maxPxRaw != null && String(maxPxRaw).trim() !== ''
    ? Number(maxPxRaw)
    : (7680 * 4320); // default: allow up to 8K UHD

  const w = Number(analysis?.videoWidth || 0);
  const h = Number(analysis?.videoHeight || 0);
  if (!w || !h) {
    const unknownOk = String(process.env.CAST_LIVE_FULLTRANSCODE_UNKNOWN_DIMS ?? '1').toLowerCase();
    return !(unknownOk === '0' || unknownOk === 'false' || unknownOk === 'no');
  }
  if (!Number.isFinite(maxPx) || maxPx <= 0) return true;
  return (w * h) <= maxPx;
}

function getLiveTranscodeVariantsForJob(job) {
  if (!job || job.playbackMode !== 'full-transcode') return [undefined];
  const le = job.liveEncode;
  if (!le || le.encoder !== 'h264_nvenc') return ['libx264'];
  const order = [];
  if (le.cudaFirst) order.push('nvenc_cuda');
  order.push('nvenc_plain');
  order.push('libx264');
  return order;
}

function tryOneLiveFfmpegStream(jobId, res, cmd) {
  return new Promise((resolve) => {
    let resolved = false;
    let sawData = false;
    function finish(ok, reason) {
      if (resolved) return;
      resolved = true;
      resolve({ ok, reason: reason || '' });
    }

    const conn = new Client();
    conn.on('ready', () => {
      const stateReady = getLiveJobResponseState(jobId);
      if (stateReady.status !== 200) {
        try { conn.end(); } catch (_) {}
        setLiveCors(res);
        sendLiveJobError(res, stateReady);
        return finish(false, stateReady.reason || 'job inactive');
      }
      conn.exec(cmd, (err, stream) => {
        if (err) {
          try { conn.end(); } catch (_) {}
          return finish(false, String(err.message || err));
        }

        stream.once('data', (chunk) => {
          sawData = true;
          const stateExec = getLiveJobResponseState(jobId);
          if (stateExec.status !== 200) {
            try { stream.destroy(); } catch (_) {}
            try { conn.end(); } catch (_) {}
            setLiveCors(res);
            sendLiveJobError(res, stateExec);
            return finish(false, stateExec.reason || 'job inactive');
          }

          if (!liveCastJobStreams.has(jobId)) liveCastJobStreams.set(jobId, new Set());
          liveCastJobStreams.get(jobId).add({ conn, stream });

          const cleanup = () => {
            const set = liveCastJobStreams.get(jobId);
            if (set) {
              for (const ref of set) {
                if (ref.conn === conn && ref.stream === stream) set.delete(ref);
              }
              if (set.size === 0) liveCastJobStreams.delete(jobId);
            }
            try { stream.destroy(); } catch (_) {}
            try { conn.end(); } catch (_) {}
          };
          res.on('close', cleanup);
          stream.on('close', cleanup);

          try {
            res.write(chunk);
          } catch (e) {
            cleanup();
            return finish(false, String(e.message || e));
          }
          stream.pipe(res);
          finish(true);
        });

        stream.on('close', (code) => {
          try { conn.end(); } catch (_) {}
          if (!sawData) finish(false, `ffmpeg exited ${code ?? '?'} before output`);
        });

        if (stream.stderr) {
          stream.stderr.on('data', () => {});
        }
      });
    });

    conn.on('error', (e) => finish(false, e.message));
    conn.connect(getSshConfig());
  });
}

async function pipeLiveFfmpegVariants(jobId, res, job, variants) {
  let nvencFailureReason = '';
  for (let i = 0; i < variants.length; i++) {
    if (res.writableEnded || res.headersSent) return;

    const stateCheck = getLiveJobResponseState(jobId);
    if (stateCheck.status !== 200) {
      setLiveCors(res);
      return sendLiveJobError(res, stateCheck);
    }

    const teePath = (job.cachePath && job.startSeconds === 0) ? job.cachePath : null;
    const cmd = buildLiveFfmpegCmd({
      filePath: job.filePath,
      analysis: { ...job.analysis, playbackMode: job.playbackMode },
      startSeconds: job.startSeconds || 0,
      teePath,
      fullTranscodeVariant: variants[i],
    });

    /* eslint-disable no-await-in-loop -- sequential encoder fallback */
    const r = await tryOneLiveFfmpegStream(jobId, res, cmd);
    /* eslint-enable no-await-in-loop */
    if (r.ok) {
      const variant = variants[i];
      if (variant === 'libx264' && nvencFailureReason) {
        mediaLog(`chromecast live-full-transcode: encoder=libx264 fallback; reason=${nvencFailureReason}`);
      }
      return;
    }

    const variant = variants[i];
    if (variant === 'nvenc_cuda' || variant === 'nvenc_plain') {
      nvencFailureReason = nvencFailureReason
        ? `${nvencFailureReason}; ${variant}: ${r.reason}`
        : `${variant}: ${r.reason}`;
    }

    if (res.writableEnded || res.headersSent) return;
  }

  if (!res.headersSent) {
    setLiveCors(res);
    res.status(500).json({
      success: false,
      error: 'Live ffmpeg failed to start',
      detail: nvencFailureReason || 'unknown',
    });
  }
}

function cancelLiveJob(jobId) {
  markLiveJobCancelled(jobId, 'cancelled');
}

const castSessions = createSessionStore();
const hlsJobs = createHlsJobManager({
  sshExec,
  sshConfig: getSshConfig(),
  cfg: CFG,
  resolveEncoder: resolveLiveFullTranscodeEncoder,
  logger: mediaLog,
});

const chromecastProvider = createChromecastProvider({
  cfg: CFG,
  sshExec,
  upsertDevice: upsertCastDevice,
  setSelectedDevice: setSelectedCastDevice,
  getSelectedDevice: getSelectedCastDevice,
});

const airplayProvider = createAirPlayProvider({
  sidecarUrl: process.env.AIRPLAY_SIDECAR_URL || 'http://127.0.0.1:8765',
  upsertDevice: upsertCastDevice,
  setSelectedDevice: setSelectedCastDevice,
  getSelectedDevice: getSelectedCastDevice,
  getDevice: getCastDevice,
  saveCredentials: updateCastDeviceCredentials,
});

const castProviders = {
  chromecast: chromecastProvider,
  airplay: airplayProvider,
};

function normalizeProviderName(value) {
  const provider = String(value || '').toLowerCase();
  return provider === 'airplay' ? 'airplay' : 'chromecast';
}

function getProvider(providerName) {
  const provider = castProviders[normalizeProviderName(providerName)];
  if (!provider) throw new Error(`Unknown cast provider: ${providerName}`);
  return provider;
}

async function getSelectedDeviceForProvider(providerName) {
  const provider = normalizeProviderName(providerName);
  const selected = getSelectedCastDevice(provider, { includeCredentials: false });
  if (selected) return selected;
  if (provider === 'chromecast') {
    const id = makeProviderDeviceId('chromecast', CFG.chromecastName);
    upsertCastDevice({ id, provider: 'chromecast', name: CFG.chromecastName, selected: true });
    setSelectedCastDevice('chromecast', id);
    return getSelectedCastDevice('chromecast', { includeCredentials: false });
  }
  return null;
}

async function cleanupPreparedMedia(prepared, reason = 'cleanup') {
  if (!prepared) return;
  if (prepared.backend === 'hls' && prepared.jobId) await hlsJobs.cancelJob(prepared.jobId, reason).catch(() => {});
  if (prepared.backend === 'vlc' && prepared.jobId) await cancelVlcCastJob(prepared.jobId, reason).catch(() => {});
}

async function restartActiveCastAt(seconds) {
  if (!activeCastSession.filePath && !activeCastSession.streamUrl) {
    throw new Error('No active cast session to restart');
  }

  const startSeconds = Math.max(0, Math.floor(Number(seconds) || 0));

  if (activeCastSession.backend === 'vlc') {
    if (!activeCastSession.filePath) throw new Error('No active VLC session to restart');
    let base = null;
    if (activeCastSession.streamUrl) {
      try {
        const u = new URL(activeCastSession.streamUrl);
        u.pathname = '';
        u.search = '';
        u.hash = '';
        base = u.toString().replace(/\/+$/, '');
      } catch (_) { base = null; }
    }
    const oldJobId = activeCastSession.vlcJobId;
    if (oldJobId) await cancelVlcCastJob(oldJobId, 'restart');
    const job = await createVlcCastJob({
      req: null,
      filePath: activeCastSession.filePath,
      startSeconds,
      title: activeCastSession.title || path.basename(activeCastSession.filePath),
      publicBaseUrl: base || process.env.CAST_PUBLIC_BASE_URL || process.env.PUBLIC_BASE_URL || process.env.SERVER_PUBLIC_URL,
    });
    if (!job.streamUrl) {
      throw new Error('VLC restart requires CAST_PUBLIC_BASE_URL (or an existing session streamUrl) to build a receiver-reachable URL');
    }
    await castMediaAtPosition({
      streamUrl: job.streamUrl,
      filePath: activeCastSession.filePath,
      seconds: startSeconds,
      receiverSeek: false,
      title: activeCastSession.title,
      type: activeCastSession.type,
      backend: 'vlc',
    });
    activeCastSession.backend = 'vlc';
    activeCastSession.vlcJobId = job.jobId;
    activeCastSession.streamUrl = job.streamUrl;
    activeCastSession.liveJobId = null;
    activeCastSession.resolvedPath = null;
    activeCastSession.startSeconds = startSeconds;
    activeCastSession.lastKnownTime = startSeconds;
    return { success: true, startSeconds, mode: 'vlc' };
  }

  // If current session is a live job stream, regenerate a new live job at the target time.
  if (activeCastSession.liveJobId && activeCastSession.filePath && activeCastSession.playbackMode && activeCastSession.analysisSummary) {
    pruneLiveJobs();
    const jobId = stableHash(JSON.stringify({
      v: 1,
      filePath: activeCastSession.filePath,
      playbackMode: activeCastSession.playbackMode,
      analysis: {
        videoStreamIndex: activeCastSession.analysisSummary.videoStreamIndex,
        audioStreamIndex: activeCastSession.analysisSummary.audioStreamIndex,
        videoCodec: activeCastSession.analysisSummary.videoCodec,
        audioCodec: activeCastSession.analysisSummary.audioCodec,
      },
      startSeconds,
      t: Date.now(),
    }));

    const job = {
      filePath: activeCastSession.filePath,
      playbackMode: activeCastSession.playbackMode,
      analysis: { ...activeCastSession.analysisSummary, playbackMode: activeCastSession.playbackMode },
      createdAt: Date.now(),
      startSeconds,
      cacheDir: '/tmp/cast_transcodes_v2',
      cachePath: null,
      liveEncode: activeCastSession.liveEncode || null,
    };
    liveCastJobs.set(jobId, job);

    const liveUrl = activeCastSession.streamUrl
      ? activeCastSession.streamUrl.replace(/\/api\/cast\/live\/[^/?#]+.*/, `/api/cast/live/${encodeURIComponent(jobId)}`)
      : null;

    // Don't ask receiver to seek; the stream starts at -ss.
    const result = await castMediaAtPosition({
      streamUrl: liveUrl,
      filePath: activeCastSession.filePath,
      seconds: startSeconds,
      receiverSeek: false,
      title: activeCastSession.title,
      type: activeCastSession.type,
    });

    activeCastSession.liveJobId = jobId;
    activeCastSession.playbackMode = job.playbackMode;
    activeCastSession.analysisSummary = job.analysis;
    activeCastSession.resolvedPath = null;
    activeCastSession.streamUrl = liveUrl;
    activeCastSession.startSeconds = startSeconds;
    activeCastSession.lastKnownTime = startSeconds;
    return result;
  }

  return castMediaAtPosition({
    filePath: activeCastSession.resolvedPath || activeCastSession.filePath,
    streamUrl: activeCastSession.streamUrl,
    seconds: startSeconds,
    title: activeCastSession.title,
    type: activeCastSession.type,
    subtitlePath: activeCastSession.subtitlePath,
  });
}

// ─── LIVE CAST STREAM ENDPOINT ───────────────────────────────
function setLiveCors(res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, HEAD, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Range');
}

app.options('/api/cast/live/:jobId', (req, res) => {
  setLiveCors(res);
  return res.sendStatus(204);
});

app.head('/api/cast/live/:jobId', (req, res) => {
  const jobId = String(req.params.jobId || '').trim();
  const state = getLiveJobResponseState(jobId);
  setLiveCors(res);
  if (state.status !== 200) {
    return res.status(state.status).end();
  }
  res.setHeader('Content-Type', 'video/mp4');
  res.setHeader('Cache-Control', 'no-store');
  res.setHeader('Connection', 'keep-alive');
  return res.status(200).end();
});

app.get('/api/cast/live/:jobId', async (req, res) => {
  try {
    const jobId = String(req.params.jobId || '').trim();
    const state0 = getLiveJobResponseState(jobId);
    if (state0.status !== 200) {
      setLiveCors(res);
      return sendLiveJobError(res, state0);
    }
    const job = state0.job;

    // Never serve arbitrary paths.
    if (!isPathSafe(job.filePath)) {
      setLiveCors(res);
      return res.status(403).json({ error: 'Cannot stream this path' });
    }

    const stateBeforeStart = getLiveJobResponseState(jobId);
    if (stateBeforeStart.status !== 200) {
      setLiveCors(res);
      return sendLiveJobError(res, stateBeforeStart);
    }

    res.setHeader('Content-Type', 'video/mp4');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Connection', 'keep-alive');
    setLiveCors(res);

    const variants = getLiveTranscodeVariantsForJob(job);
    await pipeLiveFfmpegVariants(jobId, res, job, variants);
  } catch (err) {
    if (!res.headersSent) fileManagerError(res, err, 'Stream failed');
  }
});

// ─── VLC JOB DIAGNOSTIC ENDPOINT ─────────────────────────────
app.get('/api/cast/vlc/:jobId', async (req, res) => {
  try {
    const jobId = String(req.params.jobId || '').trim();
    if (!jobId) return res.status(404).json({ success: false, error: 'Unknown VLC job' });
    pruneVlcCastJobs();
    const job = vlcCastJobs.get(jobId);
    if (!job) return res.status(404).json({ success: false, error: 'Unknown VLC job' });
    if (job.cancelled) return res.status(410).json({ success: false, error: job.cancelReason || 'VLC job cancelled' });
    res.json({
      success: true,
      backend: 'vlc',
      job: {
        jobId: job.jobId,
        createdAt: job.createdAt,
        startSeconds: job.startSeconds,
        streamUrl: job.streamUrl,
        title: job.title,
        port: job.port,
      },
    });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

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
    fileManagerError(res, err, 'Cast failed');
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
    conn.connect(getSshConfig());
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
    const requestedPath = req.query.path || CFG.fileManagerRoot;
    const sudoPwd = getSudoPassword(req);
    const dir = await assertRemotePathInsideRoot(requestedPath, { sudoPwd });
    const showHidden = String(req.query.showHidden || '').toLowerCase() === 'true' || req.query.showHidden === '1';
    const hiddenFilter = showHidden ? '' : " ! -name '.*'";
    const sudoPrefix = makeSudoPrefix(sudoPwd);

    const dirCheck = await sshExec(`${sudoPrefix}test -d ${escCmd(dir)}`);
    if (dirCheck.code !== 0) {
      throw commandFailure(dirCheck.code, dirCheck.stderr, 'NOT_A_DIRECTORY', 'Path is not a directory');
    }

    const listResult = await sshExec(
      `${sudoPrefix}find ${escCmd(dir)} -mindepth 1 -maxdepth 1${hiddenFilter} -printf '%y\\t%s\\t%T@\\t%p\\0'`,
      45000
    );
    if (listResult.code !== 0) {
      throw commandFailure(listResult.code, listResult.stderr, 'READ_FAILED', 'Could not read this directory');
    }

    const files = [];
    const folders = [];
    for (const record of listResult.stdout.split('\0')) {
      if (!record.trim()) continue;
      const [type, size, mtime, ...pathParts] = record.split('\t');
      const filePath = pathParts.join('\t');
      if (!filePath || filePath === dir) continue;
      const entry = buildFileMetadata(filePath, type, size, mtime);
      entry.starred = isStarred(filePath);
      files.push(entry);
      if (entry.type === 'folder') folders.push(entry);
    }

    if (folders.length > 0) {
      try {
        const measurableFolders = folders.filter(f => !(dir === '/' && VIRTUAL_USAGE_PATHS.has(f.path)));
        const duPaths = measurableFolders.map(f => escCmd(f.path)).join(' ');
        const duScript = duPaths ? `${sudoPrefix}du -x -sb ${duPaths} 2>/dev/null || true` : '';
        const { stdout: duOut } = duScript
          ? await sshExec(`timeout 25s bash -lc ${escCmd(duScript)}`, 30000)
          : { stdout: '' };
        const sizeMap = {};
        for (const line of duOut.split('\n')) {
          if (!line.trim()) continue;
          const tabIdx = line.indexOf('\t');
          if (tabIdx > 0) {
            sizeMap[line.substring(tabIdx + 1).trim()] = parseInt(line.substring(0, tabIdx), 10) || 0;
          }
        }
        for (const f of folders) {
          if (sizeMap[f.path] !== undefined) f.size = sizeMap[f.path];
          else if (dir === '/' && VIRTUAL_USAGE_PATHS.has(f.path)) {
            f.size = 0;
            f.sizeUnavailable = true;
          }
        }
      } catch (e) { /* folder sizes are helpful, not required */ }

      try {
        const countCmd = folders.map((f) => (
          `printf '%s\\t' ${escCmd(f.path)}; ${sudoPrefix}find ${escCmd(f.path)} -mindepth 1 -maxdepth 1${hiddenFilter} 2>/dev/null | wc -l`
        )).join('; ');
        const { stdout: countOut } = await sshExec(countCmd);
        const countMap = {};
        for (const line of countOut.split('\n')) {
          const tabIdx = line.indexOf('\t');
          if (tabIdx > 0) countMap[line.substring(0, tabIdx)] = parseInt(line.substring(tabIdx + 1), 10) || 0;
        }
        for (const f of folders) f.itemCount = countMap[f.path] || 0;
      } catch (e) { /* skip item counts */ }
    }

    files.sort((a, b) => {
      if (a.type === 'folder' && b.type !== 'folder') return -1;
      if (a.type !== 'folder' && b.type === 'folder') return 1;
      return a.name.localeCompare(b.name, undefined, { sensitivity: 'base' });
    });

    const parent = safeParentPath(CFG.fileManagerRoot, dir);
    res.json({
      ok: true,
      success: true,
      files,
      currentPath: dir,
      relativePath: toRelativePath(CFG.fileManagerRoot, dir),
      parentPath: parent,
      isRoot: dir === CFG.fileManagerRoot,
      rootPath: CFG.fileManagerRoot,
      rootLabel: CFG.fileManagerRootLabel,
      showHidden,
      breadcrumbs: buildBreadcrumbs(CFG.fileManagerRoot, dir, CFG.fileManagerRootLabel),
    });
  } catch (err) {
    fileManagerError(res, err, 'Could not list files');
  }
});

app.post('/api/files/info', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.body.filePath);
    ensureNotProtectedFile(filePath, 'inspected');
    const { stdout } = await sshExec(
      `ffprobe -v error -show_entries format=duration,size,bit_rate -show_entries stream=codec_name,codec_type,width,height,channels -of json ${escCmd(filePath)}`
    );
    res.json({ ok: true, success: true, ...JSON.parse(stdout) });
  } catch (err) {
    fileManagerError(res, err, 'Could not inspect file');
  }
});

app.post('/api/files/duration', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.body.filePath);
    ensureNotProtectedFile(filePath, 'read');
    const { stdout } = await sshExec(
      `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 ${escCmd(filePath)}`
    );
    res.json({ ok: true, success: true, duration: parseFloat(stdout) || 0 });
  } catch (err) {
    fileManagerError(res, err, 'Could not read media duration');
  }
});

app.get('/api/files/read', async (req, res) => {
  try {
    const sudoPwd = getSudoPassword(req);
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const filePath = await assertRemotePathInsideRoot(req.query.path, { sudoPwd });
    ensureNotProtectedFile(filePath, 'read');
    const { stdout: statOut, stderr: statErr, code: statCode } = await sshExec(
      `${sudoPrefix}stat -c '%F\\t%s\\t%Y' ${escCmd(filePath)}`
    );
    if (statCode !== 0) {
      throw commandFailure(statCode, statErr, 'NOT_A_FILE', 'Path is not a file');
    }
    const [kindRaw, sizeRaw, mtimeRaw] = statOut.split('\t');
    if (!String(kindRaw || '').toLowerCase().includes('regular file')) {
      throw makeFileManagerError('NOT_A_FILE', 'Path is not a file');
    }
    const size = parseInt(sizeRaw, 10) || 0;
    const mtime = parseInt(mtimeRaw, 10) || 0;
    const { stdout: mimeOut } = await sshExec(`${sudoPrefix}file -b --mime-type ${escCmd(filePath)} 2>/dev/null || echo application/octet-stream`);
    const mimeType = (mimeOut || 'application/octet-stream').split('\n')[0].trim();
    const metadata = buildFileMetadata(filePath, 'f', size, mtime);

    if (size > FILE_MANAGER_MAX_PREVIEW_BYTES) {
      return res.json({
        ok: true,
        success: true,
        metadata,
        previewAvailable: false,
        editable: false,
        tooLarge: true,
        maxBytes: FILE_MANAGER_MAX_PREVIEW_BYTES,
        message: `File is larger than ${FILE_MANAGER_MAX_PREVIEW_BYTES} bytes`,
      });
    }

    if (!isLikelyTextFile(filePath, mimeType)) {
      return res.json({
        ok: true,
        success: true,
        metadata,
        previewAvailable: false,
        editable: false,
        binary: true,
        mimeType,
        message: 'Preview unavailable for this file type',
      });
    }

    const contentResult = await sshExec(`${sudoPrefix}base64 -w 0 -- ${escCmd(filePath)}`, 45000);
    if (contentResult.code !== 0) {
      throw commandFailure(contentResult.code, contentResult.stderr, 'READ_FAILED', 'Could not read file contents');
    }
    const content = Buffer.from(contentResult.stdout.replace(/\s/g, ''), 'base64').toString('utf8');

    res.json({
      ok: true,
      success: true,
      metadata: { ...metadata, mimeType },
      previewAvailable: true,
      editable: true,
      content,
      maxBytes: FILE_MANAGER_MAX_PREVIEW_BYTES,
    });
  } catch (err) {
    fileManagerError(res, err, 'Could not read file');
  }
});

app.post('/api/files/write', async (req, res) => {
  try {
    const sudoPwd = getSudoPassword(req);
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const filePath = await assertRemotePathInsideRoot(req.body.filePath, { sudoPwd });
    ensureNotProtectedFile(filePath, 'edited');
    const content = typeof req.body.content === 'string' ? req.body.content : '';
    if (Buffer.byteLength(content, 'utf8') > FILE_MANAGER_MAX_PREVIEW_BYTES) {
      throw makeFileManagerError('FILE_TOO_LARGE', 'File is too large to edit in the browser');
    }
    const fileCheck = await sshExec(`${sudoPrefix}test -f ${escCmd(filePath)}`);
    if (fileCheck.code !== 0) {
      throw commandFailure(fileCheck.code, fileCheck.stderr, 'NOT_A_FILE', 'Path is not a file');
    }

    const tmpPath = `/tmp/cast_manager_edit_${Date.now()}_${crypto.randomBytes(6).toString('hex')}`;
    const remoteTmpPath = `/tmp/${path.basename(tmpPath)}`;
    fs.writeFileSync(tmpPath, content, 'utf8');
    try {
      await new Promise((resolve, reject) => {
        const conn = new Client();
        conn.on('ready', () => {
          conn.sftp((err, sftp) => {
            if (err) { conn.end(); return reject(err); }
            sftp.fastPut(tmpPath, sudoPwd ? remoteTmpPath : filePath, (putErr) => {
              conn.end();
              if (putErr) reject(putErr); else resolve();
            });
          });
        });
        conn.on('error', reject);
        conn.connect(getSshConfig());
      });
    } catch (putErr) {
      if (/permission denied|not permitted/i.test(String(putErr?.message || ''))) {
        throw makeFileManagerError('PERMISSION_DENIED', 'Permission denied. Enter the server sudo password to retry.');
      }
      throw putErr;
    } finally {
      try { fs.unlinkSync(tmpPath); } catch (_) {}
    }

    if (sudoPwd) {
      const overwrite = await sshExec(`${sudoPrefix}sh -c ${escCmd(`cat ${escCmd(remoteTmpPath)} > ${escCmd(filePath)}`)}; rm -f ${escCmd(remoteTmpPath)}`);
      if (overwrite.code !== 0) {
        throw commandFailure(overwrite.code, overwrite.stderr, 'WRITE_FAILED', 'Could not write file');
      }
    }

    logActivity('write_file', filePath);
    res.json({ ok: true, success: true, filePath });
  } catch (err) {
    fileManagerError(res, err, 'Could not write file');
  }
});

app.post('/api/files/create', async (req, res) => {
  try {
    const sudoPwd = getSudoPassword(req);
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const parentPath = await assertRemotePathInsideRoot(req.body.parentPath || CFG.fileManagerRoot, { sudoPwd });
    const name = validateItemName(req.body.name);
    const newPath = joinChild(CFG.fileManagerRoot, parentPath, name);
    ensureNotProtectedFile(newPath, 'created');
    await assertParentPathInsideRoot(newPath, { sudoPwd });
    const { code, stderr } = await sshExec(`if ${sudoPrefix}test -e ${escCmd(newPath)}; then exit 17; fi; ${sudoPrefix}touch ${escCmd(newPath)}`);
    if (code === 17) throw makeFileManagerError('ALREADY_EXISTS', 'A file or folder with that name already exists');
    if (code !== 0) throw commandFailure(code, stderr, 'WRITE_FAILED', 'Could not create file');
    logActivity('create_file', newPath);
    res.json({ ok: true, success: true, path: newPath });
  } catch (err) {
    fileManagerError(res, err, 'Could not create file');
  }
});

app.post('/api/files/delete', async (req, res) => {
  try {
    const { filePath, permanent, sudoPwd } = req.body;
    const safePath = await assertRemotePathInsideRoot(filePath, { sudoPwd });
    if (isRootPath(safePath)) throw makeFileManagerError('INVALID_PATH', 'The file-manager root cannot be deleted');
    ensureNotCriticalSystemPath(safePath, 'deleted');
    ensureNotProtectedFile(safePath, 'deleted');
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    if (permanent) {
      const result = await sshExec(`${sudoPrefix}rm -rf ${escCmd(safePath)}`);
      if (result.code !== 0) throw commandFailure(result.code, result.stderr, 'DELETE_FAILED', 'Delete failed');
      logActivity('delete_permanent', safePath);
    } else {
      const trashDir = CFG.trashDir;
      const filename = path.basename(safePath);
      const trashName = `${Date.now()}_${filename}`;
      const trashPath = `${trashDir}/${trashName}`;
      await sshExec(`mkdir -p ${escCmd(trashDir)}`);
      const { stdout: sizeStr } = await sshExec(`${sudoPrefix}stat -c%s ${escCmd(safePath)} 2>/dev/null || echo 0`);
      const size = parseInt(sizeStr.trim()) || 0;
      const ext = path.extname(filename).toLowerCase();
      const videoExts = ['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v'];
      const audioExts = ['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus'];
      const fileType = videoExts.includes(ext) ? 'video' : audioExts.includes(ext) ? 'audio' : 'other';
      const moveResult = await sshExec(`${sudoPrefix}mv ${escCmd(safePath)} ${escCmd(trashPath)}`);
      if (moveResult.code !== 0) throw commandFailure(moveResult.code, moveResult.stderr, 'DELETE_FAILED', 'Could not move item to trash');
      addToTrash(safePath, trashPath, filename, fileType, size);
      logActivity('trash', safePath, { trashPath });
    }
    res.json({ ok: true, success: true, trashed: !permanent });
  } catch (err) { fileManagerError(res, err, 'Delete failed'); }
});

app.post('/api/files/rename', async (req, res) => {
  try {
    const { oldPath, sudoPwd } = req.body;
    const safeOldPath = await assertRemotePathInsideRoot(oldPath, { sudoPwd });
    if (isRootPath(safeOldPath)) throw makeFileManagerError('INVALID_PATH', 'The file-manager root cannot be renamed');
    ensureNotCriticalSystemPath(safeOldPath, 'renamed');
    ensureNotProtectedFile(safeOldPath, 'renamed');
    const newName = validateItemName(req.body.newName);
    const dir = path.posix.dirname(safeOldPath);
    const newPath = joinChild(CFG.fileManagerRoot, dir, newName);
    ensureNotProtectedFile(newPath, 'renamed');
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const { code, stderr } = await sshExec(`if ${sudoPrefix}test -e ${escCmd(newPath)}; then exit 17; fi; ${sudoPrefix}mv ${escCmd(safeOldPath)} ${escCmd(newPath)}`);
    if (code === 17) throw makeFileManagerError('ALREADY_EXISTS', 'A file or folder with that name already exists');
    if (code !== 0) throw commandFailure(code, stderr, 'WRITE_FAILED', 'Rename failed');
    res.json({ ok: true, success: true, newPath });
  } catch (err) { fileManagerError(res, err, 'Rename failed'); }
});

app.post('/api/files/copy', async (req, res) => {
  try {
    const { filePath, sudoPwd } = req.body;
    const safePath = await assertRemotePathInsideRoot(filePath, { sudoPwd });
    ensureNotCriticalSystemPath(safePath, 'copied');
    ensureNotProtectedFile(safePath, 'copied');
    const destName = validateItemName(req.body.destName);
    const dir = path.posix.dirname(safePath);
    const newPath = joinChild(CFG.fileManagerRoot, dir, destName);
    ensureNotProtectedFile(newPath, 'copied');
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const { code, stderr } = await sshExec(`if ${sudoPrefix}test -e ${escCmd(newPath)}; then exit 17; fi; ${sudoPrefix}cp -r ${escCmd(safePath)} ${escCmd(newPath)}`, 120000);
    if (code === 17) throw makeFileManagerError('ALREADY_EXISTS', 'A file or folder with that name already exists');
    if (code !== 0) throw commandFailure(code, stderr, 'WRITE_FAILED', 'Copy failed');
    res.json({ ok: true, success: true, newPath });
  } catch (err) { fileManagerError(res, err, 'Copy failed'); }
});

app.post('/api/files/mkdir', async (req, res) => {
  try {
    const { sudoPwd } = req.body;
    const parentPath = await assertRemotePathInsideRoot(req.body.parentPath || CFG.fileManagerRoot, { sudoPwd });
    const name = validateItemName(req.body.name);
    const newDir = joinChild(CFG.fileManagerRoot, parentPath, name);
    ensureNotProtectedFile(newDir, 'created');
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const { code, stderr } = await sshExec(`if ${sudoPrefix}test -e ${escCmd(newDir)}; then exit 17; fi; ${sudoPrefix}mkdir ${escCmd(newDir)}`);
    if (code === 17) throw makeFileManagerError('ALREADY_EXISTS', 'A file or folder with that name already exists');
    if (code !== 0) throw commandFailure(code, stderr, 'WRITE_FAILED', 'Could not create directory');
    res.json({ ok: true, success: true, path: newDir });
  } catch (err) { fileManagerError(res, err, 'Could not create directory'); }
});

app.post('/api/files/move', async (req, res) => {
  try {
    const { sourcePath, destDir, sudoPwd } = req.body;
    const safeSourcePath = await assertRemotePathInsideRoot(sourcePath, { sudoPwd });
    const safeDestDir = await assertRemotePathInsideRoot(destDir || CFG.fileManagerRoot, { sudoPwd });
    if (isRootPath(safeSourcePath)) throw makeFileManagerError('INVALID_PATH', 'The file-manager root cannot be moved');
    ensureNotCriticalSystemPath(safeSourcePath, 'moved');
    ensureNotProtectedFile(safeSourcePath, 'moved');
    const fileName = path.basename(safeSourcePath);
    const destPath = joinChild(CFG.fileManagerRoot, safeDestDir, fileName);
    if (safeDestDir === safeSourcePath || safeDestDir.startsWith(`${safeSourcePath}/`)) {
      throw makeFileManagerError('INVALID_PATH', 'A folder cannot be moved into itself or its children');
    }
    const sudoPrefix = makeSudoPrefix(sudoPwd);
    const { code, stderr } = await sshExec(`if ${sudoPrefix}test -e ${escCmd(destPath)}; then exit 17; fi; ${sudoPrefix}mv ${escCmd(safeSourcePath)} ${escCmd(destPath)}`);
    if (code === 17) throw makeFileManagerError('ALREADY_EXISTS', 'Destination already has an item with that name');
    if (code !== 0) throw commandFailure(code, stderr, 'WRITE_FAILED', 'Move failed');
    res.json({ ok: true, success: true, newPath: destPath });
  } catch (err) { fileManagerError(res, err, 'Move failed'); }
});

app.get('/api/files/download', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.query.path);
    ensureNotProtectedFile(filePath, 'downloaded');
    const fileName = path.basename(filePath);

    // Get file size first
    const { stdout: sizeStr } = await sshExec(`stat -c%s ${escCmd(filePath)} 2>/dev/null || echo 0`);
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
    conn.connect(getSshConfig());
  } catch (err) {
    if (!res.headersSent) fileManagerError(res, err, 'Download failed');
  }
});

// ─── STREAMING ENDPOINT (Range support + FFmpeg transcode for browser) ───
app.get('/api/files/stream', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.query.path);
    ensureNotProtectedFile(filePath, 'streamed');

    const ext = path.extname(filePath).toLowerCase();
    const mimeType = getMimeType(filePath);
    const fileStat = await getRemoteStat(filePath);
    const fileSize = fileStat.size;
    if (fileSize === 0) return res.status(404).json({ error: 'File not found or empty' });

    // Check if this is a media file that might need transcoding
    const videoExts = ['.mkv', '.avi', '.wmv', '.flv', '.ts', '.m4v', '.mov', '.mp4', '.webm'];
    const audioExts = ['.flac', '.wma', '.ogg', '.wav', '.m4a', '.aac', '.mp3', '.opus'];
    const isMedia = videoExts.includes(ext) || audioExts.includes(ext);
    const isVideo = videoExts.includes(ext);

    const autoTranscode = String(req.query.autoTranscode || 'auto').toLowerCase();
    if (isMedia && !req.query.raw) {
      try {
        const analysis = await analyzeMediaCompatibility(filePath, 'browser', { autoTranscode }, { sshExec });
        const playbackMode = analysis.playbackMode;

        if (playbackMode !== 'direct' && playbackMode !== 'unsupported') {
          const cacheDir = '/tmp/cast_manager_cache';
          const cacheKey = buildMediaCacheKey({
            filePath,
            stat: fileStat,
            target: 'browser',
            playbackMode,
            ffmpegPlan: analysis.ffmpegPlan,
            streamSelection: { v: analysis.videoStreamIndex, a: analysis.audioStreamIndex },
          });
          const cachePath = `${cacheDir}/${cacheKey}.mp4`;
          const { stdout: cacheCheck } = await sshExec(`test -f "${cachePath}" && stat -c%s "${cachePath}" 2>/dev/null || echo 0`);
          const cacheSize = parseInt(String(cacheCheck || '').trim(), 10) || 0;

          if (cacheSize > 0) {
            const range = req.headers.range;
            const conn = new Client();
            conn.on('ready', () => {
              conn.sftp((err, sftp) => {
                if (err) { conn.end(); return res.status(500).end(); }
                if (range) {
                  const parts = range.replace(/bytes=/, '').split('-');
                  const start = parseInt(parts[0], 10);
                  const end = parts[1] ? parseInt(parts[1], 10) : Math.min(start + 10 * 1024 * 1024 - 1, cacheSize - 1);
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
            conn.connect(getSshConfig());
            return;
          }

          // No cache — remux/transcode on-the-fly; keep existing "pipe + tee cache" strategy.
          res.writeHead(200, {
            'Content-Type': 'video/mp4',
            'Transfer-Encoding': 'chunked',
            'Cache-Control': 'no-cache',
            'Access-Control-Allow-Origin': '*',
          });

          const transcodeConn = new Client();
          transcodeConn.on('ready', () => {
            const mapVideo = analysis.videoStreamIndex != null ? `-map 0:${analysis.videoStreamIndex}` : '';
            const mapAudio = analysis.audioStreamIndex != null ? `-map 0:${analysis.audioStreamIndex}` : '';

            let codecs = '';
            let audioTiming = '';
            if (playbackMode === 'remux') {
              codecs = '-c:v copy -c:a copy';
              mediaLog(`browser remux: selecting streams v:${analysis.videoStreamIndex} a:${analysis.audioStreamIndex}`);
            } else if (playbackMode === 'audio-transcode') {
              codecs = '-c:v copy -c:a aac -b:a 192k -ac 2';
              audioTiming = analysis.audioStreamIndex != null ? '-af aresample=async=1:first_pts=0' : '';
              mediaLog(`browser audio-transcode: audio ${analysis.audioCodec} -> AAC; copy video ${analysis.videoCodec}`);
            } else if (playbackMode === 'full-transcode') {
              codecs = '-c:v libx264 -preset veryfast -crf 22 -c:a aac -b:a 192k -ac 2';
              audioTiming = analysis.audioStreamIndex != null ? '-af aresample=async=1:first_pts=0' : '';
              mediaLog(`browser full-transcode: video ${analysis.videoCodec} -> H264; audio -> AAC`);
            }

            const cmd = `mkdir -p ${cacheDir} && ffmpeg -fflags +genpts -i "${filePath}" ${mapVideo} ${mapAudio} ${codecs} ${audioTiming} -movflags frag_keyframe+empty_moov+default_base_moof -avoid_negative_ts make_zero -max_interleave_delta 0 -muxdelay 0 -muxpreload 0 -f mp4 pipe:1 2>/dev/null | tee "${cachePath}"`;
            transcodeConn.exec(cmd, (err, stream) => {
              if (err) { transcodeConn.end(); return res.end(); }
              stream.pipe(res);
              stream.on('close', () => transcodeConn.end());
              stream.stderr.on('data', () => {});
              res.on('close', () => { stream.destroy(); transcodeConn.end(); });
            });
          });
          transcodeConn.on('error', () => { if (!res.headersSent) res.status(500).end(); });
          transcodeConn.connect(getSshConfig());
          return;
        }

        if (playbackMode === 'unsupported') {
          return res.status(415).json({ error: 'Unsupported media for browser playback', reasons: analysis.reasons || [] });
        }
      } catch (probeErr) {
        // Preserve legacy fallback: if analysis fails, fall through to direct streaming.
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
    conn.connect(getSshConfig());
  } catch (err) {
    if (!res.headersSent) res.status(500).json({ error: err.message });
  }
});

// ─── THUMBNAIL ENDPOINTS ────────────────────────────────────
app.post('/api/thumbnail', async (req, res) => {
  try {
    let { filePath, type } = req.body;
    filePath = await assertRemotePathInsideRoot(filePath);
    ensureNotProtectedFile(filePath, 'previewed');
    const thumbDir = `${CFG.fileManagerRoot}/.thumbnails`;
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
        `ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 ${escCmd(filePath)}`
      );
      const dur = parseFloat(durStr) || 60;
      const seekTo = Math.floor(dur * 0.1);
      await sshExec(
        `ffmpeg -y -ss ${seekTo} -i ${escCmd(filePath)} -vframes 1 -q:v 3 -vf scale=400:-1 ${escCmd(thumbPath)} 2>/dev/null`,
        60000
      );
    } else if (type === 'audio') {
      await sshExec(
        `ffmpeg -y -i ${escCmd(filePath)} -an -vcodec copy ${escCmd(thumbPath)} 2>/dev/null`,
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
    const thumbName = validateItemName(req.params.name);
    const thumbPath = joinChild(CFG.fileManagerRoot, `${CFG.fileManagerRoot}/.thumbnails`, thumbName);
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
    conn.connect(getSshConfig());
  } catch (err) {
    res.status(500).end();
  }
});

// ─── PROVIDER-BASED CASTING ENDPOINTS ───────────────────────
async function discoverCastDevices(providerFilter = 'all') {
  const providers = providerFilter === 'all'
    ? ['chromecast', 'airplay']
    : [normalizeProviderName(providerFilter)];
  const results = [];
  const errors = [];
  for (const providerName of providers) {
    try {
      const devices = await getProvider(providerName).discover();
      results.push(...devices);
    } catch (err) {
      errors.push({ provider: providerName, error: err.message });
    }
  }
  return { devices: results, errors };
}

async function resolveSubtitleUrlForStart(req, filePath) {
  const { subtitlePath, customSubtitlePath, subtitleId } = req.body || {};
  const customPath = customSubtitlePath || subtitlePath;
  if (customPath) {
    const customSubtitleId = await registerCustomSubtitlePath(filePath, customPath);
    return buildSubtitleUrl(req, customSubtitleId);
  }
  if (subtitleId) {
    pruneSubtitleIndex();
    const indexedSubtitle = subtitleIndex.get(String(subtitleId || '').trim());
    if (!indexedSubtitle) throw makeFileManagerError('NOT_FOUND', 'Subtitle selection expired. Reopen the cast dialog and choose it again.');
    if (indexedSubtitle.filePath !== filePath) throw makeFileManagerError('INVALID_PATH', 'Subtitle does not belong to the selected video');
    return buildSubtitleUrl(req, subtitleId);
  }
  return null;
}

async function startProviderCast(req, res) {
  let prepared = null;
  try {
    let { filePath, seekTo } = req.body || {};
    filePath = await assertRemotePathInsideRoot(filePath);
    ensureNotProtectedFile(filePath, 'cast');

    const selectedAny = getSelectedCastDevice(null, { includeCredentials: false });
    const providerName = normalizeProviderName(req.body?.provider || selectedAny?.provider || 'chromecast');
    const provider = getProvider(providerName);
    const selected = req.body?.deviceId
      ? { device_id: req.body.deviceId, name: req.body.deviceName || '' }
      : await getSelectedDeviceForProvider(providerName);
    if (!selected?.device_id) {
      return res.status(409).json({ success: false, error: `No ${providerName} device selected. Scan and select a device first.`, provider: providerName });
    }
    if (req.body?.deviceId) await provider.selectDevice(req.body.deviceId);

    const seconds = seekTo ? parseTimeToSeconds(String(seekTo)) : Number(req.body?.startSeconds || 0) || 0;
    const subtitleUrl = await resolveSubtitleUrlForStart(req, filePath);
    const mode = req.body?.mode || req.body?.backend || process.env.CAST_BACKEND_DEFAULT || 'auto';
    const autoTranscode = String(req.body?.autoTranscode || 'auto').toLowerCase();

    prepared = await prepareMediaForCast({
      req,
      cfg: CFG,
      filePath,
      target: providerName,
      mode,
      autoTranscode,
      startSeconds: seconds,
      hlsJobs,
      generateStreamToken,
      getMimeType,
      sshExec,
      createVlcJob: (input) => createVlcCastJob(input),
      logger: mediaLog,
    });

    mediaLog(`cast start provider=${providerName} device=${selected.device_id} file=${path.basename(filePath)} pipeline=${prepared.pipelineMode} playback=${prepared.playbackMode}`);
    const result = await provider.play({
      deviceId: selected.device_id,
      filePath,
      streamUrl: prepared.streamUrl,
      title: prepared.title,
      mimeType: prepared.mimeType,
      mediaKind: prepared.mediaKind,
      startSeconds: prepared.startSeconds,
      subtitlesUrl: subtitleUrl,
      preparedMedia: prepared,
    });

    const session = castSessions.create({
      provider: providerName,
      deviceId: selected.device_id,
      deviceName: selected.name || stripProviderPrefix(providerName, selected.device_id),
      filePath,
      title: prepared.title,
      mediaKind: prepared.mediaKind,
      preparedMedia: summarizePrepared(prepared),
      streamUrl: prepared.streamUrl,
      jobId: prepared.jobId,
      pipelineMode: prepared.pipelineMode,
      backend: prepared.backend,
      startSeconds: prepared.startSeconds,
      duration: prepared.duration,
      state: 'playing',
    });

    activeCastSession.filePath = filePath;
    activeCastSession.resolvedPath = null;
    activeCastSession.streamUrl = prepared.streamUrl;
    activeCastSession.backend = prepared.backend;
    activeCastSession.liveJobId = null;
    activeCastSession.vlcJobId = prepared.backend === 'vlc' ? prepared.jobId : null;
    activeCastSession.playbackMode = prepared.playbackMode;
    activeCastSession.analysisSummary = prepared.analysis;
    activeCastSession.title = prepared.title;
    activeCastSession.duration = prepared.duration || 0;
    activeCastSession.type = prepared.mediaKind;
    activeCastSession.startedAt = new Date().toISOString();
    activeCastSession.startSeconds = prepared.startSeconds;
    activeCastSession.lastKnownTime = prepared.startSeconds;
    activeCastSession.lastCommandAt = Date.now();
    activeCastSession.state = 'playing';

    return res.json({
      success: true,
      provider: providerName,
      deviceId: selected.device_id,
      session,
      backend: prepared.backend,
      pipelineMode: prepared.pipelineMode,
      playbackMode: prepared.playbackMode,
      jobId: prepared.jobId || null,
      live: prepared.backend === 'hls' || prepared.backend === 'vlc',
      streamUrl: prepared.streamUrl,
      preparedMedia: summarizePrepared(prepared),
      receiver: result,
    });
  } catch (err) {
    await cleanupPreparedMedia(prepared, 'start-failed');
    const status = err.status || (err.code === 'LOCALHOST_RECEIVER_URL' ? 409 : 500);
    return res.status(status).json({ success: false, error: err.message, reasons: err.reasons || undefined });
  }
}

app.get('/api/cast/devices', async (req, res) => {
  try {
    const provider = String(req.query.provider || 'all').toLowerCase();
    let persisted = listCastDevices(provider === 'all' ? null : normalizeProviderName(provider), { includeCredentials: false });
    persisted = persisted.filter((d) => !(d.provider === 'chromecast' && /^\d+\.\d+\.\d+\.\d+/.test(String(d.name || '')) && !/^\d+\.\d+\.\d+\.\d+/.test(String(d.host || ''))));
    if (!persisted.length && (provider === 'all' || normalizeProviderName(provider) === 'chromecast')) {
      const id = makeProviderDeviceId('chromecast', CFG.chromecastName);
      upsertCastDevice({ id, provider: 'chromecast', name: CFG.chromecastName, selected: true });
      setSelectedCastDevice('chromecast', id);
      persisted = listCastDevices(provider === 'all' ? null : normalizeProviderName(provider), { includeCredentials: false });
    }
    res.json({ success: true, devices: persisted });
  } catch (err) { res.status(500).json({ success: false, error: err.message }); }
});

app.post('/api/cast/devices/scan', async (req, res) => {
  const provider = String(req.body?.provider || req.query.provider || 'all').toLowerCase();
  const result = await discoverCastDevices(provider);
  res.json({ success: result.errors.length === 0, ...result });
});

app.post('/api/cast/devices/select', async (req, res) => {
  try {
    const providerName = normalizeProviderName(req.body?.provider);
    const deviceId = req.body?.deviceId || req.body?.id || (providerName === 'chromecast' ? makeProviderDeviceId('chromecast', req.body?.name) : '');
    const result = await getProvider(providerName).selectDevice(deviceId);
    res.json(result);
  } catch (err) { res.status(500).json({ success: false, error: err.message }); }
});

app.post('/api/cast/airplay/pair/start', async (req, res) => {
  try { res.json(await airplayProvider.pairStart(req.body || {})); }
  catch (err) { res.status(err.status || 500).json({ success: false, error: err.message, detail: err.details }); }
});

app.post('/api/cast/airplay/pair/finish', async (req, res) => {
  try { res.json(await airplayProvider.pairFinish(req.body || {})); }
  catch (err) { res.status(err.status || 500).json({ success: false, error: err.message, detail: err.details }); }
});

app.post('/api/cast/start', startProviderCast);

async function getReceiverStatus() {
  const backend = process.env.AIRPLAY_RECEIVER_BACKEND || 'uxplay';
  const service = process.env.AIRPLAY_RECEIVER_SERVICE || (backend === 'shairport-sync' ? 'shairport-sync' : 'uxplay-receiver');
  const receiverName = process.env.AIRPLAY_RECEIVER_NAME || 'Cast Manager';
  const [installed, active, avahi, uxplayPath, shairportPath, ip, displays, audioSinks] = await Promise.all([
    sshExec(`systemctl list-unit-files ${escCmd(`${service}.service`)} --no-legend 2>/dev/null | awk '{print $1}' || true`, 5000).catch((e) => ({ stdout: '', error: e.message })),
    sshExec(`systemctl is-active ${escCmd(service)} 2>/dev/null || true`, 5000).catch((e) => ({ stdout: 'unknown', error: e.message })),
    sshExec(`systemctl is-active avahi-daemon 2>/dev/null || true`, 5000).catch((e) => ({ stdout: 'unknown', error: e.message })),
    sshExec('command -v uxplay 2>/dev/null || true', 5000).catch(() => ({ stdout: '' })),
    sshExec('command -v shairport-sync 2>/dev/null || true', 5000).catch(() => ({ stdout: '' })),
    sshExec(`hostname -I 2>/dev/null | awk '{print $1}' || true`, 5000).catch(() => ({ stdout: '' })),
    sshExec(`DISPLAY=${escCmd(process.env.AIRPLAY_RECEIVER_DISPLAY || ':0')} XAUTHORITY=${escCmd(process.env.AIRPLAY_RECEIVER_XAUTHORITY || '/run/user/1000/gdm/Xauthority')} xrandr --query 2>/dev/null | awk '/ connected/{print}' || true`, 5000).catch(() => ({ stdout: '' })),
    sshExec(`XDG_RUNTIME_DIR=${escCmd(process.env.AIRPLAY_RECEIVER_RUNTIME_DIR || '/run/user/1000')} pactl list short sinks 2>/dev/null || true`, 5000).catch(() => ({ stdout: '' })),
  ]);
  const isInstalled = String(installed.stdout || '').includes(`${service}.service`) || (backend === 'uxplay' ? !!uxplayPath.stdout : !!shairportPath.stdout);
  const status = String(active.stdout || '').trim() || 'unknown';
  const displayOutputs = String(displays.stdout || '').split('\n').map((line) => line.trim()).filter(Boolean);
  const connectedDisplays = displayOutputs.filter((line) => /^\S+\s+connected\b/.test(line));
  const sinkRows = String(audioSinks.stdout || '').split('\n').map((line) => line.trim()).filter(Boolean);
  const audioSinkNames = sinkRows.map((line) => line.split(/\s+/)[1]).filter(Boolean);
  const usableAudioSinks = audioSinkNames.filter((name) => name && name !== 'auto_null');
  const warnings = [];
  if (backend === 'uxplay' && isInstalled && status === 'active' && connectedDisplays.length === 0) {
    warnings.push('UxPlay is active, but Linux does not detect a connected HDMI/DP display. AirPlay mirroring can connect without appearing on the TV.');
  }
  if (backend === 'uxplay' && isInstalled && status === 'active' && usableAudioSinks.length === 0) {
    warnings.push('No real PulseAudio sink is available. AirPlay music/audio will connect but cannot play through TV speakers until HDMI/audio output is detected.');
  }
  return {
    success: true,
    enabled: String(process.env.AIRPLAY_RECEIVER_ENABLED || '1') !== '0',
    receiverName,
    backend,
    service,
    installed: isInstalled,
    running: status === 'active',
    status: isInstalled ? status : 'not-installed',
    localIp: String(ip.stdout || '').trim(),
    avahi: String(avahi.stdout || '').trim() || 'unknown',
    uxplayPath: String(uxplayPath.stdout || '').trim() || null,
    shairportSyncPath: String(shairportPath.stdout || '').trim() || null,
    displayConnected: connectedDisplays.length > 0,
    displayOutputs,
    audioSinkAvailable: usableAudioSinks.length > 0,
    audioSinks: audioSinkNames,
    warnings,
  };
}

async function receiverServiceAction(action) {
  const status = await getReceiverStatus();
  if (!status.installed) {
    const err = new Error(`${status.service} is not installed or no service file exists`);
    err.status = 409;
    throw err;
  }
  const verb = action === 'restart' ? 'restart' : action === 'stop' ? 'stop' : 'start';
  const result = await sshExec(`sudo -n systemctl ${verb} ${escCmd(status.service)} 2>&1`, 15000);
  if (result.code !== 0) {
    const err = new Error(result.stderr || result.stdout || `systemctl ${verb} failed`);
    err.status = 500;
    throw err;
  }
  return getReceiverStatus();
}

app.get('/api/receiver/status', async (req, res) => {
  try { res.json(await getReceiverStatus()); }
  catch (err) { res.status(500).json({ success: false, error: err.message }); }
});

for (const action of ['start', 'stop', 'restart']) {
  app.post(`/api/receiver/${action}`, async (req, res) => {
    try { res.json(await receiverServiceAction(action)); }
    catch (err) { res.status(err.status || 500).json({ success: false, error: err.message }); }
  });
}

app.get('/api/receiver/logs', async (req, res) => {
  try {
    const status = await getReceiverStatus();
    const lines = Math.max(20, Math.min(500, parseInt(req.query.lines, 10) || 120));
    const { stdout } = await sshExec(`journalctl -u ${escCmd(status.service)} -n ${lines} --no-pager 2>/dev/null || true`, 10000);
    res.type('text/plain').send(stdout || '');
  } catch (err) { res.status(500).json({ success: false, error: err.message }); }
});

app.get('/api/cast/jobs/:jobId', async (req, res) => {
  const info = await hlsJobs.getJobInfo(req.params.jobId);
  if (!info) return res.status(404).json({ success: false, error: 'Unknown cast job' });
  return res.json({ success: true, job: info });
});

app.get('/api/cast/jobs/:jobId/logs', async (req, res) => {
  try {
    const logs = await hlsJobs.readJobLog(req.params.jobId, parseInt(req.query.lines, 10) || 200);
    res.type('text/plain').send(logs);
  } catch (err) { res.status(err.status || 500).json({ success: false, error: err.message }); }
});

app.delete('/api/cast/jobs/:jobId', async (req, res) => {
  const ok = await hlsJobs.cancelJob(req.params.jobId, 'api-delete');
  res.json({ success: ok });
});

app.get('/api/cast/jobs/:jobId/master.m3u8', (req, res) => hlsJobs.servePlaylist(req, res, req.params.jobId));
app.get('/api/cast/jobs/:jobId/segment/:segmentName', (req, res) => hlsJobs.serveSegment(res, req.params.jobId, req.params.segmentName));

// Legacy Chromecast/CATT routes are kept for old UI paths.
app.get('/api/devices', async (req, res) => {
  try {
    const { devices, errors } = await discoverCastDevices('chromecast');
    res.json({ devices: devices.map((d) => ({ name: d.name, ip: d.host, id: d.id, provider: d.provider })), errors });
  } catch (err) {
    res.json({ devices: [{ name: CFG.chromecastName, ip: '', id: makeProviderDeviceId('chromecast', CFG.chromecastName), provider: 'chromecast' }], errors: [{ provider: 'chromecast', error: err.message }] });
  }
});

app.post('/api/devices/select', async (req, res) => {
  try {
    const { name, deviceId } = req.body;
    const id = deviceId || makeProviderDeviceId('chromecast', name);
    const result = await chromecastProvider.selectDevice(id);
    res.json({ success: true, device: result.name, deviceId: result.deviceId, provider: 'chromecast' });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

function usesAppClockForSession(session) {
  const prepared = session && session.preparedMedia;
  return !!(
    session
    && (
      session.backend === 'hls'
      || session.backend === 'vlc'
      || String(session.pipelineMode || '').startsWith('hls')
      || prepared?.receiverSeek === false
    )
  );
}

function estimateSessionTime(session, state = session?.state) {
  if (!session) return 0;
  const base = Math.max(0, Number(session.lastKnownTime ?? session.startSeconds ?? 0) || 0);
  const duration = Math.max(0, Number(session.duration || 0) || 0);
  if (String(state || '').toLowerCase() !== 'playing') return base;
  const lastKnownAt = Number(session.lastKnownAt || 0);
  const elapsed = lastKnownAt > 0 ? Math.max(0, (Date.now() - lastKnownAt) / 1000) : 0;
  const estimated = base + elapsed;
  return duration > 0 ? Math.min(duration, estimated) : estimated;
}

app.get('/api/cast/status', async (req, res) => {
  const session = castSessions.get();
  if (!session) return res.json(await getNormalizedCastStatus());
  const provider = getProvider(session.provider);
  const receiver = await provider.status(session.deviceId);
  const state = receiver.state && receiver.state !== 'unknown' ? receiver.state : session.state;
  const appClock = usesAppClockForSession(session);
  const appCurrentTime = estimateSessionTime(session, state);
  const receiverDuration = Number(receiver.duration || 0);
  const sessionDuration = Number(session.duration || 0);
  const usableReceiverDuration = Number.isFinite(receiverDuration) && receiverDuration > 0
    && receiverDuration < 12 * 60 * 60
    && (!sessionDuration || receiverDuration <= sessionDuration * 1.5);
  const receiverTime = Number(receiver.currentTime || 0);
  const usableReceiverTime = Number.isFinite(receiverTime) && receiverTime >= 0
    && !appClock
    && (!sessionDuration || receiverTime <= sessionDuration + 30);
  if (receiver.success !== false && receiver.receiverReachable !== false) {
    castSessions.update({
      state,
      lastKnownTime: usableReceiverTime && receiverTime > 0 ? receiverTime : appCurrentTime,
      duration: usableReceiverDuration ? receiverDuration : session.duration,
      statusSource: 'receiver',
    });
  }
  const updated = castSessions.get();
  const currentTime = appClock
    ? estimateSessionTime(updated, updated.state || state)
    : (usableReceiverTime ? receiverTime : Number(updated.lastKnownTime || 0));
  res.json({
    success: receiver.success !== false,
    provider: session.provider,
    deviceId: session.deviceId,
    deviceName: session.deviceName,
    activeSession: true,
    fallbackAvailable: true,
    receiverReachable: receiver.receiverReachable !== false,
    state: updated.state || state,
    currentTime,
    duration: usableReceiverDuration ? receiverDuration : Number(updated.duration || 0),
    title: receiver.title || updated.title,
    volumeLevel: receiver.volumeLevel ?? 100,
    session: updated,
    receiverError: receiver.error,
  });
});

function parseTimeToSeconds(timeStr) {
  if (!timeStr) return 0;
  const parts = timeStr.split(':').map(Number);
  if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
  if (parts.length === 2) return parts[0] * 60 + parts[1];
  return parts[0] || 0;
}

function normalizeCastBackend(value) {
  const raw = String(value || 'auto').toLowerCase().trim();
  if (!raw || raw === 'auto') return 'auto';
  if (['ffmpeg', 'ffmpeg-live', 'live'].includes(raw)) return 'ffmpeg';
  if (['vlc', 'vlc-renderer', 'vlc-http', 'vlc-backend', 'renderer'].includes(raw)) return 'vlc';
  if (['hls', 'http-live-streaming'].includes(raw)) return 'hls';
  return 'auto';
}

function shouldFallbackFromVlcToFfmpeg() {
  const raw = String(process.env.CAST_VLC_FALLBACK_TO_FFMPEG ?? '1').toLowerCase();
  return !(raw === '0' || raw === 'false' || raw === 'no' || raw === 'off');
}

app.post('/api/cast', async (req, res) => {
  return startProviderCast(req, res);
  try {
    let { filePath, seekTo } = req.body;
    filePath = await assertRemotePathInsideRoot(filePath);
    ensureNotProtectedFile(filePath, 'cast');
    const autoTranscode = String(req.body?.autoTranscode || 'auto').toLowerCase();
    const backendReq = String(
      req.body?.backend
      || process.env.CAST_BACKEND_DEFAULT
      || 'auto'
    ).toLowerCase();
    const backend = normalizeCastBackend(backendReq);
    const fileStat = await getRemoteStat(filePath);

    const analysis = await analyzeMediaCompatibility(filePath, 'chromecast', { autoTranscode }, { sshExec });
    const playbackMode = analysis.playbackMode;
    let castPath = filePath;

    const planKey = buildMediaCacheKey({
      filePath,
      stat: fileStat,
      target: 'chromecast',
      playbackMode,
      ffmpegPlan: analysis.ffmpegPlan,
      streamSelection: { v: analysis.videoStreamIndex, a: analysis.audioStreamIndex },
    });

    if (playbackMode === 'unsupported') {
      return res.status(415).json({ error: 'Unsupported media for Chromecast', reasons: analysis.reasons || [] });
    }

    const vlcEnabled = String(process.env.CAST_ENABLE_VLC_BACKEND || '0').toLowerCase();
    const hlsEnabled = String(process.env.CAST_ENABLE_HLS_BACKEND || '0').toLowerCase();
    const vlcFeatureOn = (vlcEnabled === '1' || vlcEnabled === 'true' || vlcEnabled === 'yes' || vlcEnabled === 'on');
    const hlsFeatureOn = (hlsEnabled === '1' || hlsEnabled === 'true' || hlsEnabled === 'yes' || hlsEnabled === 'on');

    function wouldPreferVlcAuto() {
      // Conservative heuristic: only prefer VLC when we are already in full-transcode + UHD HEVC
      // (the known-problematic class: 4K HEVC + HDR signaling + DTS-like audio).
      const isUhd = Number(analysis.videoWidth || 0) >= 3840 || Number(analysis.videoHeight || 0) >= 2160;
      const isHevc = String(analysis.videoCodec || '').toLowerCase() === 'hevc';
      const a = String(analysis.audioCodec || '').toLowerCase();
      const isDtsish = a.includes('dts') || a.includes('truehd');
      return playbackMode === 'full-transcode' && isUhd && isHevc && isDtsish;
    }

    let chosenBackend = backend;
    if (chosenBackend === 'auto') {
      if (vlcFeatureOn && wouldPreferVlcAuto()) chosenBackend = 'vlc';
      else chosenBackend = 'ffmpeg';
    }

    if (chosenBackend === 'hls') {
      if (!hlsFeatureOn) {
        mediaLog('cast backend=hls requested but disabled (CAST_ENABLE_HLS_BACKEND=0)');
        return res.status(409).json({ success: false, error: 'HLS backend is disabled (set CAST_ENABLE_HLS_BACKEND=1)' });
      }
      mediaLog('cast backend=hls requested but not implemented');
      return res.status(501).json({ success: false, error: 'HLS backend not implemented yet (prototype pending)' });
    }

    if (chosenBackend === 'vlc') {
      if (!vlcFeatureOn) {
        mediaLog('cast backend=vlc requested but disabled (CAST_ENABLE_VLC_BACKEND=0)');
        if (!shouldFallbackFromVlcToFfmpeg()) {
          return res.status(409).json({ success: false, error: 'VLC backend is disabled (set CAST_ENABLE_VLC_BACKEND=1)' });
        }
        chosenBackend = 'ffmpeg';
      }

      if (chosenBackend === 'vlc') {
        const seconds = seekTo ? parseTimeToSeconds(String(seekTo)) : 0;
        let job;
        try {
          job = await createVlcCastJob({
            req,
            filePath,
            startSeconds: seconds,
            title: path.basename(filePath),
          });

          // Cast VLC HTTP stream URL. Receiver should treat as a normal HTTP media URL.
          await castMediaAtPosition({
            streamUrl: job.streamUrl,
            filePath,
            seconds,
            receiverSeek: false,
            title: path.basename(filePath),
            type: 'video',
            backend: 'vlc',
          });

          activeCastSession.backend = 'vlc';
          activeCastSession.vlcJobId = job.jobId;
          activeCastSession.filePath = filePath;
          activeCastSession.resolvedPath = null;
          activeCastSession.streamUrl = job.streamUrl;
          activeCastSession.liveJobId = null;
          activeCastSession.playbackMode = playbackMode;
          activeCastSession.analysisSummary = makeAnalysisSummary(analysis);
          activeCastSession.startSeconds = seconds;
          activeCastSession.lastKnownTime = seconds;

          return res.json({
            success: true,
            backend: 'vlc',
            live: true,
            jobId: job.jobId,
            streamUrl: job.streamUrl,
            playbackMode,
            analysis: makeAnalysisSummary(analysis),
          });
        } catch (e) {
          const msg = String(e?.message || e || 'VLC backend failed');
          if (job?.jobId) await cancelVlcCastJob(job.jobId, 'fallback-to-ffmpeg');
          if (!shouldFallbackFromVlcToFfmpeg()) {
            const status = msg.toLowerCase().includes('vlc backend unavailable') || msg.toLowerCase().includes('cvlc') ? 409 : 500;
            return res.status(status).json({ success: false, backend: 'vlc', error: msg });
          }
          mediaLog(`vlc backend failed; falling back to ffmpeg: ${msg}`);
          chosenBackend = 'ffmpeg';
        }
      }
    }

    // Prefer VLC-style instant casting. Completed disk transcode (if any) wins over re-encoding the source.
    if (playbackMode !== 'direct') {
      const outDir = '/tmp/cast_transcodes_v2';
      const outPath = `${outDir}/${planKey}.mp4`;
      const tempPath = `${outPath}.part`;
      const { stdout: checkExist } = await sshExec(`test -f "${outPath}" && echo exists`);

      if (checkExist.includes('exists')) {
        castPath = outPath;
      } else {
        const allowLive = (playbackMode === 'remux' || playbackMode === 'audio-transcode')
          || (playbackMode === 'full-transcode' && liveFullTranscodeFeasible(analysis));

        if (allowLive) {
          pruneLiveJobs();
          const jobId = stableHash(JSON.stringify({ v: 2, planKey, t: Date.now(), n: Math.random() }));
          const cacheDir = '/tmp/cast_transcodes_v2';
          const cachePath = `${cacheDir}/${planKey}.mp4`;
          await sshExec(`mkdir -p "${cacheDir}"`);

          let liveEncode = null;
          if (playbackMode === 'full-transcode') {
            const choice = await resolveLiveFullTranscodeEncoder();
            const caps = await probeFfmpegCapabilities();
            liveEncode = choice.encoder === 'h264_nvenc'
              ? { encoder: 'h264_nvenc', cudaFirst: !!caps.cudaHwaccel }
              : { encoder: 'libx264' };
            const vcodec = analysis.videoCodec || 'unknown';
            if (choice.encoder === 'libx264') {
              const fallback = choice.reason !== 'CAST_LIVE_TRANSCODE_ENCODER=libx264';
              mediaLog(`chromecast live-full-transcode: encoder=libx264${fallback ? ' fallback' : ''}; reason=${choice.reason}`);
            } else {
              mediaLog(`chromecast live-full-transcode: encoder=h264_nvenc; ${vcodec} -> h264; audio -> aac`);
            }
          }

          liveCastJobs.set(jobId, {
            filePath,
            playbackMode,
            analysis,
            createdAt: Date.now(),
            startSeconds: 0,
            cacheDir,
            cachePath,
            liveEncode,
          });

          const seconds = seekTo ? parseTimeToSeconds(String(seekTo)) : 0;
          const liveUrl = buildLiveStreamUrl(req, jobId);

          mediaLog(`chromecast live-${playbackMode}: ${analysis.container} -> fmp4; v:${analysis.videoCodec || 'n/a'} a:${analysis.audioCodec || 'n/a'}`);

          if (seconds > 0) {
            const seekJobId = stableHash(`${jobId}:ss:${seconds}:${Date.now()}`);
            liveCastJobs.set(seekJobId, {
              filePath,
              playbackMode,
              analysis,
              createdAt: Date.now(),
              startSeconds: seconds,
              cacheDir,
              cachePath: null,
              liveEncode,
            });
            const seekUrl = buildLiveStreamUrl(req, seekJobId);
            await castMediaAtPosition({
              streamUrl: seekUrl,
              filePath,
              seconds,
              receiverSeek: false,
              title: path.basename(filePath),
              type: 'video',
            backend: 'ffmpeg',
            });
            activeCastSession.filePath = filePath;
            activeCastSession.resolvedPath = null;
            activeCastSession.streamUrl = seekUrl;
            activeCastSession.liveJobId = seekJobId;
            activeCastSession.playbackMode = playbackMode;
            activeCastSession.analysisSummary = makeAnalysisSummary(analysis);
            activeCastSession.liveEncode = liveEncode;
          activeCastSession.backend = 'ffmpeg';
          activeCastSession.vlcJobId = null;
            return res.json({ success: true, live: true, jobId: seekJobId, playbackMode, streamUrl: seekUrl, analysis: makeAnalysisSummary(analysis) });
          }

          await castMediaAtPosition({
            streamUrl: liveUrl,
            filePath,
            seconds: 0,
            receiverSeek: false,
            title: path.basename(filePath),
            type: 'video',
          backend: 'ffmpeg',
          });
          activeCastSession.filePath = filePath;
          activeCastSession.resolvedPath = null;
          activeCastSession.streamUrl = liveUrl;
          activeCastSession.liveJobId = jobId;
          activeCastSession.playbackMode = playbackMode;
          activeCastSession.analysisSummary = makeAnalysisSummary(analysis);
          activeCastSession.liveEncode = liveEncode;
        activeCastSession.backend = 'ffmpeg';
        activeCastSession.vlcJobId = null;

          return res.json({ success: true, live: true, jobId, playbackMode, streamUrl: liveUrl, analysis: makeAnalysisSummary(analysis) });
        }

        // Legacy disk pre-transcode — opt-in only for full-transcode; remux/audio-transcode always use live above.
        const allowPretranscode =
          req.body?.allowPretranscode === true || req.body?.allowPrecode === true;
        if (playbackMode === 'full-transcode' && !allowPretranscode) {
          mediaLog('chromecast pretranscode-blocked: disk encode requires allowPretranscode=true (live full-transcode unavailable for this media)');
          return res.status(409).json({
            success: false,
            error: 'Live full-transcode is unavailable (CAST_LIVE_FULLTRANSCODE=0, resolution over CAST_LIVE_FULLTRANSCODE_MAX_PIXELS, or unknown video dimensions). Chromecast will not start a multi-hour disk transcode unless you pass allowPretranscode: true.',
            playbackMode,
            videoCodec: analysis.videoCodec,
            videoWidth: analysis.videoWidth,
            videoHeight: analysis.videoHeight,
            duration: analysis.duration,
            fileSize: fileStat?.size,
            reasons: analysis.reasons || [],
          });
        }

        mediaLog(`chromecast pretranscode-explicit: allowPretranscode=true; starting background disk ffmpeg -> ${outPath}`);
        await sshExec(`mkdir -p "${outDir}"`);
        const mapVideo = analysis.videoStreamIndex != null ? `-map 0:${analysis.videoStreamIndex}` : '';
        const mapAudio = analysis.audioStreamIndex != null ? `-map 0:${analysis.audioStreamIndex}` : '';

        let codecs = '';
        let friendly = '';
        let audioTiming = '';
        if (playbackMode === 'remux') {
          codecs = '-c:v copy -c:a copy';
          friendly = 'Remuxing (selecting compatible streams, no re-encode)';
          mediaLog(`chromecast remux: ${analysis.container} -> mp4; v:${analysis.videoCodec} a:${analysis.audioCodec} (streams v:${analysis.videoStreamIndex} a:${analysis.audioStreamIndex})`);
        } else if (playbackMode === 'audio-transcode') {
          codecs = '-c:v copy -c:a aac -ac 2 -b:a 256k';
          audioTiming = analysis.audioStreamIndex != null ? '-af aresample=async=1:first_pts=0' : '';
          friendly = `Transcoding ${String(analysis.audioCodec || '').toUpperCase()} audio to AAC stereo (copying video)`;
          mediaLog(`chromecast audio-transcode: audio ${analysis.audioCodec} -> AAC; copy video ${analysis.videoCodec}`);
        } else if (playbackMode === 'full-transcode') {
          codecs = '-c:v libx264 -preset veryfast -crf 22 -c:a aac -ac 2 -b:a 256k';
          audioTiming = analysis.audioStreamIndex != null ? '-af aresample=async=1:first_pts=0' : '';
          friendly = 'Transcoding video to H.264 and audio to AAC';
          mediaLog(`chromecast full-transcode: video ${analysis.videoCodec} -> H264; audio -> AAC`);
        }

        const script = `#!/bin/bash\nffmpeg -y -fflags +genpts -i "${filePath}" ${mapVideo} ${mapAudio} ${codecs} ${audioTiming} -movflags +faststart -avoid_negative_ts make_zero -max_interleave_delta 0 -muxdelay 0 -muxpreload 0 -f mp4 "${tempPath}" >>/tmp/ffmpeg_transcode.log 2>&1 && mv "${tempPath}" "${outPath}"\n`;
        const b64 = Buffer.from(script).toString('base64');
        await sshExec(`echo '${b64}' | base64 -d > /tmp/cast_transcode_v2.sh && chmod +x /tmp/cast_transcode_v2.sh`);
        await sshExec(`nohup setsid /tmp/cast_transcode_v2.sh </dev/null >/dev/null 2>&1 &`, 5000);

        return res.json({
          success: true,
          transcoding: true,
          jobId: planKey,
          playbackMode,
          audioCodec: analysis.audioCodec || '',
          videoCodec: analysis.videoCodec || '',
          message: `${friendly}. This may take a long time for large files.`,
          transcodedPath: outPath,
        });
      }
    }

    // Cast the file (this is the canonical "start playback from" flow)
    const seconds = seekTo ? parseTimeToSeconds(String(seekTo)) : 0;
    await castMediaAtPosition({
      filePath: castPath,
      seconds,
      title: path.basename(filePath),
      type: 'video',
      backend: 'ffmpeg',
    });
    // Preserve original path in session for UI/queue tracking
    activeCastSession.filePath = filePath;
    activeCastSession.resolvedPath = castPath;
    activeCastSession.backend = 'ffmpeg';
    activeCastSession.vlcJobId = null;
    res.json({ success: true, transcoded: playbackMode !== 'direct', playbackMode, audioCodec: analysis.audioCodec || '' });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/cast/controls', async (req, res) => {
  try {
    const { action, value } = req.body;
    const providerSession = castSessions.get();
    if (providerSession) {
      const provider = getProvider(providerSession.provider);
      if (action === 'stop') {
        await provider.stop(providerSession.deviceId).catch((err) => mediaLog(`provider stop failed: ${err.message}`));
        await cleanupPreparedMedia(providerSession.preparedMedia, 'stopped');
        castSessions.clear();
        clearActiveCastSession();
        return res.json({ success: true, state: 'idle', provider: providerSession.provider });
      }
      if (action === 'pause') {
        const result = await provider.pause(providerSession.deviceId);
        castSessions.update({ state: 'paused' });
        activeCastSession.state = 'paused';
        return res.json({ success: true, state: 'paused', provider: providerSession.provider, receiver: result });
      }
      if (action === 'play' || action === 'resume') {
        const result = await provider.resume(providerSession.deviceId);
        castSessions.update({ state: 'playing' });
        activeCastSession.state = 'playing';
        return res.json({ success: true, state: 'playing', provider: providerSession.provider, receiver: result });
      }
      if (action === 'seek') {
        const targetSeconds = normalizeSeekTarget(value);
        if (targetSeconds === null) return res.status(400).json({ error: 'invalid seek seconds' });
        let fallbackUsed = false;
        if (providerSession.backend === 'hls' || providerSession.backend === 'vlc') {
          const oldPrepared = providerSession.preparedMedia;
          const prepared = await prepareMediaForCast({
            req,
            cfg: CFG,
            filePath: providerSession.filePath,
            target: providerSession.provider,
            mode: providerSession.backend === 'vlc' ? 'vlc' : 'hls',
            autoTranscode: 'auto',
            startSeconds: targetSeconds,
            hlsJobs,
            generateStreamToken,
            getMimeType,
            sshExec,
            createVlcJob: (input) => createVlcCastJob(input),
            logger: mediaLog,
          });
          await provider.play({
            deviceId: providerSession.deviceId,
            filePath: providerSession.filePath,
            streamUrl: prepared.streamUrl,
            title: prepared.title,
            mimeType: prepared.mimeType,
            mediaKind: prepared.mediaKind,
            startSeconds: targetSeconds,
            preparedMedia: prepared,
          });
          await cleanupPreparedMedia(oldPrepared, 'seek-restart');
          castSessions.update({
            preparedMedia: summarizePrepared(prepared),
            streamUrl: prepared.streamUrl,
            jobId: prepared.jobId,
            pipelineMode: prepared.pipelineMode,
            backend: prepared.backend,
            startSeconds: targetSeconds,
            lastKnownTime: targetSeconds,
            state: 'playing',
          });
          activeCastSession.streamUrl = prepared.streamUrl;
          activeCastSession.backend = prepared.backend;
          activeCastSession.vlcJobId = prepared.backend === 'vlc' ? prepared.jobId : null;
          activeCastSession.lastKnownTime = targetSeconds;
          activeCastSession.startSeconds = targetSeconds;
          activeCastSession.state = 'playing';
          fallbackUsed = true;
        } else {
          await provider.seek(providerSession.deviceId, targetSeconds);
          castSessions.update({ lastKnownTime: targetSeconds, state: 'playing' });
          activeCastSession.lastKnownTime = targetSeconds;
          activeCastSession.state = 'playing';
        }
        return res.json({ success: true, state: 'playing', currentTime: targetSeconds, fallbackUsed, provider: providerSession.provider });
      }
      if (action === 'volume') {
        const result = await provider.volume(providerSession.deviceId, value);
        return res.json({ success: result.success !== false, provider: providerSession.provider, ...result });
      }
    }
    let result;
    switch (action) {
      case 'play': {
        activeCastSession.lastCommandAt = Date.now();
        // Resume robustness:
        // - if paused: play
        // - if idle but we have an active session: restart at last known time / start seconds
        let st = null;
        try {
          const { stdout, stderr } = await cattCmd('status', 8000);
          const out = stdout || stderr || '';
          const stateMatch = out.match(/State:\s*(\w+)/i);
          st = stateMatch ? stateMatch[1].toLowerCase() : null;
          if (!st && out.includes('Nothing is currently playing')) st = 'idle';
        } catch (_) { /* ignore */ }

        if (st === 'idle' && (activeCastSession.filePath || activeCastSession.streamUrl)) {
          const resumeAt = Math.max(
            0,
            Math.floor(activeCastSession.lastKnownTime || activeCastSession.startSeconds || 0)
          );
          await restartActiveCastAt(resumeAt);
          return res.json({ success: true, state: 'playing', currentTime: resumeAt, fallbackUsed: true });
        }

        result = await cattCmdWithRetry('play', { attempts: 3, timeout: 12000, retryDelayMs: 250 });
        activeCastSession.state = 'playing';
        return res.json({ success: true, state: 'playing', fallbackUsed: false, output: result?.stdout });
      }
      case 'pause':
        activeCastSession.lastCommandAt = Date.now();
        result = await cattCmdWithRetry('pause', { attempts: 3, timeout: 12000, retryDelayMs: 250 });
        activeCastSession.state = 'paused';
        return res.json({ success: true, state: 'paused', fallbackUsed: false, output: result?.stdout });
      case 'stop':
        activeCastSession.lastCommandAt = Date.now();
        // Invalidate live job before Chromecast/session teardown so /api/cast/live/:id returns 410 immediately.
        const liveJobToCancel = activeCastSession.liveJobId;
        if (liveJobToCancel) markLiveJobCancelled(liveJobToCancel, 'stopped');
        if (activeCastSession.backend === 'vlc' && activeCastSession.vlcJobId) {
          await cancelVlcCastJob(activeCastSession.vlcJobId, 'stopped');
        }
        result = await cattCmdWithRetry('stop', { attempts: 2, timeout: 15000, retryDelayMs: 300 });
        clearActiveCastSession();
        return res.json({ success: true, state: 'idle', fallbackUsed: false, output: result?.stdout });
      case 'seek': {
        activeCastSession.lastCommandAt = Date.now();
        const targetSeconds = normalizeSeekTarget(value);
        if (targetSeconds === null) return res.status(400).json({ error: 'invalid seek seconds' });

        // Optimistic session update (intent)
        activeCastSession.lastKnownTime = targetSeconds;

        let fallbackUsed = false;
        let verified = false;

        if (activeCastSession.backend === 'vlc') {
          if (!activeCastSession.filePath) return res.status(409).json({ error: 'No active VLC session to restart' });
          const oldJobId = activeCastSession.vlcJobId;
          if (oldJobId) await cancelVlcCastJob(oldJobId, 'seek-restart');
          const job = await createVlcCastJob({
            req,
            filePath: activeCastSession.filePath,
            startSeconds: targetSeconds,
            title: activeCastSession.title || path.basename(activeCastSession.filePath),
          });
          await castMediaAtPosition({
            streamUrl: job.streamUrl,
            filePath: activeCastSession.filePath,
            seconds: targetSeconds,
            receiverSeek: false,
            title: activeCastSession.title || path.basename(activeCastSession.filePath),
            type: activeCastSession.type || 'video',
            backend: 'vlc',
          });
          activeCastSession.backend = 'vlc';
          activeCastSession.vlcJobId = job.jobId;
          activeCastSession.streamUrl = job.streamUrl;
          activeCastSession.liveJobId = null;
          activeCastSession.resolvedPath = null;
          activeCastSession.lastKnownTime = targetSeconds;
          activeCastSession.state = 'playing';
          return res.json({ success: true, state: 'playing', currentTime: targetSeconds, fallbackUsed: true, verified: true, backend: 'vlc' });
        }

        try {
          await cattCmdWithRetry(`seek ${targetSeconds}`, { attempts: 2, timeout: 15000, retryDelayMs: 300 });
          // Reliability preference: play after seek to avoid post-seek idle.
          await cattCmdWithRetry('play', { attempts: 2, timeout: 12000, retryDelayMs: 250 });

          const v = await verifySeekSettled(targetSeconds);
          verified = !!v.ok;
          if (!v.ok) {
            if (activeCastSession.filePath || activeCastSession.streamUrl) {
              await restartActiveCastAt(targetSeconds);
              fallbackUsed = true;
              verified = true;
            } else {
              return res.status(409).json({ error: 'Seek did not settle and no active session to restart' });
            }
          }
        } catch (e) {
          if (activeCastSession.filePath || activeCastSession.streamUrl) {
            await restartActiveCastAt(targetSeconds);
            fallbackUsed = true;
            verified = true;
          } else {
            throw e;
          }
        }

        activeCastSession.lastKnownTime = targetSeconds;
        activeCastSession.state = 'playing';
        return res.json({
          success: true,
          state: 'playing',
          currentTime: targetSeconds,
          fallbackUsed,
          verified,
        });
      }
      case 'volume': {
        const vol = Math.max(0, Math.min(100, parseInt(value, 10)));
        if (Number.isNaN(vol)) return res.status(400).json({ error: 'invalid volume value' });
        result = await cattCmdWithRetry(`volume ${vol}`, { attempts: 2, timeout: 12000, retryDelayMs: 250 });
        activeCastSession.lastCommandAt = Date.now();
        return res.json({ success: true, volumeLevel: vol, fallbackUsed: false, output: result?.stdout });
      }
      case 'skip':
        result = await cattCmdWithRetry('skip', { attempts: 2, timeout: 12000, retryDelayMs: 250 });
        activeCastSession.lastCommandAt = Date.now();
        return res.json({ success: true, fallbackUsed: false, output: result?.stdout });
      default: return res.status(400).json({ error: 'Unknown action' });
    }
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
    // Track as active session so play/resume/seek fallback can restart stream URLs too
    activeCastSession.streamUrl = url;
    activeCastSession.filePath = null;
    activeCastSession.resolvedPath = null;
    activeCastSession.subtitlePath = null;
    activeCastSession.title = url;
    activeCastSession.type = 'stream';
    activeCastSession.startedAt = new Date().toISOString();
    activeCastSession.startSeconds = 0;
    activeCastSession.lastKnownTime = 0;
    activeCastSession.lastCommandAt = Date.now();
    activeCastSession.state = 'playing';
    activeCastSession.backend = 'url';
    res.json({ success: true });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── AUDIO METADATA ─────────────────────────────────────────
app.post('/api/audio/metadata', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.body.filePath);
    ensureNotProtectedFile(filePath, 'read');
    const { stdout } = await sshExec(
      `ffprobe -v error -show_entries format_tags=artist,album,title,genre,track -show_entries format=duration -of json ${escCmd(filePath)}`
    );
    res.json(JSON.parse(stdout));
  } catch (err) {
    fileManagerError(res, err, 'Could not read audio metadata');
  }
});

// ─── SUBTITLES ──────────────────────────────────────────────
app.post('/api/subtitles', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.body.filePath);
    ensureNotProtectedFile(filePath, 'read');
    const dir = path.dirname(filePath);
    const base = path.basename(filePath, path.extname(filePath));
    const { stdout: sidecarsOut } = await sshExec(
      `find ${escCmd(dir)} -maxdepth 1 -name ${escCmd(`${base}*`)} \\( -name "*.srt" -o -name "*.ass" -o -name "*.vtt" -o -name "*.sub" \\) 2>/dev/null`
    );
    const sidecars = String(sidecarsOut || '').split('\n').map(s => s.trim()).filter(Boolean);

    // Embedded subtitle streams.
    const analysis = await analyzeMediaCompatibility(filePath, 'chromecast', { autoTranscode: 'auto' }, { sshExec });
    const embedded = (analysis.subtitleStreams || []).map((s) => ({
      index: s.index,
      codec: s.codec,
      language: s.language,
    }));

    pruneSubtitleIndex();
    const now = Date.now();

    const items = [];
    for (const p of sidecars) {
      const id = stableHash(JSON.stringify({ v: 1, kind: 'sidecar', filePath, sidecarPath: p }));
      const label = `Sidecar: ${path.basename(p)}`;
      subtitleIndex.set(id, { filePath, kind: 'sidecar', sidecarPath: p, label, createdAt: now });
      items.push({ id, kind: 'sidecar', label, sourcePath: p });
    }
    for (const s of embedded) {
      const id = stableHash(JSON.stringify({ v: 1, kind: 'embedded', filePath, streamIndex: s.index }));
      const parts = [];
      if (s.language) parts.push(String(s.language).toUpperCase());
      if (s.codec) parts.push(String(s.codec));
      const meta = parts.length ? ` (${parts.join(', ')})` : '';
      const label = `Embedded: stream ${s.index}${meta}`;
      subtitleIndex.set(id, { filePath, kind: 'embedded', streamIndex: s.index, label, createdAt: now });
      items.push({ id, kind: 'embedded', label, streamIndex: s.index });
    }

    res.json({ subtitles: items });
  } catch (err) {
    res.json({ subtitles: [] });
  }
});

app.get('/api/subtitles/:id.vtt', async (req, res) => {
  try {
    const id = String(req.params.id || '').trim();
    if (!id) return res.status(404).end();
    pruneSubtitleIndex();
    const item = subtitleIndex.get(id);
    if (!item) return res.status(404).json({ success: false, error: 'Unknown subtitle id' });

    const vttPath = await ensureSubtitleVttRemote({
      filePath: item.filePath,
      subtitleId: id,
      kind: item.kind,
      sidecarPath: item.sidecarPath,
      streamIndex: item.streamIndex,
    });

    res.setHeader('Content-Type', 'text/vtt; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');

    // Stream from the remote host (avoid buffering large subtitle files).
    const conn = new Client();
    conn.on('ready', () => {
      conn.exec(`cat ${escCmd(vttPath)}`, (err, stream) => {
        if (err) {
          try { conn.end(); } catch (_) {}
          return res.status(500).json({ success: false, error: 'Failed to read subtitle' });
        }
        stream.on('close', () => { try { conn.end(); } catch (_) {} });
        stream.pipe(res);
      });
    });
    conn.on('error', () => { if (!res.headersSent) res.status(500).end(); });
    conn.connect(getSshConfig());
  } catch (err) {
    if (!res.headersSent) res.status(500).json({ success: false, error: err.message });
  }
});

app.post('/api/subtitles/prepare', async (req, res) => {
  try {
    const { filePath, subtitlePath } = req.body;
    const safeFilePath = await assertRemotePathInsideRoot(filePath);
    ensureNotProtectedFile(safeFilePath, 'read');
    const subtitleId = await registerCustomSubtitlePath(safeFilePath, subtitlePath);
    const subtitleUrl = buildSubtitleUrl(req, subtitleId);
    res.json({ success: true, subtitleId, subtitleUrl });
  } catch (err) {
    fileManagerError(res, err, 'Could not prepare subtitle');
  }
});

app.post('/api/cast/subtitles', async (req, res) => {
  try {
    const { filePath, subtitlePath, subtitleId, seekTo } = req.body;
    const safeFilePath = await assertRemotePathInsideRoot(filePath);
    ensureNotProtectedFile(safeFilePath, 'cast');
    const seconds = seekTo ? parseTimeToSeconds(String(seekTo)) : 0;
    let resolvedSubtitle = '';
    if (subtitlePath) {
      const customSubtitleId = await registerCustomSubtitlePath(safeFilePath, subtitlePath);
      resolvedSubtitle = buildSubtitleUrl(req, customSubtitleId);
    } else if (subtitleId) {
      pruneSubtitleIndex();
      const indexedSubtitle = subtitleIndex.get(String(subtitleId || '').trim());
      if (!indexedSubtitle) throw makeFileManagerError('NOT_FOUND', 'Subtitle selection expired. Reopen the cast dialog and choose it again.');
      if (indexedSubtitle.filePath !== safeFilePath) throw makeFileManagerError('INVALID_PATH', 'Subtitle does not belong to the selected video');
      resolvedSubtitle = buildSubtitleUrl(req, subtitleId);
    }
    if (!resolvedSubtitle) throw makeFileManagerError('INVALID_PATH', 'Subtitle selection is required');
    await castMediaAtPosition({
      filePath: safeFilePath,
      seconds,
      title: path.basename(safeFilePath),
      type: 'video',
      subtitlePath: resolvedSubtitle,
    });
    res.json({ success: true });
  } catch (err) {
    fileManagerError(res, err, 'Could not cast with subtitles');
  }
});

// ─── DISK SPACE ─────────────────────────────────────────────
app.get('/api/disk', async (req, res) => {
  try {
    const { stdout } = await sshExec(`df -h ${escCmd(CFG.fileManagerRoot)} | tail -1`);
    const parts = stdout.split(/\s+/);
    res.json({ size: parts[1], used: parts[2], available: parts[3], usePercent: parts[4] });
  } catch (err) {
    res.status(500).json({ error: err.message });
  }
});

// ─── TRANSCODE STATUS ───────────────────────────────────────
app.post('/api/transcode/status', async (req, res) => {
  try {
    const { filePath, jobId } = req.body || {};
    const jid = String(jobId || '').trim();

    if (jid) {
      const transcodedPath = `/tmp/cast_transcodes_v2/${jid}.mp4`;
      const tempPath = transcodedPath + '.part';
      const { stdout } = await sshExec(`test -f "${transcodedPath}" && echo exists`);
      const ready = stdout.includes('exists');
      const { stdout: tmpCheck } = await sshExec(`test -f "${tempPath}" && stat -c%s "${tempPath}" 2>/dev/null || echo 0`);
      const tmpSize = parseInt(tmpCheck) || 0;
      const { stdout: procs } = await sshExec(`pgrep -af "ffmpeg.*${jid}\\.mp4" 2>/dev/null | head -1`);
      const processing = !!String(procs || '').trim();
      return res.json({ ready, processing: processing || tmpSize > 0, transcodedPath, tmpSize, jobId: jid });
    }

    // Legacy behavior (v1): keep for backward compatibility
    const baseName = path.basename(String(filePath || '')).replace(/\.[^.]+$/, '_aac.mkv');
    const transcodedPath = `/tmp/cast_transcodes/${baseName}`;
    const tempPath = transcodedPath + '.part';
    const { stdout } = await sshExec(`test -f "${transcodedPath}" && echo exists`);
    const ready = stdout.includes('exists');
    const escapedName = path.basename(String(filePath || '')).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const { stdout: procs } = await sshExec(`pgrep -af "ffmpeg.*${escapedName}" 2>/dev/null | head -1`);
    const processing = !!procs.trim();
    const { stdout: tmpCheck } = await sshExec(`test -f "${tempPath}" && stat -c%s "${tempPath}" 2>/dev/null || echo 0`);
    const tmpSize = parseInt(tmpCheck) || 0;
    res.json({ ready, processing: processing || tmpSize > 0, transcodedPath, tmpSize });
  } catch (err) {
    res.json({ ready: false, processing: false });
  }
});

// ─── MEDIA ANALYZE (diagnostic) ─────────────────────────────
app.post('/api/media/analyze', async (req, res) => {
  try {
    const { target, autoTranscode } = req.body || {};
    const filePath = await assertRemotePathInsideRoot(req.body?.filePath);
    ensureNotProtectedFile(filePath, 'analyzed');
    const tgt = String(target || 'chromecast').toLowerCase();
    const at = String(autoTranscode || 'auto').toLowerCase();
    const analysis = await analyzeMediaCompatibility(filePath, tgt, { autoTranscode: at }, { sshExec });
    const requestedMode = req.body?.mode || req.body?.backend || 'auto';
    const { choosePipelineMode } = require('./lib/media/pipeline');
    const pipelineMode = choosePipelineMode({ analysis, target: tgt === 'airplay' ? 'airplay' : 'chromecast', requestedMode });
    res.json({ success: true, analysis: { ...analysis, recommendedPipeline: pipelineMode }, pipelineMode });
  } catch (err) { fileManagerError(res, err, 'Could not analyze media'); }
});

// ─── MOUNT v4 ROUTES ────────────────────────────────────────
const sshConfig = getSshConfig();

// Token-based stream API
app.use('/api/stream', streamRouter({
  canAccessPath: async (filePath) => {
    try {
      const safePath = await assertRemotePathInsideRoot(filePath);
      ensureNotProtectedFile(safePath, 'streamed');
      return true;
    } catch (_) {
      return false;
    }
  },
}));

// Public stream endpoint (no auth — token IS auth)
app.get('/stream/:token/:filename', createPublicStreamHandler(sshConfig, {
  canAccessPath: async (filePath) => {
    try {
      const safePath = await assertRemotePathInsideRoot(filePath);
      ensureNotProtectedFile(safePath, 'streamed');
      return true;
    } catch (_) {
      return false;
    }
  },
}));

// ─── MEDIA INFO ENDPOINT ────────────────────────────────────
app.get('/api/media/info', async (req, res) => {
  try {
    const filePath = await assertRemotePathInsideRoot(req.query.path);
    ensureNotProtectedFile(filePath, 'read');
    const { stdout } = await sshExec(
      `ffprobe -v quiet -print_format json -show_format -show_streams ${escCmd(filePath)} 2>/dev/null`
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
  } catch (err) { fileManagerError(res, err, 'Could not read media info'); }
});

function normalizeStarRequestKind(type, filePath = '') {
  const value = String(type || '').toLowerCase();
  if (['folder', 'video', 'audio', 'subtitle', 'other'].includes(value)) return value;
  return inferKindFromPath(filePath, value === 'folder');
}

async function enrichStarredItem(item) {
  const filePath = item.file_path;
  let exists = false;
  let isDir = item.item_type === 'folder' || item.kind === 'folder';
  try {
    const { stdout } = await sshExec(`if test -d ${escCmd(filePath)}; then echo folder; elif test -e ${escCmd(filePath)}; then echo file; else echo missing; fi`, 5000);
    const kind = String(stdout || '').trim();
    exists = kind !== 'missing';
    isDir = kind === 'folder';
  } catch (_) {
    exists = item.exists === 1;
  }
  const inferredKind = isDir ? 'folder' : inferKindFromPath(filePath, false);
  const name = item.name && item.name !== filePath ? item.name : path.basename(filePath);
  const parentPath = item.parent_path || parentPathOf(filePath);
  updateStarredMetadata(filePath, {
    kind: inferredKind,
    name,
    parentPath,
    exists,
    pinnedToSidebar: inferredKind === 'folder' ? Number(item.pinned_to_sidebar ?? 1) === 1 : 0,
  });
  return normalizeStarredRow({
    ...item,
    kind: inferredKind,
    item_type: inferredKind === 'folder' ? 'folder' : 'file',
    name,
    parent_path: parentPath,
    exists: exists ? 1 : 0,
    pinned_to_sidebar: inferredKind === 'folder' ? Number(item.pinned_to_sidebar ?? 1) : 0,
  });
}

// ─── STARRED ENDPOINTS ──────────────────────────────────────
app.post('/api/files/star', async (req, res) => {
  try {
    const rawPath = req.body?.path || req.body?.filePath;
    if (!rawPath) return res.status(400).json({ error: 'path required' });
    const filePath = normalizeFileManagerPath(rawPath);
    if (!isPathSafe(filePath)) return res.status(403).json({ error: 'Cannot star this path' });
    const requested = req.body?.kind || req.body?.type || req.body?.itemType;
    const remote = await sshExec(`if test -d ${escCmd(filePath)}; then echo folder; elif test -e ${escCmd(filePath)}; then echo file; else echo missing; fi`, 5000).catch(() => ({ stdout: '' }));
    const kind = String(remote.stdout || '').trim() === 'folder' ? 'folder' : normalizeStarRequestKind(requested, filePath);
    const exists = String(remote.stdout || '').trim() !== 'missing';
    starFile(filePath, kind, req.body?.name || path.basename(filePath), {
      kind,
      parentPath: req.body?.parentPath || parentPathOf(filePath),
      pinnedToSidebar: kind === 'folder' ? req.body?.pinned_to_sidebar !== false : false,
      exists,
    });
    logActivity('star', filePath, { kind });
    res.json({ success: true, starred: true, itemType: kind === 'folder' ? 'folder' : 'file', kind });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.delete('/api/files/star', (req, res) => {
  try {
    const rawPath = req.body?.path || req.body?.filePath;
    if (!rawPath) return res.status(400).json({ error: 'path required' });
    const filePath = normalizeFileManagerPath(rawPath);
    if (!isPathSafe(filePath)) return res.status(403).json({ error: 'Cannot unstar this path' });
    const result = unstarFile(filePath);
    logActivity('unstar', filePath);
    res.json({ success: true, starred: false, removed: result.changes > 0 });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/files/starred', async (req, res) => {
  try {
    const type = req.query.type ? String(req.query.type).toLowerCase() : null;
    const rows = getStarred(type === 'folder' ? 'folder' : null);
    const enriched = [];
    for (const item of rows) enriched.push(await enrichStarredItem(item));
    const filtered = type && type !== 'all'
      ? enriched.filter((item) => (type === 'folder' ? item.kind === 'folder' : item.kind === type))
      : enriched;
    res.json({ files: filtered });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.get('/api/files/starred-folders', async (req, res) => {
  try {
    const rows = getStarred('folder');
    const folders = [];
    for (const item of rows) {
      const enriched = await enrichStarredItem(item);
      if (enriched.kind === 'folder' && enriched.pinned_to_sidebar) folders.push(enriched);
    }
    res.json({ files: folders });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

app.patch('/api/files/starred/sidebar', (req, res) => {
  try {
    const rawPath = req.body?.path || req.body?.filePath;
    if (!rawPath) return res.status(400).json({ error: 'path required' });
    const filePath = normalizeFileManagerPath(rawPath);
    updateStarredMetadata(filePath, { pinnedToSidebar: req.body?.pinned !== false });
    res.json({ success: true });
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
    if (!isPathSafe(item.original_path) || !isTrashPathSafe(item.trash_path)) {
      return res.status(403).json({ error: 'Cannot restore this trash item' });
    }
    // Restore: move back to original location
    const parentDir = path.dirname(item.original_path);
    await sshExec(`mkdir -p ${escCmd(parentDir)}`);
    await sshExec(`mv ${escCmd(item.trash_path)} ${escCmd(item.original_path)}`);
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
      if (!isTrashPathSafe(item.trash_path)) continue;
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
    if (!isTrashPathSafe(item.trash_path)) return res.status(403).json({ error: 'Cannot delete this trash item' });
    const sudoPrefix = sudoPwd ? `echo ${escCmd(sudoPwd)} | sudo -S ` : '';
    await sshExec(`${sudoPrefix}rm -rf ${escCmd(item.trash_path)}`);
    removeFromTrash(item.id);
    logActivity('delete_permanent', item.original_path);
    res.json({ success: true });
  } catch (err) { res.status(500).json({ error: err.message }); }
});

// ─── SHARE ENDPOINTS ────────────────────────────────────────
app.post('/api/share', async (req, res) => {
  try {
    const { permissions, password, expiresIn } = req.body;
    const filePath = await assertRemotePathInsideRoot(req.body.path);
    ensureNotProtectedFile(filePath, 'shared');
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
  } catch (err) { fileManagerError(res, err, 'Could not create share'); }
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
    if (!isPathSafe(share.file_path) || isProtectedPath(CFG.fileManagerRoot, share.file_path)) {
      return res.status(403).send(`<!DOCTYPE html><html><head><meta charset="UTF-8"><title>Forbidden</title></head>
        <body style="font-family:system-ui;display:flex;align-items:center;justify-content:center;height:100vh;background:#0d1117;color:#e6edf3">
        <div style="text-align:center"><h1>Link Unavailable</h1><p>This file is not available through Cast Manager.</p></div></body></html>`);
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
    const showHidden = String(req.query.showHidden || '').toLowerCase() === 'true' || req.query.showHidden === '1';
    if (!q || !q.trim()) return res.json({ results: [] });
    // First try the indexed search
    let results = showHidden ? [] : searchFiles(q.trim()).filter((r) => {
      const p = r.path || '';
      return isPathSafe(p) && !isProtectedPath(CFG.fileManagerRoot, p) && !hasHiddenSegment(CFG.fileManagerRoot, p);
    });
    // If no indexed results, do a live filesystem search
    if (results.length === 0) {
      const safeQuery = String(q).replace(/[\0]/g, '').slice(0, 120);
      const hiddenFilter = showHidden ? '' : " -not -name '.*'";
      const { stdout } = await sshExec(
        `timeout 8s find ${escCmd(CFG.fileManagerRoot)} -xdev -iname ${escCmd(`*${safeQuery}*`)}${hiddenFilter} -printf '%y\\t%s\\t%T@\\t%p\\n' 2>/dev/null | head -50`,
        15000
      );
      results = stdout.split('\n').filter(l => l.trim()).map((line) => {
        const [kind, size, mtime, ...pathParts] = line.split('\t');
        const p = pathParts.join('\t').trim();
        return {
          path: p,
          name: path.basename(p),
          extension: path.extname(p).toLowerCase(),
          size: parseInt(size, 10) || 0,
          mtime: parseFloat(mtime) || 0,
          is_directory: kind === 'd' ? 1 : 0,
          protected: isProtectedPath(CFG.fileManagerRoot, p),
        };
      }).filter((r) => isPathSafe(r.path) && !isProtectedPath(CFG.fileManagerRoot, r.path));
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
      `timeout 20s find ${escCmd(CFG.fileManagerRoot)} -xdev -not -name '.*' -printf '%y\\t%s\\t%T@\\t%p\\n' 2>/dev/null`,
      30000
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
    const root = CFG.fileManagerRoot;
    const { stdout: dfOut } = await sshExec(`df -B1 ${escCmd(root)} | tail -1`);
    const parts = dfOut.split(/\s+/);
    const totalSpace = parseInt(parts[1]) || 0;
    const usedSpace = parseInt(parts[2]) || 0;
    const freeSpace = parseInt(parts[3]) || 0;
    // Get breakdown by type
    const { stdout: breakdown } = await sshExec(`
      echo "video:$(timeout 8s find ${escCmd(root)} -xdev -type f \\( -name '*.mkv' -o -name '*.mp4' -o -name '*.avi' -o -name '*.mov' -o -name '*.webm' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "audio:$(timeout 8s find ${escCmd(root)} -xdev -type f \\( -name '*.mp3' -o -name '*.flac' -o -name '*.m4a' -o -name '*.aac' -o -name '*.ogg' -o -name '*.wav' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "images:$(timeout 8s find ${escCmd(root)} -xdev -type f \\( -name '*.jpg' -o -name '*.jpeg' -o -name '*.png' -o -name '*.gif' -o -name '*.webp' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "documents:$(timeout 8s find ${escCmd(root)} -xdev -type f \\( -name '*.pdf' -o -name '*.doc*' -o -name '*.txt' -o -name '*.md' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
      echo "archives:$(timeout 8s find ${escCmd(root)} -xdev -type f \\( -name '*.zip' -o -name '*.tar*' -o -name '*.rar' -o -name '*.7z' \\) -printf '%s\\n' 2>/dev/null | awk '{s+=$1}END{print s+0}')";
    `);
    const bd = {};
    for (const line of breakdown.split('\n')) {
      const [key, val] = line.split(':');
      if (key && val) bd[key.trim()] = parseInt(val.trim()) || 0;
    }
    bd.other = Math.max(0, usedSpace - Object.values(bd).reduce((a, b) => a + b, 0));
    // Largest files
    const { stdout: largest } = await sshExec(
      `timeout 10s find ${escCmd(root)} -xdev -type f -printf '%s\\t%p\\n' 2>/dev/null | sort -rn | head -10`,
      20000
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
    const dir = await assertRemotePathInsideRoot(req.query.path || CFG.fileManagerRoot);
    // du -d1 gives one-level deep directory sizes
    const usageScript = `
dir=${escCmd(dir)}
find "$dir" -mindepth 1 -maxdepth 1 -type d -print0 2>/dev/null | while IFS= read -r -d '' d; do
  size=$(timeout 8s du -x -sb "$d" 2>/dev/null | awk '{print $1}')
  printf '%s\\t%s\\n' "\${size:-0}" "$d"
done
timeout 8s du -x -sb "$dir" 2>/dev/null | tail -1
`;
    const { stdout } = await sshExec(
      `bash -lc ${escCmd(usageScript)}`,
      60000
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
      if (dir === '/' && VIRTUAL_USAGE_PATHS.has(dirPath.replace(/\/$/, ''))) {
        continue;
      }
      // show all directories including hidden ones
      dirs.push({ name, path: dirPath.replace(/\/$/, ''), size });
    }

    // Sort by size descending
    dirs.sort((a, b) => b.size - a.size);

    // Get item counts for each directory
    if (dirs.length > 0) {
      try {
        const countCmd = dirs.map(d => `printf '%s\\t' ${escCmd(d.path)}; find ${escCmd(d.path)} -mindepth 1 -maxdepth 1 -not -name '.*' 2>/dev/null | wc -l`).join('; ');
        const { stdout: countOut } = await sshExec(countCmd);
        const countMap = {};
        for (const line of countOut.split('\n')) {
          const ti = line.indexOf('\t');
          if (ti > 0) countMap[line.substring(0, ti)] = parseInt(line.substring(ti + 1), 10) || 0;
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
      parentPath: safeParentPath(CFG.fileManagerRoot, dir),
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
    const destDir = await assertRemotePathInsideRoot(req.body.path || CFG.fileManagerRoot);
    const results = [];
    for (const file of req.files) {
      let remotePath = '';
      try {
        remotePath = joinChild(CFG.fileManagerRoot, destDir, file.originalname);
        ensureNotProtectedFile(remotePath, 'uploaded');
        const { code: existsCode } = await sshExec(`test ! -e ${escCmd(remotePath)}`);
        if (existsCode !== 0) throw new Error('A file or folder with that name already exists');
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
          conn.connect(getSshConfig());
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
    const safePaths = [];
    for (const p of paths) {
      const safePath = await assertRemotePathInsideRoot(p);
      ensureNotProtectedFile(safePath, 'downloaded');
      safePaths.push(safePath);
    }
    res.set('Content-Type', 'application/zip');
    res.set('Content-Disposition', `attachment; filename="cast_manager_download.zip"`);
    const archive = archiver('zip', { zlib: { level: 5 } });
    archive.pipe(res);
    for (const filePath of safePaths) {
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
        conn.connect(getSshConfig());
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
hlsJobs.startupCleanup().catch((err) => console.log('[media] HLS startup cleanup failed:', err.message));
setInterval(() => hlsJobs.cleanupExpired().catch(() => {}), 5 * 60 * 1000);

// Background index on startup (async, non-blocking)
(async () => {
  try {
    const { stdout } = await sshExec(
      `timeout 20s find ${escCmd(CFG.fileManagerRoot)} -xdev -not -name '.*' -printf '%y\\t%s\\t%T@\\t%p\\n' 2>/dev/null | head -5000`,
      30000
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
