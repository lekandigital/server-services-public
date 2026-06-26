import { apiRequest } from './client'
import type { TorrentItem } from '../types'

export async function listTorrents() {
  return apiRequest<{ torrents: TorrentItem[] }>('/api/torrents')
}

export async function addTorrents(magnets: string[]) {
  return apiRequest('/api/torrents', { method: 'POST', body: { magnets } })
}

export async function uploadTorrent(file: File) {
  const form = new FormData()
  form.append('torrent', file)
  const res = await fetch('/api/torrents/upload', { method: 'POST', body: form, headers: { Accept: 'application/json' } })
  const ct = res.headers.get('content-type') || ''
  const data = ct.includes('json') ? await res.json() : { error: await res.text() }
  if (!res.ok) throw new Error((data as { error?: string }).error || 'Upload failed')
  return data
}

export async function pauseTorrent(id: number) {
  return apiRequest(`/api/torrents/${id}/pause`, { method: 'POST' })
}

export async function resumeTorrent(id: number) {
  return apiRequest(`/api/torrents/${id}/resume`, { method: 'POST' })
}

export async function removeTorrent(id: number, deleteData = false) {
  return apiRequest(`/api/torrents/${id}?deleteData=${deleteData}`, { method: 'DELETE' })
}

export async function setTorrentPriority(id: number, priority: string) {
  return apiRequest(`/api/torrents/${id}/priority`, { method: 'POST', body: { priority } })
}

export async function torrentInfo(id: number) {
  return apiRequest(`/api/torrents/${id}/info`, { timeout: 'metadata' })
}

export async function pauseAllTorrents() {
  return apiRequest('/api/torrents/pause-all', { method: 'POST' })
}

export async function resumeAllTorrents() {
  return apiRequest('/api/torrents/resume-all', { method: 'POST' })
}

export async function batchTorrentAction(action: string, ids: number[]) {
  return apiRequest('/api/torrents/batch', { method: 'POST', body: { action, ids } })
}
