'use strict';

const express = require('express');
const fs = require('fs');
const fsp = fs.promises;
const path = require('path');
const os = require('os');
const multer = require('multer');

const DEFAULT_LIBRARY = path.join(os.homedir(), 'file-manager', 'drive');
const SPECIAL_MUTATION_ROOTS = ['/proc', '/sys', '/dev', '/run'];
const PROTECTED_EXACT_PATHS = ['/', '/etc', '/usr', '/root', '/boot', '/bin', '/sbin', '/lib', '/lib64', '/home'];
const TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.markdown', '.json', '.py', '.js', '.jsx', '.ts', '.tsx', '.css', '.html', '.htm',
  '.xml', '.yml', '.yaml', '.csv', '.log', '.sh', '.env', '.ini', '.conf', '.toml', '.srt', '.vtt',
  '.ass', '.ssa', '.gitignore', '.dockerignore',
]);
const MIME_TYPES = {
  '.txt': 'text/plain', '.md': 'text/markdown', '.markdown': 'text/markdown', '.json': 'application/json',
  '.py': 'text/x-python', '.js': 'text/javascript', '.jsx': 'text/javascript', '.ts': 'text/plain',
  '.tsx': 'text/plain', '.css': 'text/css', '.html': 'text/html', '.htm': 'text/html', '.xml': 'text/xml',
  '.yml': 'text/yaml', '.yaml': 'text/yaml', '.csv': 'text/csv', '.log': 'text/plain', '.sh': 'text/x-shellscript',
  '.env': 'text/plain', '.ini': 'text/plain', '.conf': 'text/plain', '.toml': 'text/plain', '.srt': 'text/plain',
  '.vtt': 'text/vtt', '.ass': 'text/plain', '.ssa': 'text/plain', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg',
  '.png': 'image/png', '.gif': 'image/gif', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.bmp': 'image/bmp',
  '.pdf': 'application/pdf', '.mp3': 'audio/mpeg', '.flac': 'audio/flac', '.aac': 'audio/aac', '.ogg': 'audio/ogg',
  '.wav': 'audio/wav', '.m4a': 'audio/mp4', '.opus': 'audio/opus', '.mp4': 'video/mp4', '.mkv': 'video/x-matroska',
  '.webm': 'video/webm', '.mov': 'video/quicktime', '.m4v': 'video/mp4', '.avi': 'video/x-msvideo',
};

function driveError(code, message, status = 400) {
  const error = new Error(message);
  error.code = code;
  error.status = status;
  return error;
}

function rejectNull(value) {
  if (String(value ?? '').includes('\0')) throw driveError('INVALID_PATH', 'Paths and names cannot contain null bytes');
}

function resolveUserPath(rawPath, base, homeDir) {
  rejectNull(rawPath);
  let value = String(rawPath ?? '').trim();
  if (!value) value = base || homeDir;
  if (value === '~') value = homeDir;
  else if (value.startsWith('~/')) value = path.join(homeDir, value.slice(2));
  const resolved = path.resolve(path.isAbsolute(value) ? value : path.join(base || homeDir, value));
  return resolved;
}

function safeChildName(rawName) {
  rejectNull(rawName);
  const name = String(rawName ?? '').trim();
  if (!name) throw driveError('INVALID_NAME', 'Name cannot be empty');
  if (name === '.' || name === '..' || /[\\/]/.test(name)) {
    throw driveError('INVALID_NAME', 'Name cannot contain path separators or be . or ..');
  }
  return name.replace(/[\u0000-\u001f\u007f]/g, '_');
}

