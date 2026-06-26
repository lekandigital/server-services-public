export type FileKind = 'folder' | 'video' | 'audio' | 'image' | 'subtitle' | 'text' | 'pdf' | 'torrent' | 'other'

export interface FileEntry {
  name: string
  path: string
  extension?: string
  mimeType?: string
  size?: number
  mtime?: number
  isDirectory?: boolean
  is_directory?: number | boolean
  starred?: boolean
  protected?: boolean
  type?: string
  kind?: FileKind
}

export interface FileRoot {
  id: string
  label: string
  serverPath: string
  routePrefix: string
  available?: boolean
}

export interface MediaInfo {
  path?: string
  duration?: number
  size?: number
  container?: string
  videoCodec?: string
  audioCodec?: string
  width?: number
  height?: number
}

export interface MediaAnalysis {
  playbackMode?: string
  target?: string
  videoCodec?: string
  audioCodec?: string
  container?: string
  duration?: number
  reasons?: string[]
  subtitles?: Array<{ index?: number; codec?: string; language?: string }>
}

export type CastUiState =
  | 'idle'
  | 'analyzing'
  | 'preflighting'
  | 'starting'
  | 'waiting_for_receiver_request'
  | 'buffering'
  | 'playing'
  | 'paused'
  | 'seeking'
  | 'restarting_stream'
  | 'stopping'
  | 'ended'
  | 'error'

export interface CastSession {
  sessionId?: string
  filePath?: string
  title?: string
  backend?: string
  streamUrl?: string
  state?: string
  duration?: number
  diagnosticsUrl?: string
}

export interface CastStatus {
  success?: boolean
  provider?: string
  deviceId?: string
  deviceName?: string
  activeSession?: boolean
  state?: string
  currentTime?: number
  duration?: number
  title?: string
  volumeLevel?: number
  backend?: string
  seekInProgress?: boolean
  starting?: boolean
  restarting?: boolean
  lastCommandAt?: number
  session?: CastSession
  error?: string
}

export interface CastDevice {
  provider: string
  deviceId: string
  name: string
  host?: string
  selected?: boolean
}

export interface SubtitleItem {
  id?: string
  path?: string
  sourcePath?: string
  label?: string
  kind?: string
  format?: string
  language?: string
  codec?: string
  embedded?: boolean
  burnInRequired?: boolean
}

export interface TorrentItem {
  id: number
  name: string
  status: string
  progress: number
  downloadSpeed?: number
  uploadSpeed?: number
  eta?: number
  ratio?: number
  sizeWhenDone?: number
  downloadedEver?: number
}

export interface RecentEntry {
  file_path: string
  filename?: string
  file_type?: string
  action?: string
  accessed_at?: string
}

export interface ShareEntry { id: string; file_path: string; filename?: string; permissions?: string; expires_at?: string; access_count?: number }
export interface TrashEntry { id: number; original_path: string; trash_path?: string; filename?: string; file_type?: string; size?: number; deleted_at?: string; auto_delete_at?: string }
export interface ActivityEntry { id?: number; action?: string; file_path?: string; details?: string | Record<string, unknown>; created_at?: string }

export interface AppConfig {
  mediaRoot: string
  fileManagerRoot?: string
  fileRoots: FileRoot[]
  defaultRootId: string
  serverUrl: string
  features: {
    hls: boolean
    vlc: boolean
    castDoctor: boolean
    diagnostics: boolean
    cast?: boolean
    torrents?: boolean
    shares?: boolean
    trash?: boolean
    starred?: boolean
    newFolder?: boolean
  }
}

export interface ApiErrorDetails {
  message: string
  status?: number
  method?: string
  url?: string
  body?: unknown
  isRouteMismatch?: boolean
}

export interface ToastItem {
  id: string
  message: string
  type: 'info' | 'success' | 'warning' | 'error'
  priority?: 'low' | 'normal'
}

export interface DiagnosticEntry {
  id: string
  ts: number
  category: string
  message: string
  details?: unknown
}

export type NavSection =
  | 'dashboard'
  | 'drive'
  | 'library'
  | 'recent'
  | 'starred'
  | 'shared'
  | 'torrents'
  | 'queue'
  | 'playlists'
  | 'storage'
  | 'trash'
  | 'activity'
  | 'settings'
  | 'diagnostics'
