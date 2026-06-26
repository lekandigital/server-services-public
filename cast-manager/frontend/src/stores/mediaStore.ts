import { defineStore } from 'pinia'
import { analyzeMedia, fetchThumbnail, getMediaInfo, listSubtitles } from '../api/media'
import type { MediaAnalysis, MediaInfo, SubtitleItem } from '../types'
import { useAppStore } from './appStore'
import { ApiError } from '../api/client'

export type ThumbState = 'loading' | 'available' | 'unavailable' | 'error'

export const useMediaStore = defineStore('media', {
  state: () => ({
    infoCache: {} as Record<string, MediaInfo>,
    analysisCache: {} as Record<string, MediaAnalysis>,
    subtitleCache: {} as Record<string, SubtitleItem[]>,
    thumbnails: {} as Record<string, { state: ThumbState; url?: string; reason?: string }>,
  }),
  actions: {
    async loadInfo(path: string) {
      if (this.infoCache[path]) return this.infoCache[path]
      const info = await getMediaInfo(path)
      this.infoCache[path] = info
      return info
    },
    async loadAnalysis(path: string, target = 'browser') {
      const key = `${path}:${target}`
      if (this.analysisCache[key]) return this.analysisCache[key]
      const analysis = await analyzeMedia(path, target)
      this.analysisCache[key] = analysis
      return analysis
    },
    async loadSubtitles(path: string) {
      if (this.subtitleCache[path]) return this.subtitleCache[path]
      const data = await listSubtitles(path)
      const subs = (data.subtitles || []) as SubtitleItem[]
      this.subtitleCache[path] = subs
      return subs
    },
    async loadThumbnail(path: string, type: string) {
      if (this.thumbnails[path]?.state === 'available') return this.thumbnails[path]
      this.thumbnails[path] = { state: 'loading' }
      try {
        const data = await fetchThumbnail(path, type)
        if (data.thumbnail) {
          this.thumbnails[path] = { state: 'available', url: data.thumbnail }
        } else {
          this.thumbnails[path] = { state: data.status === 'failed' ? 'error' : 'unavailable', reason: data.reason }
          useAppStore().logDiagnostic('thumbnail', `Unavailable for ${path}`, data)
        }
      } catch (err) {
        this.thumbnails[path] = { state: 'error', reason: err instanceof Error ? err.message : 'error' }
        if (!(err instanceof ApiError)) useAppStore().logDiagnostic('thumbnail', String(err))
      }
      return this.thumbnails[path]
    },
    retryThumbnail(path: string, type: string) {
      delete this.thumbnails[path]
      return this.loadThumbnail(path, type)
    },
  },
})