function mimeType(filePath) {
  return MIME_TYPES[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
}

function humanSize(bytes) {
  if (bytes === null || bytes === undefined) return '—';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB', 'PB'];
  let size = bytes / 1024;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
  return `${size >= 10 ? size.toFixed(1) : size.toFixed(2)} ${units[unit]}`;
}

function permissionsString(mode, kind) {
  let prefix = '-';
  if (kind === 'folder') prefix = 'd';
  else if (kind === 'symlink') prefix = 'l';
  else if (kind === 'special') {
    const fileType = mode & 0o170000;
    if (fileType === 0o020000) prefix = 'c';
    else if (fileType === 0o060000) prefix = 'b';
    else if (fileType === 0o010000) prefix = 'p';
    else if (fileType === 0o140000) prefix = 's';
  }
  const bits = [[0o400, 'r'], [0o200, 'w'], [0o100, 'x'], [0o040, 'r'], [0o020, 'w'], [0o010, 'x'], [0o004, 'r'], [0o002, 'w'], [0o001, 'x']];
  return prefix + bits.map(([bit, char]) => (mode & bit ? char : '-')).join('');
}

function loadIdentityMaps() {
  const users = new Map();
  const groups = new Map();
  try {
    for (const line of fs.readFileSync('/etc/passwd', 'utf8').split('\n')) {
      const fields = line.split(':');
      if (fields.length > 2) users.set(Number(fields[2]), fields[0]);
    }
  } catch (_) {}
  try {
    for (const line of fs.readFileSync('/etc/group', 'utf8').split('\n')) {
      const fields = line.split(':');
      if (fields.length > 2) groups.set(Number(fields[2]), fields[0]);
    }
  } catch (_) {}
  return { users, groups };
}

async function accessFlag(filePath, mode) {
  try { await fsp.access(filePath, mode); return true; } catch (_) { return false; }
}

function kindFromStat(stat) {
  if (stat.isSymbolicLink()) return 'symlink';
  if (stat.isDirectory()) return 'folder';
  if (stat.isFile()) return 'file';
  return 'special';
}

async function pathMetadata(filePath, identities) {
  let stat;
  try {
    stat = await fsp.lstat(filePath);
  } catch (error) {
    return {
      name: path.basename(filePath) || '/', path: filePath, type: error.code === 'EACCES' ? 'unreadable' : 'special',
      is_dir: false, is_file: false, is_symlink: false, is_hidden: path.basename(filePath).startsWith('.'),
      size: null, size_human: '—', modified: null, permissions: '??????????', owner: null, group: null,
      readable: false, writable: false, executable: false, mime: null, symlink_target: null,
      error: friendlyFsMessage(error),
    };
  }
  const kind = kindFromStat(stat);
  let symlinkTarget = null;
  let itemError = null;
  if (stat.isSymbolicLink()) {
    try { symlinkTarget = await fsp.readlink(filePath); } catch (error) { itemError = friendlyFsMessage(error); }
    try { await fsp.stat(filePath); } catch (error) { itemError = error.code === 'ENOENT' ? 'Broken symlink' : friendlyFsMessage(error); }
  }
  const [readable, writable, executable] = await Promise.all([
    accessFlag(filePath, fs.constants.R_OK), accessFlag(filePath, fs.constants.W_OK), accessFlag(filePath, fs.constants.X_OK),
  ]);
  const isFile = stat.isFile();
  const isDir = stat.isDirectory();
  return {
    name: path.basename(filePath) || '/', path: filePath, type: kind,
    is_dir: isDir, is_file: isFile, is_symlink: stat.isSymbolicLink(), is_hidden: path.basename(filePath).startsWith('.'),
    size: isFile ? stat.size : 0, size_human: isFile ? humanSize(stat.size) : '—',
    modified: stat.mtime.toISOString(), modifiedAt: stat.mtime.toISOString(), mtime: stat.mtimeMs,
    permissions: permissionsString(stat.mode, kind), owner: identities.users.get(stat.uid) || String(stat.uid),
    group: identities.groups.get(stat.gid) || String(stat.gid), readable, writable, executable,
    mime: isFile ? mimeType(filePath) : null, mimeType: isFile ? mimeType(filePath) : undefined,
    symlink_target: symlinkTarget, error: itemError,
    isDirectory: isDir, is_directory: isDir, isFile, isHidden: path.basename(filePath).startsWith('.'),
    extension: isFile ? path.extname(filePath).toLowerCase() : '',
  };
}

function friendlyFsMessage(error) {
  if (!error) return 'Filesystem operation failed';
  if (error.code === 'EACCES' || error.code === 'EPERM') return 'Permission denied by Linux permissions';
  if (error.code === 'ENOENT') return 'Path does not exist';
  if (error.code === 'ENOTDIR') return 'A path component is not a directory';
  if (error.code === 'EISDIR') return 'Path is a directory, not a file';
  if (error.code === 'EEXIST') return 'Destination already exists';
  if (error.code === 'ENOSPC') return 'No space left on device';
  if (error.code === 'EROFS') return 'Filesystem is read-only';
  return error.message || 'Filesystem operation failed';
}

function statusForFsError(error) {
  if (error.status) return error.status;
  if (error.code === 'ENOENT') return 404;
  if (error.code === 'EACCES' || error.code === 'EPERM') return 403;
  if (error.code === 'EEXIST') return 409;
  return 400;
}

function sendDriveError(res, error, fallback = 'Filesystem operation failed') {
  const message = error && friendlyFsMessage(error) || fallback;
  res.status(statusForFsError(error || {})).json({ ok: false, error: message, code: error?.code || 'FILESYSTEM_ERROR' });
}

async function assertExisting(filePath) {
  try { return await fsp.lstat(filePath); }
  catch (error) { throw error; }
}

async function assertDirectory(filePath, writable = false) {
  const stat = await fsp.stat(filePath);
  if (!stat.isDirectory()) throw driveError('NOT_A_DIRECTORY', 'Path is not a directory');
  await fsp.access(filePath, writable ? fs.constants.W_OK | fs.constants.X_OK : fs.constants.R_OK | fs.constants.X_OK);
  return stat;
}

async function assertRegularFile(filePath) {
  const stat = await fsp.stat(filePath);
  if (!stat.isFile()) throw driveError('NOT_A_FILE', 'Path is not a regular file');
  await fsp.access(filePath, fs.constants.R_OK);
  return stat;
}

function assertSafeMutationPath(filePath) {
  if (PROTECTED_EXACT_PATHS.includes(filePath)) {
    throw driveError('PROTECTED_PATH', `${filePath} cannot be changed from the file manager`, 403);
  }
  // Block bare user home directories like /home/REDACTED_USER
  if (/^\/home\/[^/]+$/.test(filePath)) throw driveError('PROTECTED_PATH', 'User home directories cannot be changed from the file manager', 403);
  // Block virtual/system mount trees recursively
  if (SPECIAL_MUTATION_ROOTS.some((root) => filePath === root || filePath.startsWith(`${root}/`))) {
    throw driveError('PROTECTED_PATH', `Changes under ${SPECIAL_MUTATION_ROOTS.find((root) => filePath === root || filePath.startsWith(`${root}/`))} are disabled`, 403);
  }
}

async function destinationPath(source, rawDestination, homeDir) {
  const destination = resolveUserPath(rawDestination, path.dirname(source), homeDir);
  try {
    const stat = await fsp.lstat(destination);
    if (stat.isDirectory()) {
      const nested = path.join(destination, path.basename(source));
      try { await fsp.lstat(nested); throw driveError('ALREADY_EXISTS', 'Destination already exists', 409); }
      catch (nestedError) { if (nestedError.code !== 'ENOENT') throw nestedError; }
      return nested;
    }
    throw driveError('ALREADY_EXISTS', 'Destination already exists', 409);
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  return destination;
}

function deduplicateFilenameSync(directory, filename) {
  const ext = path.extname(filename);
  const stem = path.basename(filename, ext);
  let candidate = filename;
  let counter = 1;
  while (fs.existsSync(path.join(directory, candidate))) {
    candidate = `${stem} (${counter})${ext}`;
    counter += 1;
  }
  return candidate;
}

function createDriveRouter(options = {}) {
  const router = express.Router();
  const libraryPath = path.resolve(options.libraryPath || process.env.FILE_MANAGER_LIBRARY || DEFAULT_LIBRARY);
  const homeDir = path.resolve(options.homeDir || os.homedir());
  const currentUser = options.currentUser || os.userInfo().username;
  const maxUploadMb = Number(process.env.FILE_MANAGER_MAX_UPLOAD_MB || 4096);
  const textPreviewMb = Number(process.env.FILE_MANAGER_TEXT_PREVIEW_MB || 2);
  const textPreviewBytes = textPreviewMb * 1024 * 1024;
  const identities = loadIdentityMaps();

  fs.mkdirSync(libraryPath, { recursive: true });
  console.log(`[Drive] Integrated server files enabled at ${libraryPath}`);

  const storage = multer.diskStorage({
    destination(req, file, callback) {
      try {
        const target = resolveUserPath(req.body.path, libraryPath, homeDir);
        assertSafeMutationPath(target);
        const stat = fs.statSync(target);
        if (!stat.isDirectory()) throw driveError('NOT_A_DIRECTORY', 'Upload target is not a directory');
        fs.accessSync(target, fs.constants.W_OK | fs.constants.X_OK);
        req.driveUploadTarget = target;
        callback(null, target);
      } catch (error) { callback(error); }
    },
    filename(req, file, callback) {
      try {
        const safeName = safeChildName(file.originalname);
        callback(null, deduplicateFilenameSync(req.driveUploadTarget, safeName));
      } catch (error) { callback(error); }
    },
  });
  const driveUpload = multer({ storage, limits: { fileSize: maxUploadMb * 1024 * 1024, files: 100 } });

  router.get('/api/files/config', (req, res) => {
    res.json({ ok: true, service: 'File Manager', feature: 'Drive', port: Number(process.env.PORT || 8004), library_path: libraryPath, current_user: currentUser, max_upload_mb: maxUploadMb, text_preview_mb: textPreviewMb });
  });

  router.get('/api/files/list', async (req, res) => {
    let directory;
    try {
      directory = resolveUserPath(req.query.path, libraryPath, homeDir);
      await assertDirectory(directory);
      const names = await fsp.readdir(directory);
      const entries = await Promise.all(names.map((name) => pathMetadata(path.join(directory, name), identities)));
      entries.sort((a, b) => Number(b.is_dir) - Number(a.is_dir) || a.name.localeCompare(b.name, undefined, { sensitivity: 'base' }));
      const metadata = await pathMetadata(directory, identities);
      res.json({ ok: true, path: directory, currentPath: directory, parent: directory === '/' ? null : path.dirname(directory), is_root: directory === '/', readable: metadata.readable, writable: metadata.writable, entries, files: entries, error: null });
    } catch (error) { sendDriveError(res, error, `Could not list ${directory || 'directory'}`); }
  });

  router.get('/api/files/stat', async (req, res) => {
    try {
      const filePath = resolveUserPath(req.query.path, libraryPath, homeDir);
      await assertExisting(filePath);
      res.json({ ok: true, entry: await pathMetadata(filePath, identities) });
    } catch (error) { sendDriveError(res, error); }
  });

  router.get('/api/files/preview', async (req, res) => {
    try {
      const filePath = resolveUserPath(req.query.path, libraryPath, homeDir);
      const stat = await assertRegularFile(filePath);
      const metadata = await pathMetadata(filePath, identities);
      const mime = mimeType(filePath);
      const ext = path.extname(filePath).toLowerCase();
      if (TEXT_EXTENSIONS.has(ext) || TEXT_EXTENSIONS.has(path.basename(filePath).toLowerCase()) || mime.startsWith('text/') || mime === 'application/json') {
        if (stat.size > textPreviewBytes) {
          return res.json({ ok: true, kind: 'too_large', metadata, message: 'File too large to preview. Download instead.' });
        }
        const content = await fsp.readFile(filePath, 'utf8');
        return res.json({ ok: true, kind: 'text', metadata, content });
      }
      if (mime.startsWith('image/')) return res.json({ ok: true, kind: 'image', metadata, preview_url: `/api/files/content?path=${encodeURIComponent(filePath)}` });
      if (mime === 'application/pdf') return res.json({ ok: true, kind: 'pdf', metadata, preview_url: `/api/files/content?path=${encodeURIComponent(filePath)}` });
      if (mime.startsWith('audio/')) return res.json({ ok: true, kind: 'audio', metadata, preview_url: `/api/files/content?path=${encodeURIComponent(filePath)}` });
      if (mime.startsWith('video/')) return res.json({ ok: true, kind: 'video', metadata, preview_url: `/api/files/content?path=${encodeURIComponent(filePath)}` });
      return res.json({ ok: true, kind: 'unsupported', metadata, message: 'This file type cannot be previewed safely. Download it instead.' });
    } catch (error) { sendDriveError(res, error, 'Preview failed'); }
  });

  router.get('/api/files/content', async (req, res) => {
    try {
      const filePath = resolveUserPath(req.query.path, libraryPath, homeDir);
      await assertRegularFile(filePath);
      res.type(mimeType(filePath));
      res.set('Content-Disposition', 'inline');
      res.sendFile(filePath, (error) => {
        if (error && !res.headersSent) sendDriveError(res, error, 'Could not stream file');
        else if (error && !res.writableEnded) res.end();
      });
    } catch (error) { sendDriveError(res, error, 'Could not stream file'); }
  });

  router.get('/api/files/download', async (req, res) => {
    try {
      const filePath = resolveUserPath(req.query.path, libraryPath, homeDir);
      await assertRegularFile(filePath);
      res.download(filePath, path.basename(filePath), (error) => {
        if (error && !res.headersSent) sendDriveError(res, error, 'Download failed');
        else if (error && !res.writableEnded) res.end();
      });
    } catch (error) { sendDriveError(res, error, 'Download failed'); }
  });

  router.post('/api/files/upload', (req, res) => {
    driveUpload.array('files', 100)(req, res, (error) => {
      if (error) return sendDriveError(res, error, 'Upload failed');
      if (!req.files?.length) return res.status(400).json({ ok: false, error: 'No files provided', code: 'NO_FILES' });
      const uploaded = req.files.map((file) => ({ original_name: file.originalname, saved_name: file.filename, path: file.path, size: file.size }));
      res.json({ ok: true, uploaded, errors: [], results: uploaded.map((file) => ({ name: file.saved_name, path: file.path, success: true })) });
    });
  });

  router.post('/api/files/mkdir', async (req, res, next) => {
    if (req.body.path === undefined) return next();
    try {
      const parent = resolveUserPath(req.body.path, libraryPath, homeDir);
      await assertDirectory(parent, true);
      assertSafeMutationPath(parent);
      const target = path.join(parent, safeChildName(req.body.name));
      await fsp.mkdir(target);
      res.json({ ok: true, path: target });
    } catch (error) { sendDriveError(res, error, 'Could not create folder'); }
  });

  router.post('/api/files/rename', async (req, res, next) => {
    if (req.body.path === undefined) return next();
    try {
      const source = resolveUserPath(req.body.path, libraryPath, homeDir);
      await assertExisting(source);
      assertSafeMutationPath(source);
      const destination = path.join(path.dirname(source), safeChildName(req.body.new_name || req.body.newName));
      try { await fsp.lstat(destination); throw driveError('ALREADY_EXISTS', 'Destination already exists', 409); } catch (error) { if (error.code !== 'ENOENT') throw error; }
      await fsp.rename(source, destination);
      res.json({ ok: true, path: destination, newPath: destination });
    } catch (error) { sendDriveError(res, error, 'Rename failed'); }
  });

  router.post('/api/files/copy', async (req, res, next) => {
    if (req.body.source === undefined) return next();
    try {
      const source = resolveUserPath(req.body.source, libraryPath, homeDir);
      await assertExisting(source);
      assertSafeMutationPath(source);
      const destination = await destinationPath(source, req.body.destination, homeDir);
      assertSafeMutationPath(destination);
      await fsp.access(path.dirname(destination), fs.constants.W_OK | fs.constants.X_OK);
      await fsp.cp(source, destination, { recursive: true, errorOnExist: true, force: false, preserveTimestamps: true, verbatimSymlinks: true });
      res.json({ ok: true, path: destination, newPath: destination });
    } catch (error) { sendDriveError(res, error, 'Copy failed'); }
  });

  router.post('/api/files/move', async (req, res, next) => {
    if (req.body.source === undefined) return next();
    try {
      if (req.body.confirm !== true) throw driveError('CONFIRMATION_REQUIRED', 'Move requires confirmation');
      const source = resolveUserPath(req.body.source, libraryPath, homeDir);
      await assertExisting(source);
      assertSafeMutationPath(source);
      const destination = await destinationPath(source, req.body.destination, homeDir);
      assertSafeMutationPath(destination);
      if (destination === source || destination.startsWith(`${source}${path.sep}`)) throw driveError('INVALID_PATH', 'A folder cannot be moved into itself');
      await fsp.access(path.dirname(destination), fs.constants.W_OK | fs.constants.X_OK);
      try { await fsp.rename(source, destination); }
      catch (error) {
        if (error.code !== 'EXDEV') throw error;
        await fsp.cp(source, destination, { recursive: true, errorOnExist: true, force: false, preserveTimestamps: true, verbatimSymlinks: true });
        const stat = await fsp.lstat(source);
        if (stat.isDirectory() && !stat.isSymbolicLink()) await fsp.rm(source, { recursive: true });
        else await fsp.unlink(source);
      }
      res.json({ ok: true, path: destination, newPath: destination });
    } catch (error) { sendDriveError(res, error, 'Move failed'); }
  });

  router.post('/api/files/delete', async (req, res, next) => {
    if (req.body.path === undefined) return next();
    try {
      if (req.body.confirm !== true) throw driveError('CONFIRMATION_REQUIRED', 'Delete requires confirmation');
      const target = resolveUserPath(req.body.path, libraryPath, homeDir);
      const stat = await assertExisting(target);
      assertSafeMutationPath(target);
      if (stat.isDirectory() && !stat.isSymbolicLink()) await fsp.rm(target, { recursive: true });
      else await fsp.unlink(target);
      res.json({ ok: true, deleted: target });
    } catch (error) { sendDriveError(res, error, 'Delete failed'); }
  });

  return router;
}

module.exports = { createDriveRouter, resolveUserPath, safeChildName, pathMetadata };
