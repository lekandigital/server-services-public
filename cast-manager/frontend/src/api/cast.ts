import { apiRequest } from './client'
import type { CastDevice, CastStatus } from '../types'

export async function getCastStatus() {
  return apiRequest<CastStatus>('/api/cast/status')
}

export async function getReceiverStatus() {
  return apiRequest('/api/receiver/status')
}

export async function getCastDevices(provider = 'all') {
  return apiRequest<{ devices: CastDevice[]; errors?: unknown[] }>(`/api/cast/devices?provider=${provider}`)
}

export async function scanCastDevices(provider = 'all') {
  return apiRequest('/api/cast/devices/scan', { method: 'POST', body: { provider } })
}

export async function selectCastDevice(provider: string, deviceId: string) {
  return apiRequest('/api/cast/devices/select', { method: 'POST', body: { provider, deviceId } })
}

export async function castPreflight(body: Record<string, unknown>) {
  return apiRequest('/api/cast/preflight', { method: 'POST', body, timeout: 'cast' })
}

export async function castStart(body: Record<string, unknown>) {
  return apiRequest('/api/cast/start', { method: 'POST', body, timeout: 'cast' })
}

export async function castControl(action: string, value?: number) {
  const body: Record<string, unknown> = { action }
  if (value !== undefined) body.value = value
  return apiRequest('/api/cast/controls', { method: 'POST', body })
}

export async function getCastDiagnostics(sessionId?: string) {
  const path = sessionId ? `/api/cast/diagnostics/${sessionId}` : '/api/cast/diagnostics'
  return apiRequest(path, { timeout: 'metadata' })
}

export async function getCastDoctor() {
  return apiRequest('/api/cast/doctor', { timeout: 'metadata' })
}

export async function runCastDoctor() {
  return apiRequest('/api/cast/doctor/run', { method: 'POST', timeout: 'cast' })
}


export async function castSubtitles(body: Record<string, unknown>) {
  return apiRequest('/api/cast/subtitles', { method: 'POST', body })
}

export async function legacyCast(body: Record<string, unknown>) {
  return apiRequest('/api/cast', { method: 'POST', body, timeout: 'cast' })
}

export async function quickStreamUrl(url: string) {
  return apiRequest('/api/stream', { method: 'POST', body: { url } })
}

export interface UrlAnalysis {
  kind: 'direct-media' | 'hls' | 'known-site' | 'html-embed' | 'web-page'
  supported: boolean
  castMethod: 'direct' | 'site' | null
  message: string
}

export async function analyzeUrl(url: string) {
  return apiRequest<UrlAnalysis>('/api/url/analyze', { method: 'POST', body: { url }, timeout: 'metadata' })
}
