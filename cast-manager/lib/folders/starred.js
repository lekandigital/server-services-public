const path = require('path').posix;

const VIDEO_EXTS = new Set(['.mkv', '.mp4', '.avi', '.mov', '.webm', '.m4v', '.ts', '.flv', '.wmv']);
const AUDIO_EXTS = new Set(['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus', '.wma']);
const SUBTITLE_EXTS = new Set(['.srt', '.ass', '.vtt', '.sub']);

function inferKindFromPath(filePath, isDirectory = false) {
  if (isDirectory) return 'folder';
  const ext = path.extname(String(filePath || '')).toLowerCase();
  if (VIDEO_EXTS.has(ext)) return 'video';
  if (AUDIO_EXTS.has(ext)) return 'audio';
  if (SUBTITLE_EXTS.has(ext)) return 'subtitle';
  return 'other';
}

function parentPathOf(filePath) {
  const value = String(filePath || '');
  if (!value || value === '/') return '/';
  return path.dirname(value);
}

function normalizeStarredRow(row) {
  const filePath = row.file_path || row.path || '';
  const kind = row.kind || row.item_type || inferKindFromPath(filePath, row.item_type === 'folder');
  return {
    path: filePath,
    file_path: filePath,
    name: row.name || path.basename(filePath),
    type: kind,
    kind,
    item_type: kind === 'folder' ? 'folder' : 'file',
    file_type: kind,
    parentPath: row.parent_path || parentPathOf(filePath),
    parent_path: row.parent_path || parentPathOf(filePath),
    pinned_to_sidebar: kind === 'folder' ? Number(row.pinned_to_sidebar ?? 1) : Number(row.pinned_to_sidebar ?? 0),
    exists: row.exists == null ? null : !!row.exists,
    starred_at: row.starred_at,
  };
}

module.exports = {
  inferKindFromPath,
  normalizeStarredRow,
  parentPathOf,
};
