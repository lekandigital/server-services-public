import { apiRequest } from './client'
import type { MediaAnalysis, MediaInfo } from '../types'

export async function getMediaInfo(path: string) {
  return apiRequest<MediaInfo>(`/api/media/info?path=${encodeURIComponent(path)}`, { timeout: 'metadata' })
}

export async function analyzeMedia(path: string, target = 'browser', autoTranscode = 'auto') {
  return apiRequest<MediaAnalysis>('/api/media/analyze', {
    method: 'POST',
    body: { filePath: path, target, autoTranscode },
    timeout: 'metadata',
  })
}

export async function fetchThumbnail(filePath: string, type: string) {
  return apiRequest<{ thumbnail: string | null; status?: string; reason?: string }>('/api/thumbnail', {
    method: 'POST',
    body: { filePath, type },
    timeout: 'thumbnail',
  })
}

export async function listSubtitles(filePath: string) {
  return apiRequest<{ subtitles?: unknown[] }>('/api/subtitles', { method: 'POST', body: { filePath }, timeout: 'metadata' })
}

export async function prepareSubtitle(filePath: string, subtitlePath: string) {
  return apiRequest('/api/subtitles/prepare', { method: 'POST', body: { filePath, subtitlePath }, timeout: 'metadata' })
}

export function subtitleVttUrl(id: string) {
  return `/api/subtitles/${encodeURIComponent(id)}.vtt`
}
