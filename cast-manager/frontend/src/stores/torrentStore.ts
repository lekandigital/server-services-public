import { defineStore } from 'pinia'
import {
  addTorrents,
  listTorrents,
  pauseAllTorrents,
  pauseTorrent,
  removeTorrent,
  resumeAllTorrents,
  resumeTorrent,
  setTorrentPriority,
  uploadTorrent,
} from '../api/torrents'
import type { TorrentItem } from '../types'
import { useAppStore } from './appStore'
import { ApiError } from '../api/client'

export const useTorrentStore = defineStore('torrents', {
  state: () => ({
    torrents: [] as TorrentItem[],
    filter: 'all' as 'all' | 'active' | 'completed' | 'stopped',
    search: '',
    loading: false,
    error: null as string | null,
  }),
  getters: {
    filtered(state) {
      let items = state.torrents
      if (state.filter === 'active') items = items.filter((t) => ['downloading', 'seeding'].includes(t.status))
      else if (state.filter === 'completed') items = items.filter((t) => t.progress >= 1)
      else if (state.filter === 'stopped') items = items.filter((t) => ['stopped', 'paused'].includes(t.status))
      if (state.search.trim()) {
        const q = state.search.toLowerCase()
        items = items.filter((t) => t.name.toLowerCase().includes(q))
      }
      return items
    },
    stats(state) {
      const active = state.torrents.filter((t) => t.status === 'downloading').length
      const completed = state.torrents.filter((t) => t.progress >= 1).length
      const down = state.torrents.reduce((s, t) => s + (t.downloadSpeed || 0), 0)
      return { active, completed, down }
    },
  },
  actions: {
    async load() {
      this.loading = true
      this.error = null
      try {
        const data = await listTorrents()
        this.torrents = data.torrents || []
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Failed to load torrents'
        useAppStore().recordApiError(err as ApiError)
      } finally {
        this.loading = false
      }
    },
    async addMagnet(magnet: string) {
      await addTorrents([magnet])
      useAppStore().toast('Torrent added', 'success')
      await this.load()
    },
    async addMagnets(magnets: string[]) {
      await addTorrents(magnets)
      useAppStore().toast(`Added ${magnets.length} torrent(s)`, 'success')
      await this.load()
    },
    async upload(file: File) {
      await uploadTorrent(file)
      useAppStore().toast('Torrent uploaded', 'success')
      await this.load()
    },
    async pause(id: number) { await pauseTorrent(id); await this.load() },
    async resume(id: number) { await resumeTorrent(id); await this.load() },
    async remove(id: number, deleteData = false) {
      await removeTorrent(id, deleteData)
      useAppStore().toast(deleteData ? 'Torrent and data removed' : 'Torrent removed', 'info')
      await this.load()
    },
    async setPriority(id: number, priority: string) {
      await setTorrentPriority(id, priority)
      await this.load()
    },
    async pauseAll() { await pauseAllTorrents(); await this.load() },
    async resumeAll() { await resumeAllTorrents(); await this.load() },
  },
})
