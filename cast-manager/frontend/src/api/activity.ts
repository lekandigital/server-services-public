import { apiRequest } from './client'

export async function getActivity(page = 1, limit = 50) {
  return apiRequest(`/api/activity?page=${page}&limit=${limit}`)
}

export async function getTrash() {
  return apiRequest<{ files: unknown[] }>('/api/files/trash')
}

export async function restoreTrashItem(id: number) {
  return apiRequest('/api/files/restore', { method: 'POST', body: { id } })
}

export async function deleteTrashItem(id: number, sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest(`/api/files/trash/${id}`, { method: 'DELETE', body: { sudoPwd }, headers })
}

export async function emptyTrash(sudoPwd?: string) {
  const headers = sudoPwd ? { 'X-Sudo-Password': sudoPwd } : undefined
  return apiRequest('/api/files/trash/empty', { method: 'DELETE', body: { sudoPwd }, headers })
}
