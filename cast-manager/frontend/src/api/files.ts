import { apiRequest } from './client'
import type { AppConfig, FileEntry, FileRoot } from '../types'

const DEFAULT_MEDIA_ROOT = '/home/REDACTED_USER/file-manager/drive/watch_list'
const DEFAULT_ROOTS: FileRoot[] = [
  { id: 'drive', label: 'Drive', serverPath: '/home/REDACTED_USER/file-manager/drive', routePrefix: '/file-manager/drive' },
  { id: 'watch_list', label: 'Media Library', serverPath: '/home/REDACTED_USER/file-manager/drive/watch_list', routePrefix: '/file-manager/library' },
  { id: 'downloads', label: 'Downloads', serverPath: '/home/REDACTED_USER/downloads', routePrefix: '/file-manager/user/o/downloads' },
  { id: 'Downloads', label: 'Downloads (capital D)', serverPath: '/home/REDACTED_USER/Downloads', routePrefix: '/file-manager/user/o/Downloads' },
]

export async function fetchConfig(): Promise<AppConfig> {
  try {
    return await apiRequest<AppConfig>('/api/config')
  } catch {
    return {
      mediaRoot: DEFAULT_MEDIA_ROOT,
      fileRoots: DEFAULT_ROOTS,
      defaultRootId: 'watch_list',
      serverUrl: window.location.origin,
      features: { hls: false, vlc: true, castDoctor: true, diagnostics: true, cast: true, shares: true, starred: true, trash: true, torrents: true, newFolder: true },
    }
  }
}

export async function listFiles(path: string, sudoPwd?: string): Promise<{ files: FileEntry[]; path?: string; root?: string; currentPath?: string; rootPath?: string }> {
  const params = new URLSearchParams({ path })
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest(`/api/files?${params}`, { headers })
}

export async function fileInfo(path: string) {
  return apiRequest('/api/files/info', { method: 'POST', body: { path }, timeout: 'metadata' })
}

export async function readFile(path: string, sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest<{ content?: string; truncated?: boolean }>(`/api/files/read?path=${encodeURIComponent(path)}`, { headers, timeout: 'metadata' })
}

export async function searchFiles(q: string, type = 'all', signal?: AbortSignal) {
  const params = new URLSearchParams({ q, type })
  return apiRequest<{ results: FileEntry[] }>(`/api/search?${params}`, { signal })
}

export async function trackRecent(payload: { path: string; action: string; type?: string }) {
  return apiRequest('/api/files/recent', { method: 'POST', body: payload })
}

export async function getRecent(limit = 50) {
  return apiRequest<{ files: unknown[] }>(`/api/files/recent?limit=${limit}`)
}

export async function starFile(path: string, type = 'file') {
  return apiRequest('/api/files/star', { method: 'POST', body: { path, type } })
}

export async function unstarFile(path: string) {
  return apiRequest('/api/files/star', { method: 'DELETE', body: { path } })
}

export async function getStarred() {
  return apiRequest<{ files: FileEntry[] }>('/api/files/starred')
}

export async function renameFile(path: string, newName: string, sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest('/api/files/rename', { method: 'POST', body: { oldPath: path, newName }, headers })
}

export async function copyFile(path: string, destName: string) {
  return apiRequest('/api/files/copy', { method: 'POST', body: { filePath: path, destName } })
}

export async function moveFile(path: string, destDir: string, sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest('/api/files/move', { method: 'POST', body: { sourcePath: path, destDir }, headers })
}

export async function deleteFile(path: string, sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest('/api/files/delete', { method: 'POST', body: { filePath: path }, headers })
}

export async function mkdir(parentPath: string, name: string, sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest('/api/files/mkdir', { method: 'POST', body: { parentPath, name }, headers })
}

export async function listAnyFiles(path: string, sudoPwd?: string): Promise<{ files: FileEntry[]; entries?: FileEntry[]; path?: string; root?: string; currentPath?: string; rootPath?: string }> {
  const params = new URLSearchParams({ path })
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest(`/api/files/list?${params}`, { headers })
}

export async function generateStreamToken(filePath: string, expiresIn = 24) {
  return apiRequest<{ url?: string; token?: string }>('/api/stream/generate', {
    method: 'POST',
    body: { filePath, expiresIn },
  })
}

export { DEFAULT_MEDIA_ROOT }
export { DEFAULT_ROOTS }
