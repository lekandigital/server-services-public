import { defineStore } from 'pinia'

export const useSettingsStore = defineStore('settings', {
  state: () => ({
    defaultView: (localStorage.getItem('cm_view_mode') as 'list' | 'grid') || 'list',
    defaultCastBackend: localStorage.getItem('cm_cast_backend') || 'auto',
    subtitlePreference: localStorage.getItem('cm_subtitle_mode') || 'auto',
    autoTranscode: localStorage.getItem('cm_auto_transcode') !== '0',
    allowPretranscode: localStorage.getItem('cm_allow_pretranscode') === '1',
    diagnosticsVerbosity: localStorage.getItem('cm_diag_verbosity') || 'normal',
    statusPollInterval: Number(localStorage.getItem('cm_poll_interval') || 2500),
    theme: 'light',
  }),
  actions: {
    persist() {
      localStorage.setItem('cm_view_mode', this.defaultView)
      localStorage.setItem('cm_cast_backend', this.defaultCastBackend)
      localStorage.setItem('cm_subtitle_mode', this.subtitlePreference)
      localStorage.setItem('cm_auto_transcode', this.autoTranscode ? '1' : '0')
      localStorage.setItem('cm_allow_pretranscode', this.allowPretranscode ? '1' : '0')
      localStorage.setItem('cm_diag_verbosity', this.diagnosticsVerbosity)
      localStorage.setItem('cm_poll_interval', String(this.statusPollInterval))
      localStorage.setItem('cm_theme', this.theme)
    },
    resetLocal() {
      const keep = ['cm_device']
      Object.keys(localStorage).filter((k) => k.startsWith('cm_') && !keep.includes(k)).forEach((k) => localStorage.removeItem(k))
      this.$reset()
      this.persist()
    },
  },
})
