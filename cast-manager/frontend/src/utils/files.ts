import type { FileEntry, FileKind } from '../types'

const VIDEO_EXTS = new Set(['.mkv', '.mp4', '.mpeg', '.mpg', '.m4v', '.mov', '.avi', '.webm', '.ts'])
const AUDIO_EXTS = new Set(['.mp3', '.flac', '.m4a', '.aac', '.ogg', '.wav', '.opus'])
const IMAGE_EXTS = new Set(['.jpg', '.jpeg', '.png', '.webp', '.gif'])
const TEXT_EXTS = new Set(['.txt', '.nfo', '.log'])
const PDF_EXTS = new Set(['.pdf'])
const SUB_EXTS = new Set(['.srt', '.vtt', '.ass', '.ssa', '.sub', '.idx'])

export function getExtension(name: string): string {
  const i = name.lastIndexOf('.')
  return i >= 0 ? name.slice(i).toLowerCase() : ''
}

export function inferFileKind(entry: FileEntry): FileKind {
  if (entry.isDirectory || entry.is_directory || entry.type === 'folder' || entry.kind === 'folder') return 'folder'
  const ext = entry.extension?.toLowerCase() || getExtension(entry.name)
  if (VIDEO_EXTS.has(ext)) return 'video'
  if (AUDIO_EXTS.has(ext)) return 'audio'
  if (IMAGE_EXTS.has(ext)) return 'image'
  if (TEXT_EXTS.has(ext)) return 'text'
  if (SUB_EXTS.has(ext)) return 'subtitle'
  if (PDF_EXTS.has(ext)) return 'pdf'
  if (ext === '.torrent') return 'torrent'
  return 'other'
}

export function formatBytes(bytes = 0): string {
  if (!bytes) return '—'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0
  let n = bytes
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024
    i++
  }
  return `${n.toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

export function formatDuration(seconds = 0): string {
  if (!seconds || !Number.isFinite(seconds)) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = Math.floor(seconds % 60)
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

export function formatDate(ts?: number): string {
  if (!ts) return '—'
  const d = new Date(ts > 1e12 ? ts : ts * 1000)
  return d.toLocaleString()
}

export function isBrowserVideoCompatible(analysis?: { playbackMode?: string; videoCodec?: string }): boolean {
  if (!analysis) return false
  const mode = analysis.playbackMode || ''
  return mode === 'direct' || mode === 'remux'
}

export function kindLabel(kind: FileKind): string {
  const map: Record<FileKind, string> = {
    folder: 'Folder',
    video: 'Video',
    audio: 'Audio',
    image: 'Image',
    text: 'Text',
    pdf: 'PDF',
    subtitle: 'Subtitle',
    torrent: 'Torrent',
    other: 'File',
  }
  return map[kind]
}

export function kindIcon(kind: FileKind): string {
  const map: Record<FileKind, string> = {
    folder: 'DIR',
    video: 'VID',
    audio: 'AUD',
    image: 'IMG',
    text: 'TXT',
    pdf: 'PDF',
    subtitle: 'SUB',
    torrent: 'TOR',
    other: 'FILE',
  }
  return map[kind]
}

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
}
