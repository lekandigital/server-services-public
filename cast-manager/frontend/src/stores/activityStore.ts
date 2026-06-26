import { defineStore } from 'pinia'
import { deleteTrashItem, getActivity, getTrash, restoreTrashItem } from '../api/activity'
import { getRecent, getStarred, unstarFile } from '../api/files'
import { listShares, revokeShare } from '../api/shares'
import type { ActivityEntry, FileEntry, RecentEntry, ShareEntry, TrashEntry } from '../types'
import { useAppStore } from './appStore'
import { ApiError } from '../api/client'

export interface QueueItem {
  path: string
  name: string
  type: string
}

export interface Playlist {
  id: string
  name: string
  items: QueueItem[]
}

export const useActivityStore = defineStore('activity', {
  state: () => ({
    recent: [] as RecentEntry[],
    starred: [] as FileEntry[],
    shares: [] as ShareEntry[],
    activity: [] as ActivityEntry[],
    trash: [] as TrashEntry[],
    queue: JSON.parse(localStorage.getItem('cm_queue') || '[]') as QueueItem[],
    queueIndex: Number(localStorage.getItem('cm_queue_index') || -1),
    playlists: JSON.parse(localStorage.getItem('cm_playlists') || '[]') as Playlist[],
    loading: false,
    error: null as string | null,
  }),
  actions: {
    saveQueue() {
      localStorage.setItem('cm_queue', JSON.stringify(this.queue))
      localStorage.setItem('cm_queue_index', String(this.queueIndex))
    },
    savePlaylists() {
      localStorage.setItem('cm_playlists', JSON.stringify(this.playlists))
    },
    async loadRecent() {
      this.loading = true; this.error = null
      try {
        const data = await getRecent(50)
        this.recent = (data.files || []) as RecentEntry[]
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Recent files are unavailable'
        useAppStore().recordApiError(err as ApiError)
      } finally { this.loading = false }
    },
    async loadStarred() {
      this.loading = true; this.error = null
      try { const data = await getStarred(); this.starred = data.files || [] }
      catch (err) { this.error = err instanceof Error ? err.message : 'Starred files are unavailable'; useAppStore().recordApiError(err as ApiError) }
      finally { this.loading = false }
    },
    async loadShares() {
      this.loading = true; this.error = null
      try { const data = await listShares(); this.shares = data.shares as ShareEntry[] || [] }
      catch (err) { this.error = err instanceof Error ? err.message : 'Shares are unavailable'; useAppStore().recordApiError(err as ApiError) }
      finally { this.loading = false }
    },
    async loadActivity() {
      this.loading = true; this.error = null
      try { const data = await getActivity(); this.activity = (data as { activities?: ActivityEntry[] }).activities || [] }
      catch (err) { this.error = err instanceof Error ? err.message : 'Activity is unavailable'; useAppStore().recordApiError(err as ApiError) }
      finally { this.loading = false }
    },
    async loadTrash() {
      this.loading = true; this.error = null
      try { const data = await getTrash(); this.trash = data.files as TrashEntry[] || [] }
      catch (err) { this.error = err instanceof Error ? err.message : 'Trash is unavailable'; useAppStore().recordApiError(err as ApiError) }
      finally { this.loading = false }
    },
    async unstar(path: string) { await unstarFile(path); await this.loadStarred() },
    async revokeShare(id: string) { await revokeShare(id); await this.loadShares() },
    async restoreTrash(id: number) { await restoreTrashItem(id); await this.loadTrash() },
    async deleteTrash(id: number) { await deleteTrashItem(id); await this.loadTrash() },
    addToQueue(item: QueueItem) {
      this.queue.push(item)
      this.saveQueue()
    },
    removeFromQueue(index: number) {
      this.queue.splice(index, 1)
      if (this.queueIndex >= this.queue.length) this.queueIndex = this.queue.length - 1
      this.saveQueue()
    },
    clearQueue() {
      this.queue = []
      this.queueIndex = -1
      this.saveQueue()
    },
    createPlaylist(name: string) {
      this.playlists.push({ id: String(Date.now()), name, items: [...this.queue] })
      this.savePlaylists()
    },
    removePlaylist(id: string) { this.playlists = this.playlists.filter((p) => p.id !== id); this.savePlaylists() },
  },
})
