import { apiRequest } from './client'

export async function listShares() {
  return apiRequest<{ shares: unknown[] }>('/api/shares')
}

export async function createShare(path: string, permissions = 'view', expiresIn?: number) {
  return apiRequest('/api/share', { method: 'POST', body: { path, permissions, expiresIn } })
}

export async function revokeShare(id: string) {
  return apiRequest(`/api/shares/${id}`, { method: 'DELETE' })
}
