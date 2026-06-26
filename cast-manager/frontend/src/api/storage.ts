import { apiRequest } from './client'

export async function getStorageStats() {
  return apiRequest('/api/storage/stats', { timeout: 'metadata' })
}

export async function getStorageDirs(path: string) {
  return apiRequest(`/api/storage/dirs?path=${encodeURIComponent(path)}`, { timeout: 'metadata' })
}

export async function getDiskStats() {
  return apiRequest('/api/disk')
}

export async function listStreamTokens() {
  return apiRequest('/api/stream/tokens')
}

export async function revokeStreamToken(token: string) {
  return apiRequest(`/api/stream/tokens/${token}`, { method: 'DELETE' })
}
