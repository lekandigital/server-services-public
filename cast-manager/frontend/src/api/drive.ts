import { apiRequest } from './client'

export interface DriveConfig {
  ok: boolean
  service: string
  feature: string
  port: number
  library_path: string
  current_user: string
  max_upload_mb: number
  text_preview_mb: number
}

export interface DriveEntry {
  name: string
  path: string
  type: 'folder' | 'file' | 'symlink' | 'special' | 'unreadable'
  is_dir: boolean
  is_file: boolean
  is_symlink: boolean
  is_hidden: boolean
  size: number | null
  size_human: string
  modified: string | null
  permissions: string
  owner: string | null
  group: string | null
  readable: boolean
  writable: boolean
  executable: boolean
  mime: string | null
  symlink_target: string | null
  error: string | null
}

export interface DriveListing {
  ok: boolean
  path: string
  parent: string | null
  is_root: boolean
  readable: boolean
  writable: boolean
  entries: DriveEntry[]
  error: string | null
}

export interface DrivePreview {
  ok: boolean
  kind: 'text' | 'image' | 'pdf' | 'audio' | 'video' | 'too_large' | 'unsupported'
  metadata: DriveEntry
  content?: string
  preview_url?: string
  message?: string
}

export const fetchDriveConfig = () => apiRequest<DriveConfig>('/api/files/config')
export const listDrive = (path: string) => apiRequest<DriveListing>(`/api/files/list?path=${encodeURIComponent(path)}`)
export const previewDriveFile = (path: string) => apiRequest<DrivePreview>(`/api/files/preview?path=${encodeURIComponent(path)}`, { timeout: 'metadata' })
export const mkdirDrive = (path: string, name: string) => apiRequest('/api/files/mkdir', { method: 'POST', body: { path, name } })
export const renameDrive = (path: string, newName: string) => apiRequest('/api/files/rename', { method: 'POST', body: { path, new_name: newName } })
export const copyDrive = (source: string, destination: string) => apiRequest('/api/files/copy', { method: 'POST', body: { source, destination }, timeout: 'cast' })
export const moveDrive = (source: string, destination: string) => apiRequest('/api/files/move', { method: 'POST', body: { source, destination, confirm: true }, timeout: 'cast' })
export const deleteDrive = (path: string) => apiRequest('/api/files/delete', { method: 'POST', body: { path, confirm: true }, timeout: 'cast' })
export const driveDownloadUrl = (path: string) => `/api/files/download?path=${encodeURIComponent(path)}`

export function uploadDriveFile(targetPath: string, file: File, onProgress: (percent: number) => void): Promise<{ saved_name: string; path: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    const form = new FormData()
    // Multer resolves the destination before opening the file stream, so this
    // field must precede files in the multipart body.
    form.append('path', targetPath)
    form.append('files', file, file.name)
    xhr.open('POST', '/api/files/upload')
    xhr.setRequestHeader('Accept', 'application/json')
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress(Math.round((event.loaded / event.total) * 100))
    }
    xhr.onerror = () => reject(new Error(`Upload failed for ${file.name}`))
    xhr.onload = () => {
      let payload: any
      try { payload = JSON.parse(xhr.responseText || '{}') }
      catch { return reject(new Error(`Invalid upload response for ${file.name}`)) }
      if (xhr.status < 200 || xhr.status >= 300 || !payload.ok) return reject(new Error(payload.error || `Upload failed for ${file.name}`))
      resolve(payload.uploaded[0])
    }
    xhr.send(form)
  })
}
