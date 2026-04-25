import { defineStore } from 'pinia'
import { useLocalStorage } from '@vueuse/core'
import { ref, onUnmounted } from 'vue'
import type { ThemeMode, ThemePreset } from '@/types/settings'

export const THEME_PRESETS: { id: ThemePreset; label: string; mode: ThemeMode }[] = [
  { id: 'default-dark', label: 'Default Dark', mode: 'dark' },
  { id: 'default-light', label: 'Default Light', mode: 'light' },
  { id: 'hacker', label: 'Hacker', mode: 'dark' },
  { id: 'paper', label: 'Paper', mode: 'light' },
  { id: 'high-contrast', label: 'High Contrast', mode: 'dark' },
]

/** Map each theme preset to its system-preferred counterpart */
const SYSTEM_DARK_MAP: Record<string, ThemePreset> = {
  'default-light': 'default-dark',
  'default-dark': 'default-dark',
  'paper': 'default-dark',
  'hacker': 'hacker',
  'high-contrast': 'high-contrast',
}
const SYSTEM_LIGHT_MAP: Record<string, ThemePreset> = {
  'default-dark': 'default-light',
  'default-light': 'default-light',
  'hacker': 'default-light',
  'high-contrast': 'default-light',
  'paper': 'paper',
}

function getSystemThemeMode(): ThemeMode {
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

function getDefaultPreset(): ThemePreset {
  return getSystemThemeMode() === 'dark' ? 'default-dark' : 'default-light'
}

export const useSettingsStore = defineStore('settings', () => {
  const theme = useLocalStorage<ThemeMode>('theme', getSystemThemeMode())
  const themePreset = useLocalStorage<ThemePreset>('themePreset', getDefaultPreset())
  const followSystemTheme = useLocalStorage('followSystemTheme', true)
  const enableMarkdown = useLocalStorage('markdown', true)
  const showSystemMessages = useLocalStorage('systemMessages', true)
  const historyLength = useLocalStorage('historyMessageLength', 10)
  const baseUrl = useLocalStorage('baseUrl', '/api')
  const autoTitle = useLocalStorage('autoTitle', true)
  const showMetrics = useLocalStorage('showMetrics', true)
  const showTokenCounter = useLocalStorage('showTokenCounter', true)
  const sendOnEnter = useLocalStorage('sendOnEnter', true)

  // Track the user's explicit preset choice (for system theme switching)
  const userPresetChoice = ref<ThemePreset>(themePreset.value)

  // Migrate old URL format
  if (
    baseUrl.value.includes('11434') ||
    baseUrl.value.includes('11435') ||
    baseUrl.value.includes('localhost')
  ) {
    baseUrl.value = '/api'
  }

  function applyTheme() {
    const root = document.documentElement
    // Remove all theme classes
    root.classList.remove(
      'dark', 'light',
      'theme-default-dark', 'theme-default-light',
      'theme-hacker', 'theme-paper', 'theme-high-contrast',
    )
    // Apply mode class (dark/light) and preset class
    root.classList.add(theme.value)
    root.classList.add(`theme-${themePreset.value}`)
  }

  function setThemePreset(preset: ThemePreset) {
    themePreset.value = preset
    userPresetChoice.value = preset
    const presetConfig = THEME_PRESETS.find((p) => p.id === preset)
    if (presetConfig) {
      theme.value = presetConfig.mode
    }
    applyTheme()
  }

  function toggleTheme() {
    // Quick toggle between default dark/light
    if (theme.value === 'dark') {
      setThemePreset('default-light')
    } else {
      setThemePreset('default-dark')
    }
  }

  /** React to OS dark/light mode changes */
  function onSystemThemeChange(e: MediaQueryListEvent) {
    if (!followSystemTheme.value) return
    const systemMode = e.matches ? 'dark' : 'light'
    const mapped = systemMode === 'dark'
      ? SYSTEM_DARK_MAP[userPresetChoice.value] ?? 'default-dark'
      : SYSTEM_LIGHT_MAP[userPresetChoice.value] ?? 'default-light'
    themePreset.value = mapped
    theme.value = systemMode
    applyTheme()
  }

  // Listen for OS theme changes
  const mq = window.matchMedia('(prefers-color-scheme: dark)')
  mq.addEventListener('change', onSystemThemeChange)
  onUnmounted(() => mq.removeEventListener('change', onSystemThemeChange))

  return {
    theme,
    themePreset,
    followSystemTheme,
    enableMarkdown,
    showSystemMessages,
    historyLength,
    baseUrl,
    autoTitle,
    showMetrics,
    showTokenCounter,
    sendOnEnter,
    applyTheme,
    toggleTheme,
    setThemePreset,
  }
})
