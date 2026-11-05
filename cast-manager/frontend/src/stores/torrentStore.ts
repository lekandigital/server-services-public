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
    sort: 'added_desc' as 'added_desc' | 'added_asc' | 'name_asc' | 'name_desc' | 'status' | 'progress_desc',
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
      return [...items].sort((a, b) => {
        if (state.sort === 'added_asc') return (a.addedDate || 0) - (b.addedDate || 0)
        if (state.sort === 'name_asc') return a.name.localeCompare(b.name)
        if (state.sort === 'name_desc') return b.name.localeCompare(a.name)
        if (state.sort === 'status') return a.status.localeCompare(b.status)
        if (state.sort === 'progress_desc') return (b.progress || 0) - (a.progress || 0)
        return (b.addedDate || 0) - (a.addedDate || 0)
      })
    },
    stats(state) {
      const active = state.torrents.filter((t) => t.status === 'downloading').length
      const completed = state.torrents.filter((t) => t.progress >= 1).length
      const down = state.torrents.reduce((s, t) => s + (t.downloadSpeed || 0), 0)
      return { active, completed, down }
    },
  },
  actions: {
    async load(background = false) {
      if (!background) this.loading = true
      this.error = null
      try {
        const data = await listTorrents()
        this.torrents = data.torrents || []
      } catch (err) {
        this.error = err instanceof Error ? err.message : 'Failed to load torrents'
        useAppStore().recordApiError(err as ApiError)
      } finally {
        if (!background) this.loading = false
      }
    },
    async addMagnet(magnet: string) {
      await addTorrents([magnet])
      useAppStore().toast('Torrent added', 'success')
      await this.load(true)
    },
    async addMagnets(magnets: string[]) {
      await addTorrents(magnets)
      useAppStore().toast(`Added ${magnets.length} torrent(s)`, 'success')
      await this.load(true)
    },
    async upload(file: File) {
      await uploadTorrent(file)
      useAppStore().toast('Torrent uploaded', 'success')
      await this.load(true)
    },
    async pause(id: number) { await pauseTorrent(id); await this.load(true) },
    async resume(id: number) { await resumeTorrent(id); await this.load(true) },
    async remove(id: number, deleteData = false) {
      await removeTorrent(id, deleteData)
      useAppStore().toast(deleteData ? 'Torrent and data removed' : 'Torrent removed', 'info')
      await this.load(true)
    },
    async setPriority(id: number, priority: string) {
      await setTorrentPriority(id, priority)
      await this.load(true)
    },
    async pauseAll() { await pauseAllTorrents(); await this.load(true) },
    async resumeAll() { await resumeAllTorrents(); await this.load(true) },
  },
})
