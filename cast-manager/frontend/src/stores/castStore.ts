import { defineStore } from 'pinia'
import {
  castControl,
  castPreflight,
  castStart,
  getCastDevices,
  getCastDoctor,
  getCastStatus,
  scanCastDevices,
  selectCastDevice,
} from '../api/cast'
import { mapCastUiState, shouldShowNowPlaying } from '../utils/cast'
import type { CastDevice, CastStatus, CastUiState } from '../types'
import { useAppStore } from './appStore'
import { ApiError } from '../api/client'
import { useSettingsStore } from './settingsStore'

export const useCastStore = defineStore('cast', {
  state: () => ({
    devices: [] as CastDevice[],
    selectedDevice: null as CastDevice | null,
    status: null as CastStatus | null,
    doctor: null as unknown,
    diagnostics: null as unknown,
    polling: null as ReturnType<typeof setInterval> | null,
    uiState: 'idle' as CastUiState,
    recentCommandAt: 0,
    scrubDragging: false,
    scrubValue: 0,
    preflight: null as unknown,
    starting: false,
    volume: Number(localStorage.getItem('cm_volume') || 100),
    backend: localStorage.getItem('cm_cast_backend') || 'auto',
    subtitleMode: localStorage.getItem('cm_subtitle_mode') || 'off',
    startPosition: 'beginning' as 'beginning' | 'resume' | 'custom',
    customStartSeconds: 0,
    selectedSubtitleId: null as string | null,
    customSubtitleSource: '',
    restartNotice: null as string | null,
    seekInFlight: false,
    queuedSeek: null as number | null,
  }),
  getters: {
    showNowPlaying(state): boolean {
      return shouldShowNowPlaying(
        state.uiState,
        !!state.status?.activeSession,
        state.recentCommandAt,
      )
    },
    currentTime(state): number {
      if (state.scrubDragging) return state.scrubValue
      return state.status?.currentTime ?? 0
    },
    duration(state): number {
      return state.status?.duration ?? state.status?.session?.duration ?? 0
    },
  },
  actions: {
    async refreshDevices() {
      try {
        const data = await getCastDevices('all')
        this.devices = data.devices || []
        const selected = this.devices.find((d) => d.selected)
        if (selected) this.selectedDevice = selected
      } catch (err) {
        useAppStore().recordApiError(err as ApiError)
      }
    },
    async scanDevices() {
      await scanCastDevices('all')
      await this.refreshDevices()
    },
    async pickDevice(device: CastDevice) {
      await selectCastDevice(device.provider, device.deviceId)
      this.selectedDevice = device
      localStorage.setItem('cm_device', device.name)
    },
    startPolling() {
      if (this.polling) return
      this.pollStatus()
      this.polling = setInterval(() => this.pollStatus(), 2500)
    },
    stopPolling() {
      if (this.polling) clearInterval(this.polling)
      this.polling = null
    },
    async pollStatus() {
      if (this.scrubDragging) return
      try {
        const status = await getCastStatus()
        this.status = status
        this.uiState = mapCastUiState(status, this.recentCommandAt)
      } catch (err) {
        useAppStore().logDiagnostic('cast', 'Status poll failed', err)
      }
    },
    async runPreflight(filePath: string) {
      this.uiState = 'preflighting'
      const body = {
        filePath,
        backend: this.backend,
        subtitle: { mode: this.subtitleMode, id: this.selectedSubtitleId, path: this.customSubtitleSource || null },
      }
      this.preflight = await castPreflight(body)
      return this.preflight
    },
    async startCast(filePath: string, title?: string) {
      this.starting = true
      this.uiState = 'starting'
      this.recentCommandAt = Date.now()
      try {
        const body: Record<string, unknown> = {
          filePath,
          backend: this.backend,
          subtitle: { mode: this.subtitleMode, id: this.selectedSubtitleId, path: this.customSubtitleSource || null },
          autoTranscode: useSettingsStore().autoTranscode ? 'auto' : 'off',
        }
        if (this.startPosition === 'resume') body.startSeconds = 'resume'
        else if (this.startPosition === 'custom') body.startSeconds = this.customStartSeconds
        const result = await castStart(body)
        this.recentCommandAt = Date.now()
        await this.pollStatus()
        this.startPolling()
        useAppStore().toast(`Casting ${title || filePath.split('/').pop()}`, 'success')
        return result
      } catch (err) {
        this.uiState = 'error'
        useAppStore().recordApiError(err as ApiError)
        useAppStore().toast(err instanceof Error ? err.message : 'Cast failed', 'error')
        throw err
      } finally {
        this.starting = false
      }
    },
    async control(action: string, value?: number) {
      this.recentCommandAt = Date.now()
      if (action === 'seek' && value !== undefined) this.uiState = 'seeking'
      try {
        const result = await castControl(action, value)
        if ((result as { restarted?: boolean; fallbackUsed?: boolean })?.restarted || (result as { fallbackUsed?: boolean })?.fallbackUsed) {
          this.uiState = 'restarting_stream'
          this.restartNotice = 'Stream restarted at the new position'
          setTimeout(() => { this.restartNotice = null }, 4000)
        }
        await this.pollStatus()
        return result
      } catch (err) {
        useAppStore().recordApiError(err as ApiError)
        useAppStore().toast(err instanceof Error ? err.message : 'Cast control failed', 'error')
        throw err
      }
    },
    async stop() {
      await this.control('stop')
      this.uiState = 'idle'
      this.status = null
    },
    setScrubDragging(dragging: boolean, value?: number) {
      this.scrubDragging = dragging
      if (value !== undefined) this.scrubValue = value
    },
    async seekFinal(seconds: number) {
      this.scrubDragging = false
      this.recentCommandAt = Date.now()
      this.queuedSeek = seconds
      if (this.seekInFlight) return
      this.seekInFlight = true
      try {
        while (this.queuedSeek !== null) {
          const target = this.queuedSeek
          this.queuedSeek = null
          this.scrubValue = target
          await this.control('seek', target)
          await new Promise((resolve) => setTimeout(resolve, 350))
        }
      } finally {
        this.seekInFlight = false
        this.scrubDragging = false
        await this.pollStatus()
      }
    },
    async loadDoctor() {
      try {
        this.doctor = await getCastDoctor()
      } catch (err) {
        useAppStore().logDiagnostic('doctor', String(err))
      }
    },
    setBackend(backend: string) {
      this.backend = backend
      localStorage.setItem('cm_cast_backend', backend)
    },
    setVolume(v: number) {
      this.volume = v
      localStorage.setItem('cm_volume', String(v))
      this.control('volume', v).catch(() => undefined)
    },
  },
})
